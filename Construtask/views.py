from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from .forms import (
    AnexoOperacionalForm,
    AditivoContratoForm,
    AditivoContratoItemFormSet,
    CompromissoForm,
    CompromissoItemFormSet,
    MedicaoForm,
    MedicaoItemFormSet,
    NotaFiscalCentroCustoFormSet,
    NotaFiscalForm,
    ObraForm,
    PlanoContasForm,
    obter_centros_da_origem_nota,
    obter_centros_do_contrato,
)
from .models import AditivoContrato, AnexoOperacional, Compromisso, CompromissoItem, FechamentoMensal, HistoricoOperacional, Medicao, MedicaoItem, NotaFiscal, NotaFiscalCentroCusto, Obra, PlanoContas
from .models_aquisicoes import Cotacao, OrdemCompra, SolicitacaoCompra
from .models_qualidade import NaoConformidade
from .models_risco import Risco
from .services import importar_plano_contas_excel, obter_dados_contrato, obter_dados_medicao
from .services_eva import EVAService
from .services_indicadores import IndicadoresService
from .services_integracao import IntegracaoService
from .domain import arredondar_moeda
from .templatetags.formatters import money_br


def _calcular_percentual(valor, total):
    if not total:
        return 0
    return round((float(valor) / float(total)) * 100, 1)


def _obter_obra_contexto(request):
    obra_id = request.session.get("obra_contexto_id")
    if not obra_id:
        return None
    return Obra.objects.filter(pk=obra_id).first()


def _filtrar_por_obra_contexto(request, queryset, campo="obra"):
    obra_contexto = _obter_obra_contexto(request)
    if not obra_contexto:
        return queryset
    return queryset.filter(**{campo: obra_contexto})


def _obter_contrato_from_request(request, instance=None):
    contrato = None
    contrato_id = request.POST.get("contrato") or request.GET.get("contrato")
    if contrato_id:
        try:
            contrato = Compromisso.objects.get(pk=contrato_id, tipo="CONTRATO")
        except Compromisso.DoesNotExist:
            contrato = None
    elif instance and getattr(instance, "contrato_id", None):
        contrato = instance.contrato
    return contrato


def _obter_origem_nota(request, instance=None):
    pedido = None
    medicao = None
    pedido_id = request.POST.get("pedido_compra") or request.GET.get("pedido_compra")
    medicao_id = request.POST.get("medicao") or request.GET.get("medicao")
    if pedido_id:
        try:
            pedido = Compromisso.objects.get(pk=pedido_id, tipo="PEDIDO_COMPRA")
        except Compromisso.DoesNotExist:
            pedido = None
    if medicao_id:
        try:
            medicao = Medicao.objects.get(pk=medicao_id)
        except Medicao.DoesNotExist:
            medicao = None
    if not pedido and not medicao and instance:
        pedido = getattr(instance, "pedido_compra", None)
        medicao = getattr(instance, "medicao", None)
    return pedido, medicao


def _construir_formset_medicao(*, data=None, instance=None, prefix="itens", contrato=None):
    centros_queryset = obter_centros_do_contrato(contrato)
    return MedicaoItemFormSet(
        data=data,
        instance=instance,
        prefix=prefix,
        centros_queryset=centros_queryset,
    )


def _construir_formset_nota(*, data=None, instance=None, prefix="rateio", pedido=None, medicao=None, obra=None):
    centros_queryset = obter_centros_da_origem_nota(instance, pedido, medicao, obra)
    return NotaFiscalCentroCustoFormSet(
        data=data,
        instance=instance,
        prefix=prefix,
        centros_queryset=centros_queryset,
    )


def _mapa_somas_por_centro(modelo, campo_valor):
    return {
        row["centro_custo_id"]: row["total"] or Decimal("0.00")
        for row in modelo.objects.values("centro_custo_id").annotate(total=Sum(campo_valor))
    }


def _filtrar_periodo(queryset, campo_data, data_inicio, data_fim):
    if data_inicio:
        queryset = queryset.filter(**{f"{campo_data}__gte": data_inicio})
    if data_fim:
        queryset = queryset.filter(**{f"{campo_data}__lte": data_fim})
    return queryset


def _filtros_compromissos(request, queryset):
    termo = request.GET.get("q", "").strip()
    obra_id = request.GET.get("obra", "").strip()
    status = request.GET.get("status", "").strip()
    fornecedor = request.GET.get("fornecedor", "").strip()
    responsavel = request.GET.get("responsavel", "").strip()
    centro_custo_id = request.GET.get("centro_custo", "").strip()
    data_inicio = request.GET.get("data_inicio", "").strip()
    data_fim = request.GET.get("data_fim", "").strip()

    if termo:
        queryset = queryset.filter(
            Q(numero__icontains=termo)
            | Q(fornecedor__icontains=termo)
            | Q(cnpj__icontains=termo)
            | Q(responsavel__icontains=termo)
            | Q(descricao__icontains=termo)
        ).distinct()
    if obra_id:
        queryset = queryset.filter(obra_id=obra_id)
    if status:
        queryset = queryset.filter(status=status)
    if fornecedor:
        queryset = queryset.filter(fornecedor__icontains=fornecedor)
    if responsavel:
        queryset = queryset.filter(responsavel__icontains=responsavel)
    if centro_custo_id:
        queryset = queryset.filter(Q(centro_custo_id=centro_custo_id) | Q(itens__centro_custo_id=centro_custo_id)).distinct()

    return _filtrar_periodo(queryset, "data_assinatura", data_inicio, data_fim)


def _filtros_medicoes(request, queryset):
    termo = request.GET.get("q", "").strip()
    obra_id = request.GET.get("obra", "").strip()
    status = request.GET.get("status", "").strip()
    fornecedor = request.GET.get("fornecedor", "").strip()
    responsavel = request.GET.get("responsavel", "").strip()
    contrato = request.GET.get("contrato", "").strip()
    centro_custo_id = request.GET.get("centro_custo", "").strip()
    data_inicio = request.GET.get("data_inicio", "").strip()
    data_fim = request.GET.get("data_fim", "").strip()

    if termo:
        queryset = queryset.filter(
            Q(numero_da_medicao__icontains=termo)
            | Q(fornecedor__icontains=termo)
            | Q(cnpj__icontains=termo)
        )
    if obra_id:
        queryset = queryset.filter(obra_id=obra_id)
    if status:
        queryset = queryset.filter(status=status)
    if fornecedor:
        queryset = queryset.filter(fornecedor__icontains=fornecedor)
    if responsavel:
        queryset = queryset.filter(responsavel__icontains=responsavel)
    if contrato:
        queryset = queryset.filter(contrato__numero__icontains=contrato)
    if centro_custo_id:
        queryset = queryset.filter(Q(centro_custo_id=centro_custo_id) | Q(itens__centro_custo_id=centro_custo_id)).distinct()

    return _filtrar_periodo(queryset, "data_medicao", data_inicio, data_fim)


def _filtros_notas(request, queryset):
    termo = request.GET.get("q", "").strip()
    obra_id = request.GET.get("obra", "").strip()
    status = request.GET.get("status", "").strip()
    fornecedor = request.GET.get("fornecedor", "").strip()
    contrato = request.GET.get("contrato", "").strip()
    centro_custo_id = request.GET.get("centro_custo", "").strip()
    data_inicio = request.GET.get("data_inicio", "").strip()
    data_fim = request.GET.get("data_fim", "").strip()

    if termo:
        queryset = queryset.filter(
            Q(numero__icontains=termo)
            | Q(fornecedor__icontains=termo)
            | Q(cnpj__icontains=termo)
        )
    if obra_id:
        queryset = queryset.filter(obra_id=obra_id)
    if status:
        queryset = queryset.filter(status=status)
    if fornecedor:
        queryset = queryset.filter(fornecedor__icontains=fornecedor)
    if contrato:
        queryset = queryset.filter(Q(medicao__contrato__numero__icontains=contrato) | Q(pedido_compra__numero__icontains=contrato))
    if centro_custo_id:
        queryset = queryset.filter(centros_custo__centro_custo_id=centro_custo_id).distinct()

    return _filtrar_periodo(queryset, "data_emissao", data_inicio, data_fim)


