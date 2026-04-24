# =============================================================================
# FASE 2 - ISO 7.5 CONTROLE DOCUMENTAL - Views
# =============================================================================

import hashlib

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import DocumentoForm, DocumentoRevisaoForm, DocumentoWorkflowForm
from .models import AuditEvent, Documento, DocumentoRevisao, Obra
from .pagination import DefaultPaginationMixin
from .permissions import (
    descricao_restricao_obra,
    filtrar_por_empresa as _filtrar_por_empresa,
    get_empresa_operacional as _get_empresa_do_request,
    get_obra_do_contexto as _get_obra_contexto,
    obra_em_somente_leitura,
)
from .services_aprovacao import can_approve_document, can_submit_for_approval


def _registrar_evento_documento(request, documento, acao, antes=None, depois=None):
    AuditEvent.objects.create(
        empresa=documento.empresa,
        usuario=getattr(request, "user", None) if getattr(request, "user", None) and request.user.is_authenticated else None,
        acao=acao,
        entidade_app="Construtask.Documento",
        entidade_label=f"Documento {documento.codigo_documento}",
        objeto_id=documento.pk,
        antes=antes,
        depois=depois,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
    )


def _snapshot_documento(documento):
    return {
        "codigo_documento": documento.codigo_documento,
        "titulo": documento.titulo,
        "status": documento.status,
        "versao_atual": documento.versao_atual,
    }


def _snapshot_revisao(revisao):
    return {
        "versao": revisao.versao,
        "status": revisao.status,
        "checksum": revisao.checksum,
        "parecer": revisao.parecer,
        "aprovador": getattr(revisao.aprobador, "username", None),
        "revisor": getattr(revisao.revisor, "username", None),
    }


def _calcular_checksum_arquivo(arquivo):
    sha256 = hashlib.sha256()
    for chunk in arquivo.chunks():
        sha256.update(chunk)
    if hasattr(arquivo, "seek"):
        arquivo.seek(0)
    return sha256.hexdigest()


