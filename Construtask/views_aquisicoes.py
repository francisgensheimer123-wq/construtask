from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import CreateView, DetailView, ListView
from django.utils import timezone

from .forms import (
    CotacaoAnexoFormSet,
    CotacaoComparativaForm,
    CotacaoForm,
    CotacaoFornecedorComparativoFormSet,
    CotacaoItemFormSet,
    FornecedorForm,
    OrdemCompraWorkflowForm,
    SolicitacaoCompraForm,
    SolicitacaoCompraItemFormSet,
)
from .models import AuditEvent, Obra
from .models_aquisicoes import Cotacao, CotacaoItem, Fornecedor, OrdemCompra, SolicitacaoCompra
from .permissions import (
    get_empresa_do_usuario,
    get_empresa_operacional,
    get_obra_do_contexto,
    usuario_tem_permissao_modulo,
)
from .services_aquisicoes import AquisicoesService
from .services_lgpd import registrar_acesso_dado_pessoal
from .views import (
    _aprovar_documento,
    _datahora_local,
    _enviar_documento_para_aprovacao,
    _exportar_excel_response,
    _obter_alcada_contexto,
    _pdf_relatorio_probatorio_response,
    _retornar_documento_para_ajuste,
    money_br,
)


def _obra_contexto(request):
    return get_obra_do_contexto(request)


def _empresa_contexto(request):
    return get_empresa_operacional(request)


def _get_queryset_solicitacao(request):
    empresa = _empresa_contexto(request)
    queryset = SolicitacaoCompra.objects.select_related("obra", "plano_contas", "solicitante")
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    obra = _obra_contexto(request)
    if obra:
        queryset = queryset.filter(obra=obra)
    return queryset


def _get_queryset_cotacao(request):
    empresa = _empresa_contexto(request)
    queryset = Cotacao.objects.select_related("obra", "solicitacao", "fornecedor", "criado_por")
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    obra = _obra_contexto(request)
    if obra:
        queryset = queryset.filter(obra=obra)
    return queryset


def _get_queryset_ordem_compra(request):
    empresa = _empresa_contexto(request)
    queryset = OrdemCompra.objects.select_related(
        "obra",
        "fornecedor",
        "solicitacao",
        "cotacao_aprovada",
        "compromisso_relacionado",
    )
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    obra = _obra_contexto(request)
    if obra:
        queryset = queryset.filter(obra=obra)
    return queryset


def _auditoria_fluxo(objeto):
    entidade_app = f"{objeto._meta.app_label}.{objeto.__class__.__name__}"
    return AuditEvent.objects.filter(entidade_app=entidade_app, objeto_id=objeto.pk).order_by("-timestamp")[:12]


def _exigir_permissao_aquisicoes(request, acao):
    if not usuario_tem_permissao_modulo(request.user, "compras", acao):
        raise PermissionDenied("Usuario sem permissao para a acao de compras solicitada.")


def _historico_pdf(objeto):
    return [
        {
            "Data": _datahora_local(evento.timestamp).strftime("%d/%m/%Y %H:%M"),
            "Acao": evento.get_acao_display(),
            "Usuario": str(evento.usuario) if evento.usuario else "Nao informado",
            "Descricao": evento.entidade_label,
        }
        for evento in _auditoria_fluxo(objeto)
    ]


def _get_solicitacao_formset(request, *, instance=None, data=None):
    kwargs = {
        "instance": instance or SolicitacaoCompra(),
        "prefix": "itens",
        "obra_contexto": _obra_contexto(request),
    }
    if data is not None:
        kwargs["data"] = data
    return SolicitacaoCompraItemFormSet(**kwargs)


def _obter_solicitacao_para_cotacao(request, form=None, instance=None):
    queryset = _get_queryset_solicitacao(request)
    solicitacao_id = None
    if form is not None:
        solicitacao_id = form["solicitacao"].value()
    if not solicitacao_id and request.method == "GET":
        solicitacao_id = request.GET.get("solicitacao")
    if not solicitacao_id and instance is not None and getattr(instance, "solicitacao_id", None):
        solicitacao_id = instance.solicitacao_id
    if not solicitacao_id:
        return None
    return queryset.filter(pk=solicitacao_id).first()


