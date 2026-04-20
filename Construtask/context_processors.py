"""
Context processors para o Construtask.
Inclui contexto de obra e permissões de usuário.
"""

from .permissions import get_obras_permitidas, is_admin_empresa_vinculado, is_admin_sistema


def obra_contexto(request):
    """
    Context processor que fornece:
    - Lista de obras permitidas para o usuário (não inclui "todas")
    - Obra atualmente selecionada no contexto
    """
    obras_permitidas = get_obras_permitidas(request.user)
    obra_id = request.session.get("obra_contexto_id")
    obra_atual = None
    
    if obra_id:
        # Verificar se a obra está na lista de permitidas
        obra_atual = obras_permitidas.filter(pk=obra_id).first()
        
        # Se não encontrou, limpar sessão
        if not obra_atual:
            request.session.pop("obra_contexto_id", None)
            obra_id = None
    
    return {
        "obras_contexto": obras_permitidas.order_by("codigo"),
        "obra_contexto_atual": obra_atual,
        # Flag para indicar se há necessidade de selecionar obra
        "obrigatorio_selecionar_obra": not obra_id and obras_permitidas.exists(),
    }


def user_permissoes(request):
    """
    Context processor que fornece informações de permissão do usuário.
    """
    from django.contrib.auth.models import AnonymousUser
    
    if isinstance(request.user, AnonymousUser) or not request.user.is_authenticated:
        return {
            "is_superuser": False,
            "is_admin_empresa": False,
            "is_admin_sistema": False,
        }
    
    is_superuser = request.user.is_superuser
    admin_sistema = is_admin_sistema(request.user)
    admin_empresa = is_admin_empresa_vinculado(request.user)
    
    return {
        "is_superuser": is_superuser,
        "is_admin_empresa": admin_empresa,
        "is_admin_sistema": admin_sistema,
    }
