from decimal import Decimal
import unicodedata

from django import forms
from django.db.models import Q, Sum
from django.forms import BaseInlineFormSet, BaseFormSet, formset_factory, inlineformset_factory

from .domain import validar_itens_compromisso_orcamento, validar_itens_medicao_contrato
from .models import (
    AnexoOperacional,
    AditivoContrato,
    AditivoContratoItem,
    Compromisso,
    CompromissoItem,
    Medicao,
    MedicaoItem,
    NotaFiscal,
    NotaFiscalCentroCusto,
    Obra,
    ParametroComunicacaoEmpresa,
    PlanoContas,
    ReuniaoComunicacao,
    ItemPautaReuniao,
)
from .models_planejamento import PlanoFisicoItem
from .models_aquisicoes import (
    Cotacao,
    CotacaoAnexo,
    CotacaoItem,
    Fornecedor,
    OrdemCompra,
    SolicitacaoCompra,
    SolicitacaoCompraItem,
)
from .models_qualidade import NaoConformidade
from .permissions import filtrar_obras_liberadas_para_lancamento, obra_em_somente_leitura
from .services import validar_rateio_nota
from .text_normalization import normalizar_texto_cadastral


def obter_plano_contas_completo(obra=None):
    queryset = PlanoContas.objects.order_by("tree_id", "lft")
    if obra:
        queryset = queryset.filter(obra=obra)
    return queryset


def obter_centros_do_contrato(contrato):
    if not contrato:
        return PlanoContas.objects.none()

    centros_ids = list(contrato.itens.values_list("centro_custo_id", flat=True).distinct())
    if centros_ids:
        return PlanoContas.objects.filter(pk__in=centros_ids).order_by("tree_id", "lft")

    if contrato.centro_custo_id:
        return PlanoContas.objects.filter(pk=contrato.centro_custo_id)

    return PlanoContas.objects.none()


def obter_centros_da_origem_nota(nota=None, pedido=None, medicao=None, obra=None):
    origem_pedido = pedido or getattr(nota, "pedido_compra", None)
    origem_medicao = medicao or getattr(nota, "medicao", None)

    if origem_pedido:
        return obter_centros_do_contrato(origem_pedido)
    if origem_medicao:
        centros_ids = list(origem_medicao.itens.values_list("centro_custo_id", flat=True).distinct())
        if centros_ids:
            return PlanoContas.objects.filter(pk__in=centros_ids).order_by("tree_id", "lft")
        if origem_medicao.centro_custo_id:
            return PlanoContas.objects.filter(pk=origem_medicao.centro_custo_id)
    if obra:
        return PlanoContas.objects.filter(obra=obra).order_by("tree_id", "lft")
    return PlanoContas.objects.none()


class NormalizeTextFieldsMixin:
    text_fields_to_normalize = ()

    def clean(self):
        cleaned_data = super().clean()
        for field_name in self.text_fields_to_normalize:
            value = cleaned_data.get(field_name)
            if isinstance(value, str):
                cleaned_data[field_name] = normalizar_texto_cadastral(value)
        return cleaned_data


def _normalizar_tipo_nota(valor):
    if not valor:
        return valor
    valor_normalizado = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii")
    valor_normalizado = valor_normalizado.upper().strip()
    if valor_normalizado in {"SERVICO", "NOTA DE SERVICO", "NOTA FISCAL DE SERVICO"}:
        return "SERVICO"
    if valor_normalizado in {"MATERIAL", "NOTA DE MATERIAL", "NOTA FISCAL DE MATERIAL"}:
        return "MATERIAL"
    return valor_normalizado


class PlanoContasChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        prefixo = "   " * getattr(obj, "level", 0)
        unidade = f" [{obj.unidade}]" if obj.unidade else ""
        return f"{prefixo}{obj.codigo} - {obj.descricao}{unidade}"


class FornecedorRazaoSocialChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.razao_social


