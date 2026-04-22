from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.forms.models import BaseInlineFormSet
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.formats import number_format
from django.utils.html import format_html
from mptt.admin import DraggableMPTTAdmin

from .models import (
    AuditEvent,
    Compromisso,
    Empresa,
    HistoricoReuniaoComunicacao,
    ItemPautaReuniao,
    Medicao,
    NotaFiscal,
    NotaFiscalCentroCusto,
    ParametroComunicacaoEmpresa,
    PlanoContas,
    ReuniaoComunicacao,
    UserProfile,
    UsuarioEmpresa,
)
from .models_risco import Risco, RiscoHistorico
from .services import importar_plano_contas_excel, obter_dados_contrato, validar_rateio_nota


def _admin_superuser_autorizado(request):
    user = getattr(request, "user", None)
    if not user or not user.is_active or not user.is_superuser:
        return False
    return user.get_username() == getattr(settings, "CONSTRUTASK_ADMIN_SUPERUSER_USERNAME", "Construtask")


admin.site.has_permission = _admin_superuser_autorizado


class ImportExcelForm(forms.Form):
    arquivo = forms.FileField()


@admin.register(PlanoContas)
class PlanoContasAdmin(DraggableMPTTAdmin):
    ordering = ("tree_id", "lft")
    change_list_template = "admin/plano_contas_change_list.html"
    list_select_related = ("parent",)
    list_per_page = 1500

    list_display = (
        "tree_actions",
        "codigo_coluna",
        "descricao_coluna",
        "unidade",
        "quantidade_formatada",
        "valor_unitario_formatado",
        "valor_total_formatado",
        "valor_comprometido",
        "valor_medido",
        "valor_executado",
        "saldo_a_comprometer_formatado",
        "saldo_a_medir_formatado",
    )
    list_display_links = ("codigo_coluna",)

    def eh_analitico(self, obj):
        return obj.level == 5 and not obj.get_children().exists()

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("parent")
            .annotate(filhos_count=Count("filhos"))
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "importar-excel/",
                self.admin_site.admin_view(self.importar_excel),
                name="importar_excel",
            ),
            path(
                "curva_abc/",
                self.admin_site.admin_view(self.curva_abc_view),
                name="curva_abc",
            ),
        ]
        return custom_urls + urls

    def curva_abc_view(self, request):
        obra_contexto_id = request.session.get("obra_contexto_id")
        qs = PlanoContas.objects.all()
        if obra_contexto_id:
            qs = qs.filter(obra_id=obra_contexto_id)

        # Curva ABC deve trazer itens do 5º nível do plano de contas.
        # No mptt, o nível é 0-based (raiz = 0), então 5º nível => level=4.
        qs = (
            qs.filter(level=4)
            .order_by("-codigo")
        )

        # Usa valor consolidado (descendentes) pois níveis intermediários podem não ter valor direto.
        valores = []
        for plano in qs:
            valores.append((plano, plano.valor_total_consolidado or Decimal("0.00")))
        valores.sort(key=lambda t: (t[1], t[0].codigo), reverse=True)

        total_geral = sum((v for _, v in valores), start=Decimal("0.00")) or Decimal("0.00")
        acumulado_perc = Decimal("0.00")
        dados = []

        for plano, valor in valores:
            percentual = (valor / total_geral * Decimal("100")) if total_geral else Decimal("0.00")
            acumulado_perc += percentual

            if acumulado_perc <= Decimal("80.00"):
                classe = "A"
            elif acumulado_perc <= Decimal("95.00"):
                classe = "B"
            else:
                classe = "C"

            dados.append(
                {
                    "codigo": plano.codigo,
                    "descricao": plano.descricao,
                    "valor_total": number_format(valor, 2, use_l10n=True),
                    "percentual": round(float(percentual), 1),
                    "acumulado": round(float(acumulado_perc), 1),
                    "classe": classe,
                }
            )

        context = dict(self.admin_site.each_context(request))
        return render(request, "admin/curva_abc.html", {"dados": dados, **context})

    def importar_excel(self, request):
        if request.method == "POST":
            form = ImportExcelForm(request.POST, request.FILES)
            if form.is_valid():
                arquivo = request.FILES["arquivo"]
                # Obter obra do contexto da sessão
                obra_id = request.session.get("obra_contexto_id")
                obra = None
                if obra_id:
                    from .models import Obra
                    obra = Obra.objects.filter(pk=obra_id).first()
                try:
                    importar_plano_contas_excel(arquivo, obra=obra)
                    self.message_user(request, "Importação concluída.", messages.SUCCESS)
                    return redirect("../")
                except Exception as exc:
                    self.message_user(request, f"Erro: {exc}", messages.ERROR)
        else:
            form = ImportExcelForm()

        return render(
            request,
            "admin/importar_plano_contas.html",
            {"form": form, "title": "Importar Plano de Contas"},
        )

    def codigo_coluna(self, obj):
        if not obj.is_leaf_node():
            return format_html("<b>{}</b>", obj.codigo)
        return obj.codigo

    codigo_coluna.short_description = "CODIGO"
    codigo_coluna.admin_order_field = "codigo"

    def descricao_coluna(self, obj):
        if not obj.is_leaf_node():
            return format_html("<b>{}</b>", obj.descricao)
        return obj.descricao

    descricao_coluna.short_description = "DESCRICAO"

    def quantidade_formatada(self, obj):
        if not self.eh_analitico(obj):
            return ""
        return number_format(obj.quantidade, 2, use_l10n=True)

    quantidade_formatada.short_description = "QTD"

    def valor_unitario_formatado(self, obj):
        if not self.eh_analitico(obj):
            return ""
        return format_html(
            '<div style="text-align:right;">{}</div>',
            number_format(obj.valor_unitario, 2, use_l10n=True),
        )

    valor_unitario_formatado.short_description = "VALOR UNIT."

    def valor_total_formatado(self, obj):
        total = obj.valor_total_consolidado
        valor = number_format(total, 2, use_l10n=True)
        if obj.filhos_count > 0:
            return format_html('<div style="text-align:right;"><strong>{}</strong></div>', valor)
        return format_html('<div style="text-align:right;">{}</div>', valor)

    valor_total_formatado.short_description = "VALOR TOTAL"

    def valor_comprometido(self, obj):
        valor = number_format(obj.valor_comprometido, 2, use_l10n=True)
        if not obj.is_leaf_node():
            return format_html('<div style="text-align:right;"><b>{}</b></div>', valor)
        return format_html('<div style="text-align:right;">{}</div>', valor)

    def valor_medido(self, obj):
        valor = number_format(obj.valor_medido or Decimal("0.00"), 2, use_l10n=True)
        if obj.get_children().exists():
            return format_html('<div style="text-align:right;font-weight:bold;">{}</div>', valor)
        return format_html('<div style="text-align:right;">{}</div>', valor)

    def valor_executado(self, obj):
        valor = number_format(obj.valor_executado or Decimal("0.00"), 2, use_l10n=True)
        if obj.get_children().exists():
            return format_html('<div style="text-align:right;font-weight:bold;">{}</div>', valor)
        return format_html('<div style="text-align:right;">{}</div>', valor)

    valor_executado.short_description = "Valor Executado"

    def saldo_a_comprometer_formatado(self, obj):
        valor = number_format(obj.saldo_a_comprometer, 2, use_l10n=True)
        if not obj.is_leaf_node():
            return format_html('<div style="text-align:right;"><strong>{}</strong></div>', valor)
        return format_html('<div style="text-align:right;">{}</div>', valor)

    saldo_a_comprometer_formatado.short_description = "SALDO A COMPROMETER"

    def saldo_a_medir_formatado(self, obj):
        valor = number_format(obj.saldo_a_medir, 2, use_l10n=True)
        if not obj.is_leaf_node():
            return format_html('<div style="text-align:right;"><strong>{}</strong></div>', valor)
        return format_html('<div style="text-align:right;">{}</div>', valor)

    saldo_a_medir_formatado.short_description = "SALDO A MEDIR"


