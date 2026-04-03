from decimal import Decimal

from django import forms
from django.db.models import Sum
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
    PlanoContas,
)
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
from .services import validar_rateio_nota


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


class PlanoContasChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        prefixo = "   " * getattr(obj, "level", 0)
        unidade = f" [{obj.unidade}]" if obj.unidade else ""
        return f"{prefixo}{obj.codigo} - {obj.descricao}{unidade}"


class PlanoContasForm(forms.ModelForm):
    class Meta:
        model = PlanoContas
        fields = ["descricao", "unidade", "quantidade", "valor_unitario"]


class ObraForm(forms.ModelForm):
    class Meta:
        model = Obra
        fields = ["codigo", "nome", "cliente", "responsavel", "status", "data_inicio", "data_fim", "descricao"]
        widgets = {
            "data_inicio": forms.DateInput(attrs={"type": "date"}),
            "data_fim": forms.DateInput(attrs={"type": "date"}),
        }


class AnexoOperacionalForm(forms.ModelForm):
    class Meta:
        model = AnexoOperacional
        fields = ["descricao", "arquivo"]


class CompromissoForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        self.fields["centro_custo"].widget = forms.HiddenInput()
        for field_name in ["obra", "torre", "bloco", "etapa", "status"]:
            self.fields[field_name].required = False
        if not self.instance.pk:
            self.fields["status"].initial = "RASCUNHO"
            if obra_contexto:
                self.fields["obra"].initial = obra_contexto.pk

    class Meta:
        model = Compromisso
        fields = [
            "tipo",
            "obra",
            "torre",
            "bloco",
            "etapa",
            "status",
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
        cleaned_data["status"] = cleaned_data.get("status") or "RASCUNHO"
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


class AditivoContratoForm(forms.ModelForm):
    class Meta:
        model = AditivoContrato
        fields = ["tipo", "descricao", "delta_dias"]

        widgets = {
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


class MedicaoForm(forms.ModelForm):
    fornecedor_info = forms.CharField(required=False, disabled=True, label="Fornecedor")
    cnpj_info = forms.CharField(required=False, disabled=True, label="CNPJ")
    responsavel_info = forms.CharField(required=False, disabled=True, label="Nome")

    def __init__(self, *args, **kwargs):
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        self.fields["contrato"].queryset = Compromisso.objects.filter(tipo="CONTRATO").order_by("numero")
        if obra_contexto:
            self.fields["contrato"].queryset = self.fields["contrato"].queryset.filter(obra=obra_contexto)
        for field_name in ["obra", "torre", "bloco", "etapa", "status"]:
            self.fields[field_name].required = False
        if not self.instance.pk:
            self.fields["status"].initial = "EM_ELABORACAO"
            if obra_contexto:
                self.fields["obra"].initial = obra_contexto.pk
        contrato = self.instance.contrato if getattr(self.instance, "contrato_id", None) else None
        if contrato:
            self.fields["fornecedor_info"].initial = contrato.fornecedor
            self.fields["cnpj_info"].initial = contrato.cnpj
            self.fields["responsavel_info"].initial = contrato.responsavel

    class Meta:
        model = Medicao
        fields = [
            "contrato",
            "obra",
            "torre",
            "bloco",
            "etapa",
            "status",
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

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["status"] = cleaned_data.get("status") or "EM_ELABORACAO"
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


class NotaFiscalForm(forms.ModelForm):
    origem_info = forms.CharField(required=False, disabled=True, label="Origem Selecionada")

    def __init__(self, *args, **kwargs):
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        self.fields["medicao"].queryset = Medicao.objects.order_by("numero_da_medicao")
        self.fields["pedido_compra"].queryset = Compromisso.objects.filter(tipo="PEDIDO_COMPRA").order_by("numero")
        if obra_contexto:
            self.fields["medicao"].queryset = self.fields["medicao"].queryset.filter(obra=obra_contexto)
            self.fields["pedido_compra"].queryset = self.fields["pedido_compra"].queryset.filter(obra=obra_contexto)
        for field_name in ["obra", "torre", "bloco", "etapa", "status"]:
            self.fields[field_name].required = False
        if not self.instance.pk:
            self.fields["status"].initial = "LANCADA"
            if obra_contexto:
                self.fields["obra"].initial = obra_contexto.pk
        if getattr(self.instance, "medicao_id", None):
            self.fields["origem_info"].initial = str(self.instance.medicao)
        elif getattr(self.instance, "pedido_compra_id", None):
            self.fields["origem_info"].initial = self.instance.pedido_compra.numero

    class Meta:
        model = NotaFiscal
        fields = [
            "numero",
            "serie",
            "tipo",
            "obra",
            "torre",
            "bloco",
            "etapa",
            "status",
            "data_emissao",
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
        }

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["status"] = cleaned_data.get("status") or "LANCADA"
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


class DocumentoForm(forms.ModelForm):
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
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields['obra'].queryset = empresa.obras.all()
            self.fields['plano_contas'].queryset = PlanoContas.objects.filter(obra__empresa=empresa)
        else:
            self.fields['obra'].queryset = Obra.objects.none()
            self.fields['plano_contas'].queryset = PlanoContas.objects.none()


class DocumentoRevisaoForm(forms.ModelForm):
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
        ('ENVIAR_REVISAO', 'Enviar para Revisão'),
        ('APROVAR', 'Aprovar Documento'),
        ('REJEITAR', 'Rejeitar'),
        ('TORNAR_OBSOLETO', 'Tornar Obsoleto'),
    ])
    parecer = forms.CharField(widget=forms.Textarea(attrs={'rows': '3'}), required=False)


class NaoConformidadeForm(forms.ModelForm):
    class Meta:
        model = NaoConformidade
        fields = [
            "obra",
            "plano_contas",
            "descricao",
            "causa",
            "acao_corretiva",
            "responsavel",
            "status",
        ]

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        obra_contexto = kwargs.pop("obra_contexto", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["obra"].queryset = Obra.objects.filter(empresa=empresa).order_by("codigo")
        else:
            self.fields["obra"].queryset = Obra.objects.none()
        if obra_contexto:
            self.fields["obra"].initial = obra_contexto
            self.fields["plano_contas"].queryset = PlanoContas.objects.filter(obra=obra_contexto).order_by("tree_id", "lft")
        else:
            self.fields["plano_contas"].queryset = PlanoContas.objects.none()


class FornecedorForm(forms.ModelForm):
    class Meta:
        model = Fornecedor
        fields = ["razao_social", "nome_fantasia", "cnpj", "contato", "telefone", "email", "ativo"]


class SolicitacaoCompraForm(forms.ModelForm):
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
 

class SolicitacaoCompraItemForm(forms.ModelForm):
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


class CotacaoForm(forms.ModelForm):
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


class CotacaoComparativaForm(forms.Form):
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
    fornecedor = forms.ModelChoiceField(queryset=Fornecedor.objects.none(), required=False)
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
            fornecedor = form.cleaned_data.get("fornecedor")
            if not fornecedor:
                continue
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


class CotacaoAnexoForm(forms.ModelForm):
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
