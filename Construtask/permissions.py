"""
Módulo de permissões e isolamento de dados por empresa.
"""

from django.core.exceptions import PermissionDenied

from .models import Obra, PermissaoModuloAcao, UsuarioEmpresa

OBRA_STATUS_SOMENTE_LEITURA = {"PARALISADA", "CONCLUIDA"}


PERMISSOES_MODULO_ACAO = {
    "planejamento": ("view", "create", "update", "approve", "export"),
    "contratos": ("view", "create", "update", "approve", "export"),
    "medicoes": ("view", "create", "update", "approve", "export"),
    "orcamento": ("view", "create", "update", "approve", "export"),
    "compras": ("view", "create", "update", "approve", "export"),
    "comunicacoes": ("view", "create", "update", "approve", "export"),
    "documentos": ("view", "create", "update", "approve", "export"),
    "riscos": ("view", "create", "update", "approve", "export"),
    "qualidade": ("view", "create", "update", "approve", "export"),
    "usuarios": ("view", "manage"),
    "lgpd": ("view", "manage", "export"),
}

PERMISSOES_PADRAO_POR_PAPEL = {
    "TECNICO_OBRAS": {
        "planejamento": {"view", "create", "update", "approve", "export"},
        "contratos": {"view", "create", "update", "approve", "export"},
        "medicoes": {"view", "create", "update", "approve", "export"},
        "orcamento": {"view", "create", "update", "approve", "export"},
        "compras": {"view", "create", "update", "approve", "export"},
        "comunicacoes": {"view", "create", "update", "export"},
        "documentos": {"view", "create", "update", "approve", "export"},
        "riscos": {"view", "create", "update", "approve", "export"},
        "qualidade": {"view", "create", "update", "approve", "export"},
    },
    "ENGENHEIRO_OBRAS": {
        "planejamento": {"view", "create", "update", "approve", "export"},
        "contratos": {"view", "create", "update", "approve", "export"},
        "medicoes": {"view", "create", "update", "approve", "export"},
        "orcamento": {"view", "create", "update", "approve", "export"},
        "compras": {"view", "create", "update", "approve", "export"},
        "comunicacoes": {"view", "create", "update", "approve", "export"},
        "documentos": {"view", "create", "update", "approve", "export"},
        "riscos": {"view", "create", "update", "approve", "export"},
        "qualidade": {"view", "create", "update", "approve", "export"},
    },
    "COORDENADOR_OBRAS": {
        "planejamento": {"view", "create", "update", "approve", "export"},
        "contratos": {"view", "create", "update", "approve", "export"},
        "medicoes": {"view", "create", "update", "approve", "export"},
        "orcamento": {"view", "create", "update", "approve", "export"},
        "compras": {"view", "create", "update", "approve", "export"},
        "comunicacoes": {"view", "create", "update", "approve", "export"},
        "documentos": {"view", "create", "update", "approve", "export"},
        "riscos": {"view", "create", "update", "approve", "export"},
        "qualidade": {"view", "create", "update", "approve", "export"},
        "usuarios": {"view"},
        "lgpd": {"view", "export"},
    },
    "GERENTE_OBRAS": {
        "planejamento": {"view", "create", "update", "approve", "export"},
        "contratos": {"view", "create", "update", "approve", "export"},
        "medicoes": {"view", "create", "update", "approve", "export"},
        "orcamento": {"view", "create", "update", "approve", "export"},
        "compras": {"view", "create", "update", "approve", "export"},
        "comunicacoes": {"view", "create", "update", "approve", "export"},
        "documentos": {"view", "create", "update", "approve", "export"},
        "riscos": {"view", "create", "update", "approve", "export"},
        "qualidade": {"view", "create", "update", "approve", "export"},
        "usuarios": {"view", "manage"},
        "lgpd": {"view", "manage", "export"},
    },
}


def is_admin_sistema(user):
    """Somente o superuser tecnico 'Construtask' pode atuar na administracao sistêmica."""
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
        and getattr(user, "username", "") == "Construtask"
    )