def _consolidar_plano_contas(planos_queryset):
    planos = list(
        planos_queryset.only(
            "id",
            "codigo",
            "descricao",
            "unidade",
            "quantidade",
            "valor_unitario",
            "valor_total",
            "parent_id",
            "tree_id",
            "lft",
            "rght",
            "level",
        )
    )
    planos_por_id = {plano.id: plano for plano in planos}
    comprometido_por_centro = _mapa_somas_por_centro(CompromissoItem, "valor_total")
    medido_por_centro = _mapa_somas_por_centro(MedicaoItem, "valor_total")
    executado_por_centro = _mapa_somas_por_centro(NotaFiscalCentroCusto, "valor")

    for plano in planos:
        plano.valor_total_consolidado_calc = plano.valor_total or Decimal("0.00")
        plano.valor_comprometido_calc = comprometido_por_centro.get(plano.id, Decimal("0.00"))
        plano.valor_medido_calc = medido_por_centro.get(plano.id, Decimal("0.00"))
        plano.valor_executado_calc = executado_por_centro.get(plano.id, Decimal("0.00"))
        plano.nivel_indentacao = getattr(plano, "level", 0)

    for plano in reversed(planos):
        if not plano.parent_id:
            continue
        parent = planos_por_id.get(plano.parent_id)
        if not parent:
            continue
        parent.valor_total_consolidado_calc += plano.valor_total_consolidado_calc
        parent.valor_comprometido_calc += plano.valor_comprometido_calc
        parent.valor_medido_calc += plano.valor_medido_calc
        parent.valor_executado_calc += plano.valor_executado_calc

    for plano in planos:
        plano.saldo_a_comprometer_calc = plano.valor_total_consolidado_calc - plano.valor_comprometido_calc
        plano.saldo_a_medir_calc = plano.valor_comprometido_calc - plano.valor_medido_calc
        plano.saldo_a_executar_calc = plano.valor_total_consolidado_calc - plano.valor_executado_calc

    return planos


def _exportar_excel_response(nome_arquivo, sheet_name, linhas):
    output = BytesIO()
    dataframe = pd.DataFrame(linhas)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False, sheet_name=sheet_name)
    output.seek(0)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}"'
    return response


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
    pdf += f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii")

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}"'
    return response


def _apagar_objeto(request, queryset, success_url):
    if request.method != "POST":
        raise Http404()
    objeto = get_object_or_404(queryset, pk=request.POST.get("id"))
    try:
        historico = _registrar_historico("EXCLUSAO", objeto, f"Exclusao de {objeto}")
        objeto.delete()
        messages.success(request, "Registro excluido com sucesso.")
    except ProtectedError:
        # Se a exclusao for protegida, removemos o historico criado para nao registrar operacao inexistente.
        try:
            if "historico" in locals() and historico and getattr(historico, "pk", None):
                historico.delete()
        except Exception:
            pass
        messages.error(
            request,
            "Este registro nao pode ser excluido porque possui vinculos em outras operacoes do sistema.",
        )
    return redirect(success_url)


def _registrar_historico(acao, objeto, descricao):
    payload = {"acao": acao, "descricao": descricao}
    if isinstance(objeto, Obra):
        if getattr(objeto, "pk", None):
            payload["obra_id"] = objeto.pk
        else:
            payload["obra"] = objeto
    elif isinstance(objeto, Compromisso):
        if getattr(objeto, "obra_id", None):
            payload["obra_id"] = objeto.obra_id
        if getattr(objeto, "pk", None):
            payload["compromisso_id"] = objeto.pk
        else:
            payload["compromisso"] = objeto
    elif isinstance(objeto, Medicao):
        if getattr(objeto, "obra_id", None):
            payload["obra_id"] = objeto.obra_id
        if getattr(objeto, "pk", None):
            payload["medicao_id"] = objeto.pk
        else:
            payload["medicao"] = objeto
    elif isinstance(objeto, NotaFiscal):
        if getattr(objeto, "obra_id", None):
            payload["obra_id"] = objeto.obra_id
        if getattr(objeto, "pk", None):
            payload["nota_fiscal_id"] = objeto.pk
        else:
            payload["nota_fiscal"] = objeto
    return HistoricoOperacional.objects.create(**payload)


@login_required
def selecionar_obra_contexto_view(request):
    proxima_url = request.POST.get("next") or reverse_lazy("home")
    obra_id = request.POST.get("obra_contexto")
    if obra_id:
        obra = Obra.objects.filter(pk=obra_id).first()
        if obra:
            request.session["obra_contexto_id"] = obra.pk
    else:
        request.session.pop("obra_contexto_id", None)
    return redirect(proxima_url)


