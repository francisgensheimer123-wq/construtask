from django.core.files.base import ContentFile

from ..permissions import get_empresa_operacional, get_obra_do_contexto
from ..queries.jobs import listar_jobs_contexto, resumir_jobs_contexto
from ..services_jobs import enfileirar_job


def contexto_jobs_request(request, *, limite=30):
    obra = get_obra_do_contexto(request)
    empresa = get_empresa_operacional(request)
    return {
        "obra_contexto": obra,
        "jobs": listar_jobs_contexto(empresa=empresa, obra=obra, limite=limite),
        "resumo_jobs": resumir_jobs_contexto(empresa=empresa, obra=obra),
    }


def enfileirar_sincronizacao_alertas(request):
    obra = get_obra_do_contexto(request)
    if not obra:
        return None
    return enfileirar_job(
        tipo="SINCRONIZAR_ALERTAS_OBRA",
        descricao=f"Sincronizacao operacional de alertas da obra {obra.codigo}",
        solicitado_por=request.user,
        empresa=obra.empresa,
        obra=obra,
        parametros={"obra_id": obra.pk},
    )


def enfileirar_importacao_plano_contas(request, arquivo):
    obra = get_obra_do_contexto(request)
    if not obra:
        return None
    job = enfileirar_job(
        tipo="IMPORTAR_PLANO_CONTAS",
        descricao=f"Importacao de plano de contas da obra {obra.codigo}",
        solicitado_por=request.user,
        empresa=obra.empresa,
        obra=obra,
        parametros={"obra_id": obra.pk, "nome_original": arquivo.name},
    )
    job.arquivo_entrada.save(arquivo.name, ContentFile(arquivo.read()), save=True)
    return job


def enfileirar_relatorio_financeiro(request, *, relatorio, parametros):
    obra = get_obra_do_contexto(request)
    if not obra:
        return None
    if relatorio == "FECHAMENTO_MENSAL":
        descricao = (
            f"Relatorio de fechamento mensal da obra {obra.codigo} "
            f"({int(parametros['mes']):02d}/{int(parametros['ano'])})"
        )
    else:
        descricao = f"Relatorio de projecao financeira da obra {obra.codigo} ({int(parametros['meses'])} meses)"
    return enfileirar_job(
        tipo="GERAR_RELATORIO_FINANCEIRO",
        descricao=descricao,
        solicitado_por=request.user,
        empresa=obra.empresa,
        obra=obra,
        parametros={"relatorio": relatorio, "obra_id": obra.pk, **parametros},
    )
