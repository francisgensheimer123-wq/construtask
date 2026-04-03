from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import NaoConformidadeForm
from .models import Obra
from .models_qualidade import NaoConformidade
from .permissions import get_empresa_operacional, get_obra_do_contexto
from .services_qualidade import QualidadeWorkflowService


def _obra_contexto(request):
    return get_obra_do_contexto(request)


class NaoConformidadeListView(LoginRequiredMixin, ListView):
    model = NaoConformidade
    template_name = "app/nao_conformidade_list.html"
    context_object_name = "nao_conformidades"

    def get_queryset(self):
        empresa = get_empresa_operacional(self.request)
        queryset = NaoConformidade.objects.select_related("obra", "plano_contas", "responsavel", "criado_por")
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        obra = _obra_contexto(self.request)
        if obra:
            queryset = queryset.filter(obra=obra)
        termo = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        if termo:
            queryset = queryset.filter(descricao__icontains=termo)
        if status:
            queryset = queryset.filter(status=status)
        return queryset.order_by("-criado_em")


class NaoConformidadeCreateView(LoginRequiredMixin, CreateView):
    model = NaoConformidade
    form_class = NaoConformidadeForm
    template_name = "app/nao_conformidade_form.html"
    success_url = reverse_lazy("nao_conformidade_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = get_empresa_operacional(self.request)
        kwargs["obra_contexto"] = _obra_contexto(self.request)
        return kwargs

    def form_valid(self, form):
        obra = form.cleaned_data["obra"]
        empresa = get_empresa_operacional(self.request, obra=obra)
        nc = QualidadeWorkflowService.abrir(
            empresa=empresa,
            obra=obra,
            plano_contas=form.cleaned_data.get("plano_contas"),
            descricao=form.cleaned_data["descricao"],
            causa=form.cleaned_data.get("causa", ""),
            acao_corretiva=form.cleaned_data.get("acao_corretiva", ""),
            responsavel=form.cleaned_data["responsavel"],
            criado_por=self.request.user,
        )
        self.object = nc
        status = form.cleaned_data.get("status")
        if status == "EM_TRATAMENTO":
            QualidadeWorkflowService.iniciar_tratamento(nc, self.request.user)
        elif status == "EM_VERIFICACAO":
            QualidadeWorkflowService.iniciar_tratamento(nc, self.request.user)
            QualidadeWorkflowService.enviar_para_verificacao(nc, self.request.user)
        elif status == "ENCERRADA":
            QualidadeWorkflowService.iniciar_tratamento(nc, self.request.user)
            QualidadeWorkflowService.enviar_para_verificacao(nc, self.request.user)
            QualidadeWorkflowService.encerrar(nc, self.request.user)
        messages.success(self.request, "Nao conformidade registrada com sucesso.")
        return redirect("nao_conformidade_detail", pk=nc.pk)


class NaoConformidadeDetailView(LoginRequiredMixin, DetailView):
    model = NaoConformidade
    template_name = "app/nao_conformidade_detail.html"
    context_object_name = "nc"

    def get_queryset(self):
        empresa = get_empresa_operacional(self.request)
        queryset = NaoConformidade.objects.select_related("obra", "plano_contas", "responsavel", "criado_por")
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        return queryset.prefetch_related("historico__usuario")

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        acao = request.POST.get("acao")
        observacao = request.POST.get("observacao", "")
        if acao == "TRATAMENTO":
            QualidadeWorkflowService.iniciar_tratamento(self.object, request.user, observacao)
        elif acao == "VERIFICACAO":
            QualidadeWorkflowService.enviar_para_verificacao(self.object, request.user, observacao)
        elif acao == "ENCERRAMENTO":
            QualidadeWorkflowService.encerrar(self.object, request.user, observacao)
        elif acao == "CANCELAMENTO":
            QualidadeWorkflowService.cancelar(self.object, request.user, observacao)
        messages.success(request, "Workflow da nao conformidade atualizado.")
        return redirect("nao_conformidade_detail", pk=self.object.pk)


class NaoConformidadeUpdateView(LoginRequiredMixin, UpdateView):
    model = NaoConformidade
    form_class = NaoConformidadeForm
    template_name = "app/nao_conformidade_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = get_empresa_operacional(self.request, obra=self.object.obra)
        kwargs["obra_contexto"] = self.object.obra
        return kwargs

    def get_success_url(self):
        messages.success(self.request, "Nao conformidade atualizada com sucesso.")
        return reverse_lazy("nao_conformidade_detail", kwargs={"pk": self.object.pk})
