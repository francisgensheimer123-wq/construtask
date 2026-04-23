from django.db import models
from django.db.models import Q


class TenantScopedQuerySet(models.QuerySet):
    def _field_names(self):
        return {field.name for field in self.model._meta.fields}

    def for_empresa(self, empresa, field_name="empresa"):
        if field_name not in self._field_names():
            return self
        if not empresa:
            return self.none()
        return self.filter(**{field_name: empresa})

    def for_obra(self, obra, field_name="obra"):
        if field_name not in self._field_names():
            return self
        if not obra:
            return self.none()
        return self.filter(**{field_name: obra})

    def for_obras(self, obras, field_name="obra", include_null=False):
        if field_name not in self._field_names():
            return self
        if hasattr(obras, "values_list"):
            obra_ids = list(obras.values_list("id", flat=True))
        else:
            obra_ids = [getattr(obra, "pk", obra) for obra in (obras or []) if getattr(obra, "pk", obra)]
        if not obra_ids and not include_null:
            return self.none()
        filtro = Q(**{f"{field_name}__in": obra_ids}) if obra_ids else Q()
        if include_null:
            filtro |= Q(**{f"{field_name}__isnull": True})
        return self.filter(filtro)

    def for_user(self, user, empresa_field="empresa", obra_field="obra"):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self

        from .permissions import get_empresa_do_usuario, get_obras_permitidas

        field_names = self._field_names()
        queryset = self
        empresa = get_empresa_do_usuario(user)

        if empresa_field in field_names:
            if not empresa:
                return self.none()
            queryset = queryset.filter(**{empresa_field: empresa})

        if obra_field in field_names:
            include_null = self.model._meta.get_field(obra_field).null
            queryset = queryset.for_obras(
                get_obras_permitidas(user),
                field_name=obra_field,
                include_null=include_null,
            )
        return queryset

    def for_request(self, request, empresa_field="empresa", obra_field="obra"):
        return self.for_user(getattr(request, "user", None), empresa_field=empresa_field, obra_field=obra_field)


TenantScopedManager = models.Manager.from_queryset(TenantScopedQuerySet)
