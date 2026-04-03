"""
Views para Planejamento Físico - Controle de Cronogramas
Atende: ISO 6.1 (Planejamento) + PMBOK 6 (Cronograma)
"""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.db.models import Q, Sum, Avg
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from .importacao_cronograma import CronogramaService, MapeamentoService
from .models import Obra
from .models_planejamento import MapaCorrespondencia, PlanoFisico, PlanoFisicoItem
from .permissions import get_empresa_operacional, get_obra_do_contexto


def _obter_obra_contexto(request):
    """Obtém obra do contexto atual."""
    return get_obra_do_contexto(request)


def _get_empresa_do_request(request):
    """Obtém empresa do contexto operacional atual."""
    return get_empresa_operacional(request)


def _filtrar_por_obra_contexto(request, queryset, campo="obra"):
    """Filtra queryset pela obra do contexto."""
    obra_contexto = _obter_obra_contexto(request)
    if not obra_contexto:
        return queryset.none()
    return queryset.filter(**{campo: obra_contexto})


class PlanoFisicoListView(ListView):
    """Lista de cronogramas físicos por obra."""
    model = PlanoFisico
    template_name = "app/plano_fisico_list.html"
    context_object_name = "planos"
    paginate_by = 20

    def get_queryset(self):
        queryset = PlanoFisico.objects.select_related(
            "obra", "responsavel_importacao"
        ).prefetch_related("itens")
        
        queryset = _filtrar_por_obra_contexto(self.request, queryset)
        
        # Filtros
        termo = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        tipo = self.request.GET.get("tipo", "").strip()

        if termo:
            queryset = queryset.filter(
                Q(titulo__icontains=termo)
                | Q(descricao__icontains=termo)
                | Q(itens__atividade__icontains=termo)
                | Q(itens__codigo_atividade__icontains=termo)
            ).distinct()
        if status:
            queryset = queryset.filter(status=status)
        if tipo:
            queryset = queryset.filter(tipo_arquivo=tipo)
            
        return queryset.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = PlanoFisico.STATUS_CHOICES
        context["tipo_choices"] = PlanoFisico.TIPO_ARQUIVO_CHOICES
        context["busca"] = self.request.GET.get("q", "")
        context["status_filtro"] = self.request.GET.get("status", "")
        context["tipo_filtro"] = self.request.GET.get("tipo", "")
        
        obra_contexto = _obter_obra_contexto(self.request)
        if obra_contexto:
            context["obra"] = obra_contexto
            base_queryset = PlanoFisico.objects.filter(obra=obra_contexto)
            context["baseline"] = base_queryset.filter(
                obra=obra_contexto, is_baseline=True
            ).order_by("-versao")
            context["total_planos"] = base_queryset.count()
            context["baselines_count"] = base_queryset.filter(is_baseline=True).count()
            context["ativos_count"] = base_queryset.filter(status="ATIVO").count()
            itens = PlanoFisicoItem.objects.filter(plano__obra=obra_contexto)
            context["atividades_total"] = itens.count()
            context["atividades_atrasadas"] = itens.filter(dias_desvio__gt=0).count()
        
        return context


