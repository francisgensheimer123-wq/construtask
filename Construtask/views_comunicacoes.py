from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.db.models import Max
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView

from .application.comunicacoes import (
    SECOES_PAUTA,
    atualizar_resumo_pauta,
    compilar_ata_reuniao,
    criar_reuniao_com_pauta_automatica,
    periodicidade_reuniao_empresa,
    proxima_data_sugerida_reuniao,
    registrar_historico_reuniao,
    resumo_reunioes_obra,
)
from .audit import AuditService
from .forms import ItemPautaManualForm, ItemPautaReuniaoFormSet, ReuniaoComunicacaoForm
from .models import AuditEvent
from .models_comunicacoes import ParametroComunicacaoEmpresa, ItemPautaReuniao, ReuniaoComunicacao
from .permissions import (
    filtrar_por_empresa,
    filtrar_por_obra_contexto,
    get_empresa_operacional,
    get_obra_do_contexto,
    usuario_tem_permissao_modulo,
)
from .services_aprovacao import can_approve_document, can_submit_for_approval
from .export_helpers import _datahora_local, _exportar_excel_response, _pdf_relatorio_probatorio_response
from .navigation_helpers import _obter_grupos_navegacao
from .pagination import DefaultPaginationMixin


def _exigir_permissao_comunicacoes(request, acao):
    if not usuario_tem_permissao_modulo(request.user, "comunicacoes", acao):
        raise PermissionDenied("Usuario sem permissao para o modulo de comunicacoes.")


def _queryset_reunioes(request):
    empresa = get_empresa_operacional(request)
    queryset = ReuniaoComunicacao.objects.select_related(
        "empresa",
        "obra",
        "criado_por",
        "pauta_validada_por",
        "enviado_para_aprovacao_por",
        "aprovado_por",
    )
    queryset = filtrar_por_empresa(queryset, empresa)
    queryset = filtrar_por_obra_contexto(request, queryset, vazio_quando_sem_obra=True)
    return queryset


def _workflow_events(reuniao):
    return AuditEvent.objects.filter(
        entidade_app=f"{reuniao._meta.app_label}.{reuniao.__class__.__name__}",
        objeto_id=reuniao.pk,
    ).order_by("-timestamp")


def _item_queryset_reuniao(reuniao, *, incluir_inativos=False):
    queryset = reuniao.itens_pauta.order_by("ordem", "id")
    if not incluir_inativos:
        queryset = queryset.filter(ativo=True)
    return queryset


def _construir_secoes_pauta(formset, *, incluir_inativos=False):
    secoes = []
    for categoria, rotulo in SECOES_PAUTA:
        forms_secao = [
            form
            for form in formset.forms
            if form.instance.categoria == categoria and (incluir_inativos or form.instance.ativo)
        ]
        if forms_secao:
            secoes.append({"categoria": categoria, "rotulo": rotulo, "forms": forms_secao})
    return secoes


def _pauta_bloqueada_para_estrutura(reuniao):
    return reuniao.status in {"PAUTA_VALIDADA", "EM_APROVACAO", "APROVADA"}


def _iterar_itens_ativos_por_secao(reuniao):
    itens = list(_item_queryset_reuniao(reuniao))
    for categoria, rotulo in SECOES_PAUTA:
        itens_secao = [item for item in itens if item.categoria == categoria]
        if itens_secao:
            yield categoria, rotulo, itens_secao


def _linhas_exportacao_pauta(reuniao):
    linhas = []
    for _, rotulo, itens_secao in _iterar_itens_ativos_por_secao(reuniao):
        for item in itens_secao:
            linhas.append(
                {
                    "Secao": rotulo,
                    "Origem": item.get_origem_tipo_display(),
                    "Item": item.titulo,
                    "Contexto": item.descricao or "-",
                }
            )
    return linhas


def _linhas_exportacao_ata(reuniao):
    linhas = []
    for _, rotulo, itens_secao in _iterar_itens_ativos_por_secao(reuniao):
        for item in itens_secao:
            linhas.append(
                {
                    "Secao": rotulo,
                    "Origem": item.get_origem_tipo_display(),
                    "Item": item.titulo,
                    "Contexto": item.descricao or "-",
                    "O que sera feito": item.resposta_o_que or "-",
                    "Quem fara": item.resposta_quem or "-",
                    "Quando": item.resposta_quando.strftime("%d/%m/%Y") if item.resposta_quando else "-",
                }
            )
    return linhas


