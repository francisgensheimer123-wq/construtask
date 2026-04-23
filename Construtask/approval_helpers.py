from django.contrib import messages
from django.utils import timezone

from .audit import AuditService
from .models import Compromisso, HistoricoOperacional, Medicao, NotaFiscal, Obra
from .services_aprovacao import (
    can_approve_value,
    can_submit_for_approval,
    get_limite_aprovacao,
    get_papel_aprovacao,
)
from .templatetags.formatters import money_br


def _obter_alcada_contexto(user, valor):
    papel = get_papel_aprovacao(user)
    limite = get_limite_aprovacao(user)
    if limite is None:
        limite_label = "ilimitada"
    else:
        limite_label = money_br(limite)
    return {
        "papel_aprovacao": papel,
        "limite_aprovacao": limite,
        "limite_aprovacao_label": limite_label,
        "pode_enviar_para_aprovacao": can_submit_for_approval(user),
        "pode_aprovar": can_approve_value(user, valor),
    }


def _registrar_historico(acao, objeto, descricao, usuario=None):
    payload = {"acao": acao, "descricao": descricao, "usuario": usuario}
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


def _enviar_documento_para_aprovacao(request, objeto, *, status_em_aprovacao, descricao):
    if not can_submit_for_approval(request.user):
        messages.error(request, "Seu usuario nao possui funcao operacional para enviar este registro para aprovacao.")
        return False
    if objeto.status == status_em_aprovacao:
        messages.info(request, "Este registro ja esta em aprovacao.")
        return False

    parecer = (request.POST.get("parecer_aprovacao") or "").strip()
    objeto.status = status_em_aprovacao
    objeto.enviado_para_aprovacao_em = timezone.now()
    objeto.enviado_para_aprovacao_por = request.user
    objeto.parecer_aprovacao = parecer
    objeto.aprovado_em = None
    objeto.aprovado_por = None
    objeto.save()
    descricao_historico = descricao if not parecer else f"{descricao} Parecer: {parecer}"
    _registrar_historico("APROVACAO", objeto, descricao_historico, request.user)
    messages.success(request, "Registro enviado para aprovacao.")
    return True


def _aprovar_documento(request, objeto, *, valor, status_aprovado, descricao):
    if not can_approve_value(request.user, valor):
        messages.error(request, "Sua funcao nao possui alcada suficiente para aprovar este valor.")
        return False
    parecer = (request.POST.get("parecer_aprovacao") or "").strip()
    before = AuditService.instance_to_dict(objeto)
    objeto.status = status_aprovado
    objeto.parecer_aprovacao = parecer
    objeto.aprovado_em = timezone.now()
    objeto.aprovado_por = request.user
    if not objeto.enviado_para_aprovacao_em:
        objeto.enviado_para_aprovacao_em = timezone.now()
    if not objeto.enviado_para_aprovacao_por:
        objeto.enviado_para_aprovacao_por = request.user
    objeto.save()
    after = AuditService.instance_to_dict(objeto)
    AuditService.log_event(request, "APPROVE", objeto, before, after)
    descricao_historico = descricao if not parecer else f"{descricao} Parecer: {parecer}"
    _registrar_historico("APROVACAO", objeto, descricao_historico, request.user)
    messages.success(request, "Registro aprovado com sucesso.")
    return True


def _retornar_documento_para_ajuste(
    request,
    objeto,
    *,
    valor,
    status_ajuste,
    descricao,
):
    if not can_approve_value(request.user, valor):
        messages.error(request, "Sua funcao nao possui alcada suficiente para devolver este valor para ajuste.")
        return False
    parecer = (request.POST.get("parecer_aprovacao") or "").strip()
    if not parecer:
        messages.error(request, "Informe um parecer para devolver o registro para ajuste.")
        return False
    before = AuditService.instance_to_dict(objeto)
    objeto.status = status_ajuste
    objeto.parecer_aprovacao = parecer
    objeto.aprovado_em = None
    objeto.aprovado_por = None
    objeto.save()
    after = AuditService.instance_to_dict(objeto)
    AuditService.log_event(request, "REJECT", objeto, before, after)
    _registrar_historico("APROVACAO", objeto, f"{descricao} Parecer: {parecer}", request.user)
    messages.success(request, "Registro devolvido para ajuste.")
    return True
