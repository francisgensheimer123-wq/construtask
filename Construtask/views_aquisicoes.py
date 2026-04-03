from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import CreateView, DetailView, ListView

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
from .models import Obra
from .models_aquisicoes import Cotacao, CotacaoItem, Fornecedor, OrdemCompra, SolicitacaoCompra
from .permissions import get_empresa_do_usuario, get_empresa_operacional, get_obra_do_contexto
from .services_aquisicoes import AquisicoesService


def _obra_contexto(request):
    return get_obra_do_contexto(request)


def _empresa_contexto(request):
    return get_empresa_operacional(request)


def _pdf_escape(texto):
    return str(texto).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_simples_response(nome_arquivo, titulo, linhas):
    conteudo = ["BT", "/F1 12 Tf", "50 800 Td", "14 TL"]
    for linha in [titulo, ""] + list(linhas):
        conteudo.append(f"({_pdf_escape(linha)}) Tj")
        conteudo.append("T*")
    conteudo.append("ET")
    stream = "\n".join(conteudo).encode("latin-1", "replace")

    objetos = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n",
        b"4 0 obj << /Length " + str(len(stream)).encode("ascii") + b" >> stream\n" + stream + b"\nendstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for obj in objetos:
        offsets.append(len(pdf))
        pdf += obj
    xref = len(pdf)
    pdf += f"xref\n0 {len(offsets)}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii")
    )

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}"'
    return response


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

    def get_queryset(self):
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
        return _get_queryset_solicitacao(self.request).order_by("-data_solicitacao", "-id")