def _linhas_exportacao_historico(reuniao):
    return [
        {
            "Data": _datahora_local(evento.criado_em).strftime("%d/%m/%Y %H:%M") if evento.criado_em else "-",
            "Acao": evento.get_acao_display(),
            "Usuario": getattr(evento.usuario, "username", "-") if evento.usuario else "-",
            "Descricao": evento.observacao or "-",
        }
        for evento in reuniao.historicos.select_related("usuario").all()
    ]


def _resumo_exportacao_reuniao(reuniao, *, tipo_documento):
    return {
        "Documento": tipo_documento,
        "Reuniao": f"{reuniao.numero} - {reuniao.titulo}",
        "Tipo": reuniao.get_tipo_reuniao_display(),
        "Obra": f"{reuniao.obra.codigo} - {reuniao.obra.nome}",
        "Status": reuniao.get_status_display(),
        "Data prevista": reuniao.data_prevista.strftime("%d/%m/%Y") if reuniao.data_prevista else "-",
        "Data realizada": reuniao.data_realizada.strftime("%d/%m/%Y") if reuniao.data_realizada else "-",
        "Itens ativos": reuniao.quantidade_itens_ativos,
    }


class ReuniaoComunicacaoListView(LoginRequiredMixin, DefaultPaginationMixin, ListView):
    model = ReuniaoComunicacao
    template_name = "app/comunicacao_reuniao_list.html"
    context_object_name = "reunioes"

    def get_queryset(self):
        _exigir_permissao_comunicacoes(self.request, "view")
        return _queryset_reunioes(self.request).order_by("-data_prevista", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = get_obra_do_contexto(self.request)
        context["obra_contexto"] = obra_contexto
        context["grupos_navegacao"] = list(_obter_grupos_navegacao().values())
        if obra_contexto:
            parametros = ParametroComunicacaoEmpresa.obter_ou_criar(obra_contexto.empresa)
            context["parametros_comunicacao"] = parametros
            context["resumo_reunioes"] = resumo_reunioes_obra(obra_contexto)
        else:
            context["parametros_comunicacao"] = None
            context["resumo_reunioes"] = {
                "total": 0,
                "rascunhos": 0,
                "pautas_validadas": 0,
                "em_aprovacao": 0,
                "aprovadas": 0,
            }
        context["pode_criar"] = usuario_tem_permissao_modulo(self.request.user, "comunicacoes", "create")
        return context


class ReuniaoComunicacaoCreateView(LoginRequiredMixin, CreateView):
    model = ReuniaoComunicacao
    form_class = ReuniaoComunicacaoForm
    template_name = "app/comunicacao_reuniao_form.html"

    def dispatch(self, request, *args, **kwargs):
        _exigir_permissao_comunicacoes(request, "create")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        obra = get_obra_do_contexto(self.request)
        if obra:
            tipo = self.request.GET.get("tipo_reuniao") or "CURTO_PRAZO"
            initial["tipo_reuniao"] = tipo
            initial["titulo"] = f"Reuniao de {dict(ReuniaoComunicacao.TIPO_REUNIAO_CHOICES).get(tipo, 'Comunicacao')} - {obra.codigo}"
            initial["data_prevista"] = timezone.localdate()
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["obra_contexto"] = get_obra_do_contexto(self.request)
        context["grupos_navegacao"] = list(_obter_grupos_navegacao().values())
        return context

    def form_valid(self, form):
        obra = get_obra_do_contexto(self.request)
        if not obra:
            form.add_error(None, "Selecione uma obra antes de criar uma reuniao.")
            return self.form_invalid(form)
        reuniao = criar_reuniao_com_pauta_automatica(
            obra,
            form.cleaned_data["tipo_reuniao"],
            self.request.user,
            data_prevista=form.cleaned_data.get("data_prevista"),
        )
        reuniao.titulo = form.cleaned_data["titulo"]
        reuniao.data_realizada = form.cleaned_data.get("data_realizada")
        reuniao.periodicidade_dias = periodicidade_reuniao_empresa(obra.empresa, reuniao.tipo_reuniao)
        reuniao.save(update_fields=["titulo", "data_realizada", "periodicidade_dias", "atualizado_em"])
        atualizar_resumo_pauta(reuniao)
        self.object = reuniao
        messages.success(self.request, "Reuniao criada com pauta automatica em rascunho.")
        return redirect("reuniao_comunicacao_detail", pk=reuniao.pk)


class ReuniaoComunicacaoDetailView(LoginRequiredMixin, DetailView):
    model = ReuniaoComunicacao
    template_name = "app/comunicacao_reuniao_detail.html"
    context_object_name = "reuniao"

    def get_queryset(self):
        _exigir_permissao_comunicacoes(self.request, "view")
        return _queryset_reunioes(self.request).prefetch_related("itens_pauta", "historicos__usuario")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["grupos_navegacao"] = list(_obter_grupos_navegacao().values())
        context["reuniao_form"] = kwargs.get("reuniao_form") or ReuniaoComunicacaoForm(instance=self.object)
        item_formset = kwargs.get("item_formset") or ItemPautaReuniaoFormSet(
            instance=self.object,
            prefix="itens",
            queryset=_item_queryset_reuniao(self.object),
        )
        context["item_formset"] = item_formset
        context["secoes_pauta"] = _construir_secoes_pauta(item_formset)
        context["item_manual_form"] = kwargs.get("item_manual_form") or ItemPautaManualForm(
            prefix="manual",
            somente_resposta=_pauta_bloqueada_para_estrutura(self.object),
        )
        context["workflow_events"] = _workflow_events(self.object)
        context["historicos"] = self.object.historicos.select_related("usuario").all()
        context["proxima_data_sugerida"] = proxima_data_sugerida_reuniao(self.object)
        context["pode_atualizar"] = usuario_tem_permissao_modulo(self.request.user, "comunicacoes", "update")
        context["pode_aprovar"] = usuario_tem_permissao_modulo(self.request.user, "comunicacoes", "approve")
        context["estrutura_pauta_bloqueada"] = _pauta_bloqueada_para_estrutura(self.object)
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        acao = (request.POST.get("acao") or "").strip()
        if acao in {"salvar_reuniao", "salvar_pauta", "validar_pauta", "enviar_para_aprovacao"}:
            _exigir_permissao_comunicacoes(request, "update" if acao != "enviar_para_aprovacao" else "create")
        elif acao in {"aprovar", "retornar_para_ajuste"}:
            _exigir_permissao_comunicacoes(request, "approve")

        if acao == "salvar_reuniao":
            return self._salvar_reuniao(request)
        if acao == "salvar_pauta":
            return self._salvar_pauta(request, validar=False)
        if acao == "validar_pauta":
            return self._salvar_pauta(request, validar=True)
        if acao == "enviar_para_aprovacao":
            return self._enviar_para_aprovacao(request)
        if acao == "aprovar":
            return self._aprovar(request)
        if acao == "retornar_para_ajuste":
            return self._retornar_para_ajuste(request)
        messages.error(request, "Acao nao reconhecida.")
        return redirect("reuniao_comunicacao_detail", pk=self.object.pk)

    def _render_invalid(self, reuniao_form=None, item_formset=None, item_manual_form=None):
        context = self.get_context_data(
            reuniao_form=reuniao_form,
            item_formset=item_formset,
            item_manual_form=item_manual_form,
        )
        return self.render_to_response(context)

    def _salvar_reuniao(self, request):
        form = ReuniaoComunicacaoForm(request.POST, instance=self.object)
        if form.is_valid():
            form.save()
            messages.success(request, "Cabecalho da reuniao atualizado.")
            return redirect("reuniao_comunicacao_detail", pk=self.object.pk)
        return self._render_invalid(reuniao_form=form)

    def _salvar_pauta(self, request, *, validar):
        formset = ItemPautaReuniaoFormSet(
            request.POST,
            instance=self.object,
            prefix="itens",
            queryset=_item_queryset_reuniao(self.object, incluir_inativos=False),
        )
        estrutura_bloqueada = _pauta_bloqueada_para_estrutura(self.object)
        manual_form = ItemPautaManualForm(request.POST, prefix="manual", somente_resposta=estrutura_bloqueada)
        reuniao_form = ReuniaoComunicacaoForm(instance=self.object)
        manual_ok = True
        if manual_form.is_bound:
            manual_ok = manual_form.is_valid()
        if formset.is_valid() and manual_ok:
            formset.save()
            if manual_ok and manual_form.has_payload():
                ultima_ordem = self.object.itens_pauta.aggregate(maximo=Max("ordem"))["maximo"] or 0
                ItemPautaReuniao.objects.create(
                    reuniao=self.object,
                    ordem=ultima_ordem + 1,
                    origem_tipo="MANUAL",
                    categoria=manual_form.cleaned_data["categoria"],
                    titulo=manual_form.cleaned_data["titulo"],
                    descricao=manual_form.cleaned_data.get("descricao", ""),
                    resposta_o_que=manual_form.cleaned_data.get("resposta_o_que", ""),
                    resposta_quem=manual_form.cleaned_data.get("resposta_quem", ""),
                    resposta_quando=manual_form.cleaned_data.get("resposta_quando"),
                )
            if validar:
                if estrutura_bloqueada:
                    messages.info(request, "A pauta ja foi validada. Apenas as respostas dos itens foram atualizadas.")
                    atualizar_resumo_pauta(self.object)
                    return redirect("reuniao_comunicacao_detail", pk=self.object.pk)
                self.object.pauta_validada_em = timezone.now()
                self.object.pauta_validada_por = request.user
                self.object.status = "PAUTA_VALIDADA"
                self.object.save(update_fields=["pauta_validada_em", "pauta_validada_por", "status", "atualizado_em"])
                registrar_historico_reuniao(
                    self.object,
                    request.user,
                    "PAUTA_VALIDADA",
                    "Pauta automatica revisada e validada pelo engenheiro da obra.",
                )
                messages.success(request, "Pauta validada com sucesso.")
            else:
                registrar_historico_reuniao(
                    self.object,
                    request.user,
                    "PAUTA_ATUALIZADA",
                    "Itens da pauta atualizados.",
                )
                messages.success(request, "Pauta atualizada com sucesso.")
            atualizar_resumo_pauta(self.object)
            return redirect("reuniao_comunicacao_detail", pk=self.object.pk)
        return self._render_invalid(reuniao_form=reuniao_form, item_formset=formset, item_manual_form=manual_form)

    def _enviar_para_aprovacao(self, request):
        if not can_submit_for_approval(request.user):
            messages.error(request, "Seu perfil nao pode enviar atas para aprovacao.")
            return redirect("reuniao_comunicacao_detail", pk=self.object.pk)
        if not self.object.itens_pauta.filter(ativo=True).exists():
            messages.error(request, "Inclua pelo menos um item ativo na pauta antes de compilar a ata.")
            return redirect("reuniao_comunicacao_detail", pk=self.object.pk)
        self.object.ata_texto = compilar_ata_reuniao(self.object)
        self.object.status = "EM_APROVACAO"
        self.object.enviado_para_aprovacao_em = timezone.now()
        self.object.enviado_para_aprovacao_por = request.user
        self.object.aprovado_em = None
        self.object.aprovado_por = None
        self.object.parecer_aprovacao = (request.POST.get("parecer_aprovacao") or "").strip()
        self.object.save()
        registrar_historico_reuniao(self.object, request.user, "ENVIO_APROVACAO", "Ata compilada e enviada para aprovacao.")
        messages.success(request, "Ata compilada e enviada para aprovacao.")
        return redirect("reuniao_comunicacao_detail", pk=self.object.pk)

    def _aprovar(self, request):
        if not can_approve_document(request.user):
            messages.error(request, "Seu perfil nao possui alcada documental para aprovar a ata.")
            return redirect("reuniao_comunicacao_detail", pk=self.object.pk)
        before = AuditService.instance_to_dict(self.object)
        self.object.status = "APROVADA"
        self.object.aprovado_em = timezone.now()
        self.object.aprovado_por = request.user
        self.object.parecer_aprovacao = (request.POST.get("parecer_aprovacao") or "").strip()
        if not self.object.ata_texto:
            self.object.ata_texto = compilar_ata_reuniao(self.object)
        self.object.save()
        after = AuditService.instance_to_dict(self.object)
        AuditService.log_event(request, "APPROVE", self.object, before, after)
        registrar_historico_reuniao(self.object, request.user, "APROVACAO", "Ata aprovada.")
        messages.success(request, "Ata aprovada com sucesso.")
        return redirect("reuniao_comunicacao_detail", pk=self.object.pk)

    def _retornar_para_ajuste(self, request):
        if not can_approve_document(request.user):
            messages.error(request, "Seu perfil nao possui alcada documental para devolver a ata.")
            return redirect("reuniao_comunicacao_detail", pk=self.object.pk)
        parecer = (request.POST.get("parecer_aprovacao") or "").strip()
        if not parecer:
            messages.error(request, "Informe um parecer para retornar a ata para ajuste.")
            return redirect("reuniao_comunicacao_detail", pk=self.object.pk)
        before = AuditService.instance_to_dict(self.object)
        self.object.status = "RASCUNHO"
        self.object.aprovado_em = None
        self.object.aprovado_por = None
        self.object.pauta_validada_em = None
        self.object.pauta_validada_por = None
        self.object.parecer_aprovacao = parecer
        self.object.save()
        after = AuditService.instance_to_dict(self.object)
        AuditService.log_event(request, "REJECT", self.object, before, after)
        registrar_historico_reuniao(self.object, request.user, "AJUSTE", f"Ata devolvida para ajuste. Parecer: {parecer}")
        messages.success(request, "Ata devolvida para ajuste.")
        return redirect("reuniao_comunicacao_detail", pk=self.object.pk)


@login_required
def reuniao_comunicacao_compilar_ata_view(request, pk):
    if request.method != "POST":
        raise Http404()
    reuniao = get_object_or_404(_queryset_reunioes(request), pk=pk)
    _exigir_permissao_comunicacoes(request, "update")
    reuniao.ata_texto = compilar_ata_reuniao(reuniao)
    reuniao.save(update_fields=["ata_texto", "atualizado_em"])
    messages.success(request, "Ata recompilada com base na pauta atual.")
    return redirect("reuniao_comunicacao_detail", pk=reuniao.pk)


@login_required
def reuniao_comunicacao_pauta_excel_view(request, pk):
    reuniao = get_object_or_404(_queryset_reunioes(request), pk=pk)
    _exigir_permissao_comunicacoes(request, "view")
    linhas = _linhas_exportacao_pauta(reuniao)
    return _exportar_excel_response(
        f"pauta_{reuniao.numero}.xlsx".replace("/", "-"),
        "Pauta de Reuniao",
        linhas or [{"Secao": "-", "Origem": "-", "Item": "-", "Contexto": "-"}],
    )


@login_required
def reuniao_comunicacao_ata_excel_view(request, pk):
    reuniao = get_object_or_404(_queryset_reunioes(request), pk=pk)
    _exigir_permissao_comunicacoes(request, "view")
    linhas = _linhas_exportacao_ata(reuniao)
    return _exportar_excel_response(
        f"ata_{reuniao.numero}.xlsx".replace("/", "-"),
        "Ata de Reuniao",
        linhas or [{"Secao": "-", "Origem": "-", "Item": "-", "Contexto": "-", "O que sera feito": "-", "Quem fara": "-", "Quando": "-"}],
    )


@login_required
def reuniao_comunicacao_pauta_pdf_view(request, pk):
    reuniao = get_object_or_404(_queryset_reunioes(request), pk=pk)
    _exigir_permissao_comunicacoes(request, "view")
    extras = []
    for _, rotulo, itens_secao in _iterar_itens_ativos_por_secao(reuniao):
        extras.append(
            {
                    "titulo": rotulo,
                    "colunas": [
                        ("Item", 160),
                        ("Contexto", 435),
                    ],
                    "linhas": [
                        {
                            "Item": item.titulo,
                            "Contexto": item.descricao or "-",
                        }
                        for item in itens_secao
                    ],
            }
        )
    return _pdf_relatorio_probatorio_response(
        f"pauta_{reuniao.numero}.pdf".replace("/", "-"),
        "Pauta de Reuniao",
        _resumo_exportacao_reuniao(reuniao, tipo_documento="Pauta"),
        _linhas_exportacao_historico(reuniao),
        [],
        extras_titulo="Itens da Pauta",
        extras_colunas=[("Item", 160), ("Contexto", 435)],
        secoes_extras=extras,
    )


@login_required
def reuniao_comunicacao_ata_pdf_view(request, pk):
    reuniao = get_object_or_404(_queryset_reunioes(request), pk=pk)
    _exigir_permissao_comunicacoes(request, "view")
    if not reuniao.ata_texto:
        reuniao.ata_texto = compilar_ata_reuniao(reuniao)
        reuniao.save(update_fields=["ata_texto", "atualizado_em"])
    extras = [
        {
            "titulo": "Texto Consolidado da Ata",
            "colunas": [("Conteudo", 595)],
            "linhas": [{"Conteudo": linha or " "} for linha in reuniao.ata_texto.splitlines()],
        }
    ]
    return _pdf_relatorio_probatorio_response(
        f"ata_{reuniao.numero}.pdf".replace("/", "-"),
        "Ata de Reuniao",
        _resumo_exportacao_reuniao(reuniao, tipo_documento="Ata"),
        _linhas_exportacao_historico(reuniao),
        [],
        extras_titulo="Ata",
        extras_colunas=[("Conteudo", 595)],
        secoes_extras=extras,
    )
