from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .audit import get_current_request
from .models import AuditEvent, Compromisso, Documento, Medicao, NotaFiscal, Obra, PlanoContas
from .models_aquisicoes import Cotacao, Fornecedor, OrdemCompra, SolicitacaoCompra
from .models_planejamento import MapaCorrespondencia, PlanoFisico
from .models_qualidade import NaoConformidade
from .models_risco import Risco


AUDITED_MODELS = (
    Obra,
    PlanoContas,
    Compromisso,
    Medicao,
    NotaFiscal,
    Documento,
    Risco,
    PlanoFisico,
    MapaCorrespondencia,
    NaoConformidade,
    Fornecedor,
    SolicitacaoCompra,
    Cotacao,
    OrdemCompra,
)


def _empresa_from_instance(instance):
    if hasattr(instance, "empresa") and instance.empresa_id:
        return instance.empresa
    if hasattr(instance, "obra") and instance.obra_id:
        return instance.obra.empresa
    return None


@receiver(post_save)
def registrar_auditoria_save(sender, instance, created, **kwargs):
    if sender not in AUDITED_MODELS:
        return
    request = get_current_request()
    empresa = _empresa_from_instance(instance)

    if request:
        from .audit import AuditService

        if created:
            AuditService.log_create(request, instance)
        else:
            AuditService.log_event(
                request,
                "UPDATE",
                instance,
                depois=AuditService.instance_to_dict(instance),
            )
        return

    AuditEvent.objects.create(
        empresa=empresa,
        usuario=None,
        acao="CREATE" if created else "UPDATE",
        entidade_app=instance._meta.label,
        entidade_label=str(instance),
        objeto_id=instance.pk,
        depois={"status": "capturado_sem_request"},
    )


@receiver(post_delete)
def registrar_auditoria_delete(sender, instance, **kwargs):
    if sender not in AUDITED_MODELS:
        return
    request = get_current_request()
    empresa = _empresa_from_instance(instance)

    if request:
        from .audit import AuditService

        AuditService.log_delete(request, instance, AuditService.instance_to_dict(instance))
        return

    AuditEvent.objects.create(
        empresa=empresa,
        usuario=None,
        acao="DELETE",
        entidade_app=instance._meta.label,
        entidade_label=str(instance),
        objeto_id=instance.pk or 0,
        antes={"status": "capturado_sem_request"},
    )
