from django.conf import settings
from django.core.checks import Error, Tags, register
from django.db import connections


@register(Tags.security, deploy=True)
def construtask_saas_checks(app_configs, **kwargs):
    if settings.DEBUG:
        return []

    errors = []
    if connections["default"].vendor != "postgresql":
        errors.append(
            Error(
                "A operacao SaaS em producao exige PostgreSQL como banco principal.",
                id="construtask.E001",
            )
        )
    if not getattr(settings, "CONSTRUTASK_MEDIA_PERSISTENT", False):
        errors.append(
            Error(
                "Defina storage persistente para arquivos em producao.",
                id="construtask.E002",
            )
        )
    if (
        getattr(settings, "MEDIA_STORAGE_BACKEND", "django.core.files.storage.FileSystemStorage") == "django.core.files.storage.FileSystemStorage"
        and not getattr(settings, "CONSTRUTASK_FILESYSTEM_MEDIA_ALLOWED_IN_PRODUCTION", False)
    ):
        errors.append(
            Error(
                "Em producao, configure backend de storage persistente explicito ou habilite volume duravel aprovado.",
                id="construtask.E007",
            )
        )
    if not getattr(settings, "CONSTRUTASK_BACKUP_ENABLED", False):
        errors.append(
            Error(
                "Habilite a politica de backup automatizado para a operacao SaaS.",
                id="construtask.E003",
            )
        )
    if not getattr(settings, "CSRF_TRUSTED_ORIGINS", []):
        errors.append(
            Error(
                "Configure CSRF_TRUSTED_ORIGINS para a operacao por dominio.",
                id="construtask.E004",
            )
        )
    if not getattr(settings, "ALLOWED_HOSTS", []):
        errors.append(
            Error(
                "Configure ALLOWED_HOSTS para a operacao SaaS.",
                id="construtask.E005",
            )
        )
    if not getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        errors.append(
            Error(
                "Configure SECURE_PROXY_SSL_HEADER para operacao atras de proxy HTTPS.",
                id="construtask.E006",
            )
        )
    return errors
