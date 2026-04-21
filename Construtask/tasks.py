# Construtask/tasks.py
import logging

from celery import shared_task

logger = logging.getLogger("construtask.request")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)

def task_sincronizar_alertas_obra(self, obra_id):
    try:
        from .models import Obra
        from .services_alertas import sincronizar_alertas_operacionais_obra

        obra = Obra.objects.get(pk=obra_id)
        sincronizar_alertas_operacionais_obra(obra)

    except Exception as exc:
        # Enriquece o evento Sentry com contexto da obra antes de relançar
        try:
            import sentry_sdk
            sentry_sdk.set_tag("obra.id", obra_id)
            sentry_sdk.set_tag("task", "sincronizar_alertas_obra")
        except Exception:
            pass

        raise self.retry(exc=exc)