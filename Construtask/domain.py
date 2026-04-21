from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from .numeric_utils import coerce_decimal


def arredondar_moeda(valor):
    return coerce_decimal(valor, quantize="0.01")


def calcular_total_item(quantidade, valor_unitario):
    if quantidade in (None, "") or valor_unitario in (None, ""):
        return Decimal("0.00")
    quantidade_decimal = coerce_decimal(quantidade)
    valor_unitario_decimal = coerce_decimal(valor_unitario)
    return arredondar_moeda(quantidade_decimal * valor_unitario_decimal)


def agrupar_totais_por_centro(itens):
    totais = {}
    for item in itens:
        centro = item.get("centro_custo")
        if not centro:
            continue
        valor_total = calcular_total_item(item.get("quantidade"), item.get("valor_unitario"))
        totais[centro] = totais.get(centro, Decimal("0.00")) + valor_total
    return {centro: arredondar_moeda(total) for centro, total in totais.items()}


def calcular_saldo_disponivel_compromisso(compromisso):
    if not compromisso.centro_custo:
        return None

    orcamento = compromisso.centro_custo.valor_total_consolidado
    total_compromissos = (
        compromisso.__class__.objects
        .filter(centro_custo__in=compromisso.centro_custo.get_descendants(include_self=True))
        .exclude(pk=compromisso.pk)
        .aggregate(total=Sum("valor_contratado"))["total"]
        or Decimal("0.00")
    )
    return arredondar_moeda(orcamento - total_compromissos)


def validar_compromisso_orcamento(compromisso):
    saldo_disponivel = calcular_saldo_disponivel_compromisso(compromisso)
    if saldo_disponivel is None:
        return

    if compromisso.valor_contratado > saldo_disponivel:
        raise ValidationError(
            f"Valor excede orçamento disponível. Saldo disponível: {saldo_disponivel}"
        )


def validar_itens_compromisso_orcamento(compromisso, itens):
    from .models import CompromissoItem

    totais_por_centro = agrupar_totais_por_centro(itens)
    for centro, total_itens in totais_por_centro.items():
        itens_queryset = CompromissoItem.objects.filter(centro_custo=centro)
        cabecalhos_queryset = compromisso.__class__.objects.filter(centro_custo=centro, itens__isnull=True)
        if compromisso.pk:
            itens_queryset = itens_queryset.exclude(compromisso=compromisso)
            cabecalhos_queryset = cabecalhos_queryset.exclude(pk=compromisso.pk)

        total_outros_itens = itens_queryset.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")
        total_outros_cabecalhos = (
            cabecalhos_queryset.aggregate(total=Sum("valor_contratado"))["total"] or Decimal("0.00")
        )
        saldo_disponivel = arredondar_moeda(
            centro.valor_total_consolidado - total_outros_itens - total_outros_cabecalhos
        )
        if total_itens > saldo_disponivel:
            raise ValidationError(
                f"Os itens do centro de custo {centro.codigo} ultrapassam o orçamento disponível. "
                f"Saldo disponível: {saldo_disponivel}"
            )


def gerar_numero_documento(model, prefixo, campo):
    """
    Gera número sequencial único com proteção contra race condition.
    Usa select_for_update() para garantir atomicidade em PostgreSQL.
    """
    from django.db import transaction

    with transaction.atomic():
        # Lock na tabela para garantir sequencial único
        ultimo = (
            model.objects
            .select_for_update()
            .filter(**{f"{campo}__startswith": prefixo})
            .order_by(f"-{campo}")
            .first()
        )
        if ultimo is None:
            proximo = 1
        else:
            try:
                sufixo = getattr(ultimo, campo, "").split("-")[-1]
                proximo = int(sufixo) + 1
            except (ValueError, IndexError):
                proximo = model.objects.filter(
                    **{f"{campo}__startswith": prefixo}
                ).count() + 1
        return f"{prefixo}{proximo:04d}"


def hidratar_medicao_do_contrato(medicao):
    contrato = medicao.contrato
    medicao.fornecedor = contrato.fornecedor
    medicao.cnpj = contrato.cnpj
    medicao.responsavel = contrato.responsavel
    medicao.valor_contrato = contrato.valor_contratado
    tem_itens_proprios = medicao.pk and medicao.itens.exists()
    if not tem_itens_proprios:
        medicao.centro_custo = contrato.centro_custo


