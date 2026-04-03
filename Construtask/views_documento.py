# =============================================================================
# FASE 2 - ISO 7.5 CONTROLE DOCUMENTAL - Views
# =============================================================================

from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import DocumentoForm, DocumentoRevisaoForm, DocumentoWorkflowForm
from .models import Documento, DocumentoRevisao
from .permissions import get_empresa_operacional, get_obra_do_contexto


def _get_empresa_do_request(request):
    return get_empresa_operacional(request)


def _filtrar_por_empresa(queryset, empresa):
    """Filtra queryset pela empresa."""
    if empresa:
        return queryset.filter(empresa=empresa)
    return queryset


class DocumentoListView(ListView):
    """Lista de documentos controlados."""
    model = Documento
    template_name = "app/documento_list.html"
    context_object_name = "documentos"

    def get_queryset(self):
        empresa = _get_empresa_do_request(self.request)
        queryset = Documento.objects.select_related("empresa", "obra", "plano_contas", "criado_por")
        queryset = _filtrar_por_empresa(queryset, empresa)
        
        # Filtros
        termo = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        tipo = self.request.GET.get("tipo", "").strip()
        obra_id = self.request.GET.get("obra", "").strip()
        
        if termo:
            queryset = queryset.filter(
                codigo_documento__icontains=termo
            ) | queryset.filter(titulo__icontains=termo)
        
        if status:
            queryset = queryset.filter(status=status)
        if tipo:
            queryset = queryset.filter(tipo_documento=tipo)
        if obra_id:
            queryset = queryset.filter(obra_id=obra_id)
            
        return queryset.order_by("-criado_em")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busca"] = self.request.GET.get("q", "")
        context["status_filtro"] = self.request.GET.get("status", "")
        context["tipo_filtro"] = self.request.GET.get("tipo", "")
        context["obra_filtro"] = self.request.GET.get("obra", "")
        context["status_choices"] = Documento.STATUS_CHOICES
        context["tipo_choices"] = Documento.TIPO_CHOICES
        return context


class DocumentoCreateView(CreateView):
    """Criar novo documento controlado."""
    model = Documento
    form_class = DocumentoForm
    template_name = "app/documento_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = _get_empresa_do_request(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Novo Documento Controlado"
        context["voltar_url"] = reverse_lazy("documento_list")
        return context

    def form_valid(self, form):
        empresa = _get_empresa_do_request(self.request)
        if not empresa:
            messages.error(self.request, "Usuário não possui empresa vinculada.")
            return redirect("documento_list")
        
        documento = form.save(commit=False)
        documento.criado_por = self.request.user
        documento.empresa = empresa
        documento.save()
        
        messages.success(self.request, f"Documento '{documento.codigo_documento}' criado com sucesso!")
        return redirect("documento_detail", pk=documento.pk)

    def get_success_url(self):
        return reverse_lazy("documento_list")


