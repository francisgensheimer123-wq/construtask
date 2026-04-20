from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from ..models import MetricaRequisicao, RastroErroAplicacao
from ..permissions import get_empresa_operacional, get_obra_do_contexto
from ..queries.jobs import resumir_jobs_contexto
from ..queries.observabilidade import endpoints_lentos, erros_recentes, metricas_recentes, resumo_erros, resumo_metricas


def contexto_observabilidade_request(request):
    empresa = get_empresa_operacional(request)
    obra = get_obra_do_contexto(request)
    return {
        "obra_contexto": obra,
        "resumo_metricas": resumo_metricas(empresa=empresa, obra=obra),
        "resumo_erros": resumo_erros(empresa=empresa, obra=obra),
        "resumo_jobs": resumir_jobs_contexto(empresa=empresa, obra=obra),
        "metricas_recentes": metricas_recentes(empresa=empresa, obra=obra, limite=25),
        "erros_recentes": erros_recentes(empresa=empresa, obra=obra, limite=20),
        "endpoints_lentos": endpoints_lentos(empresa=empresa, obra=obra, limite=10),
        "politica_retencao": {
            "metricas_dias": settings.CONSTRUTASK_METRICAS_RETENTION_DAYS,
            "erros_dias": settings.CONSTRUTASK_ERROS_APLICACAO_RETENTION_DAYS,
        },
    }


def aplicar_retencao_observabilidade(*, dry_run=False):
    agora = timezone.now()
    limite_metricas = agora - timedelta(days=settings.CONSTRUTASK_METRICAS_RETENTION_DAYS)
    limite_erros = agora - timedelta(days=settings.CONSTRUTASK_ERROS_APLICACAO_RETENTION_DAYS)

    metricas_qs = MetricaRequisicao.objects.filter(criado_em__lt=limite_metricas)
    erros_qs = RastroErroAplicacao.objects.filter(criado_em__lt=limite_erros)
    metricas_total = metricas_qs.count()
    erros_total = erros_qs.count()

    if not dry_run:
        metricas_qs.delete()
        erros_qs.delete()

    return {
        "status": "dry-run" if dry_run else "ok",
        "executado_em": agora.isoformat(),
        "metricas": {
            "retention_days": settings.CONSTRUTASK_METRICAS_RETENTION_DAYS,
            "limite": limite_metricas.isoformat(),
            "removidas": metricas_total,
        },
        "erros": {
            "retention_days": settings.CONSTRUTASK_ERROS_APLICACAO_RETENTION_DAYS,
            "limite": limite_erros.isoformat(),
            "removidos": erros_total,
        },
    }


def diagnostico_latencia_operacional(*, empresa=None, obra=None, limite=10):
    resumo = resumo_metricas(empresa=empresa, obra=obra)
    return {
        "status": "ok",
        "threshold_ms": settings.CONSTRUTASK_SLOW_REQUEST_THRESHOLD_MS,
        "resumo_metricas": resumo,
        "endpoints_lentos": endpoints_lentos(empresa=empresa, obra=obra, limite=limite),
    }
