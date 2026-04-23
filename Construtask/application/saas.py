import os
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils.module_loading import import_string
from django.core.files.storage import default_storage
from django.db import connections
from django.utils import timezone


def _status_principal(statuses):
    if any(status == "error" for status in statuses):
        return "error"
    if any(status == "warning" for status in statuses):
        return "degraded"
    return "ok"


def _check_database():
    engine = settings.DATABASES["default"]["ENGINE"]
    vendor = connections["default"].vendor
    is_postgres = vendor == "postgresql"
    ambiente_produtivo = not settings.DEBUG

    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return {
            "status": "error",
            "titulo": "Banco operacional",
            "detalhe": f"Falha ao consultar o banco principal: {exc}",
            "engine": engine,
            "vendor": vendor,
        }

    if ambiente_produtivo and not is_postgres:
        return {
            "status": "error",
            "titulo": "Banco operacional",
            "detalhe": "Em producao a base operacional deve ser PostgreSQL.",
            "engine": engine,
            "vendor": vendor,
        }

    detalhe = "PostgreSQL operacional validado." if is_postgres else "Base local validada para desenvolvimento."
    return {
        "status": "ok",
        "titulo": "Banco operacional",
        "detalhe": detalhe,
        "engine": engine,
        "vendor": vendor,
    }


def _check_storage():
    backend_path = getattr(settings, "MEDIA_STORAGE_BACKEND", default_storage.__class__.__module__ + "." + default_storage.__class__.__name__)
    backend = default_storage.__class__.__name__
    media_root = Path(getattr(settings, "MEDIA_ROOT", "")) if getattr(settings, "MEDIA_ROOT", None) else None
    persistente = bool(getattr(settings, "CONSTRUTASK_MEDIA_PERSISTENT", settings.DEBUG))
    filesystem_em_producao = bool(getattr(settings, "CONSTRUTASK_FILESYSTEM_MEDIA_ALLOWED_IN_PRODUCTION", settings.DEBUG))
    detalhe_local = None
    ambiente_produtivo = not settings.DEBUG

    try:
        import_string(backend_path)
    except Exception as exc:
        return {
            "status": "error",
            "titulo": "Storage de arquivos",
            "detalhe": f"Backend de storage invalido ou indisponivel: {exc}",
            "backend": backend,
            "backend_path": backend_path,
        }

    if media_root:
        try:
            media_root.mkdir(parents=True, exist_ok=True)
            detalhe_local = str(media_root)
        except Exception as exc:
            return {
                "status": "error",
                "titulo": "Storage de arquivos",
                "detalhe": f"Nao foi possivel preparar o diretorio de media: {exc}",
                "backend": backend,
                "backend_path": backend_path,
            }

    if not persistente and ambiente_produtivo:
        return {
            "status": "error",
            "titulo": "Storage de arquivos",
            "detalhe": "O storage atual nao foi marcado como persistente para producao.",
            "backend": backend,
            "backend_path": backend_path,
            "localizacao": detalhe_local,
        }

    if ambiente_produtivo and backend_path == "django.core.files.storage.FileSystemStorage" and not filesystem_em_producao:
        return {
            "status": "error",
            "titulo": "Storage de arquivos",
            "detalhe": "Em producao use storage persistente explicito, como volume montado ou backend externo.",
            "backend": backend,
            "backend_path": backend_path,
            "localizacao": detalhe_local,
        }

    return {
        "status": "ok",
        "titulo": "Storage de arquivos",
        "detalhe": "Storage pronto para operacao." if persistente else "Storage local aceito para desenvolvimento.",
        "backend": backend,
        "backend_path": backend_path,
        "localizacao": detalhe_local,
    }


def _parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _obter_ultima_operacao(tipo):
    try:
        from ..models import OperacaoBackupSaaS

        return OperacaoBackupSaaS.objects.filter(tipo=tipo).order_by("-executado_em").first()
    except Exception:
        return None