def is_admin_empresa_vinculado(user):
    """Admin da empresa real, sem considerar superusers globais."""
    if not getattr(user, "is_authenticated", False):
        return False
    try:
        return user.usuario_empresa.is_admin_empresa
    except (AttributeError, UsuarioEmpresa.DoesNotExist):
        return False


def get_usuario_empresa(user):
    """Obtém o UsuarioEmpresa do usuário logado."""
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return None
    try:
        return user.usuario_empresa
    except UsuarioEmpresa.DoesNotExist:
        return None


def is_admin_empresa(user):
    """Verifica se o usuário é admin de alguma empresa."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    
    try:
        return user.usuario_empresa.is_admin_empresa
    except (AttributeError, UsuarioEmpresa.DoesNotExist):
        return False


def get_obras_permitidas(user):
    """
    Retorna queryset de obras que o usuário pode acessar.
    - Superuser: todas as obras
    - Admin empresa: todas as obras da empresa
    - Usuário comum: apenas obras liberadas
    """
    from .models import Obra, UsuarioEmpresa
    
    if not user.is_authenticated:
        return Obra.objects.none()
    
    if user.is_superuser:
        return Obra.objects.all()
    
    # Buscar UsuarioEmpresa do usuário via query
    try:
        usuario_empresa = UsuarioEmpresa.objects.select_related('empresa').get(usuario=user)
    except UsuarioEmpresa.DoesNotExist:
        return Obra.objects.none()
    
    empresa = usuario_empresa.empresa
    
    if not empresa:
        return Obra.objects.none()
    
    # Admin da empresa vê todas as obras da empresa
    if usuario_empresa.is_admin_empresa:
        return Obra.objects.filter(empresa=empresa)
    
    # Usuário comum vê apenas obras permitidas
    return usuario_empresa.obras_permitidas.filter(empresa=empresa)


def can_access_obra(user, obra):
    """Verifica se o usuário pode acessar uma obra específica."""
    if not user.is_authenticated:
        return False
    
    if user.is_superuser:
        return True
    
    try:
        usuario_empresa = user.usuario_empresa
    except UsuarioEmpresa.DoesNotExist:
        return False
    
    if not usuario_empresa.empresa:
        return False
    
    # Verificar se a obra pertence à empresa do usuário
    if obra.empresa_id != usuario_empresa.empresa_id:
        return False
    
    # Admin da empresa pode acessar qualquer obra da empresa
    if usuario_empresa.is_admin_empresa:
        return True
    
    # Usuário comum: verificar se tem permissão na obra
    return usuario_empresa.obras_permitidas.filter(pk=obra.pk).exists()


def get_empresa_do_usuario(user):
    """Retorna a empresa do usuário ou None."""
    if not user.is_authenticated:
        return None
    
    if user.is_superuser:
        return None  # Superuser não tem empresa específica
    
    try:
        usuario_empresa = user.usuario_empresa
        return usuario_empresa.empresa
    except (AttributeError, UsuarioEmpresa.DoesNotExist):
        return None


def get_obra_do_contexto(request):
    """Retorna a obra selecionada no contexto principal da sessão."""
    if not getattr(request, "session", None):
        return None
    obra_id = request.session.get("obra_contexto_id") or request.session.get("obra_selecionada_id")
    if not obra_id:
        return None
    obra = Obra.objects.select_related("empresa").filter(pk=obra_id).first()
    if not obra:
        return None
    if hasattr(request, "user") and getattr(request.user, "is_authenticated", False) and not can_access_obra(request.user, obra):
        request.session.pop("obra_contexto_id", None)
        request.session.pop("obra_selecionada_id", None)
        return None
    return obra


def obra_em_somente_leitura(obra):
    return bool(obra and getattr(obra, "status", None) in OBRA_STATUS_SOMENTE_LEITURA)


def obra_permite_lancamentos(obra):
    return not obra_em_somente_leitura(obra)


def descricao_restricao_obra(obra):
    if not obra:
        return "Selecione uma obra no menu antes de continuar."
    return (
        f"A obra {obra.codigo} - {obra.nome} esta com status "
        f"{obra.get_status_display().lower()} e permite apenas visualizacao."
    )


def filtrar_obras_liberadas_para_lancamento(queryset):
    return queryset.exclude(status__in=sorted(OBRA_STATUS_SOMENTE_LEITURA))


def get_empresa_operacional(request, obra=None):
    """
    Resolve a empresa operacional do contexto atual.

    Prioridade:
    1. Empresa explícita da obra recebida
    2. Empresa da obra selecionada na sessão
    3. Empresa vinculada ao usuário
    """
    if obra and getattr(obra, "empresa_id", None):
        return obra.empresa
    obra_contexto = get_obra_do_contexto(request)
    if obra_contexto and obra_contexto.empresa_id:
        return obra_contexto.empresa
    return get_empresa_do_usuario(request.user)


def filtrar_por_empresa(queryset, empresa):
    """Aplica filtro por empresa quando houver contexto empresarial."""
    if empresa:
        return queryset.filter(empresa=empresa)
    return queryset


def filtrar_por_obra_contexto(request, queryset, campo="obra", vazio_quando_sem_obra=False):
    """
    Aplica filtro pela obra selecionada no contexto principal.

    Quando `vazio_quando_sem_obra=True`, retorna `queryset.none()` se não houver obra
    selecionada. Caso contrário, devolve o queryset original.
    """
    obra = get_obra_do_contexto(request)
    if not obra:
        return queryset.none() if vazio_quando_sem_obra else queryset
    return queryset.filter(**{campo: obra})


def pode_gerenciar_usuarios(user):
    """Verifica se o usuário pode gerenciar usuários da empresa."""
    if not user.is_authenticated:
        return False
    
    if user.is_superuser:
        return True
    
    try:
        return user.usuario_empresa.is_admin_empresa
    except (AttributeError, UsuarioEmpresa.DoesNotExist):
        return False


def get_permissoes_modulo_usuario(user):
    if not getattr(user, "is_authenticated", False):
        return {}
    if user.is_superuser:
        return {modulo: set(acoes) for modulo, acoes in PERMISSOES_MODULO_ACAO.items()}

    usuario_empresa = get_usuario_empresa(user)
    if not usuario_empresa:
        return {}

    base = {
        modulo: set(acoes)
        for modulo, acoes in PERMISSOES_PADRAO_POR_PAPEL.get(usuario_empresa.papel_aprovacao, {}).items()
    }
    if usuario_empresa.is_admin_empresa:
        base.setdefault("usuarios", set()).update({"view", "manage"})
        base.setdefault("lgpd", set()).update({"view", "manage", "export"})

    for permissao in usuario_empresa.permissoes_modulo.all():
        acoes = base.setdefault(permissao.modulo, set())
        if permissao.permitido:
            acoes.add(permissao.acao)
        else:
            acoes.discard(permissao.acao)
    return base


def usuario_tem_permissao_modulo(user, modulo, acao):
    permissoes = get_permissoes_modulo_usuario(user)
    return acao in permissoes.get(modulo, set())


def exigir_permissao_modulo(user, modulo, acao):
    if not usuario_tem_permissao_modulo(user, modulo, acao):
        raise PermissionDenied(f"Usuario sem permissao para {modulo}:{acao}.")


def atualizar_permissoes_usuario_empresa(usuario_empresa, permissoes, *, concedido_por=None):
    existentes = {
        (item.modulo, item.acao): item
        for item in usuario_empresa.permissoes_modulo.all()
    }
    chaves_recebidas = set()
    for modulo, acoes in permissoes.items():
        for acao, permitido in acoes.items():
            chave = (modulo, acao)
            chaves_recebidas.add(chave)
            permissao = existentes.get(chave)
            if permissao:
                if permissao.permitido != permitido or permissao.concedido_por_id != getattr(concedido_por, "pk", None):
                    permissao.permitido = permitido
                    permissao.concedido_por = concedido_por
                    permissao.save(update_fields=["permitido", "concedido_por", "atualizado_em"])
            else:
                PermissaoModuloAcao.objects.create(
                    usuario_empresa=usuario_empresa,
                    modulo=modulo,
                    acao=acao,
                    permitido=permitido,
                    concedido_por=concedido_por,
                )

    for chave, permissao in existentes.items():
        if chave not in chaves_recebidas:
            permissao.delete()


# Import mantido no topo para os helpers operacionais
