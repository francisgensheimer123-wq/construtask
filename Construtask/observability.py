import json
import logging
import time
import traceback
import uuid

from django.db import DatabaseError, OperationalError, ProgrammingError, connections
from django.db.transaction import TransactionManagementError
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.http import Http404
from django.utils import timezone

logger = logging.getLogger("construtask.request")
_OBSERVABILITY_TABLES_CACHE = False


def _observability_tables_ready():
    global _OBSERVABILITY_TABLES_CACHE

    if _OBSERVABILITY_TABLES_CACHE:
        return True
    try:
        from .models import MetricaRequisicao, RastroErroAplicacao

        existing_tables = set(connections["default"].introspection.table_names())
        required_tables = {
            MetricaRequisicao._meta.db_table,
            RastroErroAplicacao._meta.db_table,
        }
        _OBSERVABILITY_TABLES_CACHE = required_tables.issubset(existing_tables)
        return _OBSERVABILITY_TABLES_CACHE
    except Exception:
        return False


def _connection_ready():
    try:
        connection = connections["default"]
        if connection.in_atomic_block and connection.needs_rollback:
            return False
        return True
    except Exception:
        return False


def _safe_authenticated_user(request):
    try:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return user
    except Exception:
        return None
    return None


def _resolver_contexto_request(request):
    empresa = None
    obra = None
    usuario = _safe_authenticated_user(request)
    if usuario is not None:
        try:
            from .permissions import get_empresa_operacional, get_obra_do_contexto

            obra = get_obra_do_contexto(request)
            empresa = get_empresa_operacional(request, obra=obra)
        except Exception:
            empresa = None
            obra = None
    return empresa, obra, usuario


def health_status():
    checks = {"database": "ok"}
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        checks["database"] = "error"
    status = "ok" if all(valor == "ok" for valor in checks.values()) else "degraded"
    return {
        "status": status,
        "timestamp": timezone.now().isoformat(),
        "checks": checks,
    }


def readiness_status():
    payload = health_status()
    from .application.saas import diagnostico_base_saas

    saas = diagnostico_base_saas()
    ambiente_produtivo = saas.get("ambiente") == "production"
    if ambiente_produtivo and saas["status"] == "degraded" and payload["status"] == "ok":
        payload["status"] = "degraded"
    if ambiente_produtivo and saas["status"] == "error":
        payload["status"] = "degraded"
    payload["kind"] = "readiness"
    payload["operacao_saas"] = saas
    return payload


def health_check_view(request):
    payload = health_status()
    status_code = 200 if payload["status"] == "ok" else 503
    return JsonResponse(payload, status=status_code)


def readiness_check_view(request):
    payload = readiness_status()
    status_code = 503 if payload["status"] == "error" else 200
    return JsonResponse(payload, status=status_code)


class RequestObservabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4())[:12]
        request.request_id = request_id
        if not hasattr(request, "_audit_request_id"):
            request._audit_request_id = request_id
        started_at = time.monotonic()
        response = self.get_response(request)
        duration_ms = round((time.monotonic() - started_at) * 1000, 2)
        response["X-Request-ID"] = request_id
        logger.info(
            json.dumps(
                {
                    "event": "request_completed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "user_id": getattr(_safe_authenticated_user(request), "id", None),
                },
                ensure_ascii=True,
            )
        )
        try:
            from .models import MetricaRequisicao

            if not _observability_tables_ready() or not _connection_ready():
                return response
            empresa, obra, usuario = _resolver_contexto_request(request)
            MetricaRequisicao.objects.create(
                empresa=empresa,
                obra=obra,
                usuario=usuario,
                request_id=request_id,
                metodo=request.method,
                path=request.path[:255],
                status_code=response.status_code,
                duracao_ms=duration_ms,
            )
        except (OperationalError, ProgrammingError, TransactionManagementError, DatabaseError):
            logger.debug("Falha tolerada ao persistir metrica de requisicao", exc_info=True)
        except Exception:
            logger.debug("Erro nao bloqueante ao persistir metrica de requisicao", exc_info=True)
        return response

    def process_exception(self, request, exception):
        if isinstance(exception, (Http404, PermissionDenied)):
            return None
        self._registrar_erro(request, exception)
        return None

    def _registrar_erro(self, request, exception):
        request_id = getattr(request, "request_id", "") or getattr(request, "_audit_request_id", "")
        logger.exception(
            json.dumps(
                {
                    "event": "request_exception",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "exception_class": exception.__class__.__name__,
                    "message": str(exception),
                },
                ensure_ascii=True,
            )
        )
        try:
            from .models import RastroErroAplicacao

            if not _observability_tables_ready() or not _connection_ready():
                return
            empresa, obra, usuario = _resolver_contexto_request(request)
            RastroErroAplicacao.objects.create(
                empresa=empresa,
                obra=obra,
                usuario=usuario,
                request_id=request_id,
                metodo=request.method,
                path=request.path[:255],
                status_code=500,
                classe_erro=exception.__class__.__name__,
                mensagem=str(exception),
                stacktrace="".join(traceback.format_exception(type(exception), exception, exception.__traceback__)),
            )
        except (OperationalError, ProgrammingError, TransactionManagementError, DatabaseError):
            logger.debug("Falha tolerada ao persistir rastro de erro da aplicacao", exc_info=True)
        except Exception:
            logger.debug("Erro nao bloqueante ao persistir rastro de erro da aplicacao", exc_info=True)
