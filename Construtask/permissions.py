"""
Módulo de permissões e isolamento de dados por empresa.
"""

from django.conf import settings
from .models import Obra, UsuarioEmpresa


def get_usuario_empresa(user):
    """
    Obtém o UsuarioEmpresa do usuário logado.
    Retorna None se não tiver perfil ou UsuarioEmpresa.
    """
    if not user.is_authenticated:
        return None
    
    # Superuser tem acesso total
    if user.is_superuser:
        return None  # sinaliza acesso total
    
    # Verificar se tem perfil
    if not hasattr(user, 'perfil'):
        return None
    
    # Verificar se tem UsuarioEmpresa
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
    return Obra.objects.select_related("empresa").filter(pk=obra_id).first()


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


# Import mantido no topo para os helpers operacionais
