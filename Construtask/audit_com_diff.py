"""Compatibilidade legada para auditoria com diff."""

import json
from decimal import Decimal

from django.db import models

from .audit import AuditMiddleware, AuditService, get_current_request


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def _serialize_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, models.Model):
        return value.pk
    return value


class AuditableMixin:
    _AUDIT_EXCLUDE_FIELDS = frozenset({"lft", "rght", "tree_id", "level", "atualizado_em", "updated_at"})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._capturar_estado_original()

    def _capturar_estado_original(self):
        self.__original__ = self._estado_atual()

    def _estado_atual(self):
        estado = {}
        for field in self._meta.fields:
            if field.name in self._AUDIT_EXCLUDE_FIELDS:
                continue
            try:
                estado[field.name] = _serialize_value(getattr(self, field.attname))
            except Exception:
                continue
        return estado

    def _calcular_diff(self):
        original = getattr(self, "__original__", {})
        atual = self._estado_atual()
        if not original:
            return None, atual, list(atual.keys())
        campos_alterados = [campo for campo, valor in atual.items() if original.get(campo) != valor]
        if not campos_alterados:
            return original, atual, []
        antes = {campo: original.get(campo) for campo in campos_alterados}
        depois = {campo: atual[campo] for campo in campos_alterados}
        return antes, depois, campos_alterados

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._capturar_estado_original()
