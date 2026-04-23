import os
import subprocess
import sys


GUNICORN_CMD = [
    "gunicorn",
    "setup.wsgi",
    "--bind",
    "0.0.0.0:8080",
    "--timeout",
    "120",
    "--workers",
    "2",
    "--log-level",
    "info",
]


def _env_true(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _should_run_runtime_bootstrap():
    if _env_true("CONSTRUTASK_SKIP_RUNTIME_BOOTSTRAP", default=False):
        return False
    if _env_true("CONSTRUTASK_RUN_MIGRATIONS_ON_BOOT", default=False):
        return True
    return (
        os.getenv("CONSTRUTASK_ENVIRONMENT") == "production"
        or bool(os.getenv("RAILWAY_ENVIRONMENT"))
    )


def _run_manage_py(*args):
    subprocess.check_call([sys.executable, "manage.py", *args])


def main():
    if _should_run_runtime_bootstrap():
        _run_manage_py("check", "--deploy")
        _run_manage_py("migrate", "--noinput")
    os.execvp(GUNICORN_CMD[0], GUNICORN_CMD)


if __name__ == "__main__":
    main()
