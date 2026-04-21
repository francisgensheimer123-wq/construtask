from django.utils import timezone

from ..models import ParametroAlertaEmpresa
from ..permissions import get_obra_do_contexto
from ..queries.alertas import (
    enriquecer_alertas_operacionais,
    listar_responsaveis_alerta,
    montar_cards_resumo_alertas,
    montar_resumo_severidade_alertas,
    montar_resumo_status_alertas,
    queryset_alertas_central,
    queryset_alertas_executivos,
    regras_disponiveis_alerta,
)
from ..services_alertas import resumo_executivo_alertas_operacionais, sincronizar_alertas_operacionais_obra
from ..services_aprovacao import can_assume_alert, can_close_alert, can_justify_alert
from ..services_indicadores import IndicadoresService


def acoes_alerta_permitidas(user):
    return {
        "pode_assumir_alerta": can_assume_alert(user),
        "pode_justificar_alerta": can_justify_alert(user),
        "pode_encerrar_alerta": can_close_alert(user),
        "pode_reabrir_alerta": can_close_alert(user),
    }


def obter_contexto_central_alertas(obra_contexto, filtros):
    contexto = {
        "obra_contexto": obra_contexto,
        "sem_obra_selecionada": obra_contexto is None,
        "alertas": [],
        "regras_disponiveis": [],
        "resumo_status": [],
        "resumo_severidade": [],
        "execucoes_recentes": [],
        "catalogo_regras": [],
        "responsaveis_disponiveis": [],
        "resumo_cards": {
            "abertos": 0,
            "em_tratamento": 0,
            "justificados": 0,
            "encerrados": 0,
        },
    }
    if not obra_contexto:
        return contexto

# Sincronização assíncrona via Celery — não bloqueia a requisição
    from ..tasks import task_sincronizar_alertas_obra
    task_sincronizar_alertas_obra.delay(obra_contexto.pk)
    painel_alertas = resumo_executivo_alertas_operacionais(obra_contexto)
    parametros_alerta = ParametroAlertaEmpresa.obter_ou_criar(obra_contexto.empresa)
    alertas = list(queryset_alertas_central(obra_contexto, filtros))
    alertas = enriquecer_alertas_operacionais(alertas, parametros_alerta, timezone.localdate())

    filtro_atraso = (filtros.get("atraso") or "").strip()
    if filtro_atraso == "PRAZO":
        alertas = [alerta for alerta in alertas if alerta.em_atraso_prazo]
    elif filtro_atraso == "SLA":
        alertas = [alerta for alerta in alertas if alerta.em_atraso_sla]
    elif filtro_atraso == "QUALQUER":
        alertas = [alerta for alerta in alertas if alerta.em_atraso_prazo or alerta.em_atraso_sla]

    resumo_status = montar_resumo_status_alertas(obra_contexto)
    return {
        **contexto,
        "alertas": alertas,
        "regras_disponiveis": regras_disponiveis_alerta(obra_contexto),
        "resumo_status": resumo_status,
        "resumo_severidade": montar_resumo_severidade_alertas(obra_contexto),
        "execucoes_recentes": painel_alertas["execucoes_recentes"],
        "catalogo_regras": painel_alertas["catalogo_regras"],
        "responsaveis_disponiveis": listar_responsaveis_alerta(obra_contexto),
        "resumo_cards": montar_cards_resumo_alertas(resumo_status),
    }


def obter_dados_painel_executivo_alertas(request):
    obra_contexto = get_obra_do_contexto(request)
    dados = {
        "obra_contexto": obra_contexto,
        "sem_obra_selecionada": obra_contexto is None,
        "score_operacional": {"componentes": []},
        "prioridades_executivas": [],
        "correlacoes_operacionais": [],
        "alertas_criticos": [],
        "alertas_em_atraso": [],
        "execucoes_recentes": [],
        "catalogo_regras": [],
    }
    if not obra_contexto:
        return dados

    from ..tasks import task_sincronizar_alertas_obra
    task_sincronizar_alertas_obra.delay(obra_contexto.pk)
    painel_alertas = resumo_executivo_alertas_operacionais(obra_contexto)
    indicadores_dashboard = IndicadoresService.resumo_obra(obra_contexto)
    parametros_alerta = ParametroAlertaEmpresa.obter_ou_criar(obra_contexto.empresa)
    alertas_ativos = list(queryset_alertas_executivos(obra_contexto))
    alertas_ativos = enriquecer_alertas_operacionais(alertas_ativos, parametros_alerta, timezone.localdate())
    return {
        **dados,
        "score_operacional": indicadores_dashboard["score_operacional"],
        "prioridades_executivas": painel_alertas["prioridades_executivas"],
        "correlacoes_operacionais": painel_alertas["correlacoes_operacionais"],
        "execucoes_recentes": painel_alertas["execucoes_recentes"],
        "catalogo_regras": painel_alertas["catalogo_regras"],
        "alertas_criticos": [alerta for alerta in alertas_ativos if alerta.severidade == "CRITICA"][:10],
        "alertas_em_atraso": [
            alerta for alerta in alertas_ativos if alerta.em_atraso_prazo or alerta.em_atraso_sla
        ][:12],
    }