def _check_backup():
    habilitado = bool(getattr(settings, "CONSTRUTASK_BACKUP_ENABLED", False))
    provedor = getattr(settings, "CONSTRUTASK_BACKUP_PROVIDER", "")
    retencao = int(getattr(settings, "CONSTRUTASK_BACKUP_RETENTION_DAYS", 0) or 0)
    janela_horas = int(getattr(settings, "CONSTRUTASK_BACKUP_INTERVAL_HOURS", 24) or 24)
    janela_teste_recuperacao_dias = int(getattr(settings, "CONSTRUTASK_RECOVERY_TEST_INTERVAL_DAYS", 30) or 30)
    ultima_execucao = _parse_datetime(getattr(settings, "CONSTRUTASK_BACKUP_LAST_SUCCESS_AT", ""))
    ambiente_produtivo = not settings.DEBUG
    ultimo_backup = _obter_ultima_operacao("BACKUP")
    ultimo_teste_recuperacao = _obter_ultima_operacao("TESTE_RESTAURACAO")
    provedor_efetivo = (ultimo_backup.provedor if ultimo_backup and ultimo_backup.provedor else provedor) or "-"

    if ultimo_backup and ultimo_backup.status == "SUCESSO":
        ultima_execucao = ultimo_backup.executado_em
    teste_recuperacao_ok = bool(ultimo_teste_recuperacao and ultimo_teste_recuperacao.status == "SUCESSO")
    teste_recuperacao_recente = bool(
        teste_recuperacao_ok
        and ultimo_teste_recuperacao.executado_em
        and ultimo_teste_recuperacao.executado_em
        >= timezone.now() - timedelta(days=janela_teste_recuperacao_dias)
    )

    if not habilitado:
        return {
            "status": "error" if ambiente_produtivo else "ok",
            "titulo": "Backup e recuperacao",
            "detalhe": "A politica de backup automatizado nao esta habilitada." if ambiente_produtivo else "Backup automatico opcional no ambiente local.",
            "provedor": provedor_efetivo,
            "retencao_dias": retencao,
            "teste_recuperacao_ok": teste_recuperacao_ok,
            "teste_recuperacao_recente": teste_recuperacao_recente,
        }

    if not provedor or retencao <= 0:
        return {
            "status": "error" if ambiente_produtivo else "ok",
            "titulo": "Backup e recuperacao",
            "detalhe": "Configure provedor e retencao para a rotina de backup." if ambiente_produtivo else "Configuracao de backup simplificada para desenvolvimento.",
            "provedor": provedor_efetivo,
            "retencao_dias": retencao,
            "teste_recuperacao_ok": teste_recuperacao_ok,
            "teste_recuperacao_recente": teste_recuperacao_recente,
        }

    status = "ok"
    detalhe = "Politica de backup configurada."
    if ultima_execucao:
        limite = timezone.now() - timedelta(hours=janela_horas * 2)
        if ultima_execucao < limite:
            status = "warning" if not ambiente_produtivo else "error"
            detalhe = "A ultima execucao registrada de backup esta acima da janela esperada."
        else:
            detalhe = "Backup recente registrado dentro da janela operacional."
    elif ambiente_produtivo:
        status = "warning"
        detalhe = "Politica configurada, mas ainda sem ultima execucao registrada."

    if ambiente_produtivo and not teste_recuperacao_recente:
        status = "error" if status == "ok" else status
        detalhe = (
            f"{detalhe} Nenhum teste de recuperacao com sucesso foi registrado "
            f"dentro da janela de {janela_teste_recuperacao_dias} dias."
        )

    return {
        "status": status,
        "titulo": "Backup e recuperacao",
        "detalhe": detalhe,
        "provedor": provedor_efetivo,
        "retencao_dias": retencao,
        "janela_teste_recuperacao_dias": janela_teste_recuperacao_dias,
        "ultima_execucao": ultima_execucao,
        "ultimo_backup": ultimo_backup,
        "ultimo_teste_recuperacao": ultimo_teste_recuperacao,
        "teste_recuperacao_ok": teste_recuperacao_ok,
        "teste_recuperacao_recente": teste_recuperacao_recente,
    }