class HomeView(TemplateView):
    template_name = "app/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        
        # Se nao tem obra selecionada, nao calcula indicadores (dados vao ficar zerados)
        if not obra_contexto:
            context["sem_obra_selecionada"] = True
            context["indicadores"] = {
                "valor_orcado": Decimal("0.00"),
                "valor_comprometido": Decimal("0.00"),
                "valor_medido": Decimal("0.00"),
                "valor_pago": Decimal("0.00"),
            }
            context["indicadores_exec"] = {
                "orcado": Decimal("0.00"),
                "comprometido": Decimal("0.00"),
                "medido": Decimal("0.00"),
                "executado": Decimal("0.00"),
                "planejado": Decimal("0.00"),
            }
            context["eva"] = {
                "PV": Decimal("0.00"),
                "EV": Decimal("0.00"),
                "AC": Decimal("0.00"),
                "CPI": Decimal("0.00"),
                "SPI": Decimal("0.00"),
                "CV": Decimal("0.00"),
                "SV": Decimal("0.00"),
            }
            context["grafico_geral"] = []
            context["cards_percentuais"] = []
            context["top_itens_orcamento"] = []
            context["pendencias"] = []
            context["resumo_riscos"] = {
                "total": 0,
                "criticos": 0,
                "altos": 0,
                "medios": 0,
                "baixos": 0,
                "em_tratamento": 0,
                "fechados": 0,
            }
            context["alertas"] = []
            context["alertas_operacionais"] = []
            context["nao_conformidades_abertas"] = []
            context["pipeline_aquisicoes"] = []
            context["ultimas_ordens_compra"] = []
            return context
        folhas = PlanoContas.objects.annotate(filhos_count=Count("filhos")).filter(filhos_count=0)
        folhas = _filtrar_por_obra_contexto(self.request, folhas)
        valor_orcado = folhas.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")
        valor_comprometido = _filtrar_por_obra_contexto(self.request, Compromisso.objects.all()).aggregate(total=Sum("valor_contratado"))["total"] or Decimal("0.00")
        valor_medido = _filtrar_por_obra_contexto(self.request, Medicao.objects.all()).aggregate(total=Sum("valor_medido"))["total"] or Decimal("0.00")
        valor_pago = _filtrar_por_obra_contexto(self.request, NotaFiscal.objects.all()).aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")

        context["indicadores"] = {
            "valor_orcado": valor_orcado,
            "valor_comprometido": valor_comprometido,
            "valor_medido": valor_medido,
            "valor_pago": valor_pago,
        }
        context["indicadores_exec"] = IntegracaoService.consolidar_obra(obra_contexto)
        context["eva"] = EVAService.calcular(obra_contexto)
        context["indicadores_dashboard"] = IndicadoresService.resumo_obra(obra_contexto)
        context["grafico_geral"] = [
            {"label": "Orçado", "valor": valor_orcado, "percentual": 100 if valor_orcado else 0, "cor": "#0f172a", "mostrar_percentual": False},
            {"label": "Comprometido", "valor": valor_comprometido, "percentual": _calcular_percentual(valor_comprometido, valor_orcado), "cor": "#7f1d1d", "mostrar_percentual": True},
            {"label": "Medido", "valor": valor_medido, "percentual": _calcular_percentual(valor_medido, valor_orcado), "cor": "#434a53", "mostrar_percentual": True},
            {"label": "Valor pago", "valor": valor_pago, "percentual": _calcular_percentual(valor_pago, valor_orcado), "cor": "#2563eb", "mostrar_percentual": True},
        ]
        context["cards_percentuais"] = [
            {"label": "% Comprometido", "valor": _calcular_percentual(valor_comprometido, valor_orcado), "cor": "#651111"},
            {"label": "% Medido", "valor": _calcular_percentual(valor_medido, valor_orcado), "cor": "#2f343a"},
            {"label": "% Pago", "valor": _calcular_percentual(valor_pago, valor_orcado), "cor": "#1e3a8a"},
        ]

        # Cronograma de orçamento por mês (barras mensais + linha do acumulado).
        # Distribui o valor total orçado uniformemente entre data_inicio..data_fim da obra.
        cronograma_orcado_meses = []
        cronograma_orcado_svg_width = 1
        cronograma_orcado_polyline_points = ""
        if obra_contexto and obra_contexto.data_inicio and obra_contexto.data_fim and valor_orcado:
            start = obra_contexto.data_inicio.replace(day=1)
            end = obra_contexto.data_fim.replace(day=1)
            if end < start:
                start, end = end, start

            total_meses = ((end.year - start.year) * 12 + (end.month - start.month) + 1)
            total_meses = max(1, total_meses)

            # Distribuição estável (sem "estourar" o último mês por arredondamento):
            # base = floor(valor/meses, 2 casas) e distribui o resto (centavos) nos primeiros meses.
            cent = Decimal("0.01")
            base = (valor_orcado / Decimal(total_meses)).quantize(cent, rounding="ROUND_DOWN")
            resto = (valor_orcado - (base * Decimal(total_meses))).quantize(cent)
            # Quantidade de centavos a distribuir (sempre >= 0).
            extra_cents = int((resto / cent).to_integral_value())

            # Garante que a soma feche no valor total no último mês.
            month_starts = []
            cursor = start
            while cursor <= end:
                month_starts.append(cursor)
                if cursor.month == 12:
                    cursor = cursor.replace(year=cursor.year + 1, month=1, day=1)
                else:
                    cursor = cursor.replace(month=cursor.month + 1, day=1)

            acumulado = Decimal("0.00")
            max_val = Decimal("0.00")
            for i, _ in enumerate(month_starts):
                valor_mes = base + (cent if i < extra_cents else Decimal("0.00"))
                acumulado += valor_mes
                max_val = max(max_val, acumulado)

            # Evita divisão por zero.
            max_val = max(max_val, base)

            bar_max_height = 130
            chart_top = 20
            chart_bottom = chart_top + bar_max_height
            chart_width_step = 70
            bar_width = 22
            padding_left = 16

            polyline_points = []
            acumulado = Decimal("0.00")
            for i, ms in enumerate(month_starts):
                valor_mes = base + (cent if i < extra_cents else Decimal("0.00"))

                acumulado += valor_mes
                bar_h = int((valor_mes / max_val) * bar_max_height) if max_val else 0
                line_y = int(chart_bottom - (acumulado / max_val) * bar_max_height) if max_val else chart_bottom

                x = padding_left + i * chart_width_step
                bar_x = x
                bar_y = chart_bottom - bar_h
                line_x = x + (bar_width // 2)

                cronograma_orcado_meses.append(
                    {
                        "label": ms.strftime("%m/%Y"),
                        "valor_mes": valor_mes,
                        "acumulado": acumulado,
                        "bar_x": bar_x,
                        "bar_y": bar_y,
                        "bar_h": bar_h,
                        "line_x": line_x,
                        "line_y": line_y,
                    }
                )
                polyline_points.append(f"{line_x},{line_y}")

            cronograma_orcado_svg_width = max(1, len(cronograma_orcado_meses) * chart_width_step + padding_left + 10)
            cronograma_orcado_polyline_points = " ".join(polyline_points)

        context["cronograma_orcado_meses"] = cronograma_orcado_meses
        context["cronograma_orcado_svg_width"] = cronograma_orcado_svg_width
        context["cronograma_orcado_polyline_points"] = cronograma_orcado_polyline_points

        context["ultimos_compromissos"] = (
            _filtrar_por_obra_contexto(self.request, Compromisso.objects.select_related("centro_custo", "obra"))
            .prefetch_related(Prefetch("itens", queryset=CompromissoItem.objects.select_related("centro_custo")))
            .order_by("-id")[:5]
        )
        context["obras_ativas"] = Obra.objects.exclude(status="CONCLUIDA").count() if not obra_contexto else 1
        
        # Estatísticas de Riscos ISO 6.1
        riscos_qs = _filtrar_por_obra_contexto(self.request, Risco.objects.all())
        total_riscos = riscos_qs.count()
        riscos_criticos = riscos_qs.filter(nivel__gt=15).count()
        riscos_altos = riscos_qs.filter(nivel__gte=10, nivel__lte=15).count()
        riscos_medios = riscos_qs.filter(nivel__gte=5, nivel__lt=10).count()
        riscos_baixos = riscos_qs.filter(nivel__lt=5).count()
        riscos_em_tratamento = riscos_qs.filter(status="EM_TRATAMENTO").count()
        riscos_fechados = riscos_qs.filter(status="FECHADO").count()
        
        context["resumo_riscos"] = {
            "total": total_riscos,
            "criticos": riscos_criticos,
            "altos": riscos_altos,
            "medios": riscos_medios,
            "baixos": riscos_baixos,
            "em_tratamento": riscos_em_tratamento,
            "fechados": riscos_fechados,
        }
        
        # Substituir pendências por resumo de riscos
        context["pendencias"] = [
            {"label": "Riscos Críticos", "valor": riscos_criticos, "cor": "red"},
            {"label": "Riscos Altos", "valor": riscos_altos, "cor": "orange"},
            {"label": "Riscos Médios", "valor": riscos_medios, "cor": "yellow"},
            {"label": "Em Tratamento", "valor": riscos_em_tratamento, "cor": "blue"},
            {"label": "Fechados", "valor": riscos_fechados, "cor": "green"},
        ]
        context["alertas"] = [
            compromisso
            for compromisso in _filtrar_por_obra_contexto(self.request, Compromisso.objects.filter(tipo="CONTRATO").select_related("obra").order_by("numero"))
            if compromisso.valor_contratado > Decimal("0.00") and compromisso.saldo / compromisso.valor_contratado < Decimal("0.10")
        ][:8]
        notas_sem_rateio = _filtrar_por_obra_contexto(self.request, NotaFiscal.objects.annotate(rateio_total=Coalesce(Sum("centros_custo__valor"), Decimal("0.00")))).filter(
            Q(rateio_total=Decimal("0.00")) | ~Q(rateio_total=F("valor_total"))
        )
        context["alertas_operacionais"] = [
            {
                "label": "Contratos sem medicao",
                "valor": _filtrar_por_obra_contexto(self.request, Compromisso.objects.filter(tipo="CONTRATO", medicoes__isnull=True)).count(),
                "nivel": "medio",
            },
            {
                "label": "Notas sem apropriacao completa",
                "valor": notas_sem_rateio.count(),
                "nivel": "alto",
            },
            {
                "label": "Contratos com saldo crítico",
                "valor": len(context["alertas"]),
                "nivel": "critico",
            },
        ]
        context["nao_conformidades_abertas"] = list(
            NaoConformidade.objects.filter(obra=obra_contexto).exclude(status__in=["ENCERRADA", "CANCELADA"]).select_related("responsavel").order_by("-criado_em")[:5]
        )
        context["pipeline_aquisicoes"] = [
            {"label": "Solicitacoes abertas", "valor": SolicitacaoCompra.objects.filter(obra=obra_contexto).exclude(status__in=["ENCERRADA", "CANCELADA"]).count()},
            {"label": "Cotacoes aprovadas", "valor": Cotacao.objects.filter(obra=obra_contexto, status="APROVADA").count()},
            {"label": "Ordens emitidas", "valor": OrdemCompra.objects.filter(obra=obra_contexto).count()},
        ]
        context["ultimas_ordens_compra"] = list(
            OrdemCompra.objects.filter(obra=obra_contexto).select_related("fornecedor", "compromisso_relacionado").order_by("-data_emissao", "-id")[:5]
        )
        context["ultimos_fechamentos"] = _filtrar_por_obra_contexto(self.request, FechamentoMensal.objects.select_related("obra")).order_by("-ano", "-mes")[:5]
        context["obra_contexto"] = obra_contexto

        # Top 10 itens mais importantes do Orcamento (Nivel 5 do Plano de Contas)
        centros_nivel5_qs = PlanoContas.objects.filter(level=4).only(
            "id", "codigo", "descricao", "tree_id", "lft", "rght", "level", "obra_id"
        )
        centros_nivel5_qs = _filtrar_por_obra_contexto(self.request, centros_nivel5_qs)
        centros_nivel5 = list(centros_nivel5_qs)

        top_itens = []
        if centros_nivel5:
            desc_filter = Q()
            for c in centros_nivel5:
                desc_filter |= Q(tree_id=c.tree_id, lft__gte=c.lft, rght__lte=c.rght)
            all_desc = list(PlanoContas.objects.filter(desc_filter).values("id", "tree_id", "lft", "rght", "valor_total"))

            node_to_nivel5 = {}
            for node in all_desc:
                for c in centros_nivel5:
                    if node["tree_id"] == c.tree_id and node["lft"] >= c.lft and node["rght"] <= c.rght:
                        node_to_nivel5[node["id"]] = c.id
                        break
            all_desc_ids = list(node_to_nivel5.keys())

            orcado_by_nivel5 = defaultdict(Decimal)
            for node in all_desc:
                n5_id = node_to_nivel5.get(node["id"])
                if n5_id:
                    orcado_by_nivel5[n5_id] += node["valor_total"] or Decimal("0.00")

            comprometido_by_nivel5 = defaultdict(Decimal)
            for row in CompromissoItem.objects.filter(centro_custo_id__in=all_desc_ids).values("centro_custo_id").annotate(total=Sum("valor_total")):
                n5_id = node_to_nivel5.get(row["centro_custo_id"])
                if n5_id:
                    comprometido_by_nivel5[n5_id] += row["total"] or Decimal("0.00")

            medido_by_nivel5 = defaultdict(Decimal)
            for row in MedicaoItem.objects.filter(centro_custo_id__in=all_desc_ids).values("centro_custo_id").annotate(total=Sum("valor_total")):
                n5_id = node_to_nivel5.get(row["centro_custo_id"])
                if n5_id:
                    medido_by_nivel5[n5_id] += row["total"] or Decimal("0.00")

            pago_by_nivel5 = defaultdict(Decimal)
            for row in NotaFiscalCentroCusto.objects.filter(centro_custo_id__in=all_desc_ids).values("centro_custo_id").annotate(total=Sum("valor")):
                n5_id = node_to_nivel5.get(row["centro_custo_id"])
                if n5_id:
                    pago_by_nivel5[n5_id] += row["total"] or Decimal("0.00")

            for centro in sorted(centros_nivel5, key=lambda c: -(orcado_by_nivel5.get(c.id, Decimal("0.00")))):
                valor_orcado_centro = orcado_by_nivel5.get(centro.id, Decimal("0.00"))
                valor_comprometido_centro = comprometido_by_nivel5.get(centro.id, Decimal("0.00"))
                valor_medido_centro = medido_by_nivel5.get(centro.id, Decimal("0.00"))
                valor_pago_centro = pago_by_nivel5.get(centro.id, Decimal("0.00"))

                saldo_comprometer = arredondar_moeda(valor_orcado_centro - valor_comprometido_centro)
                saldo_medir = arredondar_moeda(valor_comprometido_centro - valor_medido_centro)
                saldo_executar = arredondar_moeda(valor_orcado_centro - valor_pago_centro)

                if saldo_executar <= Decimal("0.00"):
                    situacao = "Concluido"
                elif valor_comprometido_centro > Decimal("0.00") and saldo_medir > Decimal("0.00"):
                    situacao = "Em Medicao"
                elif valor_comprometido_centro > Decimal("0.00"):
                    situacao = "Apropriacao em andamento"
                else:
                    situacao = "Sem Compromisso"

                top_itens.append({
                    "centro": centro,
                    "situacao": situacao,
                    "valor_orcado": valor_orcado_centro,
                    "valor_comprometido": valor_comprometido_centro,
                    "valor_medido": valor_medido_centro,
                    "valor_pago": valor_pago_centro,
                    "saldo_a_comprometer": saldo_comprometer,
                    "saldo_a_medir": saldo_medir,
                    "saldo_a_executar": saldo_executar,
                })
        context["top_itens_orcamento"] = top_itens[:10]
        return context


class CurvaABCView(TemplateView):
    template_name = "app/curva_abc.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)

        # 5º nível do plano de contas (raiz=0 => level=4).
        qs = PlanoContas.objects.filter(level=4)
        if obra_contexto:
            qs = qs.filter(obra=obra_contexto)
        qs = qs.order_by("-codigo")

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
                    "valor_total": valor,
                    "percentual": round(float(percentual), 1),
                    "acumulado": round(float(acumulado_perc), 1),
                    "classe": classe,
                }
            )

        context["dados"] = dados
        context["obra_contexto"] = obra_contexto
        return context


