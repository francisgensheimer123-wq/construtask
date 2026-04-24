"""
Modulo de autenticacao e permissoes.
Mantem compatibilidade com imports legados, usando a trilha oficial de tenant.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin as DjangoLoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.sessions.models import Session
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils import timezone

from .cache_utils import critical_cache_delete, critical_cache_get, critical_cache_set
from .permissions import get_empresa_do_usuario


class LoginRequiredMixin(DjangoLoginRequiredMixin):
    redirect_field_name = "next"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, "Voce precisa fazer login para acessar esta pagina.")
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

    def _ip_cache_key(self):
        return f"construtask:login-lock-ip:{self._client_ip()}"

    def _user_ip_cache_key(self, user):
        return f"construtask:user-active-ip:{user.pk}"

    def _session_key(self):
        if not self.request.session.session_key:
            self.request.session.create()
        return self.request.session.session_key

    def _cached_session_is_active_for_user(self, estado, user):
        session_key = estado.get("session_key")
        if not session_key:
            return False
        sessao = Session.objects.filter(session_key=session_key, expire_date__gt=timezone.now()).first()
        if not sessao:
            return False
        try:
            dados_sessao = sessao.get_decoded()
        except Exception:
            return False
        return str(dados_sessao.get(SESSION_KEY)) == str(user.pk)

    def _lock_state(self, cache_key):
        return critical_cache_get(cache_key) or {"tentativas": 0, "bloqueado_ate": None}

    def _lock_config(self):
        return {
            self._cache_key(): {
                "max_tentativas": max(int(getattr(settings, "CONSTRUTASK_LOGIN_MAX_ATTEMPTS", 5) or 5), 1),
                "lockout_minutes": max(int(getattr(settings, "CONSTRUTASK_LOGIN_LOCKOUT_MINUTES", 15) or 15), 1),
            },
            self._ip_cache_key(): {
                "max_tentativas": max(int(getattr(settings, "CONSTRUTASK_LOGIN_IP_MAX_ATTEMPTS", 10) or 10), 1),
                "lockout_minutes": max(int(getattr(settings, "CONSTRUTASK_LOGIN_IP_LOCKOUT_MINUTES", 15) or 15), 1),
            },
        }

    def _lock_message(self, bloqueado_ate):
        if not bloqueado_ate:
            return "Muitas tentativas de login. Tente novamente em alguns minutos."
        return (
            "Muitas tentativas de login. Tente novamente apos "
            f"{timezone.localtime(bloqueado_ate).strftime('%d/%m/%Y %H:%M')}."
        )

    def _active_lock(self):
        agora = timezone.now()
        bloqueios_ativos = []
        for cache_key in self._lock_config():
            estado = self._lock_state(cache_key)
            bloqueado_ate = estado.get("bloqueado_ate")
            if bloqueado_ate and bloqueado_ate > agora:
                bloqueios_ativos.append(bloqueado_ate)
            elif bloqueado_ate:
                critical_cache_delete(cache_key)
        if not bloqueios_ativos:
            return None
        return max(bloqueios_ativos)

    def dispatch(self, request, *args, **kwargs):
        bloqueado_ate = self._active_lock()
        if bloqueado_ate and request.method == "POST":
            form = self.get_form()
            form.add_error(None, self._lock_message(bloqueado_ate))
            return self.render_to_response(self.get_context_data(form=form))
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return self.request.GET.get("next", reverse_lazy("home"))

    def form_valid(self, form):
        user = form.get_user()
        user_ip_cache_key = self._user_ip_cache_key(user)
        estado = critical_cache_get(user_ip_cache_key) or {}
        ip_atual = self._client_ip()
        sessao_atual = self._session_key()
        if estado.get("ip") and estado.get("ip") != ip_atual and estado.get("session_key") != sessao_atual:
            if self._cached_session_is_active_for_user(estado, user):
                form.add_error(None, "Este usuario ja possui uma sessao ativa em outro endereco IP.")
                return self.form_invalid(form)
            critical_cache_delete(user_ip_cache_key)

        for cache_key in self._lock_config():
            critical_cache_delete(cache_key)

        response = super().form_valid(form)
        critical_cache_set(
            user_ip_cache_key,
            {"ip": ip_atual, "session_key": self.request.session.session_key},
            timeout=max(int(getattr(settings, "SESSION_COOKIE_AGE", 1209600) or 1209600), 300),
        )
        return response

    def form_invalid(self, form):
        if self.request.method == "POST":
            bloqueio_disparado = None
            for cache_key, config in self._lock_config().items():
                estado = self._lock_state(cache_key)
                tentativas = int(estado.get("tentativas") or 0) + 1
                bloqueado_ate = None
                if tentativas >= config["max_tentativas"]:
                    bloqueado_ate = timezone.now() + timedelta(minutes=config["lockout_minutes"])
                    bloqueio_disparado = max(bloqueio_disparado, bloqueado_ate) if bloqueio_disparado else bloqueado_ate
                critical_cache_set(
                    cache_key,
                    {"tentativas": tentativas, "bloqueado_ate": bloqueado_ate},
                    timeout=config["lockout_minutes"] * 60,
                )
            if bloqueio_disparado:
                form.add_error(None, self._lock_message(bloqueio_disparado))
        return super().form_invalid(form)


class ConstrutaskLogoutView(LogoutView):
    next_page = reverse_lazy("login")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            cache_key = f"construtask:user-active-ip:{request.user.pk}"
            estado = critical_cache_get(cache_key) or {}
            if estado.get("session_key") == request.session.session_key:
                critical_cache_delete(cache_key)
        messages.info(request, "Voce foi desconectado com sucesso.")
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