def _check_security():
    ambiente_produtivo = not settings.DEBUG
    proxy_ssl = bool(getattr(settings, "SECURE_PROXY_SSL_HEADER", None))
    csrf_origins = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []))
    ssl_redirect = bool(getattr(settings, "SECURE_SSL_REDIRECT", False))
    allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []))

    if not ambiente_produtivo:
        return {
            "status": "ok",
            "titulo": "Configuracao segura",
            "detalhe": "Ambiente local com validacoes basicas habilitadas.",
            "ssl_redirect": ssl_redirect,
            "proxy_ssl": proxy_ssl,
            "csrf_trusted_origins": csrf_origins,
            "allowed_hosts": allowed_hosts,
        }

    faltas = []
    if not proxy_ssl:
        faltas.append("SECURE_PROXY_SSL_HEADER")
    if not csrf_origins:
        faltas.append("CSRF_TRUSTED_ORIGINS")
    if not ssl_redirect:
        faltas.append("SECURE_SSL_REDIRECT")
    if not allowed_hosts:
        faltas.append("ALLOWED_HOSTS")

    return {
        "status": "error" if faltas else "ok",
        "titulo": "Configuracao segura",
        "detalhe": "Configuracao segura pronta para operacao." if not faltas else f"Ajustes pendentes: {', '.join(faltas)}.",
        "ssl_redirect": ssl_redirect,
        "proxy_ssl": proxy_ssl,
        "csrf_trusted_origins": csrf_origins,
        "allowed_hosts": allowed_hosts,
    }


def _check_cache_infra():
    ambiente_produtivo = not settings.DEBUG
    backend = settings.CACHES["default"]["BACKEND"]
    backend_critico = settings.CACHES["critical"]["BACKEND"]
    redis_url = os.environ.get("REDIS_URL", "").strip()

    if not ambiente_produtivo:
        detalhe = (
            "Cache local pronto para desenvolvimento."
            if backend != "django_redis.cache.RedisCache"
            else "Cache Redis configurado para desenvolvimento."
        )
        return {
            "status": "ok",
            "titulo": "Cache e fila",
            "detalhe": detalhe,
            "backend": backend,
            "backend_critico": backend_critico,
            "redis_url_configurada": bool(redis_url),
        }

    if backend != "django_redis.cache.RedisCache":
        return {
            "status": "error",
            "titulo": "Cache e fila",
            "detalhe": "Em producao, a aplicacao exige cache compartilhado em Redis.",
            "backend": backend,
            "backend_critico": backend_critico,
            "redis_url_configurada": bool(redis_url),
        }

    if backend_critico != "django_redis.cache.RedisCache":
        return {
            "status": "error",
            "titulo": "Cache e fila",
            "detalhe": "Em producao, o cache critico de seguranca deve usar Redis compartilhado.",
            "backend": backend,
            "backend_critico": backend_critico,
            "redis_url_configurada": bool(redis_url),
        }

    if not redis_url:
        return {
            "status": "error",
            "titulo": "Cache e fila",
            "detalhe": "Defina REDIS_URL para cache compartilhado e execucao de jobs.",
            "backend": backend,
            "backend_critico": backend_critico,
            "redis_url_configurada": False,
        }

    try:
        from django_redis import get_redis_connection

        get_redis_connection("default").ping()
    except Exception as exc:
        return {
            "status": "error",
            "titulo": "Cache e fila",
            "detalhe": f"Nao foi possivel validar o Redis operacional: {exc}",
            "backend": backend,
            "backend_critico": backend_critico,
            "redis_url_configurada": True,
        }

    return {
        "status": "ok",
        "titulo": "Cache e fila",
        "detalhe": "Redis operacional validado para cache compartilhado e jobs.",
        "backend": backend,
        "backend_critico": backend_critico,
        "redis_url_configurada": True,
    }


def diagnostico_base_saas():
    checks = {
        "database": _check_database(),
        "storage": _check_storage(),
        "backup": _check_backup(),
        "security": _check_security(),
        "cache": _check_cache_infra(),
    }
    status = _status_principal([item["status"] for item in checks.values()])
    return {
        "status": status,
        "ambiente": getattr(settings, "CONSTRUTASK_ENVIRONMENT", "development"),
        "checks": checks,
        "atualizado_em": timezone.now(),
    }


def contexto_base_saas():
    diagnostico = diagnostico_base_saas()
    return {
        "base_saas": diagnostico,
        "base_saas_checks": list(diagnostico["checks"].values()),
        "base_saas_backup": diagnostico["checks"]["backup"],
    }
