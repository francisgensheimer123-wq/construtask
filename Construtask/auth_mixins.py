"""
Módulo de autenticação e permissões.
Inclui mixins, decorators e configurações de login.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin as DjangoLoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy


class LoginRequiredMixin(DjangoLoginRequiredMixin):
    """
    Mixin personalizado para exigir login em views.
    Redireciona para login com próximo URL e exibe mensagem.
    """
    redirect_field_name = 'next'
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Você precisa fazer login para acessar esta página.')
            return HttpResponseRedirect(f"{reverse_lazy('login')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)


class ConstrutaskLoginView(LoginView):
    """
    View de login customizada.
    """
    template_name = 'registration/login.html'
    redirect_authenticated_user = True
    
    def get_success_url(self):
        return self.request.GET.get('next', reverse_lazy('obra-list'))


class ConstrutaskLogoutView(LogoutView):
    """
    View de logout customizada.
    """
    next_page = reverse_lazy('login')
    
    def dispatch(self, request, *args, **kwargs):
        messages.info(request, 'Você foi desconectado com sucesso.')
        return super().dispatch(request, *args, **kwargs)


def get_user_empresa(user):
    """
    Helper para obter a empresa do usuário logado.
    Retorna None se o usuário não tiver perfil ou empresa associada.
    """
    if not user.is_authenticated:
        return None
    
    if hasattr(user, 'perfil') and user.perfil:
        return user.perfil.empresa
    
    return None


def get_user_empresa_id(user):
    """
    Helper para obter o ID da empresa do usuário logado.
    """
    empresa = get_user_empresa(user)
    return empresa.pk if empresa else None


def filter_queryset_by_empresa(queryset, user):
    """
    Filtra queryset pela empresa do usuário.
    Aplica filtro de empresa quando existe.
    """
    empresa = get_user_empresa(user)
    if empresa:
        return queryset.filter(empresa=empresa)
    return queryset


def filter_queryset_by_empresa_or_null(queryset, user):
    """
    Filtra queryset pela empresa do usuário, incluindo registros sem empresa.
    Usado em过渡 período onde dados legados não têm empresa.
    """
    empresa = get_user_empresa(user)
    if empresa:
        return queryset.filter(empresa=empresa) | queryset.filter(empresa__isnull=True)
    return queryset
