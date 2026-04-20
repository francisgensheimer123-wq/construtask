from __future__ import annotations

from typing import Any


DEFAULT_STATUS_META = {
    "semantic": "OUTRO",
    "stage_label": "Status operacional",
    "badge": "secondary",
}


STATUS_META_BY_MODEL = {
    "Compromisso": {
        "RASCUNHO": {"semantic": "EM_ELABORACAO", "stage_label": "Em elaboracao", "badge": "secondary"},
        "EM_APROVACAO": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Submetido para validacao", "badge": "warning"},
        "APROVADO": {"semantic": "VALIDADO", "stage_label": "Validado", "badge": "success"},
        "EM_EXECUCAO": {"semantic": "EM_EXECUCAO", "stage_label": "Em execucao", "badge": "primary"},
        "ENCERRADO": {"semantic": "ENCERRADO", "stage_label": "Encerrado", "badge": "dark"},
        "CANCELADO": {"semantic": "CANCELADO", "stage_label": "Cancelado", "badge": "dark"},
    },
    "AditivoContrato": {
        "RASCUNHO": {"semantic": "EM_ELABORACAO", "stage_label": "Em elaboracao", "badge": "secondary"},
        "EM_APROVACAO": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Submetido para validacao", "badge": "warning"},
        "APROVADO": {"semantic": "VALIDADO", "stage_label": "Validado", "badge": "success"},
        "AJUSTE": {"semantic": "AJUSTE", "stage_label": "Devolvido para ajuste", "badge": "info"},
    },
    "Medicao": {
        "EM_ELABORACAO": {"semantic": "EM_ELABORACAO", "stage_label": "Em elaboracao", "badge": "secondary"},
        "EM_APROVACAO": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Submetida para validacao", "badge": "warning"},
        "CONFERIDA": {"semantic": "VALIDADO", "stage_label": "Conferida", "badge": "info"},
        "APROVADA": {"semantic": "VALIDADO", "stage_label": "Aprovada", "badge": "success"},
        "FATURADA": {"semantic": "ENCERRADO", "stage_label": "Faturada", "badge": "dark"},
    },
    "NotaFiscal": {
        "LANCADA": {"semantic": "EM_ELABORACAO", "stage_label": "Lancada", "badge": "secondary"},
        "RATEADA": {"semantic": "EM_EXECUCAO", "stage_label": "Apropriacao em andamento", "badge": "info"},
        "PAGA": {"semantic": "ENCERRADO", "stage_label": "Paga", "badge": "success"},
    },
    "SolicitacaoCompra": {
        "RASCUNHO": {"semantic": "EM_ELABORACAO", "stage_label": "Em elaboracao", "badge": "secondary"},
        "APROVADA": {"semantic": "VALIDADO", "stage_label": "Validada", "badge": "success"},
        "COTANDO": {"semantic": "EM_EXECUCAO", "stage_label": "Em cotacao", "badge": "primary"},
        "ENCERRADA": {"semantic": "ENCERRADO", "stage_label": "Encerrada", "badge": "dark"},
        "CANCELADA": {"semantic": "CANCELADO", "stage_label": "Cancelada", "badge": "dark"},
    },
    "Cotacao": {
        "EM_ANALISE": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Em analise", "badge": "warning"},
        "APROVADA": {"semantic": "VALIDADO", "stage_label": "Aprovada", "badge": "success"},
        "REJEITADA": {"semantic": "CANCELADO", "stage_label": "Rejeitada", "badge": "dark"},
        "CANCELADA": {"semantic": "CANCELADO", "stage_label": "Cancelada", "badge": "dark"},
    },
    "OrdemCompra": {
        "RASCUNHO": {"semantic": "EM_ELABORACAO", "stage_label": "Em elaboracao", "badge": "secondary"},
        "EM_APROVACAO": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Submetida para validacao", "badge": "warning"},
        "APROVADA": {"semantic": "VALIDADO", "stage_label": "Aprovada", "badge": "success"},
        "PARCIAL": {"semantic": "EM_EXECUCAO", "stage_label": "Atendimento parcial", "badge": "info"},
        "CONCLUIDA": {"semantic": "ENCERRADO", "stage_label": "Concluida", "badge": "dark"},
        "CANCELADA": {"semantic": "CANCELADO", "stage_label": "Cancelada", "badge": "dark"},
    },
    "NaoConformidade": {
        "ABERTA": {"semantic": "ABERTO", "stage_label": "Aberta", "badge": "secondary"},
        "EM_TRATAMENTO": {"semantic": "EM_EXECUCAO", "stage_label": "Em tratamento", "badge": "warning"},
        "EM_VERIFICACAO": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Em verificacao", "badge": "info"},
        "ENCERRADA": {"semantic": "ENCERRADO", "stage_label": "Encerrada", "badge": "success"},
        "CANCELADA": {"semantic": "CANCELADO", "stage_label": "Cancelada", "badge": "dark"},
    },
    "Risco": {
        "IDENTIFICADO": {"semantic": "ABERTO", "stage_label": "Identificado", "badge": "secondary"},
        "EM_ANALISE": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Em analise", "badge": "warning"},
        "EM_TRATAMENTO": {"semantic": "EM_EXECUCAO", "stage_label": "Em tratamento", "badge": "primary"},
        "MITIGADO": {"semantic": "VALIDADO", "stage_label": "Mitigado", "badge": "success"},
        "FECHADO": {"semantic": "ENCERRADO", "stage_label": "Fechado", "badge": "dark"},
        "CANCELADO": {"semantic": "CANCELADO", "stage_label": "Cancelado", "badge": "dark"},
    },
    "OrcamentoBaseline": {
        "RASCUNHO": {"semantic": "EM_ELABORACAO", "stage_label": "Em elaboracao", "badge": "secondary"},
        "EM_APROVACAO": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Submetida para validacao", "badge": "warning"},
        "APROVADA": {"semantic": "VALIDADO", "stage_label": "Aprovada", "badge": "success"},
    },
    "PlanoFisico": {
        "RASCUNHO": {"semantic": "EM_ELABORACAO", "stage_label": "Em elaboracao", "badge": "secondary"},
        "ATIVO": {"semantic": "EM_EXECUCAO", "stage_label": "Ativo para controle", "badge": "primary"},
        "BASELINE": {"semantic": "VALIDADO", "stage_label": "Baseline vigente", "badge": "success"},
        "OBSOLETO": {"semantic": "ENCERRADO", "stage_label": "Obsoleto", "badge": "dark"},
    },
    "ReuniaoComunicacao": {
        "RASCUNHO": {"semantic": "EM_ELABORACAO", "stage_label": "Pauta em construcao", "badge": "secondary"},
        "PAUTA_VALIDADA": {"semantic": "VALIDADO", "stage_label": "Pauta validada pelo engenheiro", "badge": "info"},
        "EM_APROVACAO": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Ata submetida para aprovacao", "badge": "warning"},
        "APROVADA": {"semantic": "VALIDADO", "stage_label": "Ata aprovada", "badge": "success"},
    },
    "AlertaOperacional": {
        "ABERTO": {"semantic": "ABERTO", "stage_label": "Aguardando tratamento", "badge": "danger"},
        "EM_TRATAMENTO": {"semantic": "EM_EXECUCAO", "stage_label": "Em tratamento", "badge": "warning"},
        "JUSTIFICADO": {"semantic": "VALIDADO", "stage_label": "Justificado", "badge": "info"},
        "ENCERRADO": {"semantic": "ENCERRADO", "stage_label": "Encerrado", "badge": "success"},
    },
    "Obra": {
        "EM_ANDAMENTO": {"semantic": "EM_EXECUCAO", "stage_label": "Em andamento", "badge": "primary"},
        "CONCLUIDA": {"semantic": "ENCERRADO", "stage_label": "Concluida", "badge": "success"},
        "PARALISADA": {"semantic": "SUBMETIDO_VALIDACAO", "stage_label": "Paralisada", "badge": "warning"},
        "CANCELADA": {"semantic": "CANCELADO", "stage_label": "Cancelada", "badge": "dark"},
    },
}


def _fallback_display(instance: Any, status: str | None) -> str:
    if hasattr(instance, "get_status_display"):
        try:
            return instance.get_status_display()
        except Exception:
            pass
    if status:
        return str(status).replace("_", " ").title()
    return "Nao informado"


def get_status_metadata(instance: Any) -> dict[str, str]:
    if instance is None:
        return DEFAULT_STATUS_META.copy()

    status = getattr(instance, "status", None)
    if hasattr(instance, "STATUS_SEMANTICO") and status in getattr(instance, "STATUS_SEMANTICO", {}):
        semantic, stage_label, badge = instance.STATUS_SEMANTICO[status]
        return {
            "semantic": semantic,
            "stage_label": stage_label,
            "badge": badge,
        }

    model_name = instance.__class__.__name__
    metadata = STATUS_META_BY_MODEL.get(model_name, {}).get(status)
    if metadata:
        return metadata

    return {
        "semantic": "OUTRO",
        "stage_label": _fallback_display(instance, status),
        "badge": "secondary",
    }


def get_status_stage_label(instance: Any) -> str:
    return get_status_metadata(instance)["stage_label"]


def get_status_badge_class(instance: Any) -> str:
    return get_status_metadata(instance)["badge"]
