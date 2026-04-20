from django.conf import settings
from django.db.models import Avg, Count, Max, Q

from ..models import MetricaRequisicao, RastroErroAplicacao


def resumo_metricas(*, empresa=None, obra=None):
    queryset = MetricaRequisicao.objects.all()
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    agregados = queryset.aggregate(
        total=Count("id"),
        media_ms=Avg("duracao_ms"),
    )
    erros = queryset.filter(status_code__gte=500).count()
    lentas = queryset.filter(duracao_ms__gte=settings.CONSTRUTASK_SLOW_REQUEST_THRESHOLD_MS).count()
    return {
        "total": agregados["total"] or 0,
        "media_ms": agregados["media_ms"] or 0,
        "erros_500": erros,
        "lentas": lentas,
    }


def metricas_recentes(*, empresa=None, obra=None, limite=30):
    queryset = (
        MetricaRequisicao.objects.select_related("usuario", "obra")
        .only(
            "request_id",
            "metodo",
            "path",
            "status_code",
            "duracao_ms",
            "criado_em",
            "usuario__username",
            "obra__codigo",
        )
        .order_by("-criado_em")
    )
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    return list(queryset[:limite])


def resumo_erros(*, empresa=None, obra=None):
    queryset = RastroErroAplicacao.objects.all()
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    return {
        "total": queryset.count(),
        "abertos": queryset.filter(resolvido=False).count(),
        "resolvidos": queryset.filter(resolvido=True).count(),
    }


def erros_recentes(*, empresa=None, obra=None, limite=20):
    queryset = (
        RastroErroAplicacao.objects.select_related("usuario", "obra")
        .only(
            "request_id",
            "metodo",
            "path",
            "status_code",
            "classe_erro",
            "mensagem",
            "resolvido",
            "criado_em",
            "usuario__username",
            "obra__codigo",
        )
        .order_by("-criado_em")
    )
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    return list(queryset[:limite])


def endpoints_lentos(*, empresa=None, obra=None, limite=10):
    queryset = MetricaRequisicao.objects.all()
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    return list(
        queryset.values("metodo", "path")
        .annotate(
            total=Count("id"),
            media_ms=Avg("duracao_ms"),
            pico_ms=Max("duracao_ms"),
            erros_500=Count("id", filter=Q(status_code__gte=500)),
        )
        .order_by("-media_ms", "-pico_ms", "-total")[:limite]
    )
