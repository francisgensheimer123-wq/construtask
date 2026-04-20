from django.db.models import Case, Count, When
from django.utils import timezone

from ..models import AlertaOperacional


STATUS_DISPLAY_ALERTA = {
    "ABERTO": "Aguardando tratamento",
    "EM_TRATAMENTO": "Em tratamento",
    "JUSTIFICADO": "Justificado",
    "ENCERRADO": "Encerrado",
}


def alerta_fora_sla(alerta, parametros, data_referencia=None):
    if alerta.status == "ENCERRADO":
        return False
    data_referencia = data_referencia or timezone.localdate()
    ultima_movimentacao = alerta.ultima_acao_em.date() if alerta.ultima_acao_em else alerta.criado_em.date()
    dias_sem_movimento = max((data_referencia - ultima_movimentacao).days, 0)
    dias_em_aberto = max((data_referencia - alerta.criado_em.date()).days, 0)
    return (
        dias_sem_movimento >= parametros.alerta_sem_workflow_dias
        or dias_em_aberto > parametros.alerta_prazo_solucao_dias
    )


def alerta_com_prazo_vencido(alerta, data_referencia=None):
    if alerta.status == "ENCERRADO" or not alerta.prazo_solucao_em:
        return False
    data_referencia = data_referencia or timezone.localdate()
    return alerta.prazo_solucao_em < data_referencia


def enriquecer_alertas_operacionais(alertas, parametros, data_referencia=None):
    data_referencia = data_referencia or timezone.localdate()
    alertas_enriquecidos = []
    for alerta in alertas:
        alerta.em_atraso_sla = alerta_fora_sla(alerta, parametros, data_referencia)
        alerta.em_atraso_prazo = alerta_com_prazo_vencido(alerta, data_referencia)
        alerta.dias_atraso_prazo = (
            max((data_referencia - alerta.prazo_solucao_em).days, 0)
            if alerta.em_atraso_prazo and alerta.prazo_solucao_em
            else 0
        )
        alertas_enriquecidos.append(alerta)
    return alertas_enriquecidos


def queryset_alertas_central(obra_contexto, filtros):
    queryset = (
        AlertaOperacional.objects.filter(obra=obra_contexto)
        .select_related("responsavel", "ultima_acao_por")
        .order_by(
            Case(
                When(severidade="CRITICA", then=0),
                When(severidade="ALTA", then=1),
                When(severidade="MEDIA", then=2),
                default=3,
            ),
            Case(
                When(status="ABERTO", then=0),
                When(status="EM_TRATAMENTO", then=1),
                When(status="JUSTIFICADO", then=2),
                default=3,
            ),
            "-data_referencia",
            "-atualizado_em",
        )
    )
    if filtros.get("status"):
        queryset = queryset.filter(status=filtros["status"])
    if filtros.get("severidade"):
        queryset = queryset.filter(severidade=filtros["severidade"])
    if filtros.get("regra"):
        queryset = queryset.filter(codigo_regra=filtros["regra"])
    if filtros.get("responsavel"):
        queryset = queryset.filter(responsavel_id=filtros["responsavel"])
    return queryset


def queryset_alertas_executivos(obra_contexto):
    return (
        AlertaOperacional.objects.filter(
            obra=obra_contexto,
            status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"],
        )
        .select_related("responsavel")
        .order_by(
            Case(
                When(severidade="CRITICA", then=0),
                When(severidade="ALTA", then=1),
                When(severidade="MEDIA", then=2),
                default=3,
            ),
            "prazo_solucao_em",
            "-criado_em",
        )
    )


def listar_responsaveis_alerta(obra_contexto):
    return list(
        AlertaOperacional.objects.filter(obra=obra_contexto, responsavel__isnull=False)
        .select_related("responsavel")
        .order_by("responsavel__username")
        .values_list("responsavel_id", "responsavel__username")
        .distinct()
    )


def regras_disponiveis_alerta(obra_contexto):
    return list(
        AlertaOperacional.objects.filter(obra=obra_contexto)
        .order_by("codigo_regra")
        .values_list("codigo_regra", flat=True)
        .distinct()
    )


def montar_resumo_status_alertas(obra_contexto):
    resumo = list(
        AlertaOperacional.objects.filter(obra=obra_contexto)
        .values("status")
        .annotate(total=Count("id"))
        .order_by("status")
    )
    for item in resumo:
        item["status_display"] = STATUS_DISPLAY_ALERTA.get(
            item["status"],
            item["status"].replace("_", " ").title(),
        )
    return resumo


def montar_resumo_severidade_alertas(obra_contexto):
    return list(
        AlertaOperacional.objects.filter(obra=obra_contexto)
        .values("severidade")
        .annotate(total=Count("id"))
        .order_by("severidade")
    )


def montar_cards_resumo_alertas(resumo_status):
    return {
        "abertos": next((item["total"] for item in resumo_status if item["status"] == "ABERTO"), 0),
        "em_tratamento": next((item["total"] for item in resumo_status if item["status"] == "EM_TRATAMENTO"), 0),
        "justificados": next((item["total"] for item in resumo_status if item["status"] == "JUSTIFICADO"), 0),
        "encerrados": next((item["total"] for item in resumo_status if item["status"] == "ENCERRADO"), 0),
    }
