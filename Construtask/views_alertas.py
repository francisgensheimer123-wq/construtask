from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Case, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView, TemplateView

from .application.alertas import (
    acoes_alerta_permitidas,
    obter_contexto_central_alertas,
    obter_dados_painel_executivo_alertas,
)
from .application.jobs import enfileirar_sincronizacao_alertas
from .models import AlertaOperacional
from .permissions import get_obra_do_contexto as _obter_obra_contexto
from .services_alertas import atualizar_status_alerta, obter_regra_operacional
from .services_aprovacao import can_assume_alert, can_close_alert, can_justify_alert
from .views import (
    _exportar_excel_response,
    _grafico_score_operacional,
    _obter_grupos_navegacao,
    _pdf_relatorio_probatorio_response,
)


class CentralAlertasOperacionaisView(LoginRequiredMixin, TemplateView):
    template_name = "app/alerta_operacional_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obra_contexto = _obter_obra_contexto(self.request)
        context["obra_contexto"] = obra_contexto
        context["sem_obra_selecionada"] = obra_contexto is None
        context["grupos_navegacao"] = list(_obter_grupos_navegacao().values())
        context["filtros"] = {
            "status": (self.request.GET.get("status") or "").strip(),
            "severidade": (self.request.GET.get("severidade") or "").strip(),
            "regra": (self.request.GET.get("regra") or "").strip(),
            "responsavel": (self.request.GET.get("responsavel") or "").strip(),
            "atraso": (self.request.GET.get("atraso") or "").strip(),
        }
        context.update(acoes_alerta_permitidas(self.request.user))
        context.update(obter_contexto_central_alertas(obra_contexto, context["filtros"]))
        return context


class PainelExecutivoAlertasView(LoginRequiredMixin, TemplateView):
    template_name = "app/alerta_operacional_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["grupos_navegacao"] = list(_obter_grupos_navegacao().values())
        context.update(obter_dados_painel_executivo_alertas(self.request))
        context["score_operacional_grafico"] = _grafico_score_operacional(context["score_operacional"])
        return context


class AlertaOperacionalDetailView(LoginRequiredMixin, DetailView):
    model = AlertaOperacional
    template_name = "app/alerta_operacional_detail.html"
    context_object_name = "alerta"

    def get_queryset(self):
        obra_contexto = _obter_obra_contexto(self.request)
        queryset = (
            AlertaOperacional.objects.select_related("obra", "responsavel", "ultima_acao_por")
            .prefetch_related("historico__usuario")
        )
        if obra_contexto:
            queryset = queryset.filter(obra=obra_contexto)
        else:
            queryset = queryset.none()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["historico"] = self.object.historico.select_related("usuario").all()
        context["execucoes_regra"] = self.object.execucoes_automacao.all()[:10]
        context["regra_catalogo"] = obter_regra_operacional(self.object.codigo_regra, self.object.obra.empresa)
        context["today"] = timezone.localdate()
        context["grupos_navegacao"] = list(_obter_grupos_navegacao().values())
        context.update(acoes_alerta_permitidas(self.request.user))
        return context


@login_required
def alerta_operacional_workflow_view(request, pk):
    alerta = get_object_or_404(AlertaOperacional.objects.select_related("obra"), pk=pk)
    obra_contexto = _obter_obra_contexto(request)
    if not obra_contexto or alerta.obra_id != obra_contexto.id:
        raise Http404("Alerta operacional nao encontrado para a obra selecionada.")
    if request.method != "POST":
        return redirect("alerta_operacional_detail", pk=alerta.pk)

    acao = (request.POST.get("acao") or "").strip()
    observacao = (request.POST.get("observacao") or "").strip()
    prazo_solucao = (request.POST.get("prazo_solucao_em") or "").strip()
    next_url = (request.POST.get("next") or "").strip() or reverse("alerta_operacional_detail", args=[alerta.pk])

    if acao == "assumir":
        if not can_assume_alert(request.user):
            messages.error(request, "Seu perfil nao pode assumir alertas para tratamento.")
            return redirect(next_url)
        if not prazo_solucao:
            messages.error(request, "Informe o prazo para solucao ao assumir o alerta.")
            return redirect(next_url)
        try:
            prazo_solucao_em = date.fromisoformat(prazo_solucao)
        except ValueError:
            messages.error(request, "Informe um prazo de solucao valido.")
            return redirect(next_url)
        if prazo_solucao_em < timezone.localdate():
            messages.error(request, "O prazo para solucao nao pode estar no passado.")
            return redirect(next_url)
        atualizar_status_alerta(
            alerta,
            novo_status="EM_TRATAMENTO",
            usuario=request.user,
            observacao=observacao or "Alerta assumido para tratamento.",
            responsavel=request.user,
            acao_historico="TRATAMENTO",
            prazo_solucao_em=prazo_solucao_em,
        )
        messages.success(request, "Alerta colocado em tratamento.")
    elif acao == "justificar":
        if not can_justify_alert(request.user):
            messages.error(request, "Seu perfil nao pode justificar alertas operacionais.")
            return redirect(next_url)
        if not observacao:
            messages.error(request, "Informe a justificativa para registrar o alerta.")
            return redirect(next_url)
        atualizar_status_alerta(
            alerta,
            novo_status="JUSTIFICADO",
            usuario=request.user,
            observacao=observacao,
            responsavel=alerta.responsavel or request.user,
            acao_historico="JUSTIFICATIVA",
        )
        messages.success(request, "Justificativa registrada com sucesso.")
    elif acao == "encerrar":
        if not can_close_alert(request.user):
            messages.error(request, "Seu perfil nao pode encerrar alertas operacionais.")
            return redirect(next_url)
        if not observacao:
            messages.error(request, "Informe a evidencia ou comentario de encerramento.")
            return redirect(next_url)
        atualizar_status_alerta(
            alerta,
            novo_status="ENCERRADO",
            usuario=request.user,
            observacao=observacao,
            responsavel=alerta.responsavel or request.user,
            acao_historico="ENCERRAMENTO",
        )
        messages.success(request, "Alerta encerrado com sucesso.")
    elif acao == "reabrir":
        if not can_close_alert(request.user):
            messages.error(request, "Seu perfil nao pode reabrir alertas operacionais.")
            return redirect(next_url)
        atualizar_status_alerta(
            alerta,
            novo_status="ABERTO",
            usuario=request.user,
            observacao=observacao or "Alerta reaberto para acompanhamento.",
            responsavel=request.user,
            acao_historico="REABERTURA",
        )
        messages.success(request, "Alerta reaberto.")
    else:
        messages.error(request, "Acao do alerta nao reconhecida.")

    return redirect(next_url)


