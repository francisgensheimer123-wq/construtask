from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django.utils import timezone

from .forms import NaoConformidadeForm
from .models import Obra
from .models_qualidade import NaoConformidade
from .pagination import DefaultPaginationMixin
from .permissions import (
    descricao_restricao_obra,
    get_empresa_operacional,
    get_obra_do_contexto,
    obra_em_somente_leitura,
)
from .services_qualidade import QualidadeWorkflowService
from .services_aprovacao import can_manage_quality
from .export_helpers import (
    _datahora_local,
    _exportar_excel_response,
    _exportar_relatorio_probatorio_excel_response,
    _pdf_relatorio_probatorio_response,
)
from .templatetags.formatters import money_br


def _obra_contexto(request):
    return get_obra_do_contexto(request)


class NaoConformidadeListView(LoginRequiredMixin, DefaultPaginationMixin, ListView):
    model = NaoConformidade
    template_name = "app/nao_conformidade_list.html"
    context_object_name = "nao_conformidades"

    def dispatch(self, request, *args, **kwargs):
        if not _obra_contexto(request):
            messages.error(request, "Selecione uma obra no menu antes de acessar nao conformidades.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

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

    def dispatch(self, request, *args, **kwargs):
        obra = _obra_contexto(request)
        if not obra:
            messages.error(request, "Selecione uma obra no menu antes de registrar nao conformidades.")
            return redirect("home")
        if obra_em_somente_leitura(obra):
            messages.error(request, descricao_restricao_obra(obra))
            return redirect("nao_conformidade_list")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = get_empresa_operacional(self.request)
        kwargs["obra_contexto"] = _obra_contexto(self.request)
        return kwargs

    def form_valid(self, form):
        obra = form.cleaned_data["obra"]
        if obra_em_somente_leitura(obra):
            form.add_error("obra", descricao_restricao_obra(obra))
            return self.form_invalid(form)
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
        for campo in (
            "evidencia_tratamento",
            "evidencia_tratamento_anexo",
            "evidencia_encerramento",
            "evidencia_encerramento_anexo",
            "eficacia_observacao",
        ):
            setattr(nc, campo, form.cleaned_data.get(campo))
        nc.save()
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

    def dispatch(self, request, *args, **kwargs):
        if not _obra_contexto(request):
            messages.error(request, "Selecione uma obra no menu antes de acessar nao conformidades.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        empresa = get_empresa_operacional(self.request)
        queryset = NaoConformidade.objects.select_related("obra", "plano_contas", "responsavel", "criado_por")
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        obra = _obra_contexto(self.request)
        if obra:
            queryset = queryset.filter(obra=obra)
        return queryset.prefetch_related("historico__usuario")

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if obra_em_somente_leitura(self.object.obra):
            messages.error(request, descricao_restricao_obra(self.object.obra))
            return redirect("nao_conformidade_detail", pk=self.object.pk)

        acao = request.POST.get("acao")
        observacao = request.POST.get("observacao", "")
        if acao in {"VERIFICACAO", "ENCERRAMENTO", "CANCELAMENTO"} and not can_manage_quality(request.user):
            messages.error(request, "Seu perfil nao possui permissao para executar esta etapa da qualidade.")
            return redirect("nao_conformidade_detail", pk=self.object.pk)

        if acao in {"VERIFICACAO", "ENCERRAMENTO"}:
            self.object.evidencia_tratamento = (request.POST.get("evidencia_tratamento") or self.object.evidencia_tratamento or "").strip()
            if request.FILES.get("evidencia_tratamento_anexo"):
                self.object.evidencia_tratamento_anexo = request.FILES["evidencia_tratamento_anexo"]
            if not self.object.evidencia_tratamento or not self.object.evidencia_tratamento_anexo:
                messages.error(request, "A comprovacao de tratamento exige descricao e anexo.")
                return redirect("nao_conformidade_detail", pk=self.object.pk)

        if acao == "ENCERRAMENTO":
            self.object.evidencia_encerramento = (request.POST.get("evidencia_encerramento") or self.object.evidencia_encerramento or "").strip()
            if request.FILES.get("evidencia_encerramento_anexo"):
                self.object.evidencia_encerramento_anexo = request.FILES["evidencia_encerramento_anexo"]
            if not self.object.evidencia_encerramento or not self.object.evidencia_encerramento_anexo:
                messages.error(request, "A comprovacao de encerramento exige descricao e anexo.")
                return redirect("nao_conformidade_detail", pk=self.object.pk)

        self.object.save()
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

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if obra_em_somente_leitura(self.object.obra):
            messages.error(request, descricao_restricao_obra(self.object.obra))
            return redirect("nao_conformidade_detail", pk=self.object.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        messages.success(self.request, "Nao conformidade atualizada com sucesso.")
        return reverse_lazy("nao_conformidade_detail", kwargs={"pk": self.object.pk})


def _nao_conformidades_queryset(request):
    empresa = get_empresa_operacional(request)
    queryset = NaoConformidade.objects.select_related("obra", "plano_contas", "responsavel", "criado_por")
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    obra = _obra_contexto(request)
    if obra:
        queryset = queryset.filter(obra=obra)
    termo = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if termo:
        queryset = queryset.filter(descricao__icontains=termo)
    if status:
        queryset = queryset.filter(status=status)
    return queryset.order_by("-criado_em")


def nao_conformidade_export_view(request):
    queryset = _nao_conformidades_queryset(request)
    linhas = [
        {
            "ID": nc.numero or f"NC-{nc.pk}",
            "Obra": nc.obra.codigo,
            "Centro de Custo": f"{nc.plano_contas.codigo} - {nc.plano_contas.descricao}" if nc.plano_contas else "-",
            "Descricao": nc.descricao,
            "Status": nc.get_status_display(),
            "Responsavel": nc.responsavel,
            "Data de Abertura": nc.data_abertura.strftime("%d/%m/%Y"),
        }
        for nc in queryset
    ]
    return _exportar_excel_response("nao_conformidades.xlsx", "Nao Conformidades", linhas)


def nao_conformidade_pdf_view(request):
    queryset = _nao_conformidades_queryset(request)
    resumo = {"Quantidade de Registros": queryset.count(), "Emitido em": _datahora_local(timezone.now()).strftime("%d/%m/%Y %H:%M")}
    extras = [
        {
            "ID": nc.numero or f"NC-{nc.pk}",
            "Obra": nc.obra.codigo,
            "Descricao": nc.descricao,
            "Status": nc.get_status_display(),
            "Data": nc.data_abertura.strftime("%d/%m/%Y"),
        }
        for nc in queryset
    ]
    return _pdf_relatorio_probatorio_response(
        "nao_conformidades_lista.pdf",
        "Lista de Nao Conformidades",
        resumo,
        [],
        extras,
        extras_titulo="Lista de Nao Conformidades",
        extras_colunas=[("ID", 50), ("Obra", 55), ("Descricao", 230), ("Status", 85), ("Data", 75)],
        incluir_historico=False,
    )


def _dados_probatorio_nc(nc):
    resumo = {
        "Identificador": nc.numero or f"NC-{nc.pk:06d}",
        "Obra": f"{nc.obra.codigo} - {nc.obra.nome}",
        "Centro de Custo": f"{nc.plano_contas.codigo} - {nc.plano_contas.descricao}" if nc.plano_contas else "-",
        "Status": nc.get_status_display(),
        "Responsavel": str(nc.responsavel),
        "Criado por": str(nc.criado_por),
        "Data de Abertura": nc.data_abertura.strftime("%d/%m/%Y"),
        "Descricao": nc.descricao,
        "Causa": nc.causa or "-",
        "Acao Corretiva": nc.acao_corretiva or "-",
        "Evidencia de Tratamento": nc.evidencia_tratamento or "-",
        "Anexo de Tratamento": getattr(nc.evidencia_tratamento_anexo, "name", "-") or "-",
        "Evidencia de Encerramento": nc.evidencia_encerramento or "-",
        "Anexo de Encerramento": getattr(nc.evidencia_encerramento_anexo, "name", "-") or "-",
        "Eficacia": nc.eficacia_observacao or "-",
    }
    historico = [
        {
            "Data": item.timestamp.strftime("%d/%m/%Y %H:%M"),
            "Acao": item.get_acao_display(),
            "Usuario": str(item.usuario or "-"),
            "Descricao": item.observacao or "-",
        }
        for item in nc.historico.all()
    ]
    extras = [
        {
            "Campo": "Data de Encerramento",
            "Valor": nc.data_encerramento.strftime("%d/%m/%Y") if nc.data_encerramento else "-",
        },
        {
            "Campo": "Eficacia verificada por",
            "Valor": str(nc.eficacia_verificada_por) if nc.eficacia_verificada_por else "-",
        },
        {
            "Campo": "Data da verificacao de eficacia",
            "Valor": nc.eficacia_verificada_em.strftime("%d/%m/%Y %H:%M") if nc.eficacia_verificada_em else "-",
        },
    ]
    return resumo, historico, extras


def nao_conformidade_aprovacao_pdf_view(request, pk):
    nc = get_object_or_404(_nao_conformidades_queryset(request).prefetch_related("historico__usuario"), pk=pk)
    resumo, historico, extras = _dados_probatorio_nc(nc)
    return _pdf_relatorio_probatorio_response(
        f"nao_conformidade_{(nc.numero or f'NC-{nc.pk}').replace('/', '_')}.pdf",
        f"Relatorio Probatorio de Nao Conformidade - {nc.numero or f'NC-{nc.pk}'}",
        resumo,
        historico,
        extras,
        extras_titulo="Dados Complementares",
        extras_colunas=[("Campo", 180), ("Valor", 315)],
    )


def nao_conformidade_aprovacao_excel_view(request, pk):
    nc = get_object_or_404(_nao_conformidades_queryset(request).prefetch_related("historico__usuario"), pk=pk)
    resumo, historico, extras = _dados_probatorio_nc(nc)
    return _exportar_relatorio_probatorio_excel_response(
        f"nao_conformidade_{(nc.numero or f'NC-{nc.pk}').replace('/', '_')}.xlsx",
        "Resumo",
        resumo,
        historico,
        extras_sheet_name="Complemento",
        extras_linhas=extras,
    )