class ObraListView(ListView):
    model = Obra
    template_name = "app/obra_list.html"
    context_object_name = "obras"

    def get_queryset(self):
        queryset = Obra.objects.order_by("codigo")
        termo = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        if termo:
            queryset = queryset.filter(Q(codigo__icontains=termo) | Q(nome__icontains=termo) | Q(cliente__icontains=termo))
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busca"] = self.request.GET.get("q", "").strip()
        context["status_filtro"] = self.request.GET.get("status", "").strip()
        context["status_choices"] = Obra._meta.get_field("status").choices
        return context


class ObraCreateView(CreateView):
    model = Obra
    form_class = ObraForm
    template_name = "app/form.html"
    success_url = reverse_lazy("obra_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = "Nova Obra"
        context["voltar_url"] = reverse_lazy("obra_list")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        _registrar_historico("CRIACAO", self.object, f"Obra criada: {self.object}")
        return response


class ObraUpdateView(UpdateView):
    model = Obra
    form_class = ObraForm
    template_name = "app/form.html"
    success_url = reverse_lazy("obra_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = f"Editar Obra {self.object.codigo}"
        context["voltar_url"] = reverse_lazy("obra_list")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        _registrar_historico("ATUALIZACAO", self.object, f"Obra atualizada: {self.object}")
        return response


class PlanoContasConsultaView(ListView):
    model = PlanoContas
    template_name = "app/plano_contas_list.html"
    context_object_name = "planos_contas"

    def get_queryset(self):
        queryset = PlanoContas.objects.annotate(filhos_count=Count("filhos")).order_by("tree_id", "lft")
        queryset = _filtrar_por_obra_contexto(self.request, queryset)
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(descricao__icontains=termo) | Q(codigo__icontains=termo))
        return _consolidar_plano_contas(queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busca"] = self.request.GET.get("q", "").strip()
        return context


class PlanoContasUpdateView(UpdateView):
    model = PlanoContas
    form_class = PlanoContasForm
    template_name = "app/form.html"
    success_url = reverse_lazy("plano_contas_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["titulo"] = f"Editar Plano de Contas {self.object.codigo}"
        context["voltar_url"] = reverse_lazy("plano_contas_list")
        return context


@login_required
def plano_contas_delete_view(request):
    return _apagar_objeto(request, PlanoContas.objects.all(), "plano_contas_list")


@login_required
def plano_contas_export_view(request):
    queryset = PlanoContas.objects.annotate(filhos_count=Count("filhos")).order_by("tree_id", "lft")
    queryset = _filtrar_por_obra_contexto(request, queryset)
    termo = request.GET.get("q", "").strip()
    if termo:
        queryset = queryset.filter(Q(descricao__icontains=termo) | Q(codigo__icontains=termo))
    planos = _consolidar_plano_contas(queryset)
    linhas = [
        {
            "Codigo": plano.codigo,
            "Descricao": plano.descricao,
            "Unidade": plano.unidade or "",
            "Quantidade": plano.quantidade,
            "Valor Unitario": plano.valor_unitario,
            "Valor Total": plano.valor_total_consolidado_calc,
            "Comprometido": plano.valor_comprometido_calc,
            "Medido": plano.valor_medido_calc,
            "Valor Executado": plano.valor_executado_calc,
            "Saldo a Comprometer": plano.saldo_a_comprometer_calc,
            "Saldo a Medir": plano.saldo_a_medir_calc,
            "Saldo a Executar": plano.saldo_a_executar_calc,
        }
        for plano in planos
    ]
    return _exportar_excel_response("plano_de_contas.xlsx", "Plano de Contas", linhas)


def plano_contas_notas_view(request, pk):
    try:
        plano = PlanoContas.objects.get(pk=pk)
    except PlanoContas.DoesNotExist as exc:
        raise Http404("Centro de custo não encontrado.") from exc

    centros_ids = list(plano.get_descendants(include_self=True).values_list("id", flat=True))
    rateios = (
        NotaFiscalCentroCusto.objects
        .filter(centro_custo_id__in=centros_ids)
        .select_related("nota_fiscal", "centro_custo")
        .order_by("-nota_fiscal__data_emissao", "-nota_fiscal_id", "centro_custo__codigo")
    )
    notas = [
        {
            "id": rateio.nota_fiscal_id,
            "numero": rateio.nota_fiscal.numero,
            "fornecedor": rateio.nota_fiscal.fornecedor,
            "cnpj": rateio.nota_fiscal.cnpj,
            "descricao": rateio.nota_fiscal.descricao,
            "centro_custo": f"{rateio.centro_custo.codigo} - {rateio.centro_custo.descricao}",
            "valor": money_br(rateio.valor or Decimal("0.00")),
            "data": rateio.nota_fiscal.data_emissao.strftime("%d/%m/%Y"),
        }
        for rateio in rateios
    ]
    return JsonResponse(
        {
            "centro_custo": f"{plano.codigo} - {plano.descricao}",
            "quantidade_notas": len(notas),
            "notas": notas,
        }
    )


class CompromissoListView(ListView):
    model = Compromisso
    template_name = "app/compromisso_list.html"
    context_object_name = "compromissos"
    def get_queryset(self):
        queryset = (
            Compromisso.objects.select_related("centro_custo", "obra")
            .prefetch_related(Prefetch("itens", queryset=CompromissoItem.objects.select_related("centro_custo")))
            .order_by("-id")
        )
        queryset = _filtrar_por_obra_contexto(self.request, queryset)
        return _filtros_compromissos(self.request, queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busca"] = self.request.GET.get("q", "").strip()
        context["obra_filtro"] = self.request.GET.get("obra", "").strip()
        context["status_filtro"] = self.request.GET.get("status", "").strip()
        context["fornecedor_filtro"] = self.request.GET.get("fornecedor", "").strip()
        context["responsavel_filtro"] = self.request.GET.get("responsavel", "").strip()
        context["centro_custo_filtro"] = self.request.GET.get("centro_custo", "").strip()
        context["data_inicio"] = self.request.GET.get("data_inicio", "").strip()
        context["data_fim"] = self.request.GET.get("data_fim", "").strip()
        context["obras"] = Obra.objects.order_by("codigo")
        context["centros_custo"] = _filtrar_por_obra_contexto(self.request, PlanoContas.objects.order_by("tree_id", "lft"))
        context["status_choices"] = Compromisso._meta.get_field("status").choices
        return context


class CompromissoCreateView(CreateView):
    model = Compromisso
    form_class = CompromissoForm
    template_name = "app/compromisso_form.html"
    success_url = reverse_lazy("compromisso_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obra_contexto"] = _obter_obra_contexto(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        context["titulo"] = "Nova Compra ou Contratação"
        context["voltar_url"] = reverse_lazy("compromisso_list")
        context["item_formset"] = kwargs.get("item_formset") or CompromissoItemFormSet(prefix="itens", form_kwargs={"obra_contexto": obra_contexto})
        return context

    def form_valid(self, form):
        obra_contexto = _obter_obra_contexto(self.request)
        item_formset = CompromissoItemFormSet(self.request.POST, prefix="itens", form_kwargs={"obra_contexto": obra_contexto})
        if not item_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, item_formset=item_formset))

        self.object = form.save()
        item_formset.instance = self.object
        item_formset.save()
        self.object.recalcular_totais_por_itens()
        _registrar_historico("CRIACAO", self.object, f"Compromisso criado: {self.object.numero}")
        return HttpResponseRedirect(self.get_success_url())


class CompromissoUpdateView(UpdateView):
    model = Compromisso
    form_class = CompromissoForm
    template_name = "app/compromisso_form.html"
    success_url = reverse_lazy("compromisso_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obra_contexto"] = _obter_obra_contexto(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        context["titulo"] = f"Editar Compra ou Contratação {self.object.numero}"
        context["voltar_url"] = reverse_lazy("compromisso_list")
        context["item_formset"] = kwargs.get("item_formset") or CompromissoItemFormSet(instance=self.object, prefix="itens", form_kwargs={"obra_contexto": obra_contexto})
        return context

    def form_valid(self, form):
        obra_contexto = _obter_obra_contexto(self.request)
        item_formset = CompromissoItemFormSet(self.request.POST, instance=self.object, prefix="itens", form_kwargs={"obra_contexto": obra_contexto})
        if not item_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, item_formset=item_formset))

        self.object = form.save()
        item_formset.instance = self.object
        item_formset.save()
        self.object.recalcular_totais_por_itens()
        _registrar_historico("ATUALIZACAO", self.object, f"Compromisso atualizado: {self.object.numero}")
        return HttpResponseRedirect(self.get_success_url())


class ContratoDetailView(DetailView):
    model = Compromisso
    template_name = "app/contrato_detail.html"
    context_object_name = "contrato"

    def get_queryset(self):
        queryset = (
            Compromisso.objects
            .select_related("obra")
            .prefetch_related(
                Prefetch("itens", queryset=CompromissoItem.objects.select_related("centro_custo")),
                "medicoes",
                "anexos",
                "historicos",
                "ordens_compra_estruturadas__solicitacao",
                "ordens_compra_estruturadas__cotacao_aprovada",
            )
        )
        return _filtrar_por_obra_contexto(self.request, queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contrato = self.object
        context["medicoes_contrato"] = contrato.medicoes.order_by("-data_medicao")
        if contrato.tipo == "CONTRATO":
            context["notas_contrato"] = NotaFiscal.objects.filter(medicao__contrato=contrato).order_by("-data_emissao")
        else:
            context["notas_contrato"] = NotaFiscal.objects.filter(pedido_compra=contrato).order_by("-data_emissao")
        context["anexo_form"] = kwargs.get("anexo_form") or AnexoOperacionalForm()
        context["saldo_percentual"] = _calcular_percentual(contrato.valor_executado, contrato.valor_contratado) if contrato.valor_contratado else 0

        context["aditivos"] = contrato.aditivos.prefetch_related("itens__centro_custo").order_by("-criado_em")

        centros_queryset = obter_centros_do_contrato(contrato)
        aditivo_form = kwargs.get("aditivo_form")
        if not aditivo_form:
            aditivo_form = AditivoContratoForm(initial={"tipo": "VALOR"})

        tipo_formset = aditivo_form.data.get("tipo") if getattr(aditivo_form, "data", None) else None
        if not tipo_formset:
            tipo_formset = (getattr(aditivo_form, "initial", None) or {}).get("tipo") or "VALOR"

        aditivo_item_formset = kwargs.get("aditivo_item_formset")
        if not aditivo_item_formset:
            aditivo_instance = AditivoContrato(contrato=contrato, tipo=tipo_formset)
            aditivo_item_formset = AditivoContratoItemFormSet(
                prefix="aditivos_itens",
                instance=aditivo_instance,
                centros_queryset=centros_queryset,
            )

        context["aditivo_form"] = aditivo_form
        context["aditivo_item_formset"] = aditivo_item_formset
        context["origem_aquisicao"] = contrato.ordens_compra_estruturadas.select_related("solicitacao", "cotacao_aprovada").first()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = AnexoOperacionalForm(request.POST, request.FILES)
        if form.is_valid():
            anexo = form.save(commit=False)
            anexo.compromisso = self.object
            anexo.obra = self.object.obra
            anexo.save()
            _registrar_historico("ANEXO", self.object, f"Anexo incluido no contrato {self.object.numero}")
            messages.success(request, "Anexo incluido com sucesso.")
            return redirect("contrato_detail", pk=self.object.pk)
        return self.render_to_response(self.get_context_data(anexo_form=form))


class AditivoContratoCreateView(CreateView):
    model = AditivoContrato
    form_class = AditivoContratoForm

    def post(self, request, *args, **kwargs):
        contrato = get_object_or_404(Compromisso, pk=kwargs.get("pk"), tipo="CONTRATO")

        aditivo_form = self.form_class(request.POST)

        tipo_post = request.POST.get("tipo") or "VALOR"
        aditivo_instance = AditivoContrato(contrato=contrato, tipo=tipo_post)
        if aditivo_form.is_valid():
            aditivo_instance = aditivo_form.save(commit=False)
            aditivo_instance.contrato = contrato

        centros_queryset = obter_centros_do_contrato(contrato)
        aditivo_item_formset = AditivoContratoItemFormSet(
            request.POST,
            instance=aditivo_instance,
            prefix="aditivos_itens",
            centros_queryset=centros_queryset,
        )

        if aditivo_form.is_valid() and aditivo_item_formset.is_valid():
            aditivo_instance.save()
            aditivo_item_formset.instance = aditivo_instance
            aditivo_item_formset.save()
            _registrar_historico(
                "ADITIVO",
                contrato,
                f"Aditivo {aditivo_instance.get_tipo_display()} incluído no contrato {contrato.numero}",
            )
            messages.success(request, "Aditivo incluído com sucesso.")
            return redirect("contrato_detail", pk=contrato.pk)

        # Erros: renderiza na mesma tela do contrato.
        detail_view = ContratoDetailView()
        detail_view.request = request
        detail_view.object = contrato
        context = detail_view.get_context_data(aditivo_form=aditivo_form, aditivo_item_formset=aditivo_item_formset)
        return render(request, detail_view.template_name, context)


def compromisso_delete_view(request):
    return _apagar_objeto(request, Compromisso.objects.all(), "compromisso_list")


def compromisso_export_view(request):
    queryset = (
        Compromisso.objects.select_related("centro_custo")
        .prefetch_related(Prefetch("itens", queryset=CompromissoItem.objects.select_related("centro_custo")))
        .order_by("-id")
    )
    queryset = _filtrar_por_obra_contexto(request, queryset)
    queryset = _filtros_compromissos(request, queryset)
    linhas = [
        {
            "Numero": compromisso.numero,
            "Tipo": compromisso.get_tipo_display(),
            "CNPJ": compromisso.cnpj,
            "Fornecedor": compromisso.fornecedor,
            "Descricao": compromisso.descricao,
            "Centros de Custo": " | ".join(
                f"{item.centro_custo.codigo} - {item.centro_custo.descricao}" for item in compromisso.itens.all()
            ),
            "Quantidade": compromisso.quantidade_total,
            "Valor Unitario": compromisso.valor_unitario_medio,
            "Valor Total": compromisso.valor_contratado,
            "Valor Executado": compromisso.valor_executado,
            "Saldo": compromisso.saldo,
            "Responsavel": compromisso.responsavel,
            "Data": compromisso.data_assinatura.strftime("%d/%m/%Y"),
        }
        for compromisso in queryset
    ]
    return _exportar_excel_response("compras_contratacoes.xlsx", "Compras", linhas)


def compromisso_pdf_view(request, pk):
    queryset = (
        Compromisso.objects.select_related("obra", "centro_custo")
        .prefetch_related(Prefetch("itens", queryset=CompromissoItem.objects.select_related("centro_custo")))
    )
    compromisso = get_object_or_404(_filtrar_por_obra_contexto(request, queryset), pk=pk)
    linhas = [
        f"Tipo: {compromisso.get_tipo_display()}",
        f"Obra: {compromisso.obra.codigo if compromisso.obra else '-'} - {compromisso.obra.nome if compromisso.obra else '-'}",
        f"Fornecedor: {compromisso.fornecedor}",
        f"CNPJ: {compromisso.cnpj}",
        f"Responsavel: {compromisso.responsavel}",
        f"Status: {compromisso.get_status_display()}",
        f"Data: {compromisso.data_assinatura.strftime('%d/%m/%Y')}",
        f"Descricao: {compromisso.descricao}",
        f"Valor total: {compromisso.valor_contratado}",
        "",
        "Itens:",
    ]
    for item in compromisso.itens.all():
        linhas.append(
            f"- {item.centro_custo.codigo} | {item.centro_custo.descricao} | {item.quantidade} {item.unidade or '-'} | {item.valor_total}"
        )
    return _pdf_simples_response(f"{compromisso.numero}.pdf", f"Compras e Contratacoes {compromisso.numero}", linhas)


class MedicaoListView(ListView):
    model = Medicao
    template_name = "app/medicao_list.html"
    context_object_name = "medicoes"
    def get_queryset(self):
        queryset = (
            Medicao.objects.select_related("contrato", "centro_custo", "obra")
            .prefetch_related(Prefetch("itens", queryset=MedicaoItem.objects.select_related("centro_custo")))
            .order_by("-data_medicao", "-id")
        )
        queryset = _filtrar_por_obra_contexto(self.request, queryset)
        return _filtros_medicoes(self.request, queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busca"] = self.request.GET.get("q", "").strip()
        context["obra_filtro"] = self.request.GET.get("obra", "").strip()
        context["status_filtro"] = self.request.GET.get("status", "").strip()
        context["fornecedor_filtro"] = self.request.GET.get("fornecedor", "").strip()
        context["responsavel_filtro"] = self.request.GET.get("responsavel", "").strip()
        context["contrato_filtro"] = self.request.GET.get("contrato", "").strip()
        context["centro_custo_filtro"] = self.request.GET.get("centro_custo", "").strip()
        context["data_inicio"] = self.request.GET.get("data_inicio", "").strip()
        context["data_fim"] = self.request.GET.get("data_fim", "").strip()
        context["obras"] = Obra.objects.order_by("codigo")
        context["centros_custo"] = _filtrar_por_obra_contexto(self.request, PlanoContas.objects.order_by("tree_id", "lft"))
        context["status_choices"] = Medicao._meta.get_field("status").choices
        return context


class MedicaoCreateView(CreateView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obra_contexto"] = _obter_obra_contexto(self.request)
        return kwargs

    model = Medicao
    form_class = MedicaoForm
    template_name = "app/medicao_form.html"
    success_url = reverse_lazy("medicao_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contrato = _obter_contrato_from_request(self.request)
        context["titulo"] = "Nova Medição"
        context["voltar_url"] = reverse_lazy("medicao_list")
        context["item_formset"] = kwargs.get("item_formset") or _construir_formset_medicao(prefix="itens", contrato=contrato)
        return context

    def form_valid(self, form):
        contrato = form.cleaned_data.get("contrato")
        item_formset = _construir_formset_medicao(data=self.request.POST, prefix="itens", contrato=contrato)
        if not item_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, item_formset=item_formset))

        self.object = form.save()
        item_formset.instance = self.object
        item_formset.save()
        self.object.recalcular_totais_por_itens()
        _registrar_historico("CRIACAO", self.object, f"Medicao criada: {self.object.numero_da_medicao}")
        return HttpResponseRedirect(self.get_success_url())


class MedicaoUpdateView(UpdateView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obra_contexto"] = _obter_obra_contexto(self.request)
        return kwargs

    model = Medicao
    form_class = MedicaoForm
    template_name = "app/medicao_form.html"
    success_url = reverse_lazy("medicao_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contrato = _obter_contrato_from_request(self.request, self.object)
        context["titulo"] = f"Editar Medição {self.object.numero_da_medicao}"
        context["voltar_url"] = reverse_lazy("medicao_list")
        context["item_formset"] = kwargs.get("item_formset") or _construir_formset_medicao(instance=self.object, prefix="itens", contrato=contrato)
        return context

    def form_valid(self, form):
        contrato = form.cleaned_data.get("contrato")
        item_formset = _construir_formset_medicao(data=self.request.POST, instance=self.object, prefix="itens", contrato=contrato)
        if not item_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, item_formset=item_formset))

        self.object = form.save()
        item_formset.instance = self.object
        item_formset.save()
        self.object.recalcular_totais_por_itens()
        _registrar_historico("ATUALIZACAO", self.object, f"Medicao atualizada: {self.object.numero_da_medicao}")
        return HttpResponseRedirect(self.get_success_url())


class MedicaoDetailView(DetailView):
    model = Medicao
    template_name = "app/medicao_detail.html"
    context_object_name = "medicao"

    def get_queryset(self):
        queryset = (
            Medicao.objects.select_related("contrato", "obra")
            .prefetch_related(
                Prefetch("itens", queryset=MedicaoItem.objects.select_related("centro_custo")),
                "notas_fiscais",
                "anexos",
                "historicos",
            )
        )
        return _filtrar_por_obra_contexto(self.request, queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        medicao = self.object
        context["notas_medicao"] = medicao.notas_fiscais.order_by("-data_emissao")
        context["anexo_form"] = kwargs.get("anexo_form") or AnexoOperacionalForm()
        context["saldo_percentual"] = _calcular_percentual(medicao.valor_medido, medicao.contrato.valor_contratado) if medicao.contrato.valor_contratado else 0
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = AnexoOperacionalForm(request.POST, request.FILES)
        if form.is_valid():
            anexo = form.save(commit=False)
            anexo.medicao = self.object
            anexo.obra = self.object.obra
            anexo.save()
            _registrar_historico("ANEXO", self.object, f"Anexo incluido na medicao {self.object.numero_da_medicao}")
            messages.success(request, "Anexo incluido com sucesso.")
            return redirect("medicao_detail", pk=self.object.pk)
        return self.render_to_response(self.get_context_data(anexo_form=form))


def medicao_delete_view(request):
    return _apagar_objeto(request, Medicao.objects.all(), "medicao_list")


def medicao_export_view(request):
    queryset = (
        Medicao.objects.select_related("contrato", "centro_custo")
        .prefetch_related(Prefetch("itens", queryset=MedicaoItem.objects.select_related("centro_custo")))
        .order_by("-data_medicao", "-id")
    )
    queryset = _filtrar_por_obra_contexto(request, queryset)
    queryset = _filtros_medicoes(request, queryset)
    linhas = [
        {
            "Numero": medicao.numero_da_medicao,
            "Contrato": medicao.contrato.numero,
            "CNPJ": medicao.cnpj,
            "Fornecedor": medicao.fornecedor,
            "Descricao": medicao.descricao,
            "Itens Medidos": " | ".join(
                f"{item.centro_custo.codigo} - {item.centro_custo.descricao}" for item in medicao.itens.all()
            ),
            "Quantidade": medicao.quantidade_total,
            "Valor Unitario": medicao.valor_unitario_medio,
            "Valor Total": medicao.valor_medido,
            "Responsavel": medicao.responsavel,
            "Data": medicao.data_medicao.strftime("%d/%m/%Y"),
        }
        for medicao in queryset
    ]
    return _exportar_excel_response("medicoes.xlsx", "Medicoes", linhas)


class NotaFiscalListView(ListView):
    model = NotaFiscal
    template_name = "app/nota_fiscal_list.html"
    context_object_name = "notas_fiscais"
    def get_queryset(self):
        queryset = (
            NotaFiscal.objects.select_related("medicao", "pedido_compra", "obra")
            .prefetch_related("centros_custo__centro_custo")
            .order_by("-data_emissao", "-id")
        )
        queryset = _filtrar_por_obra_contexto(self.request, queryset)
        return _filtros_notas(self.request, queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["busca"] = self.request.GET.get("q", "").strip()
        context["obra_filtro"] = self.request.GET.get("obra", "").strip()
        context["status_filtro"] = self.request.GET.get("status", "").strip()
        context["fornecedor_filtro"] = self.request.GET.get("fornecedor", "").strip()
        context["contrato_filtro"] = self.request.GET.get("contrato", "").strip()
        context["centro_custo_filtro"] = self.request.GET.get("centro_custo", "").strip()
        context["data_inicio"] = self.request.GET.get("data_inicio", "").strip()
        context["data_fim"] = self.request.GET.get("data_fim", "").strip()
        context["obras"] = Obra.objects.order_by("codigo")
        context["centros_custo"] = _filtrar_por_obra_contexto(self.request, PlanoContas.objects.order_by("tree_id", "lft"))
        context["status_choices"] = NotaFiscal._meta.get_field("status").choices
        return context


class NotaFiscalCreateView(CreateView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obra_contexto"] = _obter_obra_contexto(self.request)
        return kwargs

    model = NotaFiscal
    form_class = NotaFiscalForm
    template_name = "app/nota_fiscal_form.html"
    success_url = reverse_lazy("nota_fiscal_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pedido, medicao = _obter_origem_nota(self.request)
        obra_contexto = _obter_obra_contexto(self.request)
        context["titulo"] = "Nova Nota Fiscal"
        context["voltar_url"] = reverse_lazy("nota_fiscal_list")
        context["rateio_formset"] = kwargs.get("rateio_formset") or _construir_formset_nota(prefix="rateio", pedido=pedido, medicao=medicao, obra=obra_contexto)
        return context

    def form_valid(self, form):
        pedido = form.cleaned_data.get("pedido_compra")
        medicao = form.cleaned_data.get("medicao")
        obra_contexto = _obter_obra_contexto(self.request)
        self.object = form.save(commit=False)
        rateio_formset = _construir_formset_nota(
            data=self.request.POST,
            instance=self.object,
            prefix="rateio",
            pedido=pedido,
            medicao=medicao,
            obra=obra_contexto,
        )
        if not rateio_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, rateio_formset=rateio_formset))

        self.object.save()
        rateio_formset.instance = self.object
        rateio_formset.save()
        _registrar_historico("CRIACAO", self.object, f"Nota fiscal criada: {self.object.numero}")
        return HttpResponseRedirect(self.get_success_url())


class NotaFiscalUpdateView(UpdateView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obra_contexto"] = _obter_obra_contexto(self.request)
        return kwargs

    model = NotaFiscal
    form_class = NotaFiscalForm
    template_name = "app/nota_fiscal_form.html"
    success_url = reverse_lazy("nota_fiscal_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pedido, medicao = _obter_origem_nota(self.request, self.object)
        obra_contexto = _obter_obra_contexto(self.request)
        context["titulo"] = f"Editar Nota Fiscal {self.object.numero}"
        context["voltar_url"] = reverse_lazy("nota_fiscal_list")
        context["rateio_formset"] = kwargs.get("rateio_formset") or _construir_formset_nota(
            instance=self.object,
            prefix="rateio",
            pedido=pedido,
            medicao=medicao,
            obra=obra_contexto,
        )
        return context

    def form_valid(self, form):
        pedido = form.cleaned_data.get("pedido_compra")
        medicao = form.cleaned_data.get("medicao")
        obra_contexto = _obter_obra_contexto(self.request)
        self.object = form.save(commit=False)
        rateio_formset = _construir_formset_nota(
            data=self.request.POST,
            instance=self.object,
            prefix="rateio",
            pedido=pedido,
            medicao=medicao,
            obra=obra_contexto,
        )
        if not rateio_formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, rateio_formset=rateio_formset))

        self.object.save()
        rateio_formset.instance = self.object
        rateio_formset.save()
        _registrar_historico("ATUALIZACAO", self.object, f"Nota fiscal atualizada: {self.object.numero}")
        return HttpResponseRedirect(self.get_success_url())


class FechamentoMensalView(TemplateView):
    template_name = "app/fechamento_mensal.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        obra_id = self.request.GET.get("obra", "").strip()
        hoje = date.today()
        ano = int(self.request.GET.get("ano") or hoje.year)
        mes = int(self.request.GET.get("mes") or hoje.month)
        obras = Obra.objects.order_by("codigo")
        if obra_id:
            obra = obras.filter(pk=obra_id).first()
        elif obra_contexto:
            obra = obra_contexto
        else:
            obra = obras.first()
        compromissos = Compromisso.objects.filter(data_assinatura__year=ano, data_assinatura__month=mes)
        medicoes = Medicao.objects.filter(data_medicao__year=ano, data_medicao__month=mes)
        notas = NotaFiscal.objects.filter(data_emissao__year=ano, data_emissao__month=mes)
        if obra:
            compromissos = compromissos.filter(obra=obra)
            medicoes = medicoes.filter(obra=obra)
            notas = notas.filter(obra=obra)
        itens_compromisso = CompromissoItem.objects.select_related("centro_custo", "compromisso").filter(
            compromisso__data_assinatura__year=ano,
            compromisso__data_assinatura__month=mes,
        )
        itens_medicao = MedicaoItem.objects.select_related("centro_custo", "medicao").filter(
            medicao__data_medicao__year=ano,
            medicao__data_medicao__month=mes,
        )
        rateios_nota = NotaFiscalCentroCusto.objects.select_related("centro_custo", "nota_fiscal").filter(
            nota_fiscal__data_emissao__year=ano,
            nota_fiscal__data_emissao__month=mes,
        )
        if obra:
            itens_compromisso = itens_compromisso.filter(compromisso__obra=obra)
            itens_medicao = itens_medicao.filter(medicao__obra=obra)
            rateios_nota = rateios_nota.filter(nota_fiscal__obra=obra)

        plano_qs = PlanoContas.objects.filter(obra=obra) if obra else PlanoContas.objects.all()
        nodes_by_id = {n.id: n for n in plano_qs.only("id", "parent_id", "level", "codigo", "descricao")}

        def get_nivel5_ancestor(node_id):
            node = nodes_by_id.get(node_id)
            while node:
                if node.level == 4:
                    return node
                if node.level < 4:
                    return None
                node = nodes_by_id.get(node.parent_id)
            return None

        resumo_nivel5 = defaultdict(lambda: {"comprometido": Decimal("0.00"), "medido": Decimal("0.00"), "notas": Decimal("0.00")})
        for item in itens_compromisso:
            anc = get_nivel5_ancestor(item.centro_custo_id)
            if anc:
                resumo_nivel5[anc.id]["centro"] = anc
                resumo_nivel5[anc.id]["comprometido"] += item.valor_total or Decimal("0.00")
        for item in itens_medicao:
            anc = get_nivel5_ancestor(item.centro_custo_id)
            if anc:
                resumo_nivel5[anc.id]["centro"] = anc
                resumo_nivel5[anc.id]["medido"] += item.valor_total or Decimal("0.00")
        for item in rateios_nota:
            anc = get_nivel5_ancestor(item.centro_custo_id)
            if anc:
                resumo_nivel5[anc.id]["centro"] = anc
                resumo_nivel5[anc.id]["notas"] += item.valor or Decimal("0.00")

        centros_fechamento = []
        for payload in resumo_nivel5.values():
            if not payload.get("centro"):
                continue
            centros_fechamento.append(
                {
                    "centro": payload["centro"],
                    "comprometido": payload["comprometido"],
                    "medido": payload["medido"],
                    "notas": payload["notas"],
                    "saldo_a_medir": payload["comprometido"] - payload["medido"],
                    "saldo_a_executar": payload["comprometido"] - payload["notas"],
                }
            )
        centros_fechamento.sort(key=lambda item: item["centro"].codigo if item.get("centro") else "")
        context["obras"] = obras
        context["obra_atual"] = obra
        context["ano"] = ano
        context["mes"] = mes
        context["resumo"] = {
            "valor_comprometido": compromissos.aggregate(total=Sum("valor_contratado"))["total"] or Decimal("0.00"),
            "valor_medido": medicoes.aggregate(total=Sum("valor_medido"))["total"] or Decimal("0.00"),
            "valor_notas": notas.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00"),
            "qtd_compromissos": compromissos.count(),
            "qtd_medicoes": medicoes.count(),
            "qtd_notas": notas.count(),
        }
        context["resumo_centros"] = centros_fechamento
        context["fechamentos"] = FechamentoMensal.objects.select_related("obra").order_by("-ano", "-mes")[:12]
        return context

    def post(self, request, *args, **kwargs):
        def _parse_int_br(value, default=None):
            if value is None:
                return default
            raw = str(value).strip()
            if not raw:
                return default
            # Aceita "2.026" (separador de milhar pt-BR) e também "2,026" por engano.
            raw = raw.replace(".", "").replace(",", "")
            return int(raw)

        obra = get_object_or_404(Obra, pk=request.POST.get("obra"))
        ano = _parse_int_br(request.POST.get("ano"))
        mes = _parse_int_br(request.POST.get("mes"))
        fechamento, _ = FechamentoMensal.objects.update_or_create(
            obra=obra,
            ano=ano,
            mes=mes,
            defaults={
                "valor_comprometido": Compromisso.objects.filter(obra=obra, data_assinatura__year=ano, data_assinatura__month=mes).aggregate(total=Sum("valor_contratado"))["total"] or Decimal("0.00"),
                "valor_medido": Medicao.objects.filter(obra=obra, data_medicao__year=ano, data_medicao__month=mes).aggregate(total=Sum("valor_medido"))["total"] or Decimal("0.00"),
                "valor_notas": NotaFiscal.objects.filter(obra=obra, data_emissao__year=ano, data_emissao__month=mes).aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00"),
            },
        )
        _registrar_historico("FECHAMENTO", obra, f"Fechamento mensal registrado: {fechamento}")
        messages.success(request, "Fechamento mensal registrado com sucesso.")
        return redirect(f"{reverse_lazy('fechamento_mensal')}?obra={obra.pk}&ano={ano}&mes={mes}")


class ProjecaoFinanceiraView(TemplateView):
    template_name = "app/projecao_financeira.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        meses_opcoes = [6, 12]
        meses_qtd = int(self.request.GET.get("meses") or 12)
        if meses_qtd not in meses_opcoes:
            meses_qtd = 12

        hoje = date.today()
        inicio = date(hoje.year, hoje.month, 1)

        def add_one_month(d):
            if d.month == 12:
                return d.replace(year=d.year + 1, month=1, day=1)
            return d.replace(month=d.month + 1, day=1)

        month_starts = [inicio]
        for _ in range(meses_qtd - 1):
            month_starts.append(add_one_month(month_starts[-1]))

        fim_exclusivo = add_one_month(month_starts[-1])
        idx_by_month = {m: i for i, m in enumerate(month_starts)}

        entradas = [Decimal("0.00") for _ in month_starts]
        saidas = [Decimal("0.00") for _ in month_starts]

        notas_qs = NotaFiscal.objects.filter(data_emissao__gte=inicio, data_emissao__lt=fim_exclusivo)
        notas_qs = _filtrar_por_obra_contexto(self.request, notas_qs)
        for nota in notas_qs:
            m = date(nota.data_emissao.year, nota.data_emissao.month, 1)
            idx = idx_by_month.get(m)
            if idx is not None:
                entradas[idx] += nota.valor_total or Decimal("0.00")

        medicoes_qs = Medicao.objects.select_related("contrato").all()
        medicoes_qs = _filtrar_por_obra_contexto(self.request, medicoes_qs)
        medicoes = list(medicoes_qs)
        contrato_ids = {m.contrato_id for m in medicoes if getattr(m, "contrato_id", None)}

        prazo_deltas = {}
        if contrato_ids:
            prazo_totais = (
                AditivoContrato.objects.filter(contrato_id__in=contrato_ids, tipo="PRAZO")
                .values("contrato_id")
                .annotate(total=Sum("delta_dias"))
            )
            for row in prazo_totais:
                prazo_deltas[row["contrato_id"]] = row["total"] or 0

        # Distribui o valor medido pelos meses "previstos" (shiftados por PRAZO).
        for m in medicoes:
            valor_medido = m.valor_medido or Decimal("0.00")
            if not valor_medido:
                continue

            med_start_raw = m.data_prevista_inicio or m.data_medicao
            med_end_raw = m.data_prevista_fim or med_start_raw
            delta = prazo_deltas.get(m.contrato_id, 0) or 0
            med_start = med_start_raw + timedelta(days=delta)
            med_end = med_end_raw + timedelta(days=delta)

            med_start_m = med_start.replace(day=1)
            med_end_m = med_end.replace(day=1)

            total_meses_intervalo = ((med_end_m.year - med_start_m.year) * 12 + (med_end_m.month - med_start_m.month) + 1)
            total_meses_intervalo = max(1, total_meses_intervalo)

            share = valor_medido / Decimal(total_meses_intervalo)
            for i, ms in enumerate(month_starts):
                if ms >= med_start_m and ms <= med_end_m:
                    saidas[i] += share

        series = []
        for i, ms in enumerate(month_starts):
            entrada = entradas[i]
            saida = saidas[i]
            saldo = arredondar_moeda(entrada - saida)
            series.append(
                {
                    "label": ms.strftime("%m/%Y"),
                    "entrada": arredondar_moeda(entrada),
                    "saida": arredondar_moeda(saida),
                    "saldo": saldo,
                }
            )

        context["meses_opcoes"] = meses_opcoes
        context["meses_qtd"] = meses_qtd
        context["series"] = series
        context["total_entradas"] = arredondar_moeda(sum(s["entrada"] for s in series))
        context["total_saidas"] = arredondar_moeda(sum(s["saida"] for s in series))
        context["total_saldo"] = arredondar_moeda(context["total_entradas"] - context["total_saidas"])
        return context


def nota_fiscal_delete_view(request):
    return _apagar_objeto(request, NotaFiscal.objects.all(), "nota_fiscal_list")


def nota_fiscal_export_view(request):
    queryset = (
        NotaFiscal.objects.select_related("medicao", "pedido_compra")
        .prefetch_related("centros_custo__centro_custo")
        .order_by("-data_emissao", "-id")
    )
    queryset = _filtrar_por_obra_contexto(request, queryset)
    queryset = _filtros_notas(request, queryset)
    linhas = [
        {
            "ID": nota.id,
            "Numero da Nota": nota.numero,
            "Origem": str(nota.medicao or nota.pedido_compra or ""),
            "CNPJ": nota.cnpj,
            "Fornecedor": nota.fornecedor,
            "Descricao": nota.descricao,
            "Centro de Custo": " | ".join(
                f"{item.centro_custo.codigo} - {item.centro_custo.descricao}" for item in nota.centros_custo.all()
            ),
            "Valor": nota.valor_total,
            "Data": nota.data_emissao.strftime("%d/%m/%Y"),
        }
        for nota in queryset
    ]
    return _exportar_excel_response("notas_fiscais.xlsx", "Notas Fiscais", linhas)


def contrato_dados_view(request, pk):
    contrato = get_object_or_404(
        _filtrar_por_obra_contexto(
            request,
            Compromisso.objects.prefetch_related(Prefetch("itens", queryset=CompromissoItem.objects.select_related("centro_custo"))),
        ),
        pk=pk,
    )
    return JsonResponse(obter_dados_contrato(contrato))


def medicao_dados_view(request, pk):
    medicao = get_object_or_404(
        _filtrar_por_obra_contexto(
            request,
            Medicao.objects.prefetch_related(Prefetch("itens", queryset=MedicaoItem.objects.select_related("centro_custo"))),
        ),
        pk=pk,
    )
    return JsonResponse(obter_dados_medicao(medicao))


@login_required
def plano_contas_importar_view(request):
    """View para importar plano de contas vinculado a obra do contexto."""
    obra_contexto = _obter_obra_contexto(request)
    
    if not obra_contexto:
        messages.error(request, "Selecione uma obra no menu antes de importar o plano de contas.")
        return redirect("plano_contas_list")
    
    if request.method == "POST":
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            messages.error(request, "Selecione um arquivo para importar.")
            return render(request, "app/plano_contas_importar.html", {"obra_contexto": obra_contexto})
        
        try:
            importar_plano_contas_excel(arquivo, obra=obra_contexto)
            messages.success(request, "Plano de contas importado com sucesso!")
            return redirect("plano_contas_list")
        except ValidationError as e:
            messages.error(request, str(e.message) if hasattr(e, "message") else str(e))
        except Exception as e:
            messages.error(request, f"Erro ao importar: {str(e)}")
    
    return render(request, "app/plano_contas_importar.html", {"obra_contexto": obra_contexto})