@login_required
def alerta_operacional_dashboard_export_view(request):
    dados = obter_dados_painel_executivo_alertas(request)
    if dados["sem_obra_selecionada"]:
        messages.error(request, "Selecione uma obra para exportar o painel executivo de alertas.")
        return redirect("alerta_operacional_dashboard")

    linhas = []
    for item in dados["prioridades_executivas"]:
        linhas.append(
            {
                "Secao": "Prioridades Executivas",
                "Item": item["frente"],
                "Nivel": item["nivel"].upper(),
                "Quantidade": item["total"],
                "Detalhe": item["acao"],
            }
        )
    for item in dados["correlacoes_operacionais"]:
        linhas.append(
            {
                "Secao": "Correlacoes Operacionais",
                "Item": item["titulo"],
                "Nivel": item["nivel"].upper(),
                "Quantidade": item["quantidade"],
                "Detalhe": item["descricao"],
            }
        )
    for alerta in dados["alertas_em_atraso"]:
        linhas.append(
            {
                "Secao": "Alertas em Atraso",
                "Item": alerta.codigo_regra,
                "Nivel": alerta.severidade,
                "Quantidade": 1,
                "Detalhe": f"{alerta.titulo} | Responsavel: {getattr(alerta.responsavel, 'username', '-') or '-'} | Prazo: {alerta.prazo_solucao_em.strftime('%d/%m/%Y') if alerta.prazo_solucao_em else '-'}",
            }
        )
    if not linhas:
        linhas.append({"Secao": "Resumo", "Item": "Sem dados", "Nivel": "-", "Quantidade": 0, "Detalhe": "Nenhum alerta executivo consolidado."})
    return _exportar_excel_response("painel_alertas_operacionais.xlsx", "Painel Alertas Operacionais", linhas)


@login_required
def alerta_operacional_dashboard_pdf_view(request):
    dados = obter_dados_painel_executivo_alertas(request)
    if dados["sem_obra_selecionada"]:
        messages.error(request, "Selecione uma obra para exportar o painel executivo de alertas.")
        return redirect("alerta_operacional_dashboard")

    obra = dados["obra_contexto"]
    score = dados["score_operacional"]
    resumo = {
        "Obra": str(obra),
        "Score Operacional": f"{score.get('pontuacao', Decimal('0.00'))}",
        "Faixa": score.get("faixa", "-"),
        "Alertas criticos": len(dados["alertas_criticos"]),
        "Alertas em atraso": len(dados["alertas_em_atraso"]),
        "Execucoes recentes": len(dados["execucoes_recentes"]),
    }
    secoes = [
        {
            "titulo": "Prioridades Executivas",
            "colunas": [
                {"chave": "Frente", "titulo": "Frente"},
                {"chave": "Nivel", "titulo": "Nivel"},
                {"chave": "Total", "titulo": "Total"},
                {"chave": "Acao", "titulo": "Acao"},
            ],
            "linhas": [
                {
                    "Frente": item["frente"],
                    "Nivel": item["nivel"].upper(),
                    "Total": item["total"],
                    "Acao": item["acao"],
                }
                for item in dados["prioridades_executivas"]
            ],
        },
        {
            "titulo": "Correlacoes Operacionais",
            "colunas": [
                {"chave": "Titulo", "titulo": "Titulo"},
                {"chave": "Nivel", "titulo": "Nivel"},
                {"chave": "Quantidade", "titulo": "Qtd"},
                {"chave": "Descricao", "titulo": "Descricao"},
            ],
            "linhas": [
                {
                    "Titulo": item["titulo"],
                    "Nivel": item["nivel"].upper(),
                    "Quantidade": item["quantidade"],
                    "Descricao": item["descricao"],
                }
                for item in dados["correlacoes_operacionais"]
            ],
        },
    ]
    return _pdf_relatorio_probatorio_response(
        "painel_alertas_operacionais.pdf",
        "Painel Executivo de Alertas",
        resumo,
        [],
        secoes[0]["linhas"] + secoes[1]["linhas"],
        extras_titulo="Leitura Executiva Consolidada",
        extras_colunas=[("Titulo", 160), ("Nivel", 60), ("Quantidade", 55), ("Descricao", 220)],
        incluir_historico=False,
    )
