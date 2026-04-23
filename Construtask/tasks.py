# Construtask/tasks.py
import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("construtask.request")


def _registrar_falha_sincronizacao_alertas(*, obra_id, observacao, motivo):
    from .models import JobAssincrono, Obra

    obra = Obra.objects.select_related("empresa").filter(pk=obra_id).first()
    agora = timezone.now()
    JobAssincrono.objects.create(
        empresa=getattr(obra, "empresa", None),
        obra=obra,
        tipo="SINCRONIZAR_ALERTAS_OBRA",
        status="FALHOU",
        descricao="Falha operacional na sincronizacao assincrona de alertas.",
        parametros={"obra_id": obra_id, "origem": "celery-task", "motivo": motivo},
        erro=observacao[:2000],
        tentativas=1,
        iniciado_em=agora,
        concluido_em=agora,
    )


def _registrar_falha_backup(*, observacao, motivo):
    from .models import OperacaoBackupSaaS

    OperacaoBackupSaaS.objects.create(
        tipo="BACKUP",
        status="FALHOU",
        ambiente=getattr(settings, "CONSTRUTASK_ENVIRONMENT", "development"),
        provedor=getattr(settings, "CONSTRUTASK_BACKUP_PROVIDER", ""),
        observacao=observacao[:2000],
        detalhes={"origem": "celery-task", "motivo": motivo},
        executado_em=timezone.now(),
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=60, ignore_result=True)
def task_sincronizar_alertas_obra(self, obra_id):
    try:
        from .models import Obra
        from .services_alertas import sincronizar_alertas_operacionais_obra

        obra = Obra.objects.get(pk=obra_id)
        sincronizar_alertas_operacionais_obra(obra)
    except SoftTimeLimitExceeded as exc:
        observacao = f"Sincronizacao de alertas interrompida por limite de tempo. obra_id={obra_id}. erro={exc}"
        logger.exception(observacao)
        _registrar_falha_sincronizacao_alertas(
            obra_id=obra_id,
            observacao=observacao,
            motivo="soft_time_limit_exceeded",
        )
        raise
    except Exception as exc:
        # Enriquece o evento Sentry com contexto da obra antes de relancar.
        try:
            import sentry_sdk

            sentry_sdk.set_tag("obra.id", obra_id)
            sentry_sdk.set_tag("task", "sincronizar_alertas_obra")
        except Exception:
            pass

        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            observacao = f"Sincronizacao de alertas falhou apos retries. obra_id={obra_id}. erro={exc}"
            logger.exception(observacao)
            _registrar_falha_sincronizacao_alertas(
                obra_id=obra_id,
                observacao=observacao,
                motivo="max_retries_exceeded",
            )
            raise


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def task_executar_backup_postgres(self):
    from django.core.management import call_command
    from io import StringIO

    if not getattr(settings, "CONSTRUTASK_BACKUP_ENABLED", False):
        logger.info("Rotina de backup ignorada: backup SaaS desabilitado.")
        return "backup disabled"

    if not getattr(settings, "CONSTRUTASK_BACKUP_PROVIDER", ""):
        logger.warning("Rotina de backup ignorada: provedor de backup nao configurado.")
        return "backup provider not configured"

    out = StringIO()
    try:
        call_command("executar_backup_r2", stdout=out)
        return out.getvalue()[-500:]
    except SoftTimeLimitExceeded as exc:
        observacao = f"Backup SaaS interrompido por limite de tempo. erro={exc}"
        logger.exception(observacao)
        _registrar_falha_backup(
            observacao=observacao,
            motivo="soft_time_limit_exceeded",
        )
        raise
    except Exception as exc:
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            observacao = f"Backup SaaS falhou apos retries. erro={exc}"
            logger.exception(observacao)
            _registrar_falha_backup(
                observacao=observacao,
                motivo="max_retries_exceeded",
            )
            raise