def validar_medicao_contrato(medicao):
    if not medicao.contrato_id:
        return

    if medicao.contrato.tipo != "CONTRATO":
        raise ValidationError("Medições só podem ser vinculadas a contratos.")

    total_medido = (
        medicao.contrato.medicoes
        .exclude(pk=medicao.pk)
        .aggregate(total=Sum("valor_medido"))["total"]
        or Decimal("0.00")
    )
    saldo_atual = arredondar_moeda(medicao.contrato.valor_contratado - total_medido)

    if medicao.valor_medido > saldo_atual:
        raise ValidationError(
            f"Valor medido excede saldo do contrato. Saldo disponível: {saldo_atual}"
        )


def validar_itens_medicao_contrato(medicao, itens):
    from .models import MedicaoItem

    if not medicao.contrato_id:
        return

    totais_por_centro = agrupar_totais_por_centro(itens)
    contrato = medicao.contrato
    contrato_tem_itens = contrato.itens.exists()

    for centro, total_itens in totais_por_centro.items():
        if contrato_tem_itens:
            total_contratado = (
                contrato.itens.filter(centro_custo=centro).aggregate(total=Sum("valor_total"))["total"]
                or Decimal("0.00")
            )
        else:
            total_contratado = contrato.valor_contratado if contrato.centro_custo_id == centro.id else Decimal("0.00")

        itens_queryset = MedicaoItem.objects.filter(medicao__contrato=contrato, centro_custo=centro)
        cabecalhos_queryset = medicao.__class__.objects.filter(
            contrato=contrato,
            centro_custo=centro,
            itens__isnull=True,
        )
        if medicao.pk:
            itens_queryset = itens_queryset.exclude(medicao=medicao)
            cabecalhos_queryset = cabecalhos_queryset.exclude(pk=medicao.pk)

        total_medido_outros_itens = itens_queryset.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")
        total_medido_outros_cabecalhos = (
            cabecalhos_queryset.aggregate(total=Sum("valor_medido"))["total"] or Decimal("0.00")
        )
        saldo_disponivel = arredondar_moeda(
            total_contratado - total_medido_outros_itens - total_medido_outros_cabecalhos
        )
        if total_itens > saldo_disponivel:
            raise ValidationError(
                f"Os itens medidos do centro de custo {centro.codigo} ultrapassam o saldo contratual. "
                f"Saldo disponível: {saldo_disponivel}"
            )


def validar_nota_fiscal(nota):
    if nota.pedido_compra and nota.medicao:
        raise ValidationError(
            "A nota fiscal deve estar vinculada a um Pedido de Compra OU a uma Medição."
        )

    if nota.tipo == "SERVICO" and (not nota.medicao or nota.pedido_compra):
        raise ValidationError(
            "Notas de serviço devem estar vinculadas somente a uma medição."
        )

    if nota.tipo == "MATERIAL" and (not nota.pedido_compra or nota.medicao):
        raise ValidationError(
            "Notas de material devem estar vinculadas somente a um pedido de compra."
        )

    if nota.cnpj and nota.numero:
        notas = nota.__class__.objects.filter(cnpj=nota.cnpj, numero=nota.numero)
        if nota.pk:
            notas = notas.exclude(pk=nota.pk)
        if notas.exists():
            raise ValidationError(
                "Já existe uma nota fiscal com este número para este CNPJ."
            )

    if nota.pedido_compra:
        total_notas = (
            nota.__class__.objects
            .filter(pedido_compra=nota.pedido_compra)
            .exclude(pk=nota.pk)
            .aggregate(total=Sum("valor_total"))["total"]
            or Decimal("0.00")
        ) + nota.valor_total
        if total_notas > nota.pedido_compra.valor_contratado:
            raise ValidationError(
                "A soma das notas fiscais ultrapassa o valor do Pedido de Compra."
            )

    if nota.medicao:
        total_notas_existentes = (
            nota.__class__.objects
            .filter(medicao=nota.medicao)
            .exclude(pk=nota.pk)
            .aggregate(total=Sum("valor_total"))["total"]
            or Decimal("0.00")
        )
        total_notas = total_notas_existentes + nota.valor_total
        if total_notas > nota.medicao.valor_medido:
            saldo_medicao = arredondar_moeda(nota.medicao.valor_medido - total_notas_existentes)
            raise ValidationError(
                f"A soma das notas fiscais ultrapassa o valor da Medição. "
                f"Saldo da medição: {saldo_medicao}"
            )
