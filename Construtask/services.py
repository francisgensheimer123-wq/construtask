import math
from decimal import Decimal

import pandas as pd
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError

from .models import PlanoContas


def tratar_decimal(valor):
    if valor is None:
        return None
    if isinstance(valor, float) and math.isnan(valor):
        return None
    if str(valor).strip() == "":
        return None
    return Decimal(str(valor))


def normalizar_codigo(codigo):
    codigo = str(codigo or "").strip().replace(" ", "")
    if codigo.endswith(".0"):
        codigo = codigo[:-2]
    return codigo


def importar_plano_contas_excel(arquivo, obra=None):
    """
    Importa plano de contas Excel.

    Quando ``obra`` é informada, a importação fica isolada naquela obra.
    Mantemos ``obra=None`` por retrocompatibilidade com o comportamento legado,
    usado em testes antigos e em cargas locais sem contexto de obra.
    """
    df = pd.read_excel(arquivo, dtype=str)
    df = df.where(pd.notnull(df), None)

    codigos_excel = []
    for _, row in df.iterrows():
        codigo = normalizar_codigo(row.get("ITEM"))
        if codigo:
            codigos_excel.append(codigo)

    possui_filhos = set()
    for codigo in codigos_excel:
        prefixo = codigo + "."
        for outro in codigos_excel:
            if outro.startswith(prefixo):
                possui_filhos.add(codigo)
                break

    with transaction.atomic():
        # Deletar apenas os planos de contas da obra específica quando houver contexto.
        # Sem obra, preservamos o comportamento legado e removemos toda a estrutura.
        try:
            planos_a_deletar = PlanoContas.objects.filter(obra=obra) if obra else PlanoContas.objects.all()
            planos_a_deletar.delete()
        except ProtectedError as exc:
            if obra:
                mensagem = (
                    "Nao e possivel importar um novo plano de contas enquanto existirem "
                    "compromissos, medicoes ou notas vinculados ao plano atual."
                )
            else:
                mensagem = (
                    "Nao e possivel substituir o plano de contas legado enquanto existirem "
                    "compromissos, medicoes ou notas vinculados ao plano atual."
                )
            raise ValidationError(mensagem) from exc

        objetos_criados = {}
        for _, row in df.iterrows():
            codigo = normalizar_codigo(row.get("ITEM"))
            if not codigo:
                continue

            descricao = str(row.get("DESCRIÇÃO", row.get("DESCRIÃ‡ÃƒO", ""))).strip()
            unidade = row.get("UN")
            quantidade = tratar_decimal(row.get("QTD"))
            valor_unitario = tratar_decimal(row.get("VALOR UNIT."))
            partes = codigo.split(".")

            if codigo not in possui_filhos:
                while len(partes) < 6:
                    partes.append("1")

            for i in range(1, len(partes) + 1):
                codigo_nivel = ".".join(partes[:i])
                if codigo_nivel in objetos_criados:
                    continue

                pai_codigo = ".".join(partes[: i - 1]) if i > 1 else None
                pai = objetos_criados.get(pai_codigo)
                descricao_nivel = descricao

                # Vincular a obra se fornecida
                kwargs = {}
                if obra:
                    kwargs['obra'] = obra
                kwargs['codigo'] = codigo_nivel
                kwargs['descricao'] = descricao_nivel
                kwargs['parent'] = pai
                kwargs['unidade'] = unidade if i == len(partes) else None
                kwargs['quantidade'] = quantidade if i == len(partes) else None
                kwargs['valor_unitario'] = valor_unitario if i == len(partes) else None
                
                obj = PlanoContas.objects.create(**kwargs)
                objetos_criados[codigo_nivel] = obj


def obter_dados_contrato(contrato):
    centros_queryset = contrato.itens.select_related("centro_custo").order_by("centro_custo__tree_id", "centro_custo__lft")
    centros = []
    vistos = set()
    for item in centros_queryset:
        centro = item.centro_custo
        if centro.id in vistos:
            continue
        vistos.add(centro.id)
        centros.append(
            {
                "id": centro.id,
                "codigo": centro.codigo,
                "descricao": centro.descricao,
                "unidade": centro.unidade or "",
                "valor_unitario": str(item.valor_unitario),
            }
        )

    if not centros and contrato.centro_custo_id:
        centro = contrato.centro_custo
        centros.append(
            {
                "id": centro.id,
                "codigo": centro.codigo,
                "descricao": centro.descricao,
                "unidade": centro.unidade or "",
                "valor_unitario": str(centro.valor_unitario or Decimal("0.00")),
            }
        )

    return {
        "fornecedor": contrato.fornecedor,
        "cnpj": contrato.cnpj,
        "responsavel": contrato.responsavel,
        "valor_contrato": str(contrato.valor_contratado),
        "centro_custo": contrato.centro_custo.codigo if contrato.centro_custo else "",
        "centros_custo": centros,
    }


def obter_dados_medicao(medicao):
    centros_queryset = medicao.itens.select_related("centro_custo").order_by("centro_custo__tree_id", "centro_custo__lft")
    centros = []
    vistos = set()
    for item in centros_queryset:
        centro = item.centro_custo
        if centro.id in vistos:
            continue
        vistos.add(centro.id)
        centros.append(
            {
                "id": centro.id,
                "codigo": centro.codigo,
                "descricao": centro.descricao,
                "unidade": centro.unidade or "",
                "valor_unitario": str(item.valor_unitario),
            }
        )

    if not centros and medicao.centro_custo_id:
        centro = medicao.centro_custo
        centros.append(
            {
                "id": centro.id,
                "codigo": centro.codigo,
                "descricao": centro.descricao,
                "unidade": centro.unidade or "",
                "valor_unitario": str(centro.valor_unitario or Decimal("0.00")),
            }
        )

    return {
        "fornecedor": medicao.fornecedor,
        "cnpj": medicao.cnpj,
        "responsavel": medicao.responsavel,
        "centros_custo": centros,
    }


def validar_rateio_nota(nota, itens_rateio):
    total_rateio = Decimal("0.00")
    centros_permitidos = None

    if nota.pedido_compra:
        centros_permitidos = set(nota.pedido_compra.itens.values_list("centro_custo_id", flat=True))
        if not centros_permitidos and nota.pedido_compra.centro_custo_id:
            centros_permitidos = {nota.pedido_compra.centro_custo_id}
    elif nota.medicao:
        centros_permitidos = set(nota.medicao.itens.values_list("centro_custo_id", flat=True))
        if not centros_permitidos and nota.medicao.centro_custo_id:
            centros_permitidos = {nota.medicao.centro_custo_id}

    for centro, valor in itens_rateio:
        valor = valor or Decimal("0.00")
        total_rateio += valor

        if centros_permitidos is not None and centro.id not in centros_permitidos:
            raise ValidationError(
                f"O centro de custo '{centro}' não pertence à origem desta nota."
            )

    if total_rateio > nota.valor_total:
        raise ValidationError("A soma do rateio ultrapassa o valor da nota fiscal.")
