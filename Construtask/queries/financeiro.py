from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DateField, Sum
from django.db.models.functions import Coalesce, TruncMonth

from ..domain import arredondar_moeda
from ..models import (
    AditivoContrato,
    Compromisso,
    CompromissoItem,
    FechamentoMensal,
    Medicao,
    MedicaoItem,
    NotaFiscal,
    NotaFiscalCentroCusto,
    Obra,
    PlanoContas,
)


def adicionar_um_mes(data_base):
    if data_base.month == 12:
        return data_base.replace(year=data_base.year + 1, month=1, day=1)
    return data_base.replace(month=data_base.month + 1, day=1)


def parse_int_query_param(valor, default):
    if valor in (None, ""):
        return default
    bruto = str(valor).strip().replace(".", "").replace(",", "")
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return default


def _obter_prazo_deltas_contratuais(*, obra=None):
    prazo_totais = AditivoContrato.objects.filter(tipo="PRAZO")
    if obra:
        prazo_totais = prazo_totais.filter(contrato__obra=obra)
    prazo_totais = prazo_totais.values("contrato_id").annotate(total=Sum("delta_dias"))

    prazo_deltas = {}
    maior_delta_positivo = 0
    menor_delta_negativo = 0
    for row in prazo_totais:
        delta = row["total"] or 0
        prazo_deltas[row["contrato_id"]] = delta
        maior_delta_positivo = max(maior_delta_positivo, delta)
        menor_delta_negativo = min(menor_delta_negativo, delta)
    return prazo_deltas, maior_delta_positivo, menor_delta_negativo


def construir_dados_fechamento_mensal(*, obra=None, ano=None, mes=None):
    hoje = date.today()
    ano = ano or hoje.year
    mes = mes or hoje.month
    obras = Obra.objects.order_by("codigo")

    compromissos = Compromisso.objects.filter(data_assinatura__year=ano, data_assinatura__month=mes)
    medicoes = Medicao.objects.filter(data_medicao__year=ano, data_medicao__month=mes)
    notas = NotaFiscal.objects.filter(data_emissao__year=ano, data_emissao__month=mes)
    if obra:
        compromissos = compromissos.filter(obra=obra)
        medicoes = medicoes.filter(obra=obra)
        notas = notas.filter(obra=obra)

    itens_compromisso = CompromissoItem.objects.select_related("centro_custo", "compromisso").filter(
        compromisso__data_assinatura__year=ano,
        compromisso__data_assinatura__month=mes,
    )
    itens_medicao = MedicaoItem.objects.select_related("centro_custo", "medicao").filter(
        medicao__data_medicao__year=ano,
        medicao__data_medicao__month=mes,
    )
    rateios_nota = NotaFiscalCentroCusto.objects.select_related("centro_custo", "nota_fiscal").filter(
        nota_fiscal__data_emissao__year=ano,
        nota_fiscal__data_emissao__month=mes,
    )
    if obra:
        itens_compromisso = itens_compromisso.filter(compromisso__obra=obra)
        itens_medicao = itens_medicao.filter(medicao__obra=obra)
        rateios_nota = rateios_nota.filter(nota_fiscal__obra=obra)

    plano_qs = PlanoContas.objects.filter(obra=obra) if obra else PlanoContas.objects.all()
    nodes_by_id = {n.id: n for n in plano_qs.only("id", "parent_id", "level", "codigo", "descricao")}

    def get_nivel5_ancestor(node_id):
        node = nodes_by_id.get(node_id)
        while node:
            if node.level == 4:
                return node
            if node.level < 4:
                return None
            node = nodes_by_id.get(node.parent_id)
        return None

    resumo_nivel5 = defaultdict(
        lambda: {"comprometido": Decimal("0.00"), "medido": Decimal("0.00"), "notas": Decimal("0.00")}
    )
    for item in itens_compromisso:
        anc = get_nivel5_ancestor(item.centro_custo_id)
        if anc:
            resumo_nivel5[anc.id]["centro"] = anc
            resumo_nivel5[anc.id]["comprometido"] += item.valor_total or Decimal("0.00")
    for item in itens_medicao:
        anc = get_nivel5_ancestor(item.centro_custo_id)
        if anc:
            resumo_nivel5[anc.id]["centro"] = anc
            resumo_nivel5[anc.id]["medido"] += item.valor_total or Decimal("0.00")
    for item in rateios_nota:
        anc = get_nivel5_ancestor(item.centro_custo_id)
        if anc:
            resumo_nivel5[anc.id]["centro"] = anc
            resumo_nivel5[anc.id]["notas"] += item.valor or Decimal("0.00")

    centros_fechamento = []
    for payload in resumo_nivel5.values():
        if not payload.get("centro"):
            continue
        centros_fechamento.append(
            {
                "centro": payload["centro"],
                "comprometido": payload["comprometido"],
                "medido": payload["medido"],
                "notas": payload["notas"],
                "saldo_a_medir": payload["comprometido"] - payload["medido"],
                "saldo_a_executar": payload["comprometido"] - payload["notas"],
            }
        )
    centros_fechamento.sort(key=lambda item: item["centro"].codigo if item.get("centro") else "")

    return {
        "obras": obras,
        "obra_atual": obra,
        "ano": ano,
        "mes": mes,
        "resumo": {
            "valor_comprometido": compromissos.aggregate(total=Sum("valor_contratado"))["total"] or Decimal("0.00"),
            "valor_medido": medicoes.aggregate(total=Sum("valor_medido"))["total"] or Decimal("0.00"),
            "valor_notas": notas.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00"),
            "qtd_compromissos": compromissos.count(),
            "qtd_medicoes": medicoes.count(),
            "qtd_notas": notas.count(),
        },
        "resumo_centros": centros_fechamento,
        "fechamentos": FechamentoMensal.objects.select_related("obra").order_by("-ano", "-mes")[:12],
    }


