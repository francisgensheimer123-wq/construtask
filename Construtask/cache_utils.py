from __future__ import annotations

from typing import Any, Callable

from django.conf import settings
from django.core.cache import caches


_MISSING = object()
_REQUEST_CACHE_ATTR = "_construtask_request_cache"


def _safe_get(cache_alias: str, key: str, default: Any = None) -> Any:
    try:
        return caches[cache_alias].get(key, default)
    except Exception:
        return default


def _safe_set(cache_alias: str, key: str, value: Any, timeout: int | None = None) -> None:
    try:
        caches[cache_alias].set(key, value, timeout=timeout)
    except Exception:
        return


def _safe_delete(cache_alias: str, key: str) -> None:
    try:
        caches[cache_alias].delete(key)
    except Exception:
        return


def _safe_add(cache_alias: str, key: str, value: Any, timeout: int | None = None) -> bool:
    try:
        return bool(caches[cache_alias].add(key, value, timeout=timeout))
    except Exception:
        return False


def _backend(cache_alias: str) -> str:
    return settings.CACHES.get(cache_alias, {}).get("BACKEND", "")


def _use_fallback_cache() -> bool:
    return _backend("default") == "django_redis.cache.RedisCache" and "critical" in settings.CACHES


def resilient_cache_get(key: str, default: Any = None) -> Any:
    value = _safe_get("default", key, _MISSING)
    if value is not _MISSING:
        return value
    if _use_fallback_cache():
        value = _safe_get("critical", key, _MISSING)
        if value is not _MISSING:
            return value
    return default


def resilient_cache_set(key: str, value: Any, timeout: int | None = None) -> Any:
    if _use_fallback_cache():
        _safe_set("critical", key, value, timeout=timeout)
    _safe_set("default", key, value, timeout=timeout)
    return value


def resilient_cache_delete(key: str) -> None:
    if _use_fallback_cache():
        _safe_delete("critical", key)
    _safe_delete("default", key)


def resilient_cache_get_or_set(key: str, builder: Callable[[], Any], timeout: int | None = None) -> Any:
    value = resilient_cache_get(key, _MISSING)
    if value is not _MISSING:
        return value
    value = builder()
    resilient_cache_set(key, value, timeout=timeout)
    return value


def critical_cache_get(key: str, default: Any = None) -> Any:
    return _safe_get("critical", key, default)


def critical_cache_set(key: str, value: Any, timeout: int | None = None) -> Any:
    _safe_set("critical", key, value, timeout=timeout)
    return value


def critical_cache_delete(key: str) -> None:
    _safe_delete("critical", key)


def critical_cache_add(key: str, value: Any, timeout: int | None = None) -> bool:
    return _safe_add("critical", key, value, timeout=timeout)


def request_local_get_or_set(request: Any, key: str, builder: Callable[[], Any]) -> Any:
    if request is None:
        return builder()
    state = getattr(request, _REQUEST_CACHE_ATTR, None)
    if state is None:
        state = {}
        setattr(request, _REQUEST_CACHE_ATTR, state)
    if key in state:
        return state[key]
    state[key] = builder()
    return state[key]