def _get_cotacao_item_formset(request, *, solicitacao, data=None):
    itens = list(solicitacao.itens.select_related("plano_contas").order_by("id")) if solicitacao else []
    initial = []
    if data is None:
        initial = [{"item_solicitacao_id": item.pk, "prazo_entrega_dias": 0} for item in itens]
    kwargs = {
        "prefix": "itens_cotacao",
        "solicitacao": solicitacao,
        "solicitacao_itens": itens,
        "initial": initial,
    }
    if data is not None:
        kwargs["data"] = data
    return CotacaoItemFormSet(**kwargs), itens


class FornecedorListView(LoginRequiredMixin, ListView):
    model = Fornecedor
    template_name = "app/fornecedor_list.html"
    context_object_name = "fornecedores"

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        registrar_acesso_dado_pessoal(
            request,
            categoria_titular="FORNECEDOR",
            entidade="Fornecedor",
            identificador="Lista de fornecedores",
            acao="ADMIN_LIST",
            finalidade="Gestao comercial e operacional de fornecedores homologados",
            detalhes="Consulta administrativa ao cadastro consolidado de fornecedores.",
        )
        return response

    def get_queryset(self):
        _exigir_permissao_aquisicoes(self.request, "view")
        empresa = get_empresa_do_usuario(self.request.user)
        queryset = Fornecedor.objects.all()
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        return queryset.order_by("razao_social")


class FornecedorCreateView(LoginRequiredMixin, CreateView):
    model = Fornecedor
    form_class = FornecedorForm
    template_name = "app/fornecedor_form.html"
    success_url = reverse_lazy("fornecedor_list")

    def form_valid(self, form):
        _exigir_permissao_aquisicoes(self.request, "create")
        fornecedor = form.save(commit=False)
        fornecedor.empresa = get_empresa_do_usuario(self.request.user)
        fornecedor.save()
        messages.success(self.request, "Fornecedor cadastrado com sucesso.")
        return redirect(self.success_url)


class SolicitacaoCompraListView(LoginRequiredMixin, ListView):
    model = SolicitacaoCompra
    template_name = "app/solicitacao_compra_list.html"
    context_object_name = "solicitacoes"

    def get_queryset(self):
        _exigir_permissao_aquisicoes(self.request, "view")
        return _get_queryset_solicitacao(self.request).order_by("-data_solicitacao", "-id")


