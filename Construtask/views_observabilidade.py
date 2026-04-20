from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.views.generic import TemplateView

from .application.observabilidade import contexto_observabilidade_request
from .permissions import is_admin_sistema


class ObservabilidadeDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "app/observabilidade_dashboard.html"

    def test_func(self):
        return is_admin_sistema(self.request.user)

    def handle_no_permission(self):
        return HttpResponse(status=403)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(contexto_observabilidade_request(self.request))
        return context


@login_required
def observabilidade_teste_erro_view(request):
    if not is_admin_sistema(request.user):
        return HttpResponse(status=403)
    raise RuntimeError("Falha de observabilidade disparada para teste controlado.")
