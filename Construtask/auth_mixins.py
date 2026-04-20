"""
Módulo de autenticação e permissões.
Mantém compatibilidade com imports legados, usando a trilha oficial de tenant.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin as DjangoLoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils import timezone

from .permissions import get_empresa_do_usuario


class LoginRequiredMixin(DjangoLoginRequiredMixin):
    redirect_field_name = "next"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, "Você precisa fazer login para acessar esta página.")
            return HttpResponseRedirect(f"{reverse_lazy('login')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)


class ConstrutaskLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def _client_ip(self):
        forwarded_for = self.request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return self.request.META.get("REMOTE_ADDR", "desconhecido")

    def _identifier(self):
        username = (self.request.POST.get("username") or self.request.GET.get("username") or "").strip().lower()
        return username or "__anonimo__"

    def _cache_key(self):
        return f"construtask:login-lock:{self._client_ip()}:{self._identifier()}"

    def _lock_state(self):
        return cache.get(self._cache_key()) or {"tentativas": 0, "bloqueado_ate": None}

    def _lock_message(self, bloqueado_ate):
        if not bloqueado_ate:
            return "Muitas tentativas de login. Tente novamente em alguns minutos."
        return f"Muitas tentativas de login. Tente novamente apos {timezone.localtime(bloqueado_ate).strftime('%d/%m/%Y %H:%M')}."

    def dispatch(self, request, *args, **kwargs):
        estado = self._lock_state()
        bloqueado_ate = estado.get("bloqueado_ate")
        if bloqueado_ate and bloqueado_ate > timezone.now():
            if request.method == "POST":
                form = self.get_form()
                form.add_error(None, self._lock_message(bloqueado_ate))
                return self.form_invalid(form)
        elif bloqueado_ate:
            cache.delete(self._cache_key())
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return self.request.GET.get("next", reverse_lazy("obra_list"))

    def form_valid(self, form):
        cache.delete(self._cache_key())
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.method == "POST":
            estado = self._lock_state()
            tentativas = int(estado.get("tentativas") or 0) + 1
            max_tentativas = max(int(getattr(settings, "CONSTRUTASK_LOGIN_MAX_ATTEMPTS", 5) or 5), 1)
            lockout_minutes = max(int(getattr(settings, "CONSTRUTASK_LOGIN_LOCKOUT_MINUTES", 15) or 15), 1)
            bloqueado_ate = None
            if tentativas >= max_tentativas:
                bloqueado_ate = timezone.now() + timedelta(minutes=lockout_minutes)
                form.add_error(None, self._lock_message(bloqueado_ate))
            cache.set(
                self._cache_key(),
                {"tentativas": tentativas, "bloqueado_ate": bloqueado_ate},
                timeout=lockout_minutes * 60,
            )
        return super().form_invalid(form)


class ConstrutaskLogoutView(LogoutView):
    next_page = reverse_lazy("login")

    def dispatch(self, request, *args, **kwargs):
        messages.info(request, "Você foi desconectado com sucesso.")
        return super().dispatch(request, *args, **kwargs)


def get_user_empresa(user):
    return get_empresa_do_usuario(user)


def get_user_empresa_id(user):
    empresa = get_user_empresa(user)
    return empresa.pk if empresa else None


def filter_queryset_by_empresa(queryset, user):
    empresa = get_user_empresa(user)
    if empresa:
        return queryset.filter(empresa=empresa)
    return queryset


def filter_queryset_by_empresa_or_null(queryset, user):
    empresa = get_user_empresa(user)
    if empresa:
        return queryset.filter(Q(empresa=empresa) | Q(empresa__isnull=True))
    return queryset
