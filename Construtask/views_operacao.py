from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.views.generic import TemplateView

from .application.operacao import contexto_operacao_request
from .permissions import is_admin_sistema


class OperacaoTecnicaDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "app/operacao_tecnica_dashboard.html"

    def test_func(self):
        return is_admin_sistema(self.request.user)

    def handle_no_permission(self):
        return HttpResponse(status=403)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(contexto_operacao_request(self.request))
        return context
