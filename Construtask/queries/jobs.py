from django.db.models import Count

from ..models import JobAssincrono


def listar_jobs_contexto(*, empresa=None, obra=None, limite=30):
    queryset = (
        JobAssincrono.objects.select_related("obra", "solicitado_por")
        .only(
            "id",
            "obra__codigo",
            "solicitado_por__username",
            "tipo",
            "status",
            "descricao",
            "criado_em",
            "concluido_em",
            "erro",
        )
        .order_by("-criado_em")
    )
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    return list(queryset[:limite])


def resumir_jobs_contexto(*, empresa=None, obra=None):
    queryset = JobAssincrono.objects.all()
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    resumo = {item["status"]: item["total"] for item in queryset.values("status").annotate(total=Count("id"))}
    return {
        "pendentes": resumo.get("PENDENTE", 0),
        "em_execucao": resumo.get("EM_EXECUCAO", 0),
        "concluidos": resumo.get("CONCLUIDO", 0),
        "falharam": resumo.get("FALHOU", 0),
    }
