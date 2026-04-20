from django.apps import AppConfig


class ConstrutaskConfig(AppConfig):
    name = 'Construtask'

    def ready(self):
        from . import checks  # noqa: F401
        from . import signals  # noqa: F401