def construir_dados_projecao_financeira(*, obra=None, meses_qtd=12):
    meses_opcoes = [6, 12]
    if meses_qtd not in meses_opcoes:
        meses_qtd = 12

    hoje = date.today()
    inicio = date(hoje.year, hoje.month, 1)
    month_starts = [inicio]
    for _ in range(meses_qtd - 1):
        month_starts.append(adicionar_um_mes(month_starts[-1]))
    fim_exclusivo = adicionar_um_mes(month_starts[-1])
    idx_by_month = {m: i for i, m in enumerate(month_starts)}

    entradas = [Decimal("0.00") for _ in month_starts]
    saidas = [Decimal("0.00") for _ in month_starts]

    planos_qs = PlanoContas.objects.annotate(filhos_count=Count("filhos")).filter(filhos_count=0)
    if obra:
        planos_qs = planos_qs.filter(obra=obra)
    total_orcado = planos_qs.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")

    medicoes_qs = (
        Medicao.objects.filter(data_medicao__gte=inicio, data_medicao__lt=fim_exclusivo)
        .annotate(mes_competencia=TruncMonth("data_medicao"))
        .values("mes_competencia")
        .annotate(total=Sum("valor_medido"))
    )
    if obra:
        medicoes_qs = medicoes_qs.filter(obra=obra)
    for medicao in medicoes_qs:
        mes_competencia = medicao["mes_competencia"]
        m = date(mes_competencia.year, mes_competencia.month, 1)
        idx = idx_by_month.get(m)
        if idx is not None:
            valor_total_mes = medicao["total"] or Decimal("0.00")
            percentual_medido = (valor_total_mes / total_orcado) if total_orcado else Decimal("0.00")
            entradas[idx] += arredondar_moeda(percentual_medido * total_orcado)

    notas_qs = (
        NotaFiscal.objects.filter(data_emissao__gte=inicio, data_emissao__lt=fim_exclusivo)
        .annotate(mes_competencia=TruncMonth("data_emissao"))
        .values("mes_competencia")
        .annotate(total=Sum("valor_total"))
    )
    if obra:
        notas_qs = notas_qs.filter(obra=obra)
    for nota in notas_qs:
        mes_competencia = nota["mes_competencia"]
        m = date(mes_competencia.year, mes_competencia.month, 1)
        idx = idx_by_month.get(m)
        if idx is not None:
            saidas[idx] += nota["total"] or Decimal("0.00")

    series = []
    for i, ms in enumerate(month_starts):
        entrada = arredondar_moeda(entradas[i])
        saida = arredondar_moeda(saidas[i])
        saldo = arredondar_moeda(entrada - saida)
        series.append({"label": ms.strftime("%m/%Y"), "entrada": entrada, "saida": saida, "saldo": saldo})

    return {
        "meses_opcoes": meses_opcoes,
        "meses_qtd": meses_qtd,
        "series": series,
        "total_orcado": total_orcado,
        "total_entradas": arredondar_moeda(sum(s["entrada"] for s in series)),
        "total_saidas": arredondar_moeda(sum(s["saida"] for s in series)),
        "total_saldo": arredondar_moeda(sum(s["saldo"] for s in series)),
    }