class DocumentoDetailView(DetailView):
    """Detalhes do documento com histórico de revisões."""
    model = Documento
    template_name = "app/documento_detail.html"
    context_object_name = "documento"

    def get_queryset(self):
        empresa = _get_empresa_do_request(self.request)
        return Documento.objects.select_related(
            "empresa", "obra", "plano_contas", "criado_por"
        ).prefetch_related("revisoes", "revisoes__criado_por", "revisoes__revisor", "revisoes__aprobador")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        documento = self.object
        context["revisoes"] = documento.revisoes.all().order_by("-versao")
        context["versao_aprovada"] = documento.get_versao_aprovada()
        context["pode_revisar"] = documento.pode_revisar()
        context["pode_aprovar"] = documento.pode_aprovar()
        context["pode_tornar_obsoleto"] = documento.pode_tornar_obsoleto()
        context["revisao_form"] = kwargs.get("revisao_form") or DocumentoRevisaoForm()
        context["workflow_form"] = kwargs.get("workflow_form") or DocumentoWorkflowForm()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        
        # Upload de nova revisão
        if "upload_revisao" in request.POST:
            return self._upload_revisao(request)
        
        # Ações de workflow
        if "workflow_action" in request.POST:
            return self._workflow_action(request)
        
        return redirect("documento_detail", pk=self.object.pk)

    def _upload_revisao(self, request):
        """Upload de nova revisão do documento."""
        documento = self.object
        if not documento.pode_revisar():
            messages.error(request, "Este documento não pode receber novas revisões.")
            return redirect("documento_detail", pk=documento.pk)
        
        form = DocumentoRevisaoForm(request.POST, request.FILES)
        if form.is_valid():
            revisao = form.save(commit=False)
            revisao.documento = documento
            revisao.criado_por = request.user
            revisao.status = "ELABORACAO"
            
            # Calcular checksum se arquivo enviado
            if revisao.arquivo:
                import hashlib
                sha256 = hashlib.sha256()
                for chunk in revisao.arquivo.chunks():
                    sha256.update(chunk)
                revisao.checksum = sha256.hexdigest()
            
            revisao.save()
            
            # Atualizar versão do documento
            documento.versao_atual = revisao.versao
            documento.save()
            
            messages.success(request, f"Revisão {revisao.versao} enviada com sucesso!")
        
        return redirect("documento_detail", pk=documento.pk)

    def _workflow_action(self, request):
        """Executa ações de workflow."""
        documento = self.object
        acao = request.POST.get("acao")
        parecer = request.POST.get("parecer", "")
        
        if acao == "ENVIAR_REVISAO":
            if documento.status == "RASCUNHO":
                documento.status = "EM_REVISAO"
                documento.save()
                messages.success(request, "Documento enviado para revisão!")
        
        elif acao == "APROVAR":
            revisao = documento.revisoes.filter(status="REVISAO").first()
            if revisao:
                revisao.status = "APROVADO"
                revisao.aprobador = request.user
                revisao.data_aprovacao = datetime.now()
                revisao.parecer = parecer
                # Copia arquivo como arquivo aprovado (imutável)
                if revisao.arquivo:
                    revisao.arquivo_aprovado = revisao.arquivo
                revisao.save()
                
                documento.status = "APROVADO"
                documento.save()
                messages.success(request, "Documento aprovado com sucesso!")
        
        elif acao == "REJEITAR":
            if documento.status == "EM_REVISAO":
                documento.status = "RASCUNHO"
                documento.save()
                messages.warning(request, "Documento retornou para rascunho.")
        
        elif acao == "TORNAR_OBSOLETO":
            if documento.pode_tornar_obsoleto():
                documento.status = "OBSOLETO"
                documento.save()
                messages.warning(request, "Documento marcado como obsoleto.")
        
        return redirect("documento_detail", pk=documento.pk)


class DocumentoUpdateView(UpdateView):
    """Editar documento controlado."""
    model = Documento
    form_class = DocumentoForm
    template_name = "app/documento_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = _get_empresa_do_request(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = f"Editar Documento {self.object.codigo_documento}"
        context["voltar_url"] = reverse_lazy("documento_detail", kwargs={"pk": self.object.pk})
        return context

    def form_valid(self, form):
        messages.success(self.request, "Documento atualizado com sucesso!")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("documento_detail", kwargs={"pk": self.object.pk})


@login_required
def documento_delete_view(request, pk):
    """Excluir documento."""
    empresa = _get_empresa_do_request(request)
    documento = get_object_or_404(Documento, pk=pk)
    
    if empresa and documento.empresa_id != empresa.id:
        raise Http404("Documento não encontrado.")
    
    if request.method == "POST":
        codigo = documento.codigo_documento
        documento.delete()
        messages.success(request, f"Documento '{codigo}' excluído com sucesso!")
        return redirect("documento_list")
    
    return redirect("documento_list")


def documento_download_view(request, pk, revisao_pk=None):
    """Download de arquivo do documento."""
    empresa = _get_empresa_do_request(request)
    documento = get_object_or_404(Documento, pk=pk)
    
    if empresa and documento.empresa_id != empresa.id:
        raise Http404("Documento não encontrado.")
    
    if revisao_pk:
        revisao = get_object_or_404(DocumentoRevisao, pk=revisao_pk, documento=documento)
        arquivo = revisao.arquivo_aprovado or revisao.arquivo
    else:
        revisao_aprovada = documento.get_versao_aprovada()
        if revisao_aprovada:
            arquivo = revisao_aprovada.arquivo_aprovado or revisao_aprovada.arquivo
        else:
            raise Http404("Nenhum arquivo disponível para download.")
    
    from django.http import FileResponse
    response = FileResponse(arquivo)
    response["Content-Disposition"] = f'attachment; filename="{arquivo.name}"'
    return response
