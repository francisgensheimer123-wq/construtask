import logging
import os
import threading
import time

from django.apps import AppConfig

logger = logging.getLogger("construtask.jobs.worker")

WORKER_INTERVAL_SECONDS = int(os.environ.get("CONSTRUTASK_JOB_WORKER_INTERVAL", "30"))
WORKER_BATCH_SIZE = int(os.environ.get("CONSTRUTASK_JOB_WORKER_BATCH", "5"))
WORKER_STARTUP_DELAY = int(os.environ.get("CONSTRUTASK_JOB_WORKER_STARTUP_DELAY", "15"))


class ConstrutaskConfig(AppConfig):
    name = "Construtask"

    def ready(self):
        from . import checks   # noqa: F401
        from . import signals  # noqa: F401

        # Evita iniciar o worker no processo filho do auto-reloader do runserver
        # RUN_MAIN=true é setado pelo reloader — o processo original não tem essa variável
        if os.environ.get("RUN_MAIN") == "true":
            return

        # Não inicia worker em comandos de management (migrate, collectstatic, etc.)
        # exceto o próprio processar_jobs_assincronos
        import sys
        argv = sys.argv
        if len(argv) >= 2 and argv[1] not in (
            "runserver", "gunicorn", "uvicorn", "processar_jobs_assincronos"
        ):
            management_commands = {
                "migrate", "makemigrations", "collectstatic", "shell",
                "createsuperuser", "dbshell", "check", "test",
                "validar_prontidao_producao", "normalizar_textos_cadastrais",
            }
            if argv[1] in management_commands:
                return

        _iniciar_worker_jobs()


def _iniciar_worker_jobs():
    def _loop():
        time.sleep(WORKER_STARTUP_DELAY)
        logger.info(
            "[JobWorker] Thread iniciada. Intervalo=%ds, batch=%d.",
            WORKER_INTERVAL_SECONDS,
            WORKER_BATCH_SIZE,
        )
        while True:
            try:
                from .services_jobs import processar_jobs_pendentes
                jobs = processar_jobs_pendentes(limite=WORKER_BATCH_SIZE)
                if jobs:
                    logger.info("[JobWorker] %d job(s) processado(s).", len(jobs))
            except Exception:
                logger.exception("[JobWorker] Erro inesperado no loop de processamento.")
            time.sleep(WORKER_INTERVAL_SECONDS)

    t = threading.Thread(target=_loop, name="construtask-job-worker", daemon=True)
    t.start()
