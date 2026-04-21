from django.core.exceptions import PermissionDenied

from .permissions import can_access_obra, get_empresa_do_usuario, get_obras_permitidas


class LimitePlanoExcedido(Exception):
    """
    Levantada quando uma ação viola os limites do plano SaaS da empresa.
    Carrega a mensagem amigável que deve ser exibida ao usuário.
    """
    pass


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

    # ── Verificações de limite de plano ────────────────────────────────────

    @classmethod
    def _get_plano(cls, empresa):
        """
        Retorna o PlanoEmpresa vinculado, ou None se não existir.
        Usa hasattr para evitar ImportError circular — o modelo é resolvido
        em tempo de execução.
        """
        try:
            return empresa.plano
        except Exception:
            return None

    @classmethod
    def verificar_limite_usuario(cls, empresa):
        """
        Levanta LimitePlanoExcedido se a empresa já atingiu o teto de usuários
        do plano contratado.

        Uso típico (em views/services de criação de usuário):
            TenantService.verificar_limite_usuario(empresa)
        """
        plano = cls._get_plano(empresa)
        if plano is None:
            # Sem plano configurado → superuser não restringiu ainda, libera
            return
        if not plano.pode_criar_usuario():
            raise LimitePlanoExcedido(plano.mensagem_limite_usuario())

    @classmethod
    def verificar_limite_obra(cls, empresa):
        """
        Levanta LimitePlanoExcedido se a empresa já atingiu o teto de obras
        do plano contratado.

        Uso típico (em views/services de criação de obra):
            TenantService.verificar_limite_obra(empresa)
        """
        plano = cls._get_plano(empresa)
        if plano is None:
            return
        if not plano.pode_criar_obra():
            raise LimitePlanoExcedido(plano.mensagem_limite_obra())

    @classmethod
    def status_plano(cls, empresa):
        """
        Retorna um dict com informações do plano para uso em templates/contexto:

        {
            "nome": "Professional",
            "max_usuarios": 23,
            "usuarios_usados": 10,
            "max_obras": 9,
            "obras_usadas": 4,
            "pode_criar_usuario": True,
            "pode_criar_obra": True,
            "alerta_usuario": None | "<mensagem>",
            "alerta_obra": None | "<mensagem>",
        }
        """
        plano = cls._get_plano(empresa)
        if plano is None:
            return {
                "nome": "Sem plano",
                "max_usuarios": None,
                "usuarios_usados": 0,
                "max_obras": None,
                "obras_usadas": 0,
                "pode_criar_usuario": True,
                "pode_criar_obra": True,
                "alerta_usuario": None,
                "alerta_obra": None,
            }

        usuarios_usados = plano.usuarios_ativos()
        obras_usadas = plano.obras_ativas()
        pode_usuario = plano.pode_criar_usuario()
        pode_obra = plano.pode_criar_obra()

        return {
            "nome": plano.get_nome_display(),
            "max_usuarios": plano.max_usuarios,
            "usuarios_usados": usuarios_usados,
            "max_obras": plano.max_obras,
            "obras_usadas": obras_usadas,
            "pode_criar_usuario": pode_usuario,
            "pode_criar_obra": pode_obra,
            "alerta_usuario": None if pode_usuario else plano.mensagem_limite_usuario(),
            "alerta_obra": None if pode_obra else plano.mensagem_limite_obra(),
        }
