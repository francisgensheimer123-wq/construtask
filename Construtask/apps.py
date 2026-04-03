from django.apps import AppConfig


class ConstrutaskConfig(AppConfig):
    name = 'Construtask'

    def ready(self):
        from . import signals  # noqa: F401