class PlanoFisicoItemForm(forms.ModelForm):
    class Meta:
        model = PlanoFisicoItem
        fields = ["data_inicio_real", "data_fim_real", "percentual_concluido", "plano_contas"]
        widgets = {
            "data_inicio_real": forms.DateInput(attrs={"type": "date"}),
            "data_fim_real": forms.DateInput(attrs={"type": "date"}),
            "percentual_concluido": forms.NumberInput(attrs={"min": "0", "max": "100", "step": "0.01"}),
        }
        labels = {
            "data_inicio_real": "Data Início Real",
            "data_fim_real": "Data Fim Real",
            "percentual_concluido": "% Concluído",
            "plano_contas": "Vincular à EAP (Orçamento)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        obra = getattr(getattr(self.instance, "plano", None), "obra", None)
        self.fields["data_inicio_real"].widget.attrs.update({"class": "form-control"})
        self.fields["data_fim_real"].widget.attrs.update({"class": "form-control"})
        self.fields["percentual_concluido"].widget.attrs.update({"class": "form-control"})
        self.fields["plano_contas"].widget.attrs.update({"class": "form-control"})
        self.fields["plano_contas"].required = False
        if obra:
            self.fields["plano_contas"].queryset = PlanoContas.objects.filter(
                obra=obra,
                filhos__isnull=True,
            ).order_by("codigo")
        else:
            self.fields["plano_contas"].queryset = PlanoContas.objects.none()
        if self.instance and self.instance.filhos.exists():
            self.fields["data_inicio_real"].disabled = True
            self.fields["data_fim_real"].disabled = True
            self.fields["percentual_concluido"].disabled = True

    def clean(self):
        cleaned_data = super().clean()
        inicio = cleaned_data.get("data_inicio_real")
        fim = cleaned_data.get("data_fim_real")
        percentual = cleaned_data.get("percentual_concluido")

        if inicio and fim and fim < inicio:
            self.add_error("data_fim_real", "A data de fim real não pode ser anterior à data de início real.")

        if self.instance and self.instance.filhos.exists():
            cleaned_data["data_inicio_real"] = self.instance.data_inicio_real
            cleaned_data["data_fim_real"] = self.instance.data_fim_real
            cleaned_data["percentual_concluido"] = self.instance.percentual_concluido
            self.instance.data_inicio_real = self.instance.data_inicio_real
            self.instance.data_fim_real = self.instance.data_fim_real
            self.instance.percentual_concluido = self.instance.percentual_concluido
        elif percentual is None:
            cleaned_data["percentual_concluido"] = 0

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.filhos.exists():
            percentual = Decimal(str(instance.percentual_concluido or 0))
            percentual = max(Decimal("0.00"), min(percentual, Decimal("100.00")))
            valor_planejado = instance.valor_planejado or Decimal("0.00")
            instance.valor_realizado = (valor_planejado * percentual / Decimal("100.00")).quantize(Decimal("0.01"))
        else:
            instance.valor_realizado = Decimal("0.00")
        if commit:
            instance.save()
        return instance


class PlanoContasForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("descricao", "unidade")

    class Meta:
        model = PlanoContas
        fields = ["descricao", "unidade", "quantidade", "valor_unitario"]


class ObraForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("codigo", "nome", "cliente", "responsavel", "descricao")

    class Meta:
        model = Obra
        fields = ["codigo", "nome", "cliente", "responsavel", "status", "data_inicio", "data_fim", "descricao"]
        widgets = {
            "data_inicio": forms.DateInput(attrs={"type": "date"}),
            "data_fim": forms.DateInput(attrs={"type": "date"}),
        }


class ParametroComunicacaoEmpresaForm(forms.ModelForm):
    class Meta:
        model = ParametroComunicacaoEmpresa
        fields = [
            "frequencia_curto_prazo_dias",
            "frequencia_medio_prazo_dias",
            "frequencia_longo_prazo_dias",
        ]
        labels = {
            "frequencia_curto_prazo_dias": "Curto prazo (dias)",
            "frequencia_medio_prazo_dias": "Medio prazo (dias)",
            "frequencia_longo_prazo_dias": "Longo prazo (dias)",
        }
        help_texts = {
            "frequencia_curto_prazo_dias": "Periodicidade padrao das reunioes de curto prazo.",
            "frequencia_medio_prazo_dias": "Periodicidade padrao das reunioes de medio prazo.",
            "frequencia_longo_prazo_dias": "Periodicidade padrao das reunioes de longo prazo.",
        }
        widgets = {
            "frequencia_curto_prazo_dias": forms.NumberInput(attrs={"min": "1", "class": "form-control"}),
            "frequencia_medio_prazo_dias": forms.NumberInput(attrs={"min": "1", "class": "form-control"}),
            "frequencia_longo_prazo_dias": forms.NumberInput(attrs={"min": "1", "class": "form-control"}),
        }


class ReuniaoComunicacaoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("titulo",)

    class Meta:
        model = ReuniaoComunicacao
        fields = ["tipo_reuniao", "titulo", "data_prevista", "data_realizada"]
        widgets = {
            "data_prevista": forms.DateInput(attrs={"type": "date"}),
            "data_realizada": forms.DateInput(attrs={"type": "date"}),
        }


class ItemPautaReuniaoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("titulo", "descricao", "resposta_o_que", "resposta_quem")

    class Meta:
        model = ItemPautaReuniao
        fields = [
            "ativo",
            "ordem",
            "titulo",
            "descricao",
            "resposta_o_que",
            "resposta_quem",
            "resposta_quando",
        ]
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 2, "class": "js-auto-expand", "data-min-rows": "2"}),
            "resposta_o_que": forms.Textarea(attrs={"rows": 2, "class": "js-auto-expand", "data-min-rows": "2"}),
            "resposta_quem": forms.TextInput(),
            "resposta_quando": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        reuniao = getattr(self.instance, "reuniao", None)
        pauta_bloqueada = getattr(reuniao, "status", None) in {"PAUTA_VALIDADA", "EM_APROVACAO", "APROVADA"}
        if pauta_bloqueada:
            for field_name in ("ativo", "ordem", "titulo", "descricao"):
                self.fields[field_name].disabled = True

        for field_name in ("descricao", "resposta_o_que"):
            css = self.fields[field_name].widget.attrs.get("class", "")
            self.fields[field_name].widget.attrs["class"] = (css + " js-auto-expand").strip()


class ItemPautaManualForm(NormalizeTextFieldsMixin, forms.Form):
    text_fields_to_normalize = ("titulo", "descricao", "resposta_o_que", "resposta_quem")

    categoria = forms.ChoiceField(choices=ItemPautaReuniao.CATEGORIA_CHOICES)
    titulo = forms.CharField(max_length=255, required=False)
    descricao = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "class": "js-auto-expand", "data-min-rows": "2"}),
    )
    resposta_o_que = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "class": "js-auto-expand", "data-min-rows": "2"}),
    )
    resposta_quem = forms.CharField(required=False, max_length=180)
    resposta_quando = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        somente_resposta = kwargs.pop("somente_resposta", False)
        super().__init__(*args, **kwargs)
        if somente_resposta:
            for field in self.fields.values():
                field.disabled = True
                field.required = False

    def clean(self):
        cleaned_data = super().clean()
        titulo = (cleaned_data.get("titulo") or "").strip()
        descricao = (cleaned_data.get("descricao") or "").strip()
        resposta_o_que = (cleaned_data.get("resposta_o_que") or "").strip()
        resposta_quem = (cleaned_data.get("resposta_quem") or "").strip()
        if titulo:
            cleaned_data["titulo"] = normalizar_texto_cadastral(titulo)
        if descricao:
            cleaned_data["descricao"] = normalizar_texto_cadastral(descricao)
        if resposta_o_que:
            cleaned_data["resposta_o_que"] = normalizar_texto_cadastral(resposta_o_que)
        if resposta_quem:
            cleaned_data["resposta_quem"] = normalizar_texto_cadastral(resposta_quem)
        return cleaned_data

    def has_payload(self):
        if any(getattr(field, "disabled", False) for field in self.fields.values()):
            return False
        return bool((self.cleaned_data.get("titulo") or "").strip())


