from django.core.exceptions import PermissionDenied

from .permissions import can_access_obra, get_empresa_do_usuario, get_obras_permitidas


class TenantService:
    """Wrapper de compatibilidade sobre a camada oficial de permissões."""

    @staticmethod
    def get_empresa(user):
        return get_empresa_do_usuario(user)

    @staticmethod
    def get_obras(user):
        return get_obras_permitidas(user)

    @classmethod
    def filtrar_por_empresa(cls, user, queryset, campo_empresa="empresa"):
        if user.is_superuser:
            return queryset
        empresa = cls.get_empresa(user)
        if not empresa:
            return queryset.none()
        return queryset.filter(**{campo_empresa: empresa})

    @classmethod
    def filtrar_por_obra(cls, user, queryset, campo_obra="obra"):
        if user.is_superuser:
            return queryset
        obras_ids = cls.get_obras(user).values_list("id", flat=True)
        return queryset.filter(**{f"{campo_obra}__in": obras_ids})

    @classmethod
    def filtrar_inteligente(cls, user, queryset, campo_obra="obra"):
        if user.is_superuser:
            return queryset
        model = queryset.model
        if hasattr(model, "empresa"):
            return cls.filtrar_por_empresa(user, queryset)
        if hasattr(model, campo_obra):
            return cls.filtrar_por_obra(user, queryset, campo_obra)
        return queryset

    @classmethod
    def validar_acesso(cls, user, obj):
        if user.is_superuser:
            return
        empresa_usuario = cls.get_empresa(user)
        if not empresa_usuario:
            raise PermissionDenied("Usuário sem empresa associada.")

        empresa_obj = getattr(obj, "empresa", None)
        if empresa_obj is not None and empresa_obj.pk != empresa_usuario.pk:
            raise PermissionDenied("Acesso não autorizado.")

        obra = getattr(obj, "obra", None)
        if obra is not None and not can_access_obra(user, obra):
            raise PermissionDenied("Acesso não autorizado.")

    @classmethod
    def contexto_obra(cls, user, session):
        from .models import Obra

        obra_id = session.get("obra_contexto_id")
        if not obra_id:
            return None
        try:
            return cls.get_obras(user).get(pk=obra_id)
        except Obra.DoesNotExist:
            session.pop("obra_contexto_id", None)
            return None
