from ..application.observabilidade import contexto_observabilidade_request
from ..application.saas import contexto_base_saas, diagnostico_base_saas
from ..application.jobs import contexto_jobs_request
from ..queries.operacao import construir_alertas_operacionais_tecnicos


CHECKLIST_DEPLOY = [
    {"fase": "Pre-deploy", "itens": [
        "Executar migrate no ambiente alvo.",
        "Validar health e readiness apos aplicacao das variaveis.",
        "Confirmar storage persistente, backup e restore recentes.",
    ]},
    {"fase": "Deploy", "itens": [
        "Publicar a release com configuracao segura e worker ativo.",
        "Verificar jobs assincronos e filas logo apos a subida.",
        "Monitorar erros 500 e endpoints lentos nos primeiros minutos.",
    ]},
    {"fase": "Pos-deploy", "itens": [
        "Abrir dashboard tecnico e revisar alertas operacionais.",
        "Executar smoke test de login, home, cronogramas e relatorios principais.",
        "Registrar evidencias da versao, horario e responsavel pelo deploy.",
    ]},
]

CHECKLIST_ROLLBACK = [
    "Interromper novas operacoes pesadas e comunicar a janela de reversao.",
    "Reaplicar a release estavel anterior e validar readiness.",
    "Executar smoke test minimo apos rollback.",
    "Registrar causa, impacto e plano corretivo antes do novo deploy.",
]

ROTINAS_ACOMPANHAMENTO = [
    {"frequencia": "Diaria", "itens": [
        "Revisar alertas tecnicos criticos e jobs falhos.",
        "Conferir erros 500, latencia alta e readiness.",
        "Confirmar se o backup anterior foi registrado.",
    ]},
    {"frequencia": "Semanal", "itens": [
        "Aplicar retencao de observabilidade.",
        "Executar diagnostico de latencia operacional.",
        "Validar amostra de documentos em storage persistente.",
    ]},
    {"frequencia": "Mensal", "itens": [
        "Executar e registrar teste de recuperacao.",
        "Revisar configuracoes de seguranca e dominios confiaveis.",
        "Atualizar baseline de riscos tecnicos e pendencias estruturais.",
    ]},
]


def contexto_operacao_request(request):
    contexto_observabilidade = contexto_observabilidade_request(request)
    contexto_jobs = contexto_jobs_request(request, limite=15)
    contexto_saas = contexto_base_saas()
    diagnostico_saas = diagnostico_base_saas()
    alertas = construir_alertas_operacionais_tecnicos(
        diagnostico_saas=diagnostico_saas,
        resumo_jobs=contexto_jobs["resumo_jobs"],
        resumo_metricas=contexto_observabilidade["resumo_metricas"],
        resumo_erros=contexto_observabilidade["resumo_erros"],
        endpoints_lentos=contexto_observabilidade["endpoints_lentos"],
    )
    return {
        **contexto_observabilidade,
        **contexto_jobs,
        **contexto_saas,
        "diagnostico_saas": diagnostico_saas,
        "alertas_operacionais_tecnicos": alertas,
        "checklist_deploy": CHECKLIST_DEPLOY,
        "checklist_rollback": CHECKLIST_ROLLBACK,
        "rotinas_acompanhamento": ROTINAS_ACOMPANHAMENTO,
    }