class SolicitacaoCompraCreateView(LoginRequiredMixin, CreateView):
    model = SolicitacaoCompra
    form_class = SolicitacaoCompraForm
    template_name = "app/solicitacao_compra_form.html"
    success_url = reverse_lazy("solicitacao_compra_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = _empresa_contexto(self.request)
        kwargs["obra_contexto"] = _obra_contexto(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("item_formset", _get_solicitacao_formset(self.request))
        return context

    def post(self, request, *args, **kwargs):
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
        return _get_queryset_solicitacao(self.request).prefetch_related(
            "itens__plano_contas",
            "cotacoes__fornecedor",
            "ordens_compra__compromisso_relacionado",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["compromissos_resultantes"] = [
            ordem.compromisso_relacionado
            for ordem in self.object.ordens_compra.select_related("compromisso_relacionado")
            if ordem.compromisso_relacionado_id
        ]
        return context


class CotacaoListView(LoginRequiredMixin, ListView):
    model = Cotacao
    template_name = "app/cotacao_list.html"
    context_object_name = "cotacoes"

    def get_queryset(self):
        return _get_queryset_cotacao(self.request).order_by("-data_cotacao", "-id")


class CotacaoCreateView(LoginRequiredMixin, CreateView):
    model = Cotacao
    form_class = CotacaoComparativaForm
    template_name = "app/cotacao_form.html"
    success_url = reverse_lazy("cotacao_list")

    def get_form_kwargs(self):
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
                        status="APROVADA" if escolhido else "REJEITADA",
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
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.POST.get("acao") == "emitir_oc":
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
                tipo_label = ordem.compromisso_relacionado.get_tipo_display() if ordem.compromisso_relacionado_id else "registro"
                messages.success(request, f"{tipo_label} gerado com sucesso a partir da cotacao.")
                return redirect("contrato_detail", pk=ordem.compromisso_relacionado.pk)
        return redirect("cotacao_detail", pk=self.object.pk)


class OrdemCompraListView(LoginRequiredMixin, ListView):
    model = OrdemCompra
    template_name = "app/ordem_compra_list.html"
    context_object_name = "ordens_compra"

    def get_queryset(self):
        empresa = get_empresa_do_usuario(self.request.user)
        queryset = OrdemCompra.objects.select_related("obra", "fornecedor", "solicitacao", "cotacao_aprovada", "compromisso_relacionado")
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        obra = _obra_contexto(self.request)
        if obra:
            queryset = queryset.filter(obra=obra)
        return queryset.order_by("-data_emissao", "-id")


class OrdemCompraDetailView(LoginRequiredMixin, DetailView):
    model = OrdemCompra
    template_name = "app/ordem_compra_detail.html"
    context_object_name = "ordem_compra"

    def get_queryset(self):
        empresa = get_empresa_do_usuario(self.request.user)
        queryset = OrdemCompra.objects.select_related("obra", "fornecedor", "solicitacao", "cotacao_aprovada", "compromisso_relacionado")
        if empresa:
            queryset = queryset.filter(empresa=empresa)
        return queryset.prefetch_related("itens__plano_contas")


def solicitacao_compra_pdf_view(request, pk):
    if not request.user.is_authenticated:
        raise Http404()
    solicitacao = get_object_or_404(_get_queryset_solicitacao(request).prefetch_related("itens", "cotacoes__fornecedor", "ordens_compra__compromisso_relacionado"), pk=pk)
    linhas = [
        f"Obra: {solicitacao.obra.codigo} - {solicitacao.obra.nome}",
        f"Status: {solicitacao.get_status_display()}",
        f"Solicitante: {solicitacao.solicitante}",
        f"Data: {solicitacao.data_solicitacao:%d/%m/%Y}",
        f"Descricao: {solicitacao.descricao or '-'}",
        "",
        "Itens:",
    ]
    for item in solicitacao.itens.all():
        linhas.append(
            f"- {item.plano_contas.codigo} | {item.plano_contas.descricao} | {item.descricao_tecnica or '-'} | {item.quantidade} {item.unidade or '-'}"
        )
    linhas.append("")
    linhas.append("Cotacoes vinculadas:")
    for cotacao in solicitacao.cotacoes.all():
        linhas.append(f"- {cotacao.numero} | {cotacao.fornecedor} | {cotacao.get_status_display()}")
    return _pdf_simples_response(f"{solicitacao.numero}.pdf", f"Solicitacao de Compra {solicitacao.numero}", linhas)


def cotacao_pdf_view(request, pk):
    if not request.user.is_authenticated:
        raise Http404()
    cotacao = get_object_or_404(_get_queryset_cotacao(request).prefetch_related("itens__item_solicitacao__plano_contas", "solicitacao__cotacoes__fornecedor", "ordens_compra__compromisso_relacionado"), pk=pk)
    linhas = [
        f"Solicitacao: {cotacao.solicitacao.numero}",
        f"Fornecedor: {cotacao.fornecedor}",
        f"Status: {cotacao.get_status_display()}",
        f"Data: {cotacao.data_cotacao:%d/%m/%Y}",
        f"Validade: {cotacao.validade_ate.strftime('%d/%m/%Y') if cotacao.validade_ate else '-'}",
        f"Justificativa: {cotacao.justificativa_escolha or '-'}",
        "",
        "Comparativo de fornecedores:",
    ]
    for comparativa in cotacao.solicitacao.cotacoes.select_related("fornecedor").all():
        linhas.append(f"- {comparativa.numero} | {comparativa.fornecedor} | {comparativa.get_status_display()}")
    linhas.append("")
    linhas.append("Itens cotados:")
    for item in cotacao.itens.all():
        linhas.append(
            f"- {item.item_solicitacao.plano_contas.codigo} | {item.item_solicitacao.plano_contas.descricao} | {item.item_solicitacao.descricao_tecnica or '-'} | {item.item_solicitacao.quantidade} | {item.valor_unitario} | {item.valor_total}"
        )
    if cotacao.anexos.exists():
        linhas.append("")
        linhas.append("Anexos:")
        for anexo in cotacao.anexos.all():
            linhas.append(f"- {anexo.descricao or '-'}")
    return _pdf_simples_response(f"{cotacao.numero}.pdf", f"Cotacao {cotacao.numero}", linhas)
