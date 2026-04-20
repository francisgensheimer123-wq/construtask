"""Wrappers legados para a camada oficial de permissões/tenant."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .permissions import get_empresa_do_usuario, get_obras_permitidas


def get_obras_do_usuario(user):
    return get_obras_permitidas(user)


class TenantMixin(LoginRequiredMixin):
    tenant_obra_field = "obra"

    def get_empresa(self):
        return get_empresa_do_usuario(self.request.user)

    def get_obras_permitidas(self):
        return get_obras_do_usuario(self.request.user)

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return qs

        obras_ids = self.get_obras_permitidas().values_list("id", flat=True)

        if hasattr(self.model, "empresa"):
            empresa = self.get_empresa()
            if empresa:
                return qs.filter(empresa=empresa)

        if self.tenant_obra_field:
            return qs.filter(**{f"{self.tenant_obra_field}__in": obras_ids})

        return qs

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        self._verificar_acesso_objeto(obj)
        return obj

    def _verificar_acesso_objeto(self, obj):
        user = self.request.user
        if user.is_superuser:
            return

        obras_ids = set(self.get_obras_permitidas().values_list("id", flat=True))

        if hasattr(obj, "empresa_id") and obj.empresa_id:
            empresa = self.get_empresa()
            if empresa and obj.empresa_id != empresa.pk:
                raise PermissionDenied("Acesso não autorizado a este recurso.")
            return

        obra_id = getattr(obj, "obra_id", None)
        if obra_id and obra_id not in obras_ids:
            raise PermissionDenied("Acesso não autorizado a este recurso.")

    def _filtrar_por_tenant(self, queryset, campo_obra="obra"):
        user = self.request.user
        if user.is_superuser:
            return queryset

        obras_ids = self.get_obras_permitidas().values_list("id", flat=True)

        if hasattr(queryset.model, "empresa"):
            empresa = self.get_empresa()
            if empresa:
                return queryset.filter(empresa=empresa)

        return queryset.filter(**{f"{campo_obra}__in": obras_ids})
