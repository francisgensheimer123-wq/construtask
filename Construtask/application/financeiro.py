from ..models import FechamentoMensal, Obra
from ..permissions import get_obra_do_contexto
from ..queries.financeiro import (
    construir_dados_fechamento_mensal,
    construir_dados_projecao_financeira,
    construir_fluxo_financeiro_contratual,
    parse_int_query_param,
)


def dados_fechamento_mensal_request(request):
    obra_contexto = get_obra_do_contexto(request)
    obra_id = (request.GET.get("obra") or "").strip()
    dados_base = construir_dados_fechamento_mensal(
        obra=resolver_obra_financeira(obra_contexto=obra_contexto, obra_id=obra_id),
        ano=parse_int_query_param(request.GET.get("ano"), None),
        mes=parse_int_query_param(request.GET.get("mes"), None),
    )
    return dados_base


def resolver_obra_financeira(*, obra_contexto=None, obra_id=None):
    obras = Obra.objects.order_by("codigo")
    if obra_id:
        obra = obras.filter(pk=obra_id).first()
        if obra:
            return obra
    if obra_contexto:
        return obra_contexto
    return obras.first()


def registrar_fechamento_mensal(*, obra, ano, mes):
    dados = construir_dados_fechamento_mensal(obra=obra, ano=ano, mes=mes)
    fechamento, _ = FechamentoMensal.objects.update_or_create(
        obra=obra,
        ano=ano,
        mes=mes,
        defaults={
            "valor_comprometido": dados["resumo"]["valor_comprometido"],
            "valor_medido": dados["resumo"]["valor_medido"],
            "valor_notas": dados["resumo"]["valor_notas"],
        },
    )
    return fechamento


def dados_projecao_financeira_request(request):
    obra = get_obra_do_contexto(request)
    meses_qtd = parse_int_query_param(request.GET.get("meses"), 12)
    return construir_dados_projecao_financeira(obra=obra, meses_qtd=meses_qtd)


def dados_fluxo_financeiro_contratual_request(request):
    obra = get_obra_do_contexto(request)
    meses_qtd = parse_int_query_param(request.GET.get("meses"), 12)
    return construir_fluxo_financeiro_contratual(obra=obra, meses_qtd=meses_qtd)
