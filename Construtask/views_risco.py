"""
Views para Gestão de Riscos - ISO 6.1 / PMBOK
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .models_risco import Risco, RiscoHistorico
from .pagination import DefaultPaginationMixin
from .permissions import (
    descricao_restricao_obra,
    filtrar_por_empresa as _filtrar_por_empresa,
    get_empresa_operacional as _get_empresa_do_request,
    get_obra_do_contexto as _get_obra_contexto,
    obra_em_somente_leitura,
)


def _registrar_historico(risco, usuario, acao, dados_anteriores=None, dados_novos=None, observacao=""):
    """Registra histórico de alteração do risco."""
    RiscoHistorico.objects.create(
        risco=risco,
        usuario=usuario,
        acao=acao,
        dados_anteriores=dados_anteriores,
        dados_novos=dados_novos,
        observacao=observacao
    )


class RiscoListView(DefaultPaginationMixin, ListView):
    """Lista de riscos por obra."""
    model = Risco
    template_name = "app/risco_list.html"
    context_object_name = "riscos"

    def dispatch(self, request, *args, **kwargs):
        if not _get_obra_contexto(request):
            messages.error(request, "Selecione uma obra no menu antes de acessar riscos.")
            return redirect("obra_list")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        empresa = _get_empresa_do_request(self.request)
        obra = _get_obra_contexto(self.request)
        
        queryset = Risco.objects.select_related(
            "empresa", "obra", "plano_contas", "responsavel", "criado_por"
        )
        
        queryset = _filtrar_por_empresa(queryset, empresa)
        
        if obra:
            queryset = queryset.filter(obra=obra)
        
        # Filtros
        termo = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        categoria = self.request.GET.get("categoria", "").strip()
        nivel = self.request.GET.get("nivel", "").strip()

        if termo:
            queryset = queryset.filter(
                Q(titulo__icontains=termo)
                | Q(descricao__icontains=termo)
                | Q(processo__icontains=termo)
                | Q(plano_contas__codigo__icontains=termo)
                | Q(plano_contas__descricao__icontains=termo)
            )
        if status:
            queryset = queryset.filter(status=status)
        if categoria:
            queryset = queryset.filter(categoria=categoria)
        if nivel:
            if nivel == "BAIXO":
                queryset = queryset.filter(nivel__lte=4)
            elif nivel == "MEDIO":
                queryset = queryset.filter(nivel__gte=5, nivel__lte=9)
            elif nivel == "ALTO":
                queryset = queryset.filter(nivel__gte=10, nivel__lte=15)
            elif nivel == "CRITICO":
                queryset = queryset.filter(nivel__gt=15)
            
        return queryset.order_by("-nivel", "-criado_em")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = Risco.STATUS_CHOICES
        context["categoria_choices"] = Risco.CATEGORIA_CHOICES
        context["busca"] = self.request.GET.get("q", "")
        context["status_filtro"] = self.request.GET.get("status", "")
        context["categoria_filtro"] = self.request.GET.get("categoria", "")
        context["nivel_filtro"] = self.request.GET.get("nivel", "")
        
        # Estatísticas
        empresa = _get_empresa_do_request(self.request)
        obra = _get_obra_contexto(self.request)
        queryset = Risco.objects.all()
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        if obra:
            queryset = queryset.filter(obra=obra)
        
        context["total_riscos"] = queryset.count()
        context["riscos_ativos"] = queryset.exclude(status__in=["FECHADO", "CANCELADO"]).count()
        context["riscos_criticos"] = queryset.filter(nivel__gt=15).count()
        context["riscos_fechados"] = queryset.filter(status="FECHADO").count()
        context["riscos_em_tratamento"] = queryset.filter(status="EM_TRATAMENTO").count()
        context["riscos_prazo"] = queryset.filter(categoria="PRAZO").count()

        return context


class RiscoCreateView(CreateView):
    """Criar novo risco."""
    model = Risco
    template_name = "app/risco_form.html"
    fields = [
        "obra", "plano_contas", "processo",
        "categoria", "titulo", "descricao", "causa",
        "probabilidade", "impacto",
        "plano_resposta", "responsavel", "data_meta_tratamento",
        "observacoes"
    ]

    def dispatch(self, request, *args, **kwargs):
        obra = _get_obra_contexto(request)
        if not obra:
            messages.error(request, "Selecione uma obra no menu antes de criar riscos.")
            return redirect("obra_list")
        if obra_em_somente_leitura(obra):
            messages.error(request, descricao_restricao_obra(obra))
            return redirect("risco_list")
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        empresa = _get_empresa_do_request(self.request)
        obra = _get_obra_contexto(self.request)
        
        # Filtrar obras pela empresa
        if empresa:
            from .models import Obra
            form.fields["obra"].queryset = Obra.objects.filter(empresa=empresa)
            if obra and obra.empresa_id == empresa.id:
                form.fields["obra"].initial = obra
        
        # Filtrar plano de contas nível 5
        if obra:
            from .models import PlanoContas
            form.fields["plano_contas"].queryset = PlanoContas.objects.filter(
                obra=obra, level=5
            )
        
        # Filtrar usuários da empresa
        if empresa:
            from django.contrib.auth.models import User
            from .models import UsuarioEmpresa
            usuarios_ids = UsuarioEmpresa.objects.filter(
                empresa=empresa
            ).values_list("usuario_id", flat=True)
            form.fields["responsavel"].queryset = User.objects.filter(
                id__in=usuarios_ids
            )
        
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Novo Risco"
        context["voltar_url"] = reverse_lazy("risco_list")
        return context

    def form_valid(self, form):
        empresa = _get_empresa_do_request(self.request)
        if not empresa:
            messages.error(self.request, "Usuário não possui empresa vinculada.")
            return redirect("risco_list")
        
        risco = form.save(commit=False)
        if obra_em_somente_leitura(risco.obra):
            messages.error(self.request, descricao_restricao_obra(risco.obra))
            return self.form_invalid(form)
        risco.empresa = empresa
        risco.criado_por = self.request.user
        risco.save()
        
        # Registrar histórico
        _registrar_historico(
            risco=risco,
            usuario=self.request.user,
            acao="CRIACAO",
            dados_novos={
                "titulo": risco.titulo,
                "categoria": risco.categoria,
                "probabilidade": risco.probabilidade,
                "impacto": risco.impacto,
                "nivel": risco.nivel
            }
        )
        
        messages.success(self.request, f"Risco '{risco.titulo}' criado com sucesso!")
        return redirect("risco_detail", pk=risco.pk)

    def get_success_url(self):
        return reverse_lazy("risco_list")


class RiscoDetailView(DetailView):
    """Detalhes do risco com histórico."""
    model = Risco
    template_name = "app/risco_detail.html"
    context_object_name = "risco"

    def dispatch(self, request, *args, **kwargs):
        if not _get_obra_contexto(request):
            messages.error(request, "Selecione uma obra no menu antes de acessar riscos.")
            return redirect("obra_list")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        empresa = _get_empresa_do_request(self.request)
        queryset = Risco.objects.select_related(
            "empresa", "obra", "plano_contas", "responsavel", "criado_por"
        )
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        risco = self.object
        context["historico"] = risco.historico.all()[:20]
        context["pode_editar"] = risco.pode_editar()
        context["pode_tratar"] = risco.pode_tratar()
        context["pode_fechar"] = risco.pode_fechar()
        
        # Matriz de risco
        context["matriz_risco"] = self._gerar_matriz()
        context["dias_para_meta"] = (
            (risco.data_meta_tratamento - timezone.now().date()).days
            if risco.data_meta_tratamento
            else None
        )

        return context

    def _gerar_matriz(self):
        """Gera matriz de risco 5x5."""
        matriz = []
        for impacto in range(5, 0, -1):
            linha = []
            for prob in range(1, 6):
                nivel = prob * impacto
                if nivel <= 4:
                    cor = "success"
                    texto = "Baixo"
                elif nivel <= 9:
                    cor = "warning"
                    texto = "Médio"
                elif nivel <= 15:
                    cor = "danger"
                    texto = "Alto"
                else:
                    cor = "dark"
                    texto = "Crítico"
                
                # Verificar se é o risco atual
                atual = (prob == self.object.probabilidade and 
                        impacto == self.object.impacto)
                
                linha.append({
                    "prob": prob,
                    "impacto": impacto,
                    "nivel": nivel,
                    "cor": cor,
                    "texto": texto,
                    "atual": atual
                })
            matriz.append(linha)
        return matriz

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if obra_em_somente_leitura(self.object.obra):
            messages.error(request, descricao_restricao_obra(self.object.obra))
            return redirect("risco_detail", pk=self.object.pk)
        
        acao = request.POST.get("acao")
        
        if acao == "iniciar_tratamento":
            return self._iniciar_tratamento(request)
        elif acao == "mitigar":
            return self._mitigar(request)
        elif acao == "fechar":
            return self._fechar(request)
        elif acao == "reabrir":
            return self._reabrir(request)
        elif acao == "cancelar":
            return self._cancelar(request)
        
        return redirect("risco_detail", pk=self.object.pk)

    def _iniciar_tratamento(self, request):
        """Inicia o tratamento do risco."""
        if not self.object.pode_tratar():
            messages.error(request, "Este risco não pode entrar em tratamento.")
            return redirect("risco_detail", pk=self.object.pk)
        
        dados_anteriores = {"status": self.object.status}
        
        self.object.status = "EM_TRATAMENTO"
        self.object.save()
        
        _registrar_historico(
            risco=self.object,
            usuario=request.user,
            acao="STATUS",
            dados_anteriores=dados_anteriores,
            dados_novos={"status": "EM_TRATAMENTO"},
            observacao="Risco entrou em tratamento"
        )
        
        messages.success(request, "Risco entrou em tratamento!")
        return redirect("risco_detail", pk=self.object.pk)

    def _mitigar(self, request):
        """Marca o risco como mitigado."""
        if self.object.status != "EM_TRATAMENTO":
            messages.error(request, "Apenas riscos em tratamento podem ser mitigados.")
            return redirect("risco_detail", pk=self.object.pk)
        
        dados_anteriores = {"status": self.object.status}
        
        self.object.status = "MITIGADO"
        self.object.save()
        
        _registrar_historico(
            risco=self.object,
            usuario=request.user,
            acao="STATUS",
            dados_anteriores=dados_anteriores,
            dados_novos={"status": "MITIGADO"},
            observacao="Risco foi mitigado"
        )
        
        messages.success(request, "Risco marcado como mitigado!")
        return redirect("risco_detail", pk=self.object.pk)

    def _fechar(self, request):
        """Fecha o risco."""
        if not self.object.pode_fechar():
            messages.error(request, "Este risco não pode ser fechado.")
            return redirect("risco_detail", pk=self.object.pk)
        
        dados_anteriores = {"status": self.object.status}
        
        self.object.status = "FECHADO"
        self.object.data_fechamento = timezone.now().date()
        self.object.save()
        
        _registrar_historico(
            risco=self.object,
            usuario=request.user,
            acao="FECHAMENTO",
            dados_anteriores=dados_anteriores,
            dados_novos={"status": "FECHADO", "data_fechamento": str(self.object.data_fechamento)},
            observacao="Risco fechado"
        )
        
        messages.success(request, "Risco fechado com sucesso!")
        return redirect("risco_detail", pk=self.object.pk)

    def _reabrir(self, request):
        """Reabre um risco fechado."""
        if self.object.status not in ["FECHADO", "CANCELADO"]:
            messages.error(request, "Apenas riscos fechados podem ser reabertos.")
            return redirect("risco_detail", pk=self.object.pk)
        
        dados_anteriores = {"status": self.object.status}
        
        self.object.status = "EM_ANALISE"
        self.object.data_fechamento = None
        self.object.save()
        
        _registrar_historico(
            risco=self.object,
            usuario=request.user,
            acao="REABERTURA",
            dados_anteriores=dados_anteriores,
            dados_novos={"status": "EM_ANALISE"},
            observacao="Risco reaberto para nova análise"
        )
        
        messages.success(request, "Risco reaberto!")
        return redirect("risco_detail", pk=self.object.pk)

    def _cancelar(self, request):
        """Cancela o risco."""
        if self.object.status == "FECHADO":
            messages.error(request, "Riscos fechados não podem ser cancelados.")
            return redirect("risco_detail", pk=self.object.pk)
        
        dados_anteriores = {"status": self.object.status}
        
        self.object.status = "CANCELADO"
        self.object.save()
        
        _registrar_historico(
            risco=self.object,
            usuario=request.user,
            acao="STATUS",
            dados_anteriores=dados_anteriores,
            dados_novos={"status": "CANCELADO"},
            observacao="Risco cancelado"
        )
        
        messages.warning(request, "Risco cancelado!")
        return redirect("risco_detail", pk=self.object.pk)


class RiscoUpdateView(UpdateView):
    """Editar risco."""
    model = Risco
    template_name = "app/risco_form.html"
    fields = [
        "categoria", "titulo", "descricao", "causa",
        "probabilidade", "impacto",
        "plano_resposta", "responsavel", "data_meta_tratamento",
        "observacoes"
    ]

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if obra_em_somente_leitura(self.object.obra):
            messages.error(request, descricao_restricao_obra(self.object.obra))
            return redirect("risco_detail", pk=self.object.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        empresa = _get_empresa_do_request(self.request)
        queryset = Risco.objects.select_related("empresa", "obra")
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = f"Editar Risco: {self.object.titulo}"
        context["voltar_url"] = reverse_lazy("risco_detail", kwargs={"pk": self.object.pk})
        
        obra = self.object.obra
        if obra:
            from .models import PlanoContas
            context["plano_contas_n5"] = PlanoContas.objects.filter(obra=obra, level=5)
        
        return context

    def form_valid(self, form):
        risco = form.save(commit=False)
        
        # Capturar dados anteriores
        dados_anteriores = {
            "categoria": risco.categoria,
            "probabilidade": risco.probabilidade,
            "impacto": risco.impacto,
            "nivel": risco.nivel,
            "plano_resposta": risco.plano_resposta
        }
        
        risco.save()
        
        # Registrar histórico
        _registrar_historico(
            risco=risco,
            usuario=self.request.user,
            acao="ALTERACAO",
            dados_anteriores=dados_anteriores,
            dados_novos={
                "categoria": risco.categoria,
                "probabilidade": risco.probabilidade,
                "impacto": risco.impacto,
                "nivel": risco.nivel,
                "plano_resposta": risco.plano_resposta
            }
        )
        
        messages.success(self.request, "Risco atualizado com sucesso!")
        return redirect("risco_detail", pk=risco.pk)

    def get_success_url(self):
        return reverse_lazy("risco_detail", kwargs={"pk": self.object.pk})

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.pode_editar():
            messages.error(request, "Este risco não pode ser editado.")
            return redirect("risco_detail", pk=self.object.pk)
        return super().get(request, *args, **kwargs)


@login_required
def risco_delete_view(request, pk):
    """Excluir risco."""
    empresa = _get_empresa_do_request(request)
    risco = get_object_or_404(Risco, pk=pk)
    
    if empresa and risco.empresa_id != empresa.id:
        raise Http404("Risco não encontrado.")
    
    if request.method == "POST":
        if not risco.pode_editar():
            messages.error(request, "Este risco não pode ser excluído.")
            return redirect("risco_detail", pk=risco.pk)
        
        titulo = risco.titulo
        risco.delete()
        messages.success(request, f"Risco '{titulo}' excluído com sucesso!")
        return redirect("risco_list")
    
    return redirect("risco_list")


def risco_dashboard_view(request):
    """Dashboard de riscos por obra."""
    empresa = _get_empresa_do_request(request)
    obra = _get_obra_contexto(request)
    
    queryset = Risco.objects.all()
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    
    # Estatísticas por nível
    baixo = queryset.filter(nivel__lte=4).count()
    medio = queryset.filter(nivel__gte=5, nivel__lte=9).count()
    alto = queryset.filter(nivel__gte=10, nivel__lte=15).count()
    critico = queryset.filter(nivel__gt=15).count()
    
    # Estatísticas por status
    identificados = queryset.filter(status="IDENTIFICADO").count()
    em_tratamento = queryset.filter(status="EM_TRATAMENTO").count()
    mitigados = queryset.filter(status="MITIGADO").count()
    fechados = queryset.filter(status="FECHADO").count()
    
    context = {
        "total": queryset.count(),
        "baixo": baixo,
        "medio": medio,
        "alto": alto,
        "critico": critico,
        "identificados": identificados,
        "em_tratamento": em_tratamento,
        "mitigados": mitigados,
        "fechados": fechados,
    }
    
    return render(request, "app/risco_dashboard.html", context)