class PlanoFisicoCreateView(CreateView):
    """Criar novo cronograma via importação."""
    model = PlanoFisico
    template_name = "app/plano_fisico_importar.html"
    fields = ["titulo", "descricao"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        context["obra"] = obra_contexto
        context["titulo_pagina"] = "Importar Cronograma"
        return context

    def get_success_url(self):
        return reverse_lazy("plano_fisico_list")

    def form_valid(self, form):
        obra_contexto = _obter_obra_contexto(self.request)
        if not obra_contexto:
            messages.error(self.request, "Selecione uma obra no menu antes de importar o cronograma.")
            return redirect("plano_fisico_list")
        
        arquivo = self.request.FILES.get("arquivo")
        if not arquivo:
            messages.error(self.request, "Selecione um arquivo para importar.")
            return self.render_to_response(self.get_context_data(form=form))
        
        # Verificar extensão
        extensao = arquivo.name.lower().split('.')[-1]
        if extensao not in ['xlsx', 'xls', 'mpp']:
            messages.error(self.request, "Arquivo deve ser .xlsx, .xls ou .mpp")
            return self.render_to_response(self.get_context_data(form=form))
        
        criar_baseline = self.request.POST.get("criar_baseline") == "on"
        
        try:
            plano = CronogramaService.importar_xlsx(
                arquivo=arquivo,
                obra=obra_contexto,
                responsavel=self.request.user,
                titulo=form.cleaned_data.get("titulo"),
                criar_baseline=criar_baseline
            )
            messages.success(self.request, f"Cronograma '{plano.titulo}' importado com sucesso!")
            return redirect("plano_fisico_detail", pk=plano.pk)
        except Exception as e:
            messages.error(self.request, f"Erro ao importar: {str(e)}")
            return self.render_to_response(self.get_context_data(form=form))


class PlanoFisicoDetailView(DetailView):
    """Detalhes do cronograma físico."""
    model = PlanoFisico
    template_name = "app/plano_fisico_detail.html"
    context_object_name = "plano"

    def get_queryset(self):
        return PlanoFisico.objects.select_related(
            "obra", "responsavel_importacao"
        ).prefetch_related(
            "itens",
            "itens__plano_contas",
            "historico_baseline"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plano = self.object
        
        # Itens do cronograma
        context["itens"] = plano.itens.select_related("plano_contas").order_by("sort_order")[:200]
        
        # Estatísticas
        total_itens = plano.itens.count()
        total_macros = plano.itens.filter(is_marco=True).count()
        
        # Calcular média de execução
        itens_com_exec = plano.itens.exclude(percentual_concluido=0)
        if itens_com_exec.exists():
            percentuais = [i.percentual_concluido for i in itens_com_exec]
            media_execucao = sum(percentuais) / len(percentuais)
        else:
            media_execucao = 0
        
        context["estatisticas"] = {
            "total_itens": total_itens,
            "total_macros": total_macros,
            "media_execucao": round(media_execucao, 1),
            "atrasados": plano.itens.filter(dias_desvio__gt=0).count(),
            "adiantados": plano.itens.filter(dias_desvio__lt=0).count(),
            "valor_planejado": plano.itens.aggregate(total=Sum("valor_planejado"))["total"] or 0,
            "valor_realizado": plano.itens.aggregate(total=Sum("valor_realizado"))["total"] or 0,
        }
        context["itens_criticos"] = plano.itens.filter(dias_desvio__gt=0).order_by("-dias_desvio", "sort_order")[:10]
        
        # Divergências de mapeamento
        divergencias = MapeamentoService.verificar_divergencias(plano.pk)
        context["divergencias"] = divergencias
        context["tem_divergencias"] = len(divergencias) > 0
        
        # Curva S
        try:
            curva_planejada = CronogramaService.gerar_curva_s_planejada(plano.pk)
            curva_realizada = CronogramaService.gerar_curva_s_realizada(plano.pk)
            context["curva_planejada"] = curva_planejada
            context["curva_realizada"] = curva_realizada
        except Exception:
            pass
        
        return context


class PlanoFisicoUpdateView(UpdateView):
    """Editar cronograma físico."""
    model = PlanoFisico
    template_name = "app/plano_fisico_form.html"
    fields = ["titulo", "descricao", "data_base", "status"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = f"Editar Cronograma: {self.object.titulo}"
        context["voltar_url"] = reverse_lazy("plano_fisico_detail", kwargs={"pk": self.object.pk})
        return context

    def get_success_url(self):
        messages.success(self.request, "Cronograma atualizado com sucesso!")
        return reverse_lazy("plano_fisico_detail", kwargs={"pk": self.object.pk})


class PlanoFisicoItemUpdateView(UpdateView):
    """Editar atividade do cronograma."""
    model = PlanoFisicoItem
    template_name = "app/plano_fisico_item_form.html"
    fields = [
        "data_inicio_real", "data_fim_real", "percentual_concluido",
        "valor_realizado", "plano_contas"
    ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item"] = self.object
        context["voltar_url"] = reverse_lazy("plano_fisico_detail", kwargs={"pk": self.object.plano.pk})
        
        # Buscar lista de Planos de Contas para o dropdown
        from .models import PlanoContas
        obra = self.object.plano.obra
        context["plano_contas_list"] = PlanoContas.objects.filter(
            obra=obra, level=4
        ).order_by("codigo")
        
        return context

    def get_success_url(self):
        messages.success(self.request, "Atividade atualizada com sucesso!")
        return reverse_lazy("plano_fisico_detail", kwargs={"pk": self.object.plano.pk})


class PlanoFisicoDashboardView(TemplateView):
    """Dashboard de acompanhamento do cronograma."""
    template_name = "app/plano_fisico_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        
        if not obra_contexto:
            messages.error(self.request, "Selecione uma obra no menu.")
            return context
        
        # Buscar cronogramas da obra
        planos = PlanoFisico.objects.filter(obra=obra_contexto).order_by("-created_at")
        
        # Baseline ativo
        baseline_ativo = planos.filter(is_baseline=True).first()
        
        context["obra"] = obra_contexto
        context["planos"] = planos
        context["baseline_ativo"] = baseline_ativo
        
        if baseline_ativo:
            # Estatísticas do baseline
            itens = baseline_ativo.itens
            context["total_atividades"] = itens.count()
            context["total_macros"] = itens.filter(is_marco=True).count()
            context["atividades_atrasadas"] = itens.filter(dias_desvio__gt=0).count()
            
            # Curva S
            curva_planejada = CronogramaService.gerar_curva_s_planejada(baseline_ativo.pk)
            curva_realizada = CronogramaService.gerar_curva_s_realizada(baseline_ativo.pk)
            context["curva_planejada"] = curva_planejada
            context["curva_realizada"] = curva_realizada
            
            # Divergências
            divergencias = MapeamentoService.verificar_divergencias(baseline_ativo.pk)
            context["divergencias"] = divergencias[:10]
            context["total_divergencias"] = len(divergencias)
        
        return context


class MapaCorrespondenciaListView(ListView):
    """Lista de mapeamentos entre cronograma e EAP."""
    model = MapaCorrespondencia
    template_name = "app/mapa_correspondencia_list.html"
    context_object_name = "mapeamentos"
    paginate_by = 50

    def get_queryset(self):
        obra_contexto = _obter_obra_contexto(self.request)
        empresa = _get_empresa_do_request(self.request)
        
        queryset = MapaCorrespondencia.objects.select_related(
            "plano_fisico_item", "plano_fisico_item__plano",
            "plano_contas", "empresa", "obra"
        )
        
        if obra_contexto:
            queryset = queryset.filter(obra=obra_contexto)
        elif empresa:
            queryset = queryset.filter(empresa=empresa)
        
        # Filtros
        status = self.request.GET.get("status", "").strip()
        if status:
            queryset = queryset.filter(status=status)
        
        return queryset.order_by("plano_fisico_item__codigo_atividade")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = MapaCorrespondencia.STATUS_CHOICES
        
        obra_contexto = _obter_obra_contexto(self.request)
        if obra_contexto:
            # Cronogramas da obra
            context["planos_fisicos"] = PlanoFisico.objects.filter(
                obra=obra_contexto
            ).order_by("-created_at")
            
            # Estatísticas
            total = MapaCorrespondencia.objects.filter(obra=obra_contexto).count()
            vinculados = MapaCorrespondencia.objects.filter(
                obra=obra_contexto, status="ATIVO", plano_contas__isnull=False
            ).count()
            nao_vinculados = total - vinculados
            
            context["estatisticas"] = {
                "total": total,
                "vinculados": vinculados,
                "nao_vinculados": nao_vinculados,
                "percentual_vinculado": round((vinculados / total * 100) if total > 0 else 0, 1)
            }
        
        return context


@login_required
def vincular_mapeamento_ajax(request):
    """Vincula atividade do cronograma a centro de custo via AJAX."""
    if request.method != "POST":
        return JsonResponse({"error": "Método não permitido"}, status=405)
    
    item_id = request.POST.get("item_id")
    plano_contas_id = request.POST.get("plano_contas_id")
    percentual = request.POST.get("percentual", 100)
    
    if not item_id:
        return JsonResponse({"error": "Item não informado"}, status=400)
    
    try:
        item = PlanoFisicoItem.objects.get(pk=item_id)
        empresa = _get_empresa_do_request(request)
        
        if not empresa:
            return JsonResponse({"error": "Empresa não encontrada"}, status=400)
        
        plano_contas = None
        if plano_contas_id:
            from .models import PlanoContas
            plano_contas = PlanoContas.objects.get(pk=plano_contas_id)
        
        # Criar ou atualizar mapeamento
        mapeamento = MapaCorrespondencia.objects.filter(
            plano_fisico_item=item,
            status="ATIVO"
        ).first()
        
        if mapeamento:
            mapeamento.plano_contas = plano_contas
            mapeamento.percentual_rateio = percentual
            mapeamento.save()
        else:
            mapeamento = MapaCorrespondencia.objects.create(
                empresa=empresa,
                obra=item.plano.obra,
                plano_fisico_item=item,
                plano_contas=plano_contas,
                percentual_rateio=percentual,
                status="ATIVO",
                created_by=request.user
            )
        
        return JsonResponse({
            "success": True,
            "mensagem": "Mapeamento atualizado com sucesso!",
            "mapeamento_id": mapeamento.pk
        })
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def sugerir_mapeamento_ajax(request):
    """Sugere mapeamento por similaridade via AJAX."""
    if request.method not in ["GET", "POST"]:
        return JsonResponse({"error": "Método não permitido"}, status=405)
    
    # Aceita GET ou POST
    item_id = request.GET.get("item_id") or request.POST.get("item_id")
    
    if not item_id:
        return JsonResponse({"error": "Item não informado"}, status=400)
    
    try:
        item = PlanoFisicoItem.objects.get(pk=item_id)
        obra = item.plano.obra
        
        # Buscar todos os centros de custo da obra
        from .models import PlanoContas
        centros = PlanoContas.objects.filter(obra=obra, level=4).order_by("codigo")
        
        # Buscar sugestões
        sugestoes = MapeamentoService.sugerir_correspondencia(item, centros)
        
        return JsonResponse({
            "success": True,
            "sugestoes": [
                {
                    "id": s["plano_contas"].id,
                    "codigo": s["plano_contas"].codigo,
                    "descricao": s["plano_contas"].descricao,
                    "pontuacao": s["pontuacao"]
                }
                for s in sugestoes
            ]
        })
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def gerar_curva_s_ajax(request):
    """Retorna dados da curva S via AJAX."""
    if request.method != "GET":
        return JsonResponse({"error": "Método não permitido"}, status=405)
    
    plano_id = request.GET.get("plano_id")
    data_corte = request.GET.get("data_corte")
    
    if not plano_id:
        return JsonResponse({"error": "Cronograma não informado"}, status=400)
    
    try:
        plano = PlanoFisico.objects.get(pk=plano_id)
        
        # Curva planejada
        curva_planejada = CronogramaService.gerar_curva_s_planejada(plano.pk)
        
        # Curva realizada
        data = None
        if data_corte:
            from datetime import datetime
            data = datetime.strptime(data_corte, "%Y-%m-%d").date()
        
        curva_realizada = CronogramaService.gerar_curva_s_realizada(plano.pk, data)
        
        return JsonResponse({
            "success": True,
            "planejada": curva_planejada,
            "realizada": curva_realizada
        })
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def plano_fisico_delete_view(request):
    """Exclui cronograma físico."""
    if request.method != "POST":
        raise Http404()
    
    plano = get_object_or_404(PlanoFisico, pk=request.POST.get("id"))
    
    try:
        titulo = plano.titulo
        plano.delete()
        messages.success(request, f"Cronograma '{titulo}' excluído com sucesso!")
    except Exception as e:
        messages.error(request, f"Erro ao excluir: {str(e)}")
    
    return redirect("plano_fisico_list")


@login_required
def criar_baseline_view(request, pk):
    """Cria baseline de uma versão do cronograma."""
    plano = get_object_or_404(PlanoFisico, pk=pk)
    
    try:
        # Criar baseline
        baseline = CronogramaService._criar_baseline(
            plano, request.user, "Baseline criado manualmente"
        )
        
        # Atualizar status do plano
        plano.is_baseline = True
        plano.status = "BASELINE"
        plano.save()
        
        messages.success(request, f"Baseline criado com sucesso! (v{baseline.versao})")
    except Exception as e:
        messages.error(request, f"Erro ao criar baseline: {str(e)}")
    
    return redirect("plano_fisico_detail", pk=pk)
