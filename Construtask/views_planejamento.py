"""
Views do modulo de planejamento fisico.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from .application.planejamento import (
    atribuir_metricas_resumo_planos,
    consolidar_arvore_cronograma,
    peso_item_planejado,
)
from .cache_utils import request_local_get_or_set, resilient_cache_delete, resilient_cache_get_or_set
from .forms import PlanoFisicoItemForm
from .importacao_cronograma import CronogramaService, MapeamentoService
from .models import PlanoContas
from .models_planejamento import MapaCorrespondencia, PlanoFisico, PlanoFisicoItem
from .pagination import DefaultPaginationMixin
from .permissions import (
    filtrar_por_obra_contexto as _filtrar_por_obra_contexto,
    get_obra_do_contexto as _obter_obra_contexto,
    obra_em_somente_leitura,
    descricao_restricao_obra,
)


def _planejamento_cache_ttl():
    return max(30, int(getattr(settings, "CONSTRUTASK_PLANEJAMENTO_CACHE_TTL", 180)))


def _cache_get_or_set_planejamento(chave, builder, ttl=None, request=None):
    return request_local_get_or_set(
        request,
        chave,
        lambda: resilient_cache_get_or_set(chave, builder, timeout=ttl or _planejamento_cache_ttl()),
    )


def _limpar_cache_plano(plano):
    if not plano:
        return
    resilient_cache_delete(f"planejamento:analise:{plano.pk}")
    resilient_cache_delete(f"planejamento:arvore:{plano.pk}")
    resilient_cache_delete(f"planejamento:divergencias:{plano.pk}")
    resilient_cache_delete(f"planejamento:curva_planejada:{plano.pk}")
    resilient_cache_delete(f"planejamento:curva_realizada:{plano.pk}")
    resilient_cache_delete(f"planejamento:resumo_mapeamento:{plano.obra_id}")


def _obter_analise_vinculos_cached(plano, request=None):
    return _cache_get_or_set_planejamento(
        f"planejamento:analise:{plano.pk}",
        lambda: MapeamentoService.analisar_vinculos(plano),
        request=request,
    )


def _obter_arvore_cronograma_cached(plano, analise_vinculos, request=None):
    return _cache_get_or_set_planejamento(
        f"planejamento:arvore:{plano.pk}",
        lambda: consolidar_arvore_cronograma(plano, analise_vinculos=analise_vinculos),
        request=request,
    )


def _obter_divergencias_plano_cached(plano, analise_vinculos, request=None):
    return _cache_get_or_set_planejamento(
        f"planejamento:divergencias:{plano.pk}",
        lambda: MapeamentoService.verificar_divergencias(plano.pk, analise=analise_vinculos),
        request=request,
    )


def _obter_curvas_cronograma_cached(plano, request=None):
    return {
        "planejada": _cache_get_or_set_planejamento(
            f"planejamento:curva_planejada:{plano.pk}",
            lambda: CronogramaService.gerar_curva_s_planejada(plano.pk),
            request=request,
        ),
        "realizada": _cache_get_or_set_planejamento(
            f"planejamento:curva_realizada:{plano.pk}",
            lambda: CronogramaService.gerar_curva_s_realizada(plano.pk),
            request=request,
        ),
    }


def _plano_referencia_mapeamento(obra):
    if not obra:
        return None
    plano = (
        PlanoFisico.objects.filter(obra=obra, is_baseline=True)
        .order_by("-versao", "-created_at")
        .first()
    )
    if plano:
        return plano
    return (
        PlanoFisico.objects.filter(obra=obra, status__in=["ATIVO", "BASELINE"])
        .order_by("-created_at")
        .first()
    )


def _descricao_eap_mapeamento(plano_contas):
    if not plano_contas:
        return ""
    parent = getattr(plano_contas, "parent", None)
    if parent and parent.descricao:
        return f"{parent.descricao} / {plano_contas.descricao}"
    return plano_contas.descricao


def _resumo_mapeamento_obra(obra):
    return _cache_get_or_set_planejamento(
        f"planejamento:resumo_mapeamento:{obra.pk}",
        lambda: _calcular_resumo_mapeamento_obra(obra),
        request=None,
    )


def _calcular_resumo_mapeamento_obra(obra):
    itens = list(
        PlanoFisicoItem.objects.filter(plano__obra=obra, filhos__isnull=True)
        .select_related("plano", "plano_contas")
        .order_by("plano_id", "sort_order", "codigo_atividade")
    )
    total_itens = len(itens)
    sem_vinculo = 0
    proximos_sem_vinculo = 0
    atrasados = len([item for item in itens if item.dias_desvio > 0])
    hoje = date.today()
    analise_por_plano = {}
    for item in itens:
        if item.plano_id not in analise_por_plano:
            analise_por_plano[item.plano_id] = _obter_analise_vinculos_cached(item.plano)
        vinculado = bool(analise_por_plano[item.plano_id]["item_to_eaps"].get(item.pk))
        if not vinculado:
            sem_vinculo += 1
        if not vinculado and item.data_inicio_prevista and 0 <= (item.data_inicio_prevista - hoje).days <= 60:
            proximos_sem_vinculo += 1
    return {
        "total_itens": total_itens,
        "sem_vinculo": sem_vinculo,
        "proximos_sem_vinculo": proximos_sem_vinculo,
        "atrasados": atrasados,
    }


def _parse_percentual_realizado(valor):
    texto = str(valor or "").strip()
    if not texto:
        return Decimal("0")
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        raise ValueError("Percentual informado invalido.")


class PlanoFisicoListView(DefaultPaginationMixin, ListView):
    model = PlanoFisico
    template_name = "app/plano_fisico_list.html"
    context_object_name = "planos"

    def get_paginate_by(self, queryset):
        return None

    def dispatch(self, request, *args, **kwargs):
        if not _obter_obra_contexto(request):
            messages.error(request, "Selecione uma obra no menu antes de acessar o cronograma.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        itens_qs = PlanoFisicoItem.objects.select_related("parent").only(
            "id",
            "plano_id",
            "parent_id",
            "codigo_atividade",
            "data_inicio_prevista",
            "data_fim_prevista",
            "percentual_concluido",
            "sort_order",
        ).order_by("sort_order", "pk")
        queryset = (
            PlanoFisico.objects.select_related("obra", "responsavel_importacao")
            .annotate(total_itens_count=Count("itens", distinct=True))
            .prefetch_related(Prefetch("itens", queryset=itens_qs, to_attr="itens_prefetchados"))
        )
        queryset = _filtrar_por_obra_contexto(self.request, queryset, vazio_quando_sem_obra=True)

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
        atribuir_metricas_resumo_planos(context["planos"])
        context["status_choices"] = PlanoFisico.STATUS_CHOICES
        context["tipo_choices"] = PlanoFisico.TIPO_ARQUIVO_CHOICES
        context["busca"] = self.request.GET.get("q", "")
        context["status_filtro"] = self.request.GET.get("status", "")
        context["tipo_filtro"] = self.request.GET.get("tipo", "")

        obra_contexto = _obter_obra_contexto(self.request)
        if obra_contexto:
            context["obra"] = obra_contexto
            base_queryset = PlanoFisico.objects.filter(obra=obra_contexto)
            context["baseline"] = base_queryset.filter(is_baseline=True).order_by("-versao")
            context["total_planos"] = base_queryset.count()
            context["baselines_count"] = base_queryset.filter(is_baseline=True).count()
            context["ativos_count"] = base_queryset.filter(status="ATIVO").count()
            itens = PlanoFisicoItem.objects.filter(plano__obra=obra_contexto)
            context["atividades_total"] = itens.count()
            context["atividades_atrasadas"] = itens.filter(dias_desvio__gt=0).count()
            context["resumo_mapeamento"] = _resumo_mapeamento_obra(obra_contexto)

        return context


class PlanoFisicoCreateView(CreateView):
    model = PlanoFisico
    template_name = "app/plano_fisico_importar.html"
    fields = ["titulo", "descricao"]

    def dispatch(self, request, *args, **kwargs):
        obra = _obter_obra_contexto(request)
        if not obra:
            messages.error(request, "Selecione uma obra no menu antes de importar o cronograma.")
            return redirect("home")
        if obra_em_somente_leitura(obra):
            messages.error(request, descricao_restricao_obra(obra))
            return redirect("plano_fisico_list")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["obra"] = _obter_obra_contexto(self.request)
        context["titulo_pagina"] = "Importar Cronograma"
        context["colunas_obrigatorias"] = CronogramaService.COLUNAS_OBRIGATORIAS
        context["colunas_opcionais"] = CronogramaService.COLUNAS_OPCIONAIS
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

        extensao = arquivo.name.lower().split(".")[-1]
        if extensao not in ["xlsx", "xls", "mpp"]:
            messages.error(self.request, "Arquivo deve ser .xlsx, .xls ou .mpp")
            return self.render_to_response(self.get_context_data(form=form))

        criar_baseline = self.request.POST.get("criar_baseline") == "on"

        try:
            plano = CronogramaService.importar_xlsx(
                arquivo=arquivo,
                obra=obra_contexto,
                responsavel=self.request.user,
                titulo=form.cleaned_data.get("titulo"),
                criar_baseline=criar_baseline,
            )
            _limpar_cache_plano(plano)
            resumo = getattr(plano, "_resumo_importacao", {})
            messages.success(
                self.request,
                f"Cronograma '{plano.titulo}' importado com sucesso! "
                f"Itens criados: {resumo.get('itens_criados', 0)} | "
                f"Atividades validas: {resumo.get('atividades_validas', 0)} | "
                f"Com EAP informada: {resumo.get('com_codigo_eap', 0)} | "
                f"EAP reconhecida: {resumo.get('eap_reconhecida', 0)}."
            )
            if resumo.get("sem_datas"):
                messages.warning(
                    self.request,
                    f"Foram encontradas {resumo['sem_datas']} linha(s) com data de inicio ou fim nao identificada(s)."
                )
            return redirect("plano_fisico_detail", pk=plano.pk)
        except Exception as e:
            messages.error(self.request, f"Erro ao importar: {str(e)}")
            return self.render_to_response(self.get_context_data(form=form))


class PlanoFisicoDetailView(DetailView):
    model = PlanoFisico
    template_name = "app/plano_fisico_detail.html"
    context_object_name = "plano"

    def dispatch(self, request, *args, **kwargs):
        if not _obter_obra_contexto(request):
            messages.error(request, "Selecione uma obra no menu antes de acessar o cronograma.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        itens_qs = PlanoFisicoItem.objects.select_related("plano_contas", "parent").order_by("sort_order", "pk")
        return _filtrar_por_obra_contexto(
            self.request,
            PlanoFisico.objects.select_related("obra", "responsavel_importacao").prefetch_related(
                "historico_baseline",
                Prefetch("itens", queryset=itens_qs, to_attr="itens_prefetchados"),
            ),
            vazio_quando_sem_obra=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plano = self.object
        analise_vinculos = _obter_analise_vinculos_cached(plano, request=self.request)
        itens = _obter_arvore_cronograma_cached(plano, analise_vinculos, request=self.request)
        folhas = [item for item in itens if not item.tem_filhos_exibicao]
        raizes = [item for item in itens if item.parent_id is None]

        total_peso = sum(
            peso_item_planejado(item, item.inicio_previsto_exibicao, item.fim_previsto_exibicao)
            for item in raizes
        )
        media_execucao = 0
        if total_peso > 0:
            soma = sum(
                Decimal(str(item.percentual_realizado_exibicao))
                * peso_item_planejado(item, item.inicio_previsto_exibicao, item.fim_previsto_exibicao)
                for item in raizes
            )
            media_execucao = round(float(soma / total_peso), 1)

        context["itens"] = itens
        context["itens_folha_editaveis"] = [item.pk for item in folhas]
        context["estatisticas"] = {
            "total_itens": len(itens),
            "total_macros": len([item for item in itens if item.is_marco]),
            "media_execucao": media_execucao,
            "atrasados": len([item for item in itens if item.dias_desvio_exibicao > 0]),
            "adiantados": len([item for item in itens if item.dias_desvio_exibicao < 0]),
            "valor_planejado": sum((item.valor_planejado_exibicao for item in folhas), Decimal("0.00")),
            "valor_realizado": sum((item.valor_realizado for item in folhas), Decimal("0.00")),
        }
        context["itens_criticos"] = sorted(
            [item for item in itens if item.dias_desvio_exibicao > 0],
            key=lambda item: (-item.dias_desvio_exibicao, item.sort_order, item.pk),
        )[:10]
        context["divergencias"] = _obter_divergencias_plano_cached(plano, analise_vinculos, request=self.request)
        context["tem_divergencias"] = bool(context["divergencias"])

        try:
            curvas = _obter_curvas_cronograma_cached(plano, request=self.request)
            context["curva_planejada"] = curvas["planejada"]
            context["curva_realizada"] = curvas["realizada"]
        except Exception:
            context["curva_planejada"] = []
            context["curva_realizada"] = []

        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if obra_em_somente_leitura(self.object.obra):
            messages.error(request, descricao_restricao_obra(self.object.obra))
            return redirect("plano_fisico_detail", pk=self.object.pk)
        acao = request.POST.get("acao")
        if acao != "atualizar_cronograma":
            return redirect("plano_fisico_detail", pk=self.object.pk)

        itens = list(self.object.itens.select_related("parent").prefetch_related("filhos").order_by("sort_order", "pk"))
        itens_por_pk = {str(item.pk): item for item in itens}
        erros = []
        processados = 0
        payload_bruto = request.POST.get("cronograma_realizado_payload", "").strip()
        if payload_bruto:
            try:
                payload = json.loads(payload_bruto)
            except json.JSONDecodeError:
                payload = {}
            pares = [(f"realizado_{item_pk}", valor) for item_pk, valor in payload.items()]
        else:
            pares = list(request.POST.items())

        for campo, valor_bruto in pares:
            if not str(campo).startswith("realizado_"):
                continue
            item_pk = str(campo).replace("realizado_", "", 1)
            item = itens_por_pk.get(item_pk)
            if not item:
                continue
            if item.filhos.exists():
                continue
            processados += 1
            try:
                percentual = _parse_percentual_realizado(valor_bruto)
            except ValueError:
                erros.append(f"{item.codigo_atividade}: percentual invalido.")
                continue
            if percentual < 0 or percentual > 100:
                erros.append(f"{item.codigo_atividade}: informe um percentual entre 0 e 100.")
                continue

            percentual_inteiro = int(percentual.quantize(Decimal("1")))
            item.aplicar_percentual_realizado(percentual_inteiro)

        if erros:
            for erro in erros:
                messages.error(request, erro)
            return self.render_to_response(self.get_context_data())

        if processados:
            _limpar_cache_plano(self.object)
            messages.success(request, "Cronograma atualizado com sucesso.")
        else:
            messages.info(request, "Nenhuma atividade filha teve o realizado alterado.")
        return redirect("plano_fisico_detail", pk=self.object.pk)


class PlanoFisicoUpdateView(UpdateView):
    model = PlanoFisico
    template_name = "app/plano_fisico_form.html"
    fields = ["titulo", "descricao", "data_base", "status"]

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if obra_em_somente_leitura(self.object.obra):
            messages.error(request, descricao_restricao_obra(self.object.obra))
            return redirect("plano_fisico_detail", pk=self.object.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = f"Editar Cronograma: {self.object.titulo}"
        context["voltar_url"] = reverse_lazy("plano_fisico_detail", kwargs={"pk": self.object.pk})
        return context

    def get_success_url(self):
        messages.success(self.request, "Cronograma atualizado com sucesso!")
        return reverse_lazy("plano_fisico_detail", kwargs={"pk": self.object.pk})


class PlanoFisicoItemUpdateView(UpdateView):
    model = PlanoFisicoItem
    template_name = "app/plano_fisico_item_form.html"
    form_class = PlanoFisicoItemForm
    pk_url_kwarg = "item_pk"

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if obra_em_somente_leitura(self.object.plano.obra):
            messages.error(request, descricao_restricao_obra(self.object.plano.obra))
            return redirect("plano_fisico_detail", pk=self.object.plano.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return PlanoFisicoItem.objects.select_related("plano", "plano__obra", "plano_contas").filter(
            plano__in=_filtrar_por_obra_contexto(
                self.request,
                PlanoFisico.objects.all(),
                vazio_quando_sem_obra=True,
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item"] = self.object
        context["voltar_url"] = reverse_lazy("plano_fisico_detail", kwargs={"pk": self.object.plano.pk})
        context["plano_contas_list"] = PlanoContas.objects.filter(obra=self.object.plano.obra).order_by("codigo")
        context["item_eh_pai"] = self.object.filhos.exists()
        return context

    def form_valid(self, form):
        item = self.object
        if item.filhos.exists():
            messages.warning(
                self.request,
                "Este item possui filhos. Datas reais e percentual realizado sao consolidados automaticamente a partir das atividades filhas."
            )

        if form.instance.plano_contas_id:
            MapeamentoService.validar_conjunto_vinculos_item(item, [form.instance.plano_contas_id])
            form.instance.erro_vinculo_eap = ""

        response = super().form_valid(form)
        MapeamentoService.recalcular_valores_planejados(item.plano)
        _limpar_cache_plano(item.plano)
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Nao foi possivel salvar a atividade. Revise os campos destacados.")
        return super().form_invalid(form)

    def get_success_url(self):
        messages.success(self.request, "Atividade atualizada com sucesso!")
        return reverse_lazy("plano_fisico_detail", kwargs={"pk": self.object.plano.pk})


class PlanoFisicoDashboardView(TemplateView):
    template_name = "app/plano_fisico_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        if not obra_contexto:
            messages.error(self.request, "Selecione uma obra no menu.")
            return context

        planos = PlanoFisico.objects.filter(obra=obra_contexto).order_by("-created_at")
        baseline_ativo = planos.filter(is_baseline=True).first()
        context["obra"] = obra_contexto
        context["planos"] = planos
        context["baseline_ativo"] = baseline_ativo

        if baseline_ativo:
            analise_vinculos = _obter_analise_vinculos_cached(baseline_ativo, request=self.request)
            itens = _obter_arvore_cronograma_cached(baseline_ativo, analise_vinculos, request=self.request)
            context["total_atividades"] = len(itens)
            context["total_macros"] = len([item for item in itens if item.is_marco])
            context["atividades_atrasadas"] = len([item for item in itens if item.dias_desvio_exibicao > 0])
            curvas = _obter_curvas_cronograma_cached(baseline_ativo, request=self.request)
            context["curva_planejada"] = curvas["planejada"]
            context["curva_realizada"] = curvas["realizada"]
            divergencias = _obter_divergencias_plano_cached(baseline_ativo, analise_vinculos, request=self.request)
            context["divergencias"] = divergencias[:10]
            context["total_divergencias"] = len(divergencias)

        return context


class MapaCorrespondenciaListView(TemplateView):
    template_name = "app/mapa_correspondencia_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        context["obra"] = obra_contexto
        context["plano_referencia"] = _plano_referencia_mapeamento(obra_contexto)
        context["linhas_mapeamento"] = []
        context["grupos_eap"] = []
        context["grupos_atividade"] = []
        context["estatisticas"] = {"total": 0, "vinculados": 0, "nao_vinculados": 0, "percentual_vinculado": 0}
        if not obra_contexto or not context["plano_referencia"]:
            return context

        plano_referencia = context["plano_referencia"]
        eaps_disponiveis = list(
            PlanoContas.objects.filter(obra=obra_contexto, filhos__isnull=True)
            .select_related("parent")
            .order_by("codigo")
        )
        itens = list(
            PlanoFisicoItem.objects.filter(plano=plano_referencia, filhos__isnull=True)
            .select_related("plano", "plano_contas")
            .order_by("data_inicio_prevista", "sort_order", "codigo_atividade")
        )
        hoje = date.today()
        linhas = []
        agrupado_por_eap = defaultdict(list)
        agrupado_por_atividade = []
        vinculados = 0
        analise = MapeamentoService.analisar_vinculos(plano_referencia)

        for item in itens:
            centros = analise["item_to_eaps"].get(item.pk, [])
            codigos = [centro.codigo for centro in centros]
            vinculado = bool(codigos)
            if vinculado:
                vinculados += 1
            dias_para_inicio = (item.data_inicio_prevista - hoje).days if item.data_inicio_prevista else None

            linhas.append(
                {
                    "item": item,
                    "codigos_eap": codigos,
                    "vinculado": vinculado,
                    "dias_para_inicio": dias_para_inicio,
                    "codigo_eap_importado": item.codigo_eap_importado or "-",
                    "erro_eap": analise["mensagens"].get(item.pk) or item.erro_vinculo_eap or "",
                }
            )
            agrupado_por_atividade.append(
                {
                    "item": item,
                    "codigos_eap": codigos,
                    "erro_eap": analise["mensagens"].get(item.pk) or item.erro_vinculo_eap or "",
                }
            )
            for centro in centros:
                if item.pk not in [atividade.pk for atividade in agrupado_por_eap[centro.codigo]]:
                    agrupado_por_eap[centro.codigo].append(item)

        context["linhas_mapeamento"] = linhas
        context["grupos_eap"] = [
            {
                "codigo": codigo,
                "descricao": _descricao_eap_mapeamento(next((eap for eap in eaps_disponiveis if eap.codigo == codigo), None)),
                "atividades": atividades,
            }
            for codigo, atividades in sorted(agrupado_por_eap.items())
        ]
        context["grupos_atividade"] = agrupado_por_atividade
        context["eaps_disponiveis"] = eaps_disponiveis
        total = len(itens)
        context["estatisticas"] = {
            "total": total,
            "vinculados": vinculados,
            "nao_vinculados": total - vinculados,
            "percentual_vinculado": round((vinculados / total * 100) if total > 0 else 0, 1),
        }
        return context


@login_required
def sugerir_mapeamento_ajax(request):
    if request.method not in ["GET", "POST"]:
        return JsonResponse({"error": "Metodo nao permitido"}, status=405)

    item_id = request.GET.get("item_id") or request.POST.get("item_id")
    if not item_id:
        return JsonResponse({"error": "Item nao informado"}, status=400)

    try:
        item = get_object_or_404(
            PlanoFisicoItem.objects.select_related("plano", "plano_contas"),
            pk=item_id,
            plano__in=_filtrar_por_obra_contexto(request=request, queryset=PlanoFisico.objects.all(), vazio_quando_sem_obra=True),
        )
        centros = list(
            PlanoContas.objects.filter(obra=item.plano.obra, filhos__isnull=True)
            .select_related("parent")
            .order_by("codigo")
        )
        codigo_eap_importado = CronogramaService._normalizar_codigo_eap(item.codigo_eap_importado)
        sugestoes_ids = [
            centro.id
            for centro in centros
            if codigo_eap_importado and CronogramaService._normalizar_codigo_eap(centro.codigo) == codigo_eap_importado
        ]
        vinculos_atuais = list(
            MapaCorrespondencia.objects.filter(
                plano_fisico_item=item,
                status="ATIVO",
                plano_contas__isnull=False,
            ).order_by("plano_contas__codigo").values_list("plano_contas_id", flat=True)
        )
        if item.plano_contas_id and item.plano_contas_id not in vinculos_atuais:
            vinculos_atuais.append(item.plano_contas_id)

        return JsonResponse({
            "success": True,
            "vinculos_atuais": vinculos_atuais,
            "sugestoes_ids": sugestoes_ids,
            "opcoes": [
                {
                    "id": centro.id,
                    "codigo": centro.codigo,
                    "descricao": _descricao_eap_mapeamento(centro),
                }
                for centro in centros
            ],
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def vincular_mapeamento_ajax(request):
    if request.method != "POST":
        return JsonResponse({"error": "Metodo nao permitido"}, status=405)

    item_id = request.POST.get("item_id")
    ids_brutos = request.POST.getlist("plano_contas_ids[]") or request.POST.getlist("plano_contas_ids")
    if not ids_brutos:
        ids_unico = (request.POST.get("plano_contas_ids") or "").strip()
        if ids_unico:
            ids_brutos = [parte.strip() for parte in ids_unico.split(",") if parte.strip()]

    if not item_id:
        return JsonResponse({"error": "Item nao informado"}, status=400)

    try:
        item = get_object_or_404(
            PlanoFisicoItem.objects.select_related("plano", "plano__obra"),
            pk=item_id,
            plano__in=_filtrar_por_obra_contexto(request, PlanoFisico.objects.all(), vazio_quando_sem_obra=True),
        )
        ids = []
        for valor in ids_brutos:
            try:
                ids.append(int(valor))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys(ids))

        centros = list(PlanoContas.objects.filter(obra=item.plano.obra, filhos__isnull=True, pk__in=ids).order_by("codigo"))
        if len(centros) != len(ids):
            return JsonResponse({"error": "Um ou mais itens da EAP sao invalidos para a obra atual."}, status=400)

        MapeamentoService.validar_conjunto_vinculos_item(item, ids)

        MapaCorrespondencia.objects.filter(
            plano_fisico_item=item,
            status="ATIVO",
        ).exclude(plano_contas_id__in=ids).update(status="INATIVO")

        for centro in centros:
            MapaCorrespondencia.objects.update_or_create(
                empresa=item.plano.obra.empresa,
                obra=item.plano.obra,
                plano_fisico_item=item,
                plano_contas=centro,
                defaults={
                    "status": "ATIVO",
                    "percentual_rateio": 100,
                    "created_by": request.user,
                },
            )

        item.plano_contas = centros[0] if len(centros) == 1 else None
        item.erro_vinculo_eap = ""
        item.save(update_fields=["plano_contas", "erro_vinculo_eap", "updated_at"])
        MapeamentoService.recalcular_valores_planejados(item.plano)

        return JsonResponse({
            "success": True,
            "mensagem": "Vinculos atualizados com sucesso!",
            "codigos": [centro.codigo for centro in centros],
        })
    except ValidationError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def gerar_curva_s_ajax(request):
    if request.method != "GET":
        return JsonResponse({"error": "Metodo nao permitido"}, status=405)

    plano_id = request.GET.get("plano_id")
    data_corte = request.GET.get("data_corte")
    if not plano_id:
        return JsonResponse({"error": "Cronograma nao informado"}, status=400)

    try:
        plano = get_object_or_404(
            _filtrar_por_obra_contexto(request, PlanoFisico.objects.all(), vazio_quando_sem_obra=True),
            pk=plano_id,
        )

        curva_planejada = CronogramaService.gerar_curva_s_planejada(plano.pk)
        data = None
        if data_corte:
            from datetime import datetime

            data = datetime.strptime(data_corte, "%Y-%m-%d").date()

        curva_realizada = CronogramaService.gerar_curva_s_realizada(plano.pk, data)
        return JsonResponse({"success": True, "planejada": curva_planejada, "realizada": curva_realizada})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def plano_fisico_delete_view(request):
    if request.method != "POST":
        raise Http404()

    plano = get_object_or_404(
        _filtrar_por_obra_contexto(request, PlanoFisico.objects.all(), vazio_quando_sem_obra=True),
        pk=request.POST.get("id"),
    )

    try:
        titulo = plano.titulo
        plano.delete()
        messages.success(request, f"Cronograma '{titulo}' excluido com sucesso!")
    except Exception as e:
        messages.error(request, f"Erro ao excluir: {str(e)}")

    return redirect("plano_fisico_list")


@login_required
def criar_baseline_view(request, pk):
    plano = get_object_or_404(
        _filtrar_por_obra_contexto(request, PlanoFisico.objects.all(), vazio_quando_sem_obra=True),
        pk=pk,
    )

    try:
        baseline = CronogramaService._criar_baseline(plano, request.user, "Baseline criado manualmente")
        plano.is_baseline = True
        plano.status = "BASELINE"
        plano.save()
        messages.success(request, f"Baseline criado com sucesso! (v{baseline.versao})")
    except Exception as e:
        messages.error(request, f"Erro ao criar baseline: {str(e)}")

    return redirect("plano_fisico_detail", pk=pk)
