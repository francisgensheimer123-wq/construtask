from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import TemplateView

from .application.jobs import (
    contexto_jobs_request,
    enfileirar_importacao_plano_contas,
    enfileirar_relatorio_financeiro,
    enfileirar_sincronizacao_alertas,
)
from .permissions import get_obra_do_contexto
from .queries.jobs import listar_jobs_contexto


class JobAssincronoListView(LoginRequiredMixin, TemplateView):
    template_name = "app/job_assincrono_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(contexto_jobs_request(self.request, limite=30))
        return context


@login_required
def sincronizar_alertas_job_view(request):
    obra = get_obra_do_contexto(request)
    if request.method != "POST":
        return redirect("alerta_operacional_list")
    if not obra:
        messages.error(request, "Selecione uma obra antes de solicitar a sincronizacao dos alertas.")
        return redirect("alerta_operacional_list")
    enfileirar_sincronizacao_alertas(request)
    messages.success(request, "Sincronizacao de alertas enfileirada com sucesso.")
    return redirect("alerta_operacional_list")


@login_required
def plano_contas_importar_job_view(request):
    obra = get_obra_do_contexto(request)
    jobs_recentes = listar_jobs_contexto(empresa=getattr(obra, "empresa", None), obra=obra, limite=10)
    if not obra:
        messages.error(request, "Selecione uma obra no menu antes de importar o plano de contas.")
        return redirect("plano_contas_list")
    if request.method != "POST":
        return render(request, "app/plano_contas_importar.html", {"obra_contexto": obra, "jobs_recentes": jobs_recentes})

    arquivo = request.FILES.get("arquivo")
    if not arquivo:
        messages.error(request, "Selecione um arquivo para importar.")
        return render(
            request,
            "app/plano_contas_importar.html",
            {"obra_contexto": obra, "jobs_recentes": jobs_recentes},
        )

    enfileirar_importacao_plano_contas(request, arquivo)
    messages.success(request, "Importacao enviada para processamento assíncrono.")
    return redirect("jobs_assincronos")


@login_required
def relatorio_financeiro_job_view(request, relatorio):
    obra = get_obra_do_contexto(request)
    if request.method != "POST":
        destino = "fechamento_mensal" if relatorio == "fechamento" else "projecao_financeira"
        return redirect(destino)
    if not obra:
        messages.error(request, "Selecione uma obra antes de solicitar a geracao do relatorio.")
        return redirect("jobs_assincronos")

    if relatorio == "fechamento":
        ano = int(request.POST.get("ano"))
        mes = int(request.POST.get("mes"))
        enfileirar_relatorio_financeiro(request, relatorio="FECHAMENTO_MENSAL", parametros={"ano": ano, "mes": mes})
        redirect_name = f"{reverse('fechamento_mensal')}?obra={obra.pk}&ano={ano}&mes={mes}"
    else:
        meses = int(request.POST.get("meses") or 12)
        enfileirar_relatorio_financeiro(request, relatorio="PROJECAO_FINANCEIRA", parametros={"meses": meses})
        redirect_name = f"{reverse('projecao_financeira')}?meses={meses}"

    messages.success(request, "Relatorio enfileirado para geracao assíncrona.")
    return redirect(redirect_name)