def construir_fluxo_financeiro_contratual(*, obra=None, meses_qtd=12):
    hoje = date.today()
    inicio = date(hoje.year, hoje.month, 1)

    def add_one_month(data_base):
        return adicionar_um_mes(data_base)

    month_starts = [inicio]
    for _ in range(meses_qtd - 1):
        month_starts.append(add_one_month(month_starts[-1]))

    fim_exclusivo = add_one_month(month_starts[-1])
    idx_by_month = {m: i for i, m in enumerate(month_starts)}

    entradas = [Decimal("0.00") for _ in month_starts]
    saidas = [Decimal("0.00") for _ in month_starts]

    notas_qs = (
        NotaFiscal.objects.filter(data_emissao__gte=inicio, data_emissao__lt=fim_exclusivo)
        .annotate(mes_competencia=TruncMonth("data_emissao"))
        .values("mes_competencia")
        .annotate(total=Sum("valor_total"))
    )
    if obra:
        notas_qs = notas_qs.filter(obra=obra)
    for nota in notas_qs:
        mes_competencia = nota["mes_competencia"]
        m = date(mes_competencia.year, mes_competencia.month, 1)
        idx = idx_by_month.get(m)
        if idx is not None:
            entradas[idx] += nota["total"] or Decimal("0.00")

    prazo_deltas, maior_delta_positivo, menor_delta_negativo = _obter_prazo_deltas_contratuais(obra=obra)
    limite_inicio_bruto = inicio - timedelta(days=maior_delta_positivo)
    limite_fim_bruto = fim_exclusivo - timedelta(days=menor_delta_negativo)

    medicoes_qs = (
        Medicao.objects.annotate(
            data_fluxo_inicio=Coalesce("data_prevista_inicio", "data_medicao", output_field=DateField()),
            data_fluxo_fim=Coalesce(
                "data_prevista_fim",
                "data_prevista_inicio",
                "data_medicao",
                output_field=DateField(),
            ),
        )
        .filter(
            data_fluxo_inicio__lt=limite_fim_bruto,
            data_fluxo_fim__gte=limite_inicio_bruto,
        )
        .only(
            "contrato_id",
            "valor_medido",
            "data_medicao",
            "data_prevista_inicio",
            "data_prevista_fim",
        )
    )
    if obra:
        medicoes_qs = medicoes_qs.filter(obra=obra)
    medicoes = list(medicoes_qs)

    for medicao in medicoes:
        valor_medido = medicao.valor_medido or Decimal("0.00")
        if not valor_medido:
            continue
        med_start_raw = medicao.data_prevista_inicio or medicao.data_medicao
        med_end_raw = medicao.data_prevista_fim or med_start_raw
        delta = prazo_deltas.get(medicao.contrato_id, 0) or 0
        med_start = med_start_raw + timedelta(days=delta)
        med_end = med_end_raw + timedelta(days=delta)

        med_start_m = med_start.replace(day=1)
        med_end_m = med_end.replace(day=1)
        total_meses_intervalo = ((med_end_m.year - med_start_m.year) * 12 + (med_end_m.month - med_start_m.month) + 1)
        total_meses_intervalo = max(1, total_meses_intervalo)

        share = valor_medido / Decimal(total_meses_intervalo)
        for i, month_start in enumerate(month_starts):
            if med_start_m <= month_start <= med_end_m:
                saidas[i] += share

    series = []
    for i, month_start in enumerate(month_starts):
        entrada = entradas[i]
        saida = saidas[i]
        saldo = arredondar_moeda(entrada - saida)
        series.append(
            {
                "label": month_start.strftime("%m/%Y"),
                "entrada": arredondar_moeda(entrada),
                "saida": arredondar_moeda(saida),
                "saldo": saldo,
            }
        )

    return {
        "meses_opcoes": [6, 12],
        "meses_qtd": meses_qtd,
        "series": series,
        "total_entradas": arredondar_moeda(sum(s["entrada"] for s in series)),
        "total_saidas": arredondar_moeda(sum(s["saida"] for s in series)),
        "total_saldo": arredondar_moeda(
            sum(((s["entrada"] - s["saida"]) for s in series), start=Decimal("0.00"))
        ),
    }