ItemPautaReuniaoFormSet = inlineformset_factory(
    ReuniaoComunicacao,
    ItemPautaReuniao,
    form=ItemPautaReuniaoForm,
    extra=0,
    can_delete=False,
)


class AnexoOperacionalForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("descricao",)

    class Meta:
        model = AnexoOperacional
        fields = ["descricao", "arquivo"]


class CompromissoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("torre", "bloco", "etapa", "descricao", "fornecedor", "cnpj", "responsavel", "telefone")

    def __init__(self, *args, **kwargs):
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        self.fields["centro_custo"].widget = forms.HiddenInput()
        for field_name in ["obra", "torre", "bloco", "etapa"]:
            self.fields[field_name].required = False
        if not self.instance.pk and obra_contexto:
            self.fields["obra"].initial = obra_contexto.pk

    class Meta:
        model = Compromisso
        fields = [
            "tipo",
            "obra",
            "torre",
            "bloco",
            "etapa",
            "centro_custo",
            "descricao",
            "fornecedor",
            "cnpj",
            "responsavel",
            "telefone",
            "data_assinatura",
            "data_prevista_inicio",
            "data_prevista_fim",
        ]
        widgets = {
            "data_assinatura": forms.DateInput(attrs={"type": "date"}),
            "data_prevista_inicio": forms.DateInput(attrs={"type": "date"}),
            "data_prevista_fim": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        return cleaned_data


class CompromissoItemForm(forms.ModelForm):
    centro_custo = PlanoContasChoiceField(queryset=PlanoContas.objects.none())

    class Meta:
        model = CompromissoItem
        fields = ["centro_custo", "unidade", "quantidade", "valor_unitario"]
        widgets = {
            "unidade": forms.TextInput(attrs={"readonly": "readonly"}),
        }

    def __init__(self, *args, **kwargs):
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        self.fields["centro_custo"].queryset = obter_plano_contas_completo(obra_contexto)


class CompromissoItemBaseFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        itens = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            centro = form.cleaned_data.get("centro_custo")
            quantidade = form.cleaned_data.get("quantidade")
            valor_unitario = form.cleaned_data.get("valor_unitario")
            if not centro:
                continue
            itens.append(
                {
                    "centro_custo": centro,
                    "quantidade": quantidade,
                    "valor_unitario": valor_unitario,
                }
            )

        if not itens:
            raise forms.ValidationError("Informe pelo menos um item para a compra ou contratação.")

        validar_itens_compromisso_orcamento(self.instance, itens)


CompromissoItemFormSet = inlineformset_factory(
    Compromisso,
    CompromissoItem,
    form=CompromissoItemForm,
    formset=CompromissoItemBaseFormSet,
    extra=1,
    can_delete=True,
)


class AditivoContratoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("descricao", "motivo_mudanca", "impacto_resumido")

    class Meta:
        model = AditivoContrato
        fields = ["tipo", "descricao", "motivo_mudanca", "impacto_resumido", "delta_dias"]

        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 2}),
            "motivo_mudanca": forms.Textarea(attrs={"rows": 3}),
            "impacto_resumido": forms.TextInput(attrs={"placeholder": "Ex.: impacto financeiro, prazo ou escopo"}),
            "delta_dias": forms.NumberInput(attrs={"step": "1"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo")
        descricao = cleaned_data.get("descricao") or ""
        cleaned_data["descricao"] = descricao.strip()
        if tipo == "PRAZO":
            delta = cleaned_data.get("delta_dias")
            if delta in (None, ""):
                raise forms.ValidationError("Informe delta de dias para aditivo de prazo.")
        return cleaned_data


class AditivoContratoItemForm(forms.ModelForm):
    centro_custo = PlanoContasChoiceField(queryset=PlanoContas.objects.none(), required=False)

    class Meta:
        model = AditivoContratoItem
        fields = ["centro_custo", "valor"]
        widgets = {
            "valor": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        centros_queryset = kwargs.pop("centros_queryset", None)
        super().__init__(*args, **kwargs)
        self.fields["centro_custo"].queryset = centros_queryset or PlanoContas.objects.none()
        # Permite que a linha extra fique vazia.
        self.fields["valor"].required = False


class AditivoContratoItemBaseFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        self.centros_queryset = kwargs.pop("centros_queryset", None)
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.fields["centro_custo"].queryset = self.centros_queryset or PlanoContas.objects.none()

    @property
    def empty_form(self):
        form = super().empty_form
        form.fields["centro_custo"].queryset = self.centros_queryset or PlanoContas.objects.none()
        return form

    def clean(self):
        super().clean()
        contrato = getattr(self.instance, "contrato", None)
        tipo = getattr(self.instance, "tipo", None)

        if not contrato or not tipo:
            return

        valores_novos = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            centro = form.cleaned_data.get("centro_custo")
            if not centro:
                continue
            valor = form.cleaned_data.get("valor")
            if tipo == "PRAZO":
                raise forms.ValidationError("Aditivo de prazo nao possui itens.")
            if valor in (None, ""):
                raise forms.ValidationError("Informe valor para cada centro de custo informado.")
            if valor <= 0:
                raise forms.ValidationError("Valor do aditivo deve ser maior que zero.")
            valores_novos.append(valor)

        if tipo == "PRAZO":
            # Não há itens para prazo.
            return

        if not valores_novos:
            raise forms.ValidationError("Informe pelo menos um item para aditivo de valor/escopo.")

        base_original = contrato.itens.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")
        if base_original <= 0:
            raise forms.ValidationError("Contrato base sem itens para validação do limite do aditivo.")

        existentes_qs = AditivoContratoItem.objects.filter(
            aditivo__contrato=contrato,
            aditivo__tipo__in=["VALOR", "ESCOPO"],
        )
        if getattr(self.instance, "pk", None):
            existentes_qs = existentes_qs.exclude(aditivo=self.instance)

        total_existente = existentes_qs.aggregate(total=Sum("valor"))["total"] or Decimal("0.00")
        total_proposto = total_existente + sum(valores_novos)

        limite = base_original * Decimal("0.75")
        if total_proposto > limite:
            raise forms.ValidationError(
                f"Soma dos aditivos ({total_proposto}) excede 75% do valor original do contrato ({limite})."
            )


AditivoContratoItemFormSet = inlineformset_factory(
    AditivoContrato,
    AditivoContratoItem,
    form=AditivoContratoItemForm,
    formset=AditivoContratoItemBaseFormSet,
    extra=1,
    can_delete=True,
)


class MedicaoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("descricao",)

    fornecedor_info = forms.CharField(required=False, disabled=True, label="Fornecedor")
    cnpj_info = forms.CharField(required=False, disabled=True, label="CNPJ")
    responsavel_info = forms.CharField(required=False, disabled=True, label="Nome")

    def __init__(self, *args, **kwargs):
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        queryset = Compromisso.objects.filter(tipo="CONTRATO", status="APROVADO").order_by("numero")
        if getattr(self.instance, "contrato_id", None):
            queryset = Compromisso.objects.filter(
                Q(tipo="CONTRATO", status="APROVADO") | Q(pk=self.instance.contrato_id)
            ).order_by("numero")
        contrato_postado = self.data.get("contrato") if self.is_bound else None
        if contrato_postado:
            queryset = Compromisso.objects.filter(Q(tipo="CONTRATO", status="APROVADO") | Q(pk=contrato_postado)).order_by(
                "numero"
            )
        self.fields["contrato"].queryset = queryset
        if obra_contexto:
            self.fields["contrato"].queryset = self.fields["contrato"].queryset.filter(obra=obra_contexto)
        contrato = None
        if getattr(self.instance, "contrato_id", None):
            contrato = self.instance.contrato
        elif contrato_postado:
            contrato = self.fields["contrato"].queryset.filter(pk=contrato_postado).first()
        if contrato:
            self.fields["fornecedor_info"].initial = contrato.fornecedor or ""
            self.fields["cnpj_info"].initial = contrato.cnpj or ""
            self.fields["responsavel_info"].initial = contrato.responsavel or ""

    class Meta:
        model = Medicao
        fields = [
            "contrato",
            "fornecedor_info",
            "cnpj_info",
            "responsavel_info",
            "descricao",
            "data_medicao",
            "data_prevista_inicio",
            "data_prevista_fim",
        ]
        widgets = {
            "data_medicao": forms.DateInput(attrs={"type": "date"}),
            "data_prevista_inicio": forms.DateInput(attrs={"type": "date"}),
            "data_prevista_fim": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "data_prevista_inicio": "Data de Início do Período",
            "data_prevista_fim": "Data de Fim do Período",
        }

    def clean(self):
        cleaned_data = super().clean()
        contrato = cleaned_data.get("contrato")
        if contrato and contrato.status != "APROVADO":
            self.add_error("contrato", "Só é possível emitir medição para contratos aprovados.")
        return cleaned_data


class MedicaoItemForm(forms.ModelForm):
    # Em formsets com "extra=1", precisamos permitir linhas vazias
    # (ex.: primeira renderização da linha extra na edição) sem quebrar validação.
    centro_custo = PlanoContasChoiceField(queryset=PlanoContas.objects.none(), required=False)

    class Meta:
        model = MedicaoItem
        fields = ["centro_custo", "unidade", "quantidade", "valor_unitario"]
        widgets = {
            "unidade": forms.TextInput(attrs={"readonly": "readonly"}),
            "valor_unitario": forms.NumberInput(attrs={"readonly": "readonly", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        centros_queryset = kwargs.pop("centros_queryset", None)
        super().__init__(*args, **kwargs)
        self.fields["centro_custo"].queryset = centros_queryset or PlanoContas.objects.none()
        # Permite que linhas vazias da "linha extra" não gerem erro de campo obrigatório.
        self.fields["quantidade"].required = False
        self.fields["valor_unitario"].required = False


class MedicaoItemBaseFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        self.centros_queryset = kwargs.pop("centros_queryset", None)
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.fields["centro_custo"].queryset = self.centros_queryset or PlanoContas.objects.none()

    @property
    def empty_form(self):
        form = super().empty_form
        form.fields["centro_custo"].queryset = self.centros_queryset or PlanoContas.objects.none()
        return form

    def clean(self):
        super().clean()
        itens = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            centro = form.cleaned_data.get("centro_custo")
            quantidade = form.cleaned_data.get("quantidade")
            valor_unitario = form.cleaned_data.get("valor_unitario")

            # Trata a "linha extra" vazia: se não há centro de custo informado,
            # não deve ser salva.
            if not centro:
                # Forca delecao apenas na "linha extra" para nao tentar salvar incompleto.
                if not getattr(form.instance, "pk", None):
                    form.cleaned_data["DELETE"] = True
                continue

            if quantidade in (None, "") or valor_unitario in (None, ""):
                raise forms.ValidationError("Para cada centro de custo informado, informe quantidade e valor unitario.")
            itens.append(
                {
                    "centro_custo": centro,
                    "quantidade": quantidade,
                    "valor_unitario": valor_unitario,
                }
            )

        if not itens:
            raise forms.ValidationError("Informe pelo menos um item para a medição.")

        validar_itens_medicao_contrato(self.instance, itens)

        contrato = getattr(self.instance, "contrato", None) if getattr(self.instance, "contrato_id", None) else None
        if contrato:
            from .domain import calcular_total_item, arredondar_moeda
            from .models import Medicao
            total_novos_itens = sum(
                calcular_total_item(i.get("quantidade"), i.get("valor_unitario")) for i in itens
            )
            medicoes_anteriores = Medicao.objects.filter(contrato=contrato)
            if getattr(self.instance, "pk", None):
                medicoes_anteriores = medicoes_anteriores.exclude(pk=self.instance.pk)
            total_ja_medido = medicoes_anteriores.aggregate(total=Sum("valor_medido"))["total"] or Decimal("0.00")
            saldo_disponivel = arredondar_moeda(contrato.valor_contratado - total_ja_medido)
            if total_novos_itens > saldo_disponivel:
                raise forms.ValidationError(
                    f"O total dos itens ({total_novos_itens}) excede o saldo disponivel do contrato. "
                    f"Saldo disponivel: {saldo_disponivel}"
                )


MedicaoItemFormSet = inlineformset_factory(
    Medicao,
    MedicaoItem,
    form=MedicaoItemForm,
    formset=MedicaoItemBaseFormSet,
    extra=1,
    can_delete=True,
)


class NotaFiscalForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("numero", "status", "fornecedor", "cnpj", "descricao")

    origem_info = forms.CharField(required=False, disabled=True, label="Origem Selecionada")

    def __init__(self, *args, **kwargs):
        obra_contexto = kwargs.pop("obra_contexto", None)
        data = kwargs.get("data")
        if data is not None:
            data = data.copy()
            data["tipo"] = _normalizar_tipo_nota(data.get("tipo"))
            kwargs["data"] = data
        super().__init__(*args, **kwargs)
        medicoes_queryset = Medicao.objects.filter(status="APROVADA").order_by("numero_da_medicao")
        pedidos_queryset = Compromisso.objects.filter(tipo="PEDIDO_COMPRA", status="APROVADO").order_by("numero")
        if getattr(self.instance, "medicao_id", None):
            medicoes_queryset = Medicao.objects.filter(Q(status="APROVADA") | Q(pk=self.instance.medicao_id)).order_by(
                "numero_da_medicao"
            )
        if getattr(self.instance, "pedido_compra_id", None):
            pedidos_queryset = Compromisso.objects.filter(
                Q(tipo="PEDIDO_COMPRA", status="APROVADO") | Q(pk=self.instance.pedido_compra_id)
            ).order_by("numero")
        medicao_postada = self.data.get("medicao") if self.is_bound else None
        if medicao_postada:
            medicoes_queryset = Medicao.objects.filter(Q(status="APROVADA") | Q(pk=medicao_postada)).order_by(
                "numero_da_medicao"
            )
        pedido_postado = self.data.get("pedido_compra") if self.is_bound else None
        if pedido_postado:
            pedidos_queryset = Compromisso.objects.filter(
                Q(tipo="PEDIDO_COMPRA", status="APROVADO") | Q(pk=pedido_postado)
            ).order_by("numero")
        self.fields["medicao"].queryset = medicoes_queryset
        self.fields["pedido_compra"].queryset = pedidos_queryset
        if obra_contexto:
            self.fields["medicao"].queryset = self.fields["medicao"].queryset.filter(obra=obra_contexto)
            self.fields["pedido_compra"].queryset = self.fields["pedido_compra"].queryset.filter(obra=obra_contexto)
        for field_name in ["status"]:
            if field_name in self.fields:
                self.fields[field_name].required = False
        if not self.instance.pk:
            self.fields["status"].initial = "LANCADA"
        if getattr(self.instance, "medicao_id", None):
            self.fields["origem_info"].initial = str(self.instance.medicao)
        elif getattr(self.instance, "pedido_compra_id", None):
            self.fields["origem_info"].initial = self.instance.pedido_compra.numero

    class Meta:
        model = NotaFiscal
        fields = [
            "numero",
            "tipo",
            "status",
            "data_emissao",
            "data_vencimento",
            "pedido_compra",
            "medicao",
            "origem_info",
            "fornecedor",
            "cnpj",
            "descricao",
            "valor_total",
        ]
        widgets = {
            "data_emissao": forms.DateInput(attrs={"type": "date"}),
            "data_vencimento": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["tipo"] = _normalizar_tipo_nota(cleaned_data.get("tipo"))
        cleaned_data["status"] = cleaned_data.get("status") or "LANCADA"
        medicao = cleaned_data.get("medicao")
        pedido = cleaned_data.get("pedido_compra")
        if medicao and medicao.status != "APROVADA":
            self.add_error("medicao", "Só é possível emitir nota fiscal para medições aprovadas.")
        if pedido and pedido.status != "APROVADO":
            self.add_error("pedido_compra", "Só é possível emitir nota fiscal para pedidos aprovados.")
        return cleaned_data


class NotaFiscalCentroCustoForm(forms.ModelForm):
    centro_custo = PlanoContasChoiceField(queryset=PlanoContas.objects.none())

    class Meta:
        model = NotaFiscalCentroCusto
        fields = ["centro_custo", "valor"]

    def __init__(self, *args, **kwargs):
        centros_queryset = kwargs.pop("centros_queryset", None)
        super().__init__(*args, **kwargs)
        self.fields["centro_custo"].queryset = centros_queryset or PlanoContas.objects.none()


class NotaFiscalCentroCustoBaseFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        self.centros_queryset = kwargs.pop("centros_queryset", None)
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.fields["centro_custo"].queryset = self.centros_queryset or PlanoContas.objects.none()

    @property
    def empty_form(self):
        form = super().empty_form
        form.fields["centro_custo"].queryset = self.centros_queryset or PlanoContas.objects.none()
        return form

    def clean(self):
        super().clean()
        itens_rateio = []
        total_rateio = Decimal("0.00")
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            centro = form.cleaned_data.get("centro_custo")
            valor = form.cleaned_data.get("valor") or Decimal("0.00")
            if not centro:
                continue
            total_rateio += valor
            itens_rateio.append((centro, valor))

        if itens_rateio:
            validar_rateio_nota(self.instance, itens_rateio)
            if total_rateio != (self.instance.valor_total or Decimal("0.00")):
                raise forms.ValidationError(
                    "A soma do rateio deve ser exatamente igual ao valor total da nota fiscal."
                )


NotaFiscalCentroCustoFormSet = inlineformset_factory(
    NotaFiscal,
    NotaFiscalCentroCusto,
    form=NotaFiscalCentroCustoForm,
    formset=NotaFiscalCentroCustoBaseFormSet,
    extra=1,
    can_delete=True,
)


# =============================================================================
# FASE 2 - ISO 7.5 CONTROLE DOCUMENTAL - Forms
# =============================================================================

from .models import Documento, DocumentoRevisao


class DocumentoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("processo", "codigo_documento", "titulo")
    arquivo_inicial = forms.FileField(
        required=True,
        widget=forms.FileInput(attrs={"accept": ".pdf,.doc,.docx"}),
        help_text="Anexe a versão inicial do documento.",
        label="Anexo do Documento",
    )

    """Formulário para criação/edição de Documentos Controlados ISO 7.5."""

    class Meta:
        model = Documento
        fields = [
            'empresa', 'obra', 'processo', 'plano_contas',
            'tipo_documento', 'codigo_documento', 'titulo'
        ]
        widgets = {
            'empresa': forms.HiddenInput(),
            'obra': forms.Select(attrs={'class': 'form-select'}),
            'processo': forms.TextInput(attrs={'placeholder': 'Ex: ISO 7.5, ISO 9.1'}),
            'tipo_documento': forms.Select(attrs={'class': 'form-select'}),
            'codigo_documento': forms.TextInput(attrs={'placeholder': 'Código único do documento'}),
            'titulo': forms.TextInput(attrs={'placeholder': 'Título do documento'}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop('empresa', None)
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields['obra'].queryset = filtrar_obras_liberadas_para_lancamento(empresa.obras.all()).order_by("codigo")
            if obra_contexto and obra_contexto.empresa_id == empresa.id:
                self.fields["obra"].initial = obra_contexto
                self.fields["plano_contas"].queryset = PlanoContas.objects.filter(obra=obra_contexto).order_by("tree_id", "lft")
            else:
                self.fields['plano_contas'].queryset = PlanoContas.objects.filter(obra__empresa=empresa).order_by("tree_id", "lft")
        else:
            self.fields['obra'].queryset = Obra.objects.none()
            self.fields['plano_contas'].queryset = PlanoContas.objects.none()
        self.fields["obra"].required = True
        self.fields["codigo_documento"].required = False
        if self.instance and self.instance.pk:
            self.fields["arquivo_inicial"].required = False

    def clean(self):
        cleaned_data = super().clean()
        obra = cleaned_data.get("obra")
        if obra_em_somente_leitura(obra):
            self.add_error("obra", "Obras concluidas ou paralisadas permitem apenas visualizacao.")
        return cleaned_data


class DocumentoRevisaoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("parecer",)

    """Formulário para upload de nova revisão de documento."""

    class Meta:
        model = DocumentoRevisao
        fields = ['arquivo', 'versao', 'parecer']
        widgets = {
            'arquivo': forms.FileInput(attrs={'accept': '.pdf,.doc,.docx'}),
            'versao': forms.NumberInput(attrs={'min': '1', 'step': '1'}),
            'parecer': forms.Textarea(attrs={'rows': '3', 'placeholder': 'Observações sobre esta revisão'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        documento = kwargs.get('instance') and kwargs['instance'].documento
        if documento:
            ultima_versao = documento.revisoes.order_by('-versao').first()
            if ultima_versao:
                self.fields['versao'].initial = ultima_versao.versao + 1


class DocumentoWorkflowForm(forms.Form):
    """Formulário para ações de workflow."""
    acao = forms.ChoiceField(choices=[
        ('ENVIAR_REVISAO', 'Enviar para Validação'),
        ('APROVAR', 'Aprovar Documento'),
        ('DEVOLVER_AJUSTE', 'Devolver para Ajuste'),
        ('TORNAR_OBSOLETO', 'Tornar Obsoleto'),
    ])
    parecer = forms.CharField(widget=forms.Textarea(attrs={'rows': '3'}), required=False)


class NaoConformidadeForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = (
        "descricao",
        "causa",
        "acao_corretiva",
        "evidencia_tratamento",
        "evidencia_encerramento",
        "eficacia_observacao",
    )

    class Meta:
        model = NaoConformidade
        fields = [
            "obra",
            "plano_contas",
            "descricao",
            "causa",
            "acao_corretiva",
            "evidencia_tratamento",
            "evidencia_encerramento",
            "eficacia_observacao",
            "responsavel",
            "status",
            "evidencia_tratamento_anexo",
            "evidencia_encerramento_anexo",
        ]
        widgets = {
            "evidencia_tratamento_anexo": forms.FileInput(),
            "evidencia_encerramento_anexo": forms.FileInput(),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["obra"].queryset = filtrar_obras_liberadas_para_lancamento(
                Obra.objects.filter(empresa=empresa)
            ).order_by("codigo")
        else:
            self.fields["obra"].queryset = Obra.objects.none()
        if obra_contexto:
            self.fields["obra"].initial = obra_contexto
            self.fields["plano_contas"].queryset = PlanoContas.objects.filter(obra=obra_contexto).order_by("tree_id", "lft")
        else:
            self.fields["plano_contas"].queryset = PlanoContas.objects.none()

    def clean(self):
        cleaned_data = super().clean()
        obra = cleaned_data.get("obra")
        status = cleaned_data.get("status")
        if obra_em_somente_leitura(obra):
            self.add_error("obra", "Obras concluidas ou paralisadas permitem apenas visualizacao.")
        if status in {"EM_VERIFICACAO", "ENCERRADA"}:
            if not (cleaned_data.get("evidencia_tratamento") or "").strip():
                self.add_error("evidencia_tratamento", "Informe a evidencia de tratamento antes de enviar para verificacao.")
            if not cleaned_data.get("evidencia_tratamento_anexo"):
                self.add_error("evidencia_tratamento_anexo", "Anexe a comprovacao da evidencia de tratamento.")
        if status == "ENCERRADA":
            if not (cleaned_data.get("evidencia_encerramento") or "").strip():
                self.add_error("evidencia_encerramento", "Informe a evidencia de encerramento antes de encerrar a NC.")
            if not cleaned_data.get("evidencia_encerramento_anexo"):
                self.add_error("evidencia_encerramento_anexo", "Anexe a comprovacao da evidencia de encerramento.")
        return cleaned_data


class FornecedorForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("razao_social", "nome_fantasia", "contato", "telefone", "email")

    class Meta:
        model = Fornecedor
        fields = ["razao_social", "nome_fantasia", "cnpj", "contato", "telefone", "email", "ativo"]

    def __init__(self, *args, **kwargs):
        self.empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)

    def clean_cnpj(self):
        from .models_aquisicoes import Fornecedor

        cnpj = self.cleaned_data.get("cnpj")
        if not cnpj or not self.empresa:
            return cnpj

        qs = Fornecedor.objects.filter(
            empresa=self.empresa,
            cnpj=cnpj,
        )
        # Excluir o próprio objeto em caso de edição
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise forms.ValidationError(
                "Já existe um fornecedor cadastrado com este CNPJ "
                "para a sua empresa."
            )
        return cnpj

class SolicitacaoCompraForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("titulo", "descricao", "status", "observacoes")

    class Meta:
        model = SolicitacaoCompra
        fields = ["titulo", "descricao", "status", "data_solicitacao", "observacoes"]
        widgets = {
            "data_solicitacao": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
 

class SolicitacaoCompraItemForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("descricao_tecnica", "unidade")

    plano_contas = PlanoContasChoiceField(queryset=PlanoContas.objects.none(), label="Centro de Custo")

    class Meta:
        model = SolicitacaoCompraItem
        fields = ["plano_contas", "descricao_tecnica", "unidade", "quantidade"]
        widgets = {
            "descricao_tecnica": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        self.fields["plano_contas"].queryset = obter_plano_contas_completo(obra_contexto)


class SolicitacaoCompraItemBaseFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        self.obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.fields["plano_contas"].queryset = obter_plano_contas_completo(self.obra_contexto)

    @property
    def empty_form(self):
        form = super().empty_form
        form.fields["plano_contas"].queryset = obter_plano_contas_completo(self.obra_contexto)
        return form

    def clean(self):
        super().clean()
        itens_validos = 0
        for form in self.forms:
            if not getattr(form, "cleaned_data", None) or form.cleaned_data.get("DELETE"):
                continue
            centro = form.cleaned_data.get("plano_contas")
            descricao = (form.cleaned_data.get("descricao_tecnica") or "").strip()
            quantidade = form.cleaned_data.get("quantidade")
            if not centro and not descricao and quantidade in (None, ""):
                if not getattr(form.instance, "pk", None):
                    form.cleaned_data["DELETE"] = True
                continue
            if not centro:
                raise forms.ValidationError("Selecione o centro de custo em cada item informado.")
            if not descricao:
                raise forms.ValidationError("Informe a descricao tecnica em cada item da solicitacao.")
            if quantidade in (None, "") or quantidade <= 0:
                raise forms.ValidationError("Informe quantidade maior que zero em cada item da solicitacao.")
            itens_validos += 1
        if not itens_validos:
            raise forms.ValidationError("Informe pelo menos um item na solicitacao de compra.")


SolicitacaoCompraItemFormSet = inlineformset_factory(
    SolicitacaoCompra,
    SolicitacaoCompraItem,
    form=SolicitacaoCompraItemForm,
    formset=SolicitacaoCompraItemBaseFormSet,
    extra=1,
    can_delete=True,
)


class CotacaoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("status", "observacoes", "justificativa_escolha")
    fornecedor = FornecedorRazaoSocialChoiceField(queryset=Fornecedor.objects.none())

    class Meta:
        model = Cotacao
        fields = [
            "solicitacao",
            "fornecedor",
            "status",
            "data_cotacao",
            "validade_ate",
            "observacoes",
            "justificativa_escolha",
        ]
        widgets = {
            "data_cotacao": forms.DateInput(attrs={"type": "date"}),
            "validade_ate": forms.DateInput(attrs={"type": "date"}),
            "justificativa_escolha": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["fornecedor"].queryset = Fornecedor.objects.filter(empresa=empresa).order_by("razao_social")
            solicitacoes = SolicitacaoCompra.objects.filter(empresa=empresa)
            if obra_contexto:
                solicitacoes = solicitacoes.filter(obra=obra_contexto)
            self.fields["solicitacao"].queryset = solicitacoes.order_by("-data_solicitacao", "-id")
        else:
            self.fields["fornecedor"].queryset = Fornecedor.objects.none()
            self.fields["solicitacao"].queryset = SolicitacaoCompra.objects.none()

    def clean_justificativa_escolha(self):
        justificativa = (self.cleaned_data.get("justificativa_escolha") or "").strip()
        if not justificativa:
            raise forms.ValidationError("Informe a justificativa da escolha do fornecedor.")
        return justificativa


class CotacaoComparativaForm(NormalizeTextFieldsMixin, forms.Form):
    text_fields_to_normalize = ("observacoes", "justificativa_escolha")

    solicitacao = forms.ModelChoiceField(queryset=SolicitacaoCompra.objects.none())
    data_cotacao = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    validade_ate = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    observacoes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    justificativa_escolha = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        if empresa:
            solicitacoes = SolicitacaoCompra.objects.filter(empresa=empresa)
            if obra_contexto:
                solicitacoes = solicitacoes.filter(obra=obra_contexto)
            self.fields["solicitacao"].queryset = solicitacoes.order_by("-data_solicitacao", "-id")
        else:
            self.fields["solicitacao"].queryset = SolicitacaoCompra.objects.none()


class CotacaoItemForm(forms.Form):
    item_solicitacao_id = forms.IntegerField(widget=forms.HiddenInput())
    valor_unitario = forms.DecimalField(max_digits=15, decimal_places=2, min_value=Decimal("0.00"))
    prazo_entrega_dias = forms.IntegerField(min_value=0, required=False, initial=0)


class CotacaoItemBaseFormSet(BaseFormSet):
    def __init__(self, *args, **kwargs):
        self.solicitacao = kwargs.pop("solicitacao", None)
        self.solicitacao_itens = list(kwargs.pop("solicitacao_itens", []))
        super().__init__(*args, **kwargs)
        for form, item in zip(self.forms, self.solicitacao_itens):
            form.fields["item_solicitacao_id"].initial = item.pk

    def clean(self):
        super().clean()
        if not self.solicitacao_itens:
            raise forms.ValidationError("Selecione uma solicitacao com itens para registrar a cotacao.")
        if len(self.forms) != len(self.solicitacao_itens):
            raise forms.ValidationError("A quantidade de itens da cotacao nao corresponde a solicitacao selecionada.")
        ids_esperados = {item.pk for item in self.solicitacao_itens}
        ids_recebidos = set()
        for form in self.forms:
            if not getattr(form, "cleaned_data", None):
                continue
            item_id = form.cleaned_data.get("item_solicitacao_id")
            valor_unitario = form.cleaned_data.get("valor_unitario")
            if item_id in ids_recebidos:
                raise forms.ValidationError("Existe item duplicado na cotacao.")
            ids_recebidos.add(item_id)
            if valor_unitario in (None, ""):
                raise forms.ValidationError("Informe o valor unitario para todos os itens da cotacao.")
        if ids_recebidos != ids_esperados:
            raise forms.ValidationError("Todos os itens da solicitacao precisam ser cotados.")


CotacaoItemFormSet = formset_factory(CotacaoItemForm, formset=CotacaoItemBaseFormSet, extra=0)


class CotacaoFornecedorComparativoForm(forms.Form):
    fornecedor = FornecedorRazaoSocialChoiceField(queryset=Fornecedor.objects.none(), required=False)
    escolhido = forms.BooleanField(required=False, label="Fornecedor vencedor")
    anexo_descricao = forms.CharField(required=False)
    anexo_arquivo = forms.FileField(required=False)

    def __init__(self, *args, **kwargs):
        fornecedor_queryset = kwargs.pop("fornecedor_queryset", Fornecedor.objects.none())
        solicitacao_itens = kwargs.pop("solicitacao_itens", [])
        super().__init__(*args, **kwargs)
        self.solicitacao_itens = list(solicitacao_itens)
        self.fields["fornecedor"].queryset = fornecedor_queryset
        for item in self.solicitacao_itens:
            self.fields[f"item_{item.pk}_valor_unitario"] = forms.DecimalField(
                max_digits=15,
                decimal_places=2,
                min_value=Decimal("0.00"),
                required=False,
            )
            self.fields[f"item_{item.pk}_prazo_entrega_dias"] = forms.IntegerField(
                min_value=0,
                required=False,
                initial=0,
            )

    def has_payload(self):
        cleaned_data = getattr(self, "cleaned_data", None) or {}
        if cleaned_data.get("fornecedor") or cleaned_data.get("escolhido") or cleaned_data.get("anexo_arquivo"):
            return True
        if (cleaned_data.get("anexo_descricao") or "").strip():
            return True
        for item in self.solicitacao_itens:
            if cleaned_data.get(f"item_{item.pk}_valor_unitario") not in (None, ""):
                return True
        return False


class CotacaoFornecedorComparativoBaseFormSet(BaseFormSet):
    def __init__(self, *args, **kwargs):
        self.fornecedor_queryset = kwargs.pop("fornecedor_queryset", Fornecedor.objects.none())
        self.solicitacao_itens = kwargs.pop("solicitacao_itens", [])
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["fornecedor_queryset"] = self.fornecedor_queryset
        kwargs["solicitacao_itens"] = self.solicitacao_itens
        return kwargs

    def clean(self):
        super().clean()
        fornecedores = []
        escolhidos = 0
        for form in self.forms:
            if not getattr(form, "cleaned_data", None):
                continue
            if not form.has_payload():
                continue
            fornecedor = form.cleaned_data.get("fornecedor")
            if not fornecedor:
                raise forms.ValidationError("Selecione o fornecedor em cada linha de comparacao preenchida.")
            fornecedores.append(fornecedor.pk)
            if form.cleaned_data.get("escolhido"):
                escolhidos += 1
            for item in self.solicitacao_itens:
                valor = form.cleaned_data.get(f"item_{item.pk}_valor_unitario")
                if valor in (None, ""):
                    raise forms.ValidationError(
                        "Informe o valor unitario de todos os itens para cada fornecedor comparado."
                    )
        if len(fornecedores) < 2:
            raise forms.ValidationError("Informe pelo menos 2 fornecedores para realizar a comparacao.")
        if len(set(fornecedores)) != len(fornecedores):
            raise forms.ValidationError("Nao repita o mesmo fornecedor na comparacao.")
        if escolhidos != 1:
            raise forms.ValidationError("Selecione exatamente 1 fornecedor vencedor na cotacao.")


CotacaoFornecedorComparativoFormSet = formset_factory(
    CotacaoFornecedorComparativoForm,
    formset=CotacaoFornecedorComparativoBaseFormSet,
    extra=4,
)


class CotacaoAnexoForm(NormalizeTextFieldsMixin, forms.ModelForm):
    text_fields_to_normalize = ("descricao",)

    class Meta:
        model = CotacaoAnexo
        fields = ["descricao", "arquivo"]


class CotacaoAnexoBaseFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        for form in self.forms:
            if not getattr(form, "cleaned_data", None) or form.cleaned_data.get("DELETE"):
                continue
            descricao = (form.cleaned_data.get("descricao") or "").strip()
            arquivo = form.cleaned_data.get("arquivo")
            if not descricao and not arquivo and not getattr(form.instance, "pk", None):
                form.cleaned_data["DELETE"] = True


CotacaoAnexoFormSet = inlineformset_factory(
    Cotacao,
    CotacaoAnexo,
    form=CotacaoAnexoForm,
    formset=CotacaoAnexoBaseFormSet,
    extra=1,
    can_delete=True,
)


class OrdemCompraWorkflowForm(forms.Form):
    tipo_resultado = forms.ChoiceField(
        choices=(
            ("PEDIDO_COMPRA", "Pedido de Compra"),
            ("CONTRATO", "Contrato"),
        ),
        initial="PEDIDO_COMPRA",
    )
    descricao = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