@admin.register(Compromisso)
class CompromissoAdmin(admin.ModelAdmin):
    list_display = (
        "numero",
        "centro_custo_codigo",
        "cnpj",
        "fornecedor",
        "descricao",
        "tipo",
        "valor_contratado",
        "valor_executado",
    )
    list_filter = ("tipo",)
    search_fields = ("numero", "centro_custo__codigo", "cnpj", "fornecedor", "descricao")

    def centro_custo_codigo(self, obj):
        if obj.centro_custo:
            return obj.centro_custo.codigo
        return "-"

    centro_custo_codigo.short_description = "Centro de Custo"
    centro_custo_codigo.admin_order_field = "centro_custo__codigo"

    def valor_executado(self, obj):
        total = (
            NotaFiscalCentroCusto.objects
            .filter(centro_custo__compromissos=obj)
            .aggregate(total=Sum("valor"))["total"]
            or Decimal("0.00")
        )
        valor = number_format(total, 2, use_l10n=True)
        return format_html('<div style="text-align:right;">{}</div>', valor)

    valor_executado.short_description = "Valor Executado"


@admin.register(Medicao)
class MedicaoAdmin(admin.ModelAdmin):
    list_display = (
        "numero_da_medicao",
        "contrato",
        "centro_custo_codigo",
        "cnpj",
        "fornecedor",
        "descricao",
        "valor_medido",
    )
    search_fields = ("numero_da_medicao", "centro_custo__codigo", "centro_custo__descricao")

    class Media:
        js = ("admin/js/medicao_auto.js",)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "buscar-contrato/<int:contrato_id>/",
                self.admin_site.admin_view(self.buscar_contrato),
                name="buscar_contrato",
            ),
        ]
        return custom_urls + urls

    def buscar_contrato(self, request, contrato_id):
        contrato = Compromisso.objects.get(pk=contrato_id)
        return JsonResponse(obter_dados_contrato(contrato))

    def centro_custo_codigo(self, obj):
        if obj.centro_custo:
            return obj.centro_custo.codigo
        return "-"

    centro_custo_codigo.short_description = "Centro de Custo"
    centro_custo_codigo.admin_order_field = "centro_custo__codigo"


class NotaFiscalCentroCustoFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        itens_rateio = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue

            centro = form.cleaned_data.get("centro_custo")
            valor = form.cleaned_data.get("valor") or Decimal("0.00")
            if centro is None:
                raise ValidationError("Informe um centro de custo para o rateio.")
            itens_rateio.append((centro, valor))

        validar_rateio_nota(self.instance, itens_rateio)


class NotaFiscalCentroCustoInline(admin.TabularInline):
    model = NotaFiscalCentroCusto
    extra = 1
    raw_id_fields = ("centro_custo",)
    formset = NotaFiscalCentroCustoFormSet


@admin.register(NotaFiscal)
class NotaFiscalAdmin(admin.ModelAdmin):
    inlines = [NotaFiscalCentroCustoInline]
    raw_id_fields = ("medicao", "pedido_compra")
    list_display = (
        "numero",
        "tipo",
        "fornecedor",
        "cnpj",
        "descricao",
        "origem",
        "valor_total_formatado",
        "data_emissao",
    )
    list_filter = ("tipo", "data_emissao")
    search_fields = (
        "numero",
        "serie",
        "fornecedor",
        "cnpj",
        "descricao",
        "medicao__numero_da_medicao",
        "pedido_compra__numero",
    )
    readonly_fields = ("criado_em",)

    def origem(self, obj):
        if obj.tipo == "SERVICO" and obj.medicao:
            return f"Medição {obj.medicao.numero_da_medicao}"
        if obj.tipo == "MATERIAL" and obj.pedido_compra:
            return f"Pedido {obj.pedido_compra.numero}"
        return "-"

    origem.short_description = "Origem"

    def valor_total_formatado(self, obj):
        return format_html(
            '<div style="text-align:right;">{}</div>',
            number_format(obj.valor_total, 2, use_l10n=True),
        )

    valor_total_formatado.short_description = "Valor Total"
    valor_total_formatado.admin_order_field = "valor_total"


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nome", "nome_fantasia", "cnpj", "email", "ativo", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("nome", "nome_fantasia", "cnpj", "email")
    readonly_fields = ("criado_em", "atualizado_em")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("usuario", "empresa", "cargo", "telefone", "ativo")
    list_filter = ("ativo", "empresa")
    search_fields = ("usuario__username", "usuario__email", "cargo")
    raw_id_fields = ("usuario",)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("acao", "entidade_label", "usuario", "empresa", "timestamp", "request_id")
    list_filter = ("acao", "empresa", "timestamp")
    search_fields = ("entidade_label", "entidade_app", "usuario__username", "request_id")
    readonly_fields = ("empresa", "usuario", "timestamp", "acao", "entidade_app", "entidade_label", "objeto_id", "antes", "depois", "ip_address", "user_agent", "request_id")
    date_hierarchy = "timestamp"
    
    def has_add_permission(self, request):
        return False  # Auditoria é apenas leitura
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UsuarioEmpresa)
class UsuarioEmpresaAdmin(admin.ModelAdmin):
    """
    Admin para gerenciar vínculo usuário-empresa.
    Apenas superusers podem criar UsuarioEmpresa.
    """
    list_display = ("usuario", "empresa", "papel_aprovacao", "is_admin_empresa", "obras_count", "criado_em")
    list_filter = ("papel_aprovacao", "is_admin_empresa", "empresa")
    search_fields = ("usuario__username", "usuario__email", "empresa__nome")
    raw_id_fields = ("usuario",)
    filter_horizontal = ("obras_permitidas",)
    readonly_fields = ("criado_em", "atualizado_em")
    
    def obras_count(self, obj):
        if obj.is_admin_empresa:
            return "Todas da empresa"
        return obj.obras_permitidas.count()
    
    obras_count.short_description = "Obras Permitidas"
    
    def has_add_permission(self, request):
        # Apenas superusers podem adicionar
        return request.user.is_superuser
    
    def has_change_permission(self, request, obj=None):
        # Apenas superusers podem modificar
        return request.user.is_superuser
    
    def has_delete_permission(self, request, obj=None):
        # Apenas superusers podem deletar
        return request.user.is_superuser
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("usuario", "empresa")