class DocumentoListView(LoginRequiredMixin, DefaultPaginationMixin, ListView):
    model = Documento
    template_name = "app/documento_list.html"
    context_object_name = "documentos"

    def dispatch(self, request, *args, **kwargs):
        if not _get_obra_contexto(request):
            messages.error(request, "Selecione uma obra no menu antes de acessar documentos.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        empresa = _get_empresa_do_request(self.request)
        obra_contexto = _get_obra_contexto(self.request)
        queryset = Documento.objects.select_related("empresa", "obra", "plano_contas", "criado_por")
        queryset = _filtrar_por_empresa(queryset, empresa)
        queryset = queryset.filter(obra=obra_contexto)

        termo = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        tipo = self.request.GET.get("tipo", "").strip()

        if termo:
            queryset = queryset.filter(codigo_documento__icontains=termo) | queryset.filter(titulo__icontains=termo)
        if status:
            queryset = queryset.filter(status=status)
        if tipo:
            queryset = queryset.filter(tipo_documento=tipo)

        return queryset.order_by("-criado_em")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        empresa = _get_empresa_do_request(self.request)
        obra_contexto = _get_obra_contexto(self.request)
        context["busca"] = self.request.GET.get("q", "")
        context["status_filtro"] = self.request.GET.get("status", "")
        context["tipo_filtro"] = self.request.GET.get("tipo", "")
        context["obra_filtro"] = str(obra_contexto.pk) if obra_contexto else ""
        context["status_choices"] = Documento.STATUS_CHOICES
        context["tipo_choices"] = Documento.TIPO_CHOICES
        context["obras"] = Obra.objects.filter(empresa=empresa, pk=getattr(obra_contexto, "pk", None)).order_by("codigo") if empresa and obra_contexto else Obra.objects.none()
        return context


class DocumentoCreateView(LoginRequiredMixin, CreateView):
    model = Documento
    form_class = DocumentoForm
    template_name = "app/documento_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = _get_empresa_do_request(self.request)
        kwargs["obra_contexto"] = _get_obra_contexto(self.request)
        return kwargs

    def dispatch(self, request, *args, **kwargs):
        obra = _get_obra_contexto(request)
        if not obra:
            messages.error(request, "Selecione uma obra no menu antes de criar documentos.")
            return redirect("home")
        if obra_em_somente_leitura(obra):
            messages.error(request, descricao_restricao_obra(obra))
            return redirect("documento_list")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Novo Documento Controlado"
        context["voltar_url"] = reverse_lazy("documento_list")
        return context

    def form_valid(self, form):
        empresa = _get_empresa_do_request(self.request)
        obra_contexto = _get_obra_contexto(self.request)
        if not empresa:
            messages.error(self.request, "Usuario nao possui empresa vinculada.")
            return redirect("documento_list")
        if not obra_contexto:
            form.add_error("obra", "Selecione uma obra no menu antes de criar documentos.")
            return self.form_invalid(form)
        if obra_em_somente_leitura(obra_contexto):
            form.add_error("obra", descricao_restricao_obra(obra_contexto))
            return self.form_invalid(form)

        with transaction.atomic():
            documento = form.save(commit=False)
            documento.criado_por = self.request.user
            documento.empresa = empresa
            documento.obra = obra_contexto
            documento.versao_atual = 1
            documento.save()

            arquivo = form.cleaned_data["arquivo_inicial"]
            revisao = DocumentoRevisao.objects.create(
                documento=documento,
                versao=1,
                arquivo=arquivo,
                checksum=_calcular_checksum_arquivo(arquivo),
                status="ELABORACAO",
                criado_por=self.request.user,
            )
            documento.versao_atual = revisao.versao
            documento.save(update_fields=["versao_atual", "atualizado_em"])

        _registrar_evento_documento(self.request, documento, "CREATE", depois=_snapshot_documento(documento))
        messages.success(self.request, f"Documento '{documento.codigo_documento}' criado com sucesso.")
        return redirect("documento_detail", pk=documento.pk)

    def get_success_url(self):
        return reverse_lazy("documento_list")


class DocumentoDetailView(LoginRequiredMixin, DetailView):
    model = Documento
    template_name = "app/documento_detail.html"
    context_object_name = "documento"

    def dispatch(self, request, *args, **kwargs):
        if not _get_obra_contexto(request):
            messages.error(request, "Selecione uma obra no menu antes de acessar documentos.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        empresa = _get_empresa_do_request(self.request)
        obra_contexto = _get_obra_contexto(self.request)
        queryset = Documento.objects.select_related(
            "empresa", "obra", "plano_contas", "criado_por"
        ).prefetch_related("revisoes", "revisoes__criado_por", "revisoes__revisor", "revisoes__aprobador")
        queryset = _filtrar_por_empresa(queryset, empresa)
        return queryset.filter(obra=obra_contexto)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        documento = self.object
        ultima_revisao = documento.ultima_revisao

        context["revisoes"] = documento.revisoes.all().order_by("-versao")
        context["ultima_revisao"] = ultima_revisao
        context["versao_aprovada"] = documento.get_versao_aprovada()
        context["pode_revisar"] = documento.pode_revisar() and not obra_em_somente_leitura(documento.obra)
        context["pode_enviar_revisao"] = (
            documento.status == "RASCUNHO"
            and ultima_revisao is not None
            and ultima_revisao.status == "ELABORACAO"
            and can_submit_for_approval(self.request.user)
            and not obra_em_somente_leitura(documento.obra)
        )
        context["pode_aprovar"] = (
            documento.status == "EM_REVISAO"
            and ultima_revisao is not None
            and ultima_revisao.status == "REVISAO"
            and can_approve_document(self.request.user)
        )
        context["pode_tornar_obsoleto"] = documento.status == "APROVADO" and can_approve_document(self.request.user)
        context["revisao_form"] = kwargs.get("revisao_form") or DocumentoRevisaoForm()
        context["workflow_form"] = kwargs.get("workflow_form") or DocumentoWorkflowForm()
        context["workflow_events"] = AuditEvent.objects.filter(
            entidade_app="Construtask.Documento",
            objeto_id=documento.pk,
        ).order_by("-timestamp")[:20]
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        if "upload_revisao" in request.POST:
            return self._upload_revisao(request)
        if "workflow_action" in request.POST:
            return self._workflow_action(request)
        return redirect("documento_detail", pk=self.object.pk)

    def _upload_revisao(self, request):
        documento = self.object
        if obra_em_somente_leitura(documento.obra):
            messages.error(request, descricao_restricao_obra(documento.obra))
            return redirect("documento_detail", pk=documento.pk)
        if not documento.pode_revisar():
            messages.error(request, "Este documento nao pode receber novas revisoes neste estagio.")
            return redirect("documento_detail", pk=documento.pk)

        form = DocumentoRevisaoForm(request.POST, request.FILES)
        if not form.is_valid():
            for campo, erros in form.errors.items():
                for erro in erros:
                    messages.error(request, f"{campo}: {erro}")
            return redirect("documento_detail", pk=documento.pk)

        revisao = form.save(commit=False)
        revisao.documento = documento
        revisao.criado_por = request.user
        revisao.status = "ELABORACAO"

        if revisao.arquivo:
            revisao.checksum = _calcular_checksum_arquivo(revisao.arquivo)

        revisao.save()
        documento.versao_atual = revisao.versao
        documento.save(update_fields=["versao_atual", "atualizado_em"])

        _registrar_evento_documento(
            request,
            documento,
            "UPLOAD",
            depois={"revisao": _snapshot_revisao(revisao), **_snapshot_documento(documento)},
        )
        messages.success(request, f"Revisao {revisao.versao} enviada com sucesso.")
        return redirect("documento_detail", pk=documento.pk)

    def _workflow_action(self, request):
        documento = self.object
        if obra_em_somente_leitura(documento.obra):
            messages.error(request, descricao_restricao_obra(documento.obra))
            return redirect("documento_detail", pk=documento.pk)

        revisao = documento.ultima_revisao
        acao = request.POST.get("acao")
        parecer = (request.POST.get("parecer") or "").strip()

        if not revisao:
            messages.error(request, "Crie uma revisao antes de executar o workflow do documento.")
            return redirect("documento_detail", pk=documento.pk)

        antes_documento = _snapshot_documento(documento)
        antes_revisao = _snapshot_revisao(revisao)

        if acao == "ENVIAR_REVISAO":
            if not can_submit_for_approval(request.user):
                messages.error(request, "Seu perfil nao pode submeter documentos para validacao.")
                return redirect("documento_detail", pk=documento.pk)
            if documento.status != "RASCUNHO" or revisao.status != "ELABORACAO":
                messages.error(request, "A revisao precisa estar em elaboracao para ser enviada.")
                return redirect("documento_detail", pk=documento.pk)

            revisao.status = "REVISAO"
            revisao.revisor = request.user
            revisao.data_revisao = timezone.now()
            if parecer:
                revisao.parecer = parecer
            revisao.save(update_fields=["status", "revisor", "data_revisao", "parecer"])
            documento.status = "EM_REVISAO"
            documento.save(update_fields=["status", "atualizado_em"])
            _registrar_evento_documento(
                request,
                documento,
                "UPDATE",
                antes={"documento": antes_documento, "revisao": antes_revisao},
                depois={"documento": _snapshot_documento(documento), "revisao": _snapshot_revisao(revisao)},
            )
            messages.success(request, "Documento enviado para validacao.")
            return redirect("documento_detail", pk=documento.pk)

        if acao == "APROVAR":
            if not can_approve_document(request.user):
                messages.error(request, "Seu perfil nao pode aprovar documentos.")
                return redirect("documento_detail", pk=documento.pk)
            if documento.status != "EM_REVISAO" or revisao.status != "REVISAO":
                messages.error(request, "A revisao precisa estar em validacao para ser aprovada.")
                return redirect("documento_detail", pk=documento.pk)

            revisao.status = "APROVADO"
            revisao.aprobador = request.user
            revisao.data_aprovacao = timezone.now()
            revisao.parecer = parecer
            if revisao.arquivo:
                revisao.arquivo_aprovado = revisao.arquivo
            revisao.save()
            documento.status = "APROVADO"
            documento.save(update_fields=["status", "atualizado_em"])
            _registrar_evento_documento(
                request,
                documento,
                "APPROVE",
                antes={"documento": antes_documento, "revisao": antes_revisao},
                depois={"documento": _snapshot_documento(documento), "revisao": _snapshot_revisao(revisao)},
            )
            messages.success(request, "Documento aprovado com sucesso.")
            return redirect("documento_detail", pk=documento.pk)

        if acao in {"REJEITAR", "DEVOLVER_AJUSTE"}:
            if not can_approve_document(request.user):
                messages.error(request, "Seu perfil nao pode devolver documentos para ajuste.")
                return redirect("documento_detail", pk=documento.pk)
            if documento.status != "EM_REVISAO" or revisao.status != "REVISAO":
                messages.error(request, "Somente revisoes em validacao podem voltar para ajuste.")
                return redirect("documento_detail", pk=documento.pk)
            if not parecer:
                messages.error(request, "Informe um parecer para devolver o documento para ajuste.")
                return redirect("documento_detail", pk=documento.pk)

            revisao.status = "ELABORACAO"
            revisao.revisor = request.user
            revisao.data_revisao = timezone.now()
            revisao.parecer = parecer
            revisao.save(update_fields=["status", "revisor", "data_revisao", "parecer"])
            documento.status = "RASCUNHO"
            documento.save(update_fields=["status", "atualizado_em"])
            _registrar_evento_documento(
                request,
                documento,
                "REJECT",
                antes={"documento": antes_documento, "revisao": antes_revisao},
                depois={"documento": _snapshot_documento(documento), "revisao": _snapshot_revisao(revisao)},
            )
            messages.warning(request, "Documento devolvido para ajuste.")
            return redirect("documento_detail", pk=documento.pk)

        if acao == "TORNAR_OBSOLETO":
            if not can_approve_document(request.user):
                messages.error(request, "Seu perfil nao pode tornar documentos obsoletos.")
                return redirect("documento_detail", pk=documento.pk)
            if not documento.pode_tornar_obsoleto():
                messages.error(request, "Este documento nao pode ser tornado obsoleto neste estagio.")
                return redirect("documento_detail", pk=documento.pk)

            documento.status = "OBSOLETO"
            documento.save(update_fields=["status", "atualizado_em"])
            _registrar_evento_documento(
                request,
                documento,
                "UPDATE",
                antes={"documento": antes_documento},
                depois={"documento": _snapshot_documento(documento)},
            )
            messages.warning(request, "Documento marcado como obsoleto.")
            return redirect("documento_detail", pk=documento.pk)

        messages.error(request, "Acao de workflow invalida.")
        return redirect("documento_detail", pk=documento.pk)


class DocumentoUpdateView(LoginRequiredMixin, UpdateView):
    model = Documento
    form_class = DocumentoForm
    template_name = "app/documento_form.html"

    def get_queryset(self):
        empresa = _get_empresa_do_request(self.request)
        obra_contexto = _get_obra_contexto(self.request)
        queryset = Documento.objects.select_related("empresa", "obra", "plano_contas", "criado_por")
        queryset = _filtrar_por_empresa(queryset, empresa)
        return queryset.filter(obra=obra_contexto)

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if obra_em_somente_leitura(self.object.obra):
            messages.error(request, descricao_restricao_obra(self.object.obra))
            return redirect("documento_detail", pk=self.object.pk)
        if self.object.status != "RASCUNHO":
            messages.error(request, "Somente documentos em rascunho podem ser editados.")
            return redirect("documento_detail", pk=self.object.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = _get_empresa_do_request(self.request)
        kwargs["obra_contexto"] = _get_obra_contexto(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = f"Editar Documento {self.object.codigo_documento}"
        context["voltar_url"] = reverse_lazy("documento_detail", kwargs={"pk": self.object.pk})
        return context

    def form_valid(self, form):
        antes = _snapshot_documento(self.object)
        self.object = form.save(commit=False)
        self.object.obra = _get_obra_contexto(self.request)
        self.object.save()
        _registrar_evento_documento(self.request, self.object, "UPDATE", antes=antes, depois=_snapshot_documento(self.object))
        messages.success(self.request, "Documento atualizado com sucesso.")
        return redirect("documento_detail", pk=self.object.pk)

    def get_success_url(self):
        return reverse_lazy("documento_detail", kwargs={"pk": self.object.pk})


@login_required
def documento_delete_view(request, pk):
    empresa = _get_empresa_do_request(request)
    obra_contexto = _get_obra_contexto(request)
    documento = get_object_or_404(Documento, pk=pk)

    if empresa and documento.empresa_id != empresa.id:
        raise Http404("Documento nao encontrado.")
    if obra_contexto and documento.obra_id != obra_contexto.id:
        raise Http404("Documento nao encontrado.")
    if obra_em_somente_leitura(documento.obra):
        messages.error(request, descricao_restricao_obra(documento.obra))
        return redirect("documento_detail", pk=documento.pk)

    if request.method == "POST":
        codigo = documento.codigo_documento
        documento.delete()
        messages.success(request, f"Documento '{codigo}' excluido com sucesso.")
        return redirect("documento_list")

    return redirect("documento_list")


@login_required
def documento_download_view(request, pk, revisao_pk=None):
    empresa = _get_empresa_do_request(request)
    obra_contexto = _get_obra_contexto(request)
    documento = get_object_or_404(Documento, pk=pk)

    if empresa and documento.empresa_id != empresa.id:
        raise Http404("Documento nao encontrado.")
    if obra_contexto and documento.obra_id != obra_contexto.id:
        raise Http404("Documento nao encontrado.")

    if revisao_pk:
        revisao = get_object_or_404(DocumentoRevisao, pk=revisao_pk, documento=documento)
        arquivo = revisao.arquivo_aprovado or revisao.arquivo
    else:
        revisao_aprovada = documento.get_versao_aprovada()
        if revisao_aprovada:
            arquivo = revisao_aprovada.arquivo_aprovado or revisao_aprovada.arquivo
        else:
            raise Http404("Nenhum arquivo disponivel para download.")

    response = FileResponse(arquivo)
    response["Content-Disposition"] = f'attachment; filename="{arquivo.name}"'
    return response
