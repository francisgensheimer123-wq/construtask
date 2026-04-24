from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.generic import TemplateView

from .application.financeiro import dados_fechamento_mensal_request, dados_projecao_financeira_request, registrar_fechamento_mensal
from .permissions import get_empresa_operacional as _get_empresa_operacional
from .services_jobs import listar_jobs_recentes
from .services_lgpd import registrar_acesso_dado_pessoal
from .approval_helpers import _registrar_historico
from .export_helpers import _datahora_local, _exportar_excel_response, _pdf_relatorio_probatorio_response
from .templatetags.formatters import money_br
from .models import Obra
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy


class FechamentoMensalView(TemplateView):
    template_name = "app/fechamento_mensal.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(dados_fechamento_mensal_request(self.request))
        context["jobs_recentes"] = listar_jobs_recentes(
            empresa=getattr(context.get("obra_atual"), "empresa", _get_empresa_operacional(self.request)),
            obra=context.get("obra_atual"),
            limite=8,
        )
        return context

    def post(self, request, *args, **kwargs):
        def _parse_int_br(value, default=None):
            if value is None:
                return default
            raw = str(value).strip()
            if not raw:
                return default
            raw = raw.replace(".", "").replace(",", "")
            return int(raw)

        obra = get_object_or_404(Obra, pk=request.POST.get("obra"))
        ano = _parse_int_br(request.POST.get("ano"))
        mes = _parse_int_br(request.POST.get("mes"))
        fechamento = registrar_fechamento_mensal(obra=obra, ano=ano, mes=mes)
        _registrar_historico("FECHAMENTO", obra, f"Fechamento mensal registrado: {fechamento}", request.user)
        messages.success(request, "Fechamento mensal registrado com sucesso.")
        return redirect(f"{reverse_lazy('fechamento_mensal')}?obra={obra.pk}&ano={ano}&mes={mes}")


@login_required
def fechamento_mensal_export_view(request):
    dados = dados_fechamento_mensal_request(request)
    registrar_acesso_dado_pessoal(
        request,
        categoria_titular="TERCEIRO",
        entidade="FechamentoMensal",
        identificador=f"{dados['mes']:02d}/{dados['ano']}",
        acao="EXPORT",
        finalidade="Exportacao de consolidacao financeira da obra",
        detalhes="Exportacao Excel do fechamento mensal.",
    )
    linhas = [
        {
            "Centro de Custo": f'{linha["centro"].codigo} - {linha["centro"].descricao}',
            "Comprometido": linha["comprometido"],
            "Medido": linha["medido"],
            "Notas": linha["notas"],
            "Saldo a Medir": linha["saldo_a_medir"],
            "Saldo a Executar": linha["saldo_a_executar"],
        }
        for linha in dados["resumo_centros"]
    ]
    return _exportar_excel_response("fechamento_mensal.xlsx", "Fechamento Mensal", linhas)


@login_required
def fechamento_mensal_pdf_view(request):
    dados = dados_fechamento_mensal_request(request)
    resumo = {
        "Obra": f'{dados["obra_atual"].codigo} - {dados["obra_atual"].nome}' if dados["obra_atual"] else "-",
        "Periodo": f'{dados["mes"]:02d}/{dados["ano"]}',
        "Comprometido": money_br(dados["resumo"]["valor_comprometido"]),
        "Medido": money_br(dados["resumo"]["valor_medido"]),
        "Notas": money_br(dados["resumo"]["valor_notas"]),
    }
    extras = [
        {
            "Centro de Custo": f'{linha["centro"].codigo} - {linha["centro"].descricao}',
            "Comprometido": money_br(linha["comprometido"]),
            "Medido": money_br(linha["medido"]),
            "Notas": money_br(linha["notas"]),
            "Saldo": money_br(linha["saldo_a_executar"]),
        }
        for linha in dados["resumo_centros"]
    ]
    return _pdf_relatorio_probatorio_response(
        "fechamento_mensal.pdf",
        "Fechamento Mensal",
        resumo,
        [],
        extras,
        extras_titulo="Consolidado por Centro de Custo",
        extras_colunas=[("Centro de Custo", 215), ("Comprometido", 70), ("Medido", 70), ("Notas", 70), ("Saldo", 70)],
        incluir_historico=False,
    )


class ProjecaoFinanceiraView(TemplateView):
    template_name = "app/projecao_financeira.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(dados_projecao_financeira_request(self.request))
        return context


@login_required
def projecao_financeira_export_view(request):
    dados = dados_projecao_financeira_request(request)
    registrar_acesso_dado_pessoal(
        request,
        categoria_titular="TERCEIRO",
        entidade="ProjecaoFinanceira",
        identificador="Horizonte financeiro",
        acao="EXPORT",
        finalidade="Exportacao de previsao financeira da obra",
        detalhes="Exportacao Excel da projecao financeira.",
    )
    linhas = [
        {"Mes": item["label"], "Executado": item["executado"], "Saidas": item["saida"], "Saldo": item["saldo"]}
        for item in dados["series"]
    ]
    return _exportar_excel_response("projecao_financeira.xlsx", "Projecao Financeira", linhas)


@login_required
def projecao_financeira_pdf_view(request):
    dados = dados_projecao_financeira_request(request)
    resumo = {
        "Total Orcado": money_br(dados["total_orcado"]),
        "Total Executado": money_br(dados["total_executado"]),
        "Total Saidas": money_br(dados["total_saidas"]),
        "Saldo no Horizonte": money_br(dados["total_saldo"]),
    }
    extras = [
        {"Mes": item["label"], "Executado": money_br(item["executado"]), "Saidas": money_br(item["saida"]), "Saldo": money_br(item["saldo"])}
        for item in dados["series"]
    ]
    return _pdf_relatorio_probatorio_response(
        "projecao_financeira.pdf",
        "Projecao Financeira",
        resumo,
        [],
        extras,
        extras_titulo="Visao Mensal",
        extras_colunas=[("Mes", 80), ("Executado", 135), ("Saidas", 135), ("Saldo", 145)],
        incluir_historico=False,
    )