@admin.register(Risco)
class RiscoAdmin(admin.ModelAdmin):
    list_display = ("titulo", "obra", "categoria", "nivel", "status", "responsavel", "criado_em")
    list_filter = ("status", "categoria", "nivel")
    search_fields = ("titulo", "descricao", "obra__codigo")
    readonly_fields = ("nivel", "criado_em", "atualizado_em")
    raw_id_fields = ("obra", "plano_contas", "responsavel", "criado_por")


@admin.register(RiscoHistorico)
class RiscoHistoricoAdmin(admin.ModelAdmin):
    list_display = ("risco", "acao", "usuario", "timestamp")
    list_filter = ("acao", "timestamp")
    search_fields = ("risco__titulo", "usuario__username")
    readonly_fields = ("timestamp",)


@admin.register(ParametroComunicacaoEmpresa)
class ParametroComunicacaoEmpresaAdmin(admin.ModelAdmin):
    list_display = ("empresa", "frequencia_curto_prazo_dias", "frequencia_medio_prazo_dias", "frequencia_longo_prazo_dias")
    search_fields = ("empresa__nome",)


class ItemPautaReuniaoInline(admin.TabularInline):
    model = ItemPautaReuniao
    extra = 0


@admin.register(ReuniaoComunicacao)
class ReuniaoComunicacaoAdmin(admin.ModelAdmin):
    list_display = ("numero", "obra", "tipo_reuniao", "status", "data_prevista", "aprovado_em")
    list_filter = ("tipo_reuniao", "status", "obra__empresa")
    search_fields = ("numero", "titulo", "obra__codigo", "obra__nome")
    readonly_fields = ("numero", "criado_em", "atualizado_em")
    inlines = [ItemPautaReuniaoInline]


@admin.register(HistoricoReuniaoComunicacao)
class HistoricoReuniaoComunicacaoAdmin(admin.ModelAdmin):
    list_display = ("reuniao", "acao", "usuario", "criado_em")
    list_filter = ("acao", "criado_em")
    search_fields = ("reuniao__numero", "usuario__username", "observacao")
    readonly_fields = ("criado_em",)


from django.contrib import admin, messages
from .models import OperacaoBackupSaaS


@admin.register(OperacaoBackupSaaS)
class OperacaoBackupSaaSAdmin(admin.ModelAdmin):
    list_display  = ("tipo", "status", "provedor", "ambiente", "executado_em", "tamanho_bytes")
    list_filter   = ("tipo", "status", "ambiente")
    ordering      = ("-executado_em",)
    readonly_fields = (
        "tipo", "status", "provedor", "ambiente",
        "identificador_artefato", "checksum", "tamanho_bytes",
        "observacao", "executado_em", "solicitado_por",
        "backup_referencia", "detalhes",
    )
    actions = ["disparar_backup_agora"]

    @admin.action(description="▶ Executar backup PostgreSQL → R2 agora")
    def disparar_backup_agora(self, request, queryset):
        """
        Dispara a task Celery de backup independente de quantos
        registros estiverem selecionados — basta selecionar qualquer um.
        """
        try:
            from .tasks import task_executar_backup_postgres
            task = task_executar_backup_postgres.delay()
            self.message_user(
                request,
                f"Backup enviado para fila Celery. Task ID: {task.id} — "
                f"acompanhe o resultado nesta lista em instantes.",
                level=messages.SUCCESS,
            )
        except Exception as exc:
            self.message_user(
                request,
                f"Falha ao enfileirar backup: {exc}",
                level=messages.ERROR,
            )