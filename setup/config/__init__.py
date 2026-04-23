import os


_environment = (os.environ.get("CONSTRUTASK_ENVIRONMENT") or "").strip().lower()

if _environment in {"production", "staging"}:
    from .production import *  # noqa: F401,F403
elif _environment in {"development", "dev", "local"}:
    from .development import *  # noqa: F401,F403
elif os.environ.get("DATABASE_URL"):
    from .production import *  # noqa: F401,F403
else:
    from .development import *  # noqa: F401,F403