class SolicitacaoCompraCreateView(LoginRequiredMixin, CreateView):
    model = SolicitacaoCompra
    form_class = SolicitacaoCompraForm
    template_name = "app/solicitacao_compra_form.html"
    success_url = reverse_lazy("solicitacao_compra_list")

    def get_form_kwargs(self):
        _exigir_permissao_aquisicoes(self.request, "create")
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = _empresa_contexto(self.request)
        kwargs["obra_contexto"] = _obra_contexto(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("item_formset", _get_solicitacao_formset(self.request))
        return context

    def post(self, request, *args, **kwargs):
        _exigir_permissao_aquisicoes(request, "create")
        self.object = None
        form = self.get_form()
        item_formset = _get_solicitacao_formset(request, data=request.POST)
        obra_contexto = _obra_contexto(request)
        empresa_contexto = _empresa_contexto(request)
        if not obra_contexto:
            form.add_error(None, "Selecione uma obra no filtro principal antes de registrar a solicitacao de compra.")
        if not empresa_contexto:
            form.add_error(None, "Nao foi possivel identificar a empresa do contexto atual. Selecione novamente a obra e tente outra vez.")
        if form.is_valid() and item_formset.is_valid() and obra_contexto and empresa_contexto:
            solicitacao = form.save(commit=False)
            solicitacao.empresa = empresa_contexto
            solicitacao.obra = obra_contexto
            solicitacao.solicitante = request.user
            solicitacao.save()
            item_formset.instance = solicitacao
            item_formset.save()
            primeiro_item = solicitacao.itens.order_by("id").first()
            if primeiro_item and solicitacao.plano_contas_id != primeiro_item.plano_contas_id:
                solicitacao.plano_contas = primeiro_item.plano_contas
                solicitacao.save(update_fields=["plano_contas"])
            messages.success(request, "Solicitacao de compra registrada com sucesso.")
            return redirect("solicitacao_compra_detail", pk=solicitacao.pk)
        return self.render_to_response(self.get_context_data(form=form, item_formset=item_formset))


class SolicitacaoCompraDetailView(LoginRequiredMixin, DetailView):
    model = SolicitacaoCompra
    template_name = "app/solicitacao_compra_detail.html"
    context_object_name = "solicitacao"

    def get_queryset(self):
        _exigir_permissao_aquisicoes(self.request, "view")
        return _get_queryset_solicitacao(self.request).prefetch_related(
            "itens__plano_contas",
            "cotacoes__fornecedor",
            "ordens_compra",
            "ordens_compra__compromisso_relacionado",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["compromissos_resultantes"] = [
            ordem.compromisso_relacionado
            for ordem in self.object.ordens_compra.select_related("compromisso_relacionado")
            if ordem.compromisso_relacionado_id
        ]
        context.update(_obter_alcada_contexto(self.request.user, self.object.valor_estimado_total))
        context["workflow_events"] = _auditoria_fluxo(self.object)
        context["pode_enviar_para_aprovacao"] = usuario_tem_permissao_modulo(self.request.user, "compras", "create")
        context["pode_aprovar"] = usuario_tem_permissao_modulo(self.request.user, "compras", "approve")
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        acao = request.POST.get("acao")
        if acao == "enviar_para_aprovacao":
            _exigir_permissao_aquisicoes(request, "create")
            _enviar_documento_para_aprovacao(
                request,
                self.object,
                status_em_aprovacao="EM_APROVACAO",
                descricao=f"{self.object.numero} enviada para aprovacao.",
            )
        elif acao == "aprovar":
            _exigir_permissao_aquisicoes(request, "approve")
            _aprovar_documento(
                request,
                self.object,
                valor=self.object.valor_estimado_total,
                status_aprovado="APROVADA",
                descricao=f"{self.object.numero} aprovada.",
            )
        elif acao == "retornar_para_ajuste":
            _exigir_permissao_aquisicoes(request, "approve")
            _retornar_documento_para_ajuste(
                request,
                self.object,
                valor=self.object.valor_estimado_total,
                status_ajuste="RASCUNHO",
                descricao=f"{self.object.numero} devolvida para ajuste.",
            )
        return redirect("solicitacao_compra_detail", pk=self.object.pk)


class CotacaoListView(LoginRequiredMixin, ListView):
    model = Cotacao
    template_name = "app/cotacao_list.html"
    context_object_name = "cotacoes"

    def get_queryset(self):
        _exigir_permissao_aquisicoes(self.request, "view")
        return _get_queryset_cotacao(self.request).order_by("-data_cotacao", "-id")


class CotacaoCreateView(LoginRequiredMixin, CreateView):
    model = Cotacao
    form_class = CotacaoComparativaForm
    template_name = "app/cotacao_form.html"
    success_url = reverse_lazy("cotacao_list")

    def get_form_kwargs(self):
        _exigir_permissao_aquisicoes(self.request, "create")
        kwargs = super().get_form_kwargs()
        kwargs.pop("instance", None)
        kwargs["empresa"] = _empresa_contexto(self.request)
        kwargs["obra_contexto"] = _obra_contexto(self.request)
        if self.request.method == "GET" and self.request.GET.get("solicitacao"):
            kwargs.setdefault("initial", {})
            kwargs["initial"]["solicitacao"] = self.request.GET.get("solicitacao")
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form") or self.get_form()
        solicitacao = context.get("solicitacao") or _obter_solicitacao_para_cotacao(self.request, form=form)
        itens_solicitacao = context.get("itens_solicitacao")
        context["solicitacao"] = solicitacao
        context["itens_solicitacao"] = itens_solicitacao or (list(solicitacao.itens.select_related("plano_contas").order_by("id")) if solicitacao else [])
        if "fornecedor_formset" not in context:
            fornecedor_queryset = Fornecedor.objects.filter(empresa=_empresa_contexto(self.request)).order_by("razao_social")
            context["fornecedor_formset"] = CotacaoFornecedorComparativoFormSet(
                prefix="fornecedores",
                fornecedor_queryset=fornecedor_queryset,
                solicitacao_itens=context["itens_solicitacao"],
            )
        supplier_rows = []
        for index, fornecedor_form in enumerate(context["fornecedor_formset"].forms):
            item_entries = []
            for item in context["itens_solicitacao"]:
                item_entries.append(
                    {
                        "item": item,
                        "valor_field": fornecedor_form[f"item_{item.pk}_valor_unitario"],
                        "prazo_field": fornecedor_form[f"item_{item.pk}_prazo_entrega_dias"],
                    }
                )
            supplier_rows.append({"index": index, "form": fornecedor_form, "item_entries": item_entries})
        context["supplier_rows"] = supplier_rows
        return context

    def post(self, request, *args, **kwargs):
        _exigir_permissao_aquisicoes(request, "create")
        self.object = None
        form = self.get_form()
        solicitacao = _obter_solicitacao_para_cotacao(request, form=form)
        itens_solicitacao = list(solicitacao.itens.select_related("plano_contas").order_by("id")) if solicitacao else []
        fornecedor_queryset = Fornecedor.objects.filter(empresa=_empresa_contexto(request)).order_by("razao_social")
        fornecedor_formset = CotacaoFornecedorComparativoFormSet(
            request.POST,
            request.FILES,
            prefix="fornecedores",
            fornecedor_queryset=fornecedor_queryset,
            solicitacao_itens=itens_solicitacao,
        )
        empresa_contexto = _empresa_contexto(request)
        if form.is_valid() and fornecedor_formset.is_valid():
            if not empresa_contexto:
                form.add_error(None, "Nao foi possivel identificar a empresa do contexto atual. Selecione novamente a obra e tente outra vez.")
            else:
                cotacao_vencedora = None
                for fornecedor_form in fornecedor_formset.forms:
                    cleaned_data = getattr(fornecedor_form, "cleaned_data", None)
                    if not cleaned_data:
                        continue
                    fornecedor = cleaned_data.get("fornecedor")
                    if not fornecedor:
                        continue
                    escolhido = cleaned_data.get("escolhido", False)
                    cotacao = Cotacao.objects.create(
                        empresa=empresa_contexto,
                        obra=solicitacao.obra,
                        solicitacao=solicitacao,
                        fornecedor=fornecedor,
                        numero="",
                        status="EM_ANALISE" if escolhido else "REJEITADA",
                        data_cotacao=form.cleaned_data["data_cotacao"],
                        validade_ate=form.cleaned_data.get("validade_ate"),
                        observacoes=form.cleaned_data.get("observacoes", ""),
                        justificativa_escolha=form.cleaned_data["justificativa_escolha"] if escolhido else "",
                        criado_por=request.user,
                    )
                    for item in itens_solicitacao:
                        CotacaoItem.objects.create(
                            cotacao=cotacao,
                            item_solicitacao=item,
                            valor_unitario=cleaned_data.get(f"item_{item.pk}_valor_unitario"),
                            prazo_entrega_dias=cleaned_data.get(f"item_{item.pk}_prazo_entrega_dias") or 0,
                        )
                    arquivo = cleaned_data.get("anexo_arquivo")
                    descricao = cleaned_data.get("anexo_descricao", "")
                    if arquivo:
                        cotacao.anexos.create(descricao=descricao, arquivo=arquivo)
                    if escolhido:
                        cotacao_vencedora = cotacao
                messages.success(request, "Cotacoes registradas com sucesso para os fornecedores comparados.")
                if cotacao_vencedora:
                    return redirect("cotacao_detail", pk=cotacao_vencedora.pk)
                return redirect("solicitacao_compra_detail", pk=solicitacao.pk)
        return self.render_to_response(
            self.get_context_data(
                form=form,
                solicitacao=solicitacao,
                itens_solicitacao=itens_solicitacao,
                fornecedor_formset=fornecedor_formset,
            )
        )


class CotacaoDetailView(LoginRequiredMixin, DetailView):
    model = Cotacao
    template_name = "app/cotacao_detail.html"
    context_object_name = "cotacao"

    def get_queryset(self):
        _exigir_permissao_aquisicoes(self.request, "view")
        return _get_queryset_cotacao(self.request).prefetch_related(
            "itens__item_solicitacao__plano_contas",
            "anexos",
            "ordens_compra__compromisso_relacionado",
            "solicitacao__cotacoes__fornecedor",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        comparativas = self.object.solicitacao.cotacoes.select_related("fornecedor").prefetch_related(
            "ordens_compra__compromisso_relacionado"
        ).order_by("fornecedor__razao_social", "id")
        context["cotacoes_comparativas"] = comparativas
        context["total_fornecedores_comparados"] = comparativas.values_list("fornecedor_id", flat=True).distinct().count()
        context["compromisso_resultante"] = self.object.ordens_compra.select_related("compromisso_relacionado").first()
        context["workflow_form"] = OrdemCompraWorkflowForm(initial={"descricao": self.object.solicitacao.titulo})
        context.update(_obter_alcada_contexto(self.request.user, self.object.valor_total))
        context["workflow_events"] = _auditoria_fluxo(self.object)
        context["pode_enviar_para_aprovacao"] = usuario_tem_permissao_modulo(self.request.user, "compras", "create")
        context["pode_aprovar"] = usuario_tem_permissao_modulo(self.request.user, "compras", "approve")
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        acao = request.POST.get("acao")
        if acao == "enviar_para_aprovacao":
            _exigir_permissao_aquisicoes(request, "create")
            _enviar_documento_para_aprovacao(
                request,
                self.object,
                status_em_aprovacao="EM_APROVACAO",
                descricao=f"{self.object.numero} enviada para aprovacao.",
            )
            return redirect("cotacao_detail", pk=self.object.pk)
        if acao == "aprovar":
            _exigir_permissao_aquisicoes(request, "approve")
            _aprovar_documento(
                request,
                self.object,
                valor=self.object.valor_total,
                status_aprovado="APROVADA",
                descricao=f"{self.object.numero} aprovada.",
            )
            return redirect("cotacao_detail", pk=self.object.pk)
        if acao == "retornar_para_ajuste":
            _exigir_permissao_aquisicoes(request, "approve")
            _retornar_documento_para_ajuste(
                request,
                self.object,
                valor=self.object.valor_total,
                status_ajuste="RASCUNHO",
                descricao=f"{self.object.numero} devolvida para ajuste.",
            )
            return redirect("cotacao_detail", pk=self.object.pk)
        if acao == "emitir_oc":
            _exigir_permissao_aquisicoes(request, "approve")
            form = OrdemCompraWorkflowForm(request.POST)
            if form.is_valid():
                try:
                    ordem = AquisicoesService.emitir_ordem_compra(
                        self.object,
                        request.user,
                        form.cleaned_data.get("descricao", ""),
                        form.cleaned_data.get("tipo_resultado", "PEDIDO_COMPRA"),
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect("cotacao_detail", pk=self.object.pk)
                messages.success(request, "Ordem de compra gerada com sucesso a partir da cotacao.")
                return redirect("ordem_compra_detail", pk=ordem.pk)
        return redirect("cotacao_detail", pk=self.object.pk)


class OrdemCompraListView(LoginRequiredMixin, ListView):
    model = OrdemCompra
    template_name = "app/ordem_compra_list.html"
    context_object_name = "ordens_compra"

    def get_queryset(self):
        _exigir_permissao_aquisicoes(self.request, "view")
        return _get_queryset_ordem_compra(self.request).order_by("-data_emissao", "-id")


class OrdemCompraDetailView(LoginRequiredMixin, DetailView):
    model = OrdemCompra
    template_name = "app/ordem_compra_detail.html"
    context_object_name = "ordem_compra"

    def get_queryset(self):
        _exigir_permissao_aquisicoes(self.request, "view")
        return _get_queryset_ordem_compra(self.request).prefetch_related("itens__plano_contas")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_obter_alcada_contexto(self.request.user, self.object.valor_total))
        context["workflow_events"] = _auditoria_fluxo(self.object)
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        acao = request.POST.get("acao")
        if acao == "enviar_para_aprovacao":
            _exigir_permissao_aquisicoes(request, "create")
            _enviar_documento_para_aprovacao(
                request,
                self.object,
                status_em_aprovacao="EM_APROVACAO",
                descricao=f"{self.object.numero} enviada para aprovacao.",
            )
            return redirect("ordem_compra_detail", pk=self.object.pk)
        if acao == "aprovar":
            _exigir_permissao_aquisicoes(request, "approve")
            _aprovar_documento(
                request,
                self.object,
                valor=self.object.valor_total,
                status_aprovado="APROVADA",
                descricao=f"{self.object.numero} aprovada.",
            )
            return redirect("ordem_compra_detail", pk=self.object.pk)
        if acao == "retornar_para_ajuste":
            _exigir_permissao_aquisicoes(request, "approve")
            _retornar_documento_para_ajuste(
                request,
                self.object,
                valor=self.object.valor_total,
                status_ajuste="RASCUNHO",
                descricao=f"{self.object.numero} devolvida para ajuste.",
            )
            return redirect("ordem_compra_detail", pk=self.object.pk)
        return redirect("ordem_compra_detail", pk=self.object.pk)


def solicitacao_compra_pdf_view(request, pk):
    if not request.user.is_authenticated:
        raise Http404()
    solicitacao = get_object_or_404(
        _get_queryset_solicitacao(request).prefetch_related("itens__plano_contas", "cotacoes__fornecedor", "ordens_compra__compromisso_relacionado"),
        pk=pk,
    )
    resumo = {
        "Numero": solicitacao.numero,
        "Obra": f"{solicitacao.obra.codigo} - {solicitacao.obra.nome}",
        "Status": solicitacao.get_status_display(),
        "Solicitante": solicitacao.solicitante,
        "Data": solicitacao.data_solicitacao.strftime("%d/%m/%Y"),
        "Titulo": solicitacao.titulo,
        "Descricao": solicitacao.descricao or "-",
        "Enviado para aprovacao": str(solicitacao.enviado_para_aprovacao_por) if solicitacao.enviado_para_aprovacao_por else "-",
        "Aprovado por": str(solicitacao.aprovado_por) if solicitacao.aprovado_por else "-",
        "Parecer": solicitacao.parecer_aprovacao or "-",
    }
    extras = [
        {
            "Centro de Custo": f"{item.plano_contas.codigo} - {item.plano_contas.descricao}",
            "Descricao Tecnica": item.descricao_tecnica or "-",
            "Unidade": item.unidade or "-",
            "Quantidade": item.quantidade,
        }
        for item in solicitacao.itens.all()
    ]
    return _pdf_relatorio_probatorio_response(
        f"{solicitacao.numero}.pdf",
        f"Solicitacao de Compra {solicitacao.numero}",
        resumo,
        _historico_pdf(solicitacao),
        extras,
        extras_titulo="Itens da Solicitacao",
        extras_colunas=[("Centro de Custo", 170), ("Descricao Tecnica", 210), ("Unidade", 45), ("Quantidade", 70)],
        incluir_historico=True,
    )


def cotacao_pdf_view(request, pk):
    if not request.user.is_authenticated:
        raise Http404()
    cotacao = get_object_or_404(
        _get_queryset_cotacao(request).prefetch_related("itens__item_solicitacao__plano_contas", "solicitacao__cotacoes__fornecedor", "anexos"),
        pk=pk,
    )
    resumo = {
        "Numero": cotacao.numero,
        "Solicitacao": cotacao.solicitacao.numero,
        "Fornecedor": cotacao.fornecedor,
        "Status": cotacao.get_status_display(),
        "Data": cotacao.data_cotacao.strftime("%d/%m/%Y"),
        "Validade": cotacao.validade_ate.strftime("%d/%m/%Y") if cotacao.validade_ate else "-",
        "Justificativa": cotacao.justificativa_escolha or "-",
        "Enviado para aprovacao": str(cotacao.enviado_para_aprovacao_por) if cotacao.enviado_para_aprovacao_por else "-",
        "Aprovado por": str(cotacao.aprovado_por) if cotacao.aprovado_por else "-",
        "Parecer": cotacao.parecer_aprovacao or "-",
    }
    extras = [
        {
            "Centro de Custo": f"{item.item_solicitacao.plano_contas.codigo} - {item.item_solicitacao.plano_contas.descricao}",
            "Descricao Tecnica": item.item_solicitacao.descricao_tecnica or "-",
            "Quantidade": item.item_solicitacao.quantidade,
            "Valor Unitario": money_br(item.valor_unitario),
            "Valor Total": money_br(item.valor_total),
        }
        for item in cotacao.itens.all()
    ]
    return _pdf_relatorio_probatorio_response(
        f"{cotacao.numero}.pdf",
        f"Cotacao {cotacao.numero}",
        resumo,
        _historico_pdf(cotacao),
        extras,
        extras_titulo="Itens Cotados",
        extras_colunas=[("Centro de Custo", 150), ("Descricao Tecnica", 165), ("Quantidade", 55), ("Valor Unitario", 60), ("Valor Total", 65)],
        incluir_historico=True,
    )


def solicitacao_compra_export_view(request):
    queryset = _get_queryset_solicitacao(request).prefetch_related("itens__plano_contas").order_by("-data_solicitacao", "-id")
    linhas = [
        {
            "Numero": solicitacao.numero,
            "Obra": solicitacao.obra.codigo,
            "Titulo": solicitacao.titulo,
            "Solicitante": solicitacao.solicitante,
            "Status": solicitacao.get_status_display(),
            "Data": solicitacao.data_solicitacao.strftime("%d/%m/%Y"),
            "Aprovador": solicitacao.aprovado_por.username if solicitacao.aprovado_por else "",
            "Parecer": solicitacao.parecer_aprovacao,
            "Itens": " | ".join(f"{item.plano_contas.codigo} - {item.descricao_tecnica}" for item in solicitacao.itens.all()),
        }
        for solicitacao in queryset
    ]
    return _exportar_excel_response("solicitacoes_compra.xlsx", "Solicitacoes", linhas)


def solicitacao_compra_lista_pdf_view(request):
    queryset = _get_queryset_solicitacao(request).order_by("-data_solicitacao", "-id")
    resumo = {"Quantidade de Registros": queryset.count(), "Emitido em": _datahora_local(timezone.now()).strftime("%d/%m/%Y %H:%M")}
    extras = [
        {
            "Numero": solicitacao.numero,
            "Obra": solicitacao.obra.codigo,
            "Titulo": solicitacao.titulo,
            "Status": solicitacao.get_status_display(),
            "Data": solicitacao.data_solicitacao.strftime("%d/%m/%Y"),
        }
        for solicitacao in queryset
    ]
    return _pdf_relatorio_probatorio_response(
        "solicitacoes_compra_lista.pdf",
        "Lista de Solicitacoes de Compra",
        resumo,
        [],
        extras,
        extras_titulo="Lista de Solicitacoes",
        extras_colunas=[("Numero", 75), ("Obra", 55), ("Titulo", 200), ("Status", 80), ("Data", 85)],
        incluir_historico=False,
    )


def cotacao_export_view(request):
    queryset = _get_queryset_cotacao(request).order_by("-data_cotacao", "-id")
    linhas = [
        {
            "Numero": cotacao.numero,
            "Solicitacao": cotacao.solicitacao.numero,
            "Fornecedor": cotacao.fornecedor,
            "Status": cotacao.get_status_display(),
            "Data": cotacao.data_cotacao.strftime("%d/%m/%Y"),
            "Valor Total": cotacao.valor_total,
            "Aprovador": cotacao.aprovado_por.username if cotacao.aprovado_por else "",
            "Parecer": cotacao.parecer_aprovacao,
        }
        for cotacao in queryset
    ]
    return _exportar_excel_response("cotacoes.xlsx", "Cotacoes", linhas)


def cotacao_lista_pdf_view(request):
    queryset = _get_queryset_cotacao(request).order_by("-data_cotacao", "-id")
    resumo = {"Quantidade de Registros": queryset.count(), "Emitido em": _datahora_local(timezone.now()).strftime("%d/%m/%Y %H:%M")}
    extras = [
        {
            "Numero": cotacao.numero,
            "Solicitacao": cotacao.solicitacao.numero,
            "Fornecedor": cotacao.fornecedor,
            "Status": cotacao.get_status_display(),
            "Valor Total": money_br(cotacao.valor_total),
        }
        for cotacao in queryset
    ]
    return _pdf_relatorio_probatorio_response(
        "cotacoes_lista.pdf",
        "Lista de Cotacoes",
        resumo,
        [],
        extras,
        extras_titulo="Lista de Cotacoes",
        extras_colunas=[("Numero", 80), ("Solicitacao", 80), ("Fornecedor", 190), ("Status", 75), ("Valor Total", 70)],
        incluir_historico=False,
    )
