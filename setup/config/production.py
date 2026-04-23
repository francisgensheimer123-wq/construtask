import os


os.environ.setdefault("CONSTRUTASK_ENVIRONMENT", "production")
os.environ.setdefault("DJANGO_DEBUG", "False")

from setup.settings_base import *  # noqa: F401,F403
