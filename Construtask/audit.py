"""
Módulo de auditoria para conformidade ISO 9.2.
Inclui middleware e utilities para registro de eventos.
"""

import uuid
from threading import local
from typing import Any, Optional

from django.db.models import Model
from django.utils import timezone

_audit_local = local()


def set_current_request(request):
    _audit_local.request = request


def get_current_request():
    return getattr(_audit_local, "request", None)


def clear_current_request():
    if hasattr(_audit_local, "request"):
        del _audit_local.request


class AuditService:
    """
    Serviço centralizado para registro de eventos de auditoria.
    """
    
    @staticmethod
    def get_request_info(request) -> tuple:
        """Extrai IP e User-Agent do request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', '')
        
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        return ip, user_agent
    
    @staticmethod
    def get_request_id(request) -> str:
        """Gera ou recupera request_id para rastreamento."""
        if not hasattr(request, '_audit_request_id'):
            request._audit_request_id = str(uuid.uuid4())[:8]
        return request._audit_request_id
    
    @classmethod
    def log_event(
        cls,
        request,
        acao: str,
        instance: Model,
        antes: Optional[dict] = None,
        depois: Optional[dict] = None,
    ):
        """
        Registra um evento de auditoria.
        
        Args:
            request: HttpRequest object
            acao: Ação performed (CREATE, UPDATE, DELETE, etc.)
            instance: Model instance
            antes: Estado antes da operação (dict)
            depois: Estado depois da operação (dict)
        """
        from .models import AuditEvent, Empresa
        
        # Obter empresa do instance ou request
        empresa = None
        if hasattr(instance, 'empresa_id'):
            empresa_id = instance.empresa_id
            if empresa_id:
                empresa = Empresa.objects.filter(pk=empresa_id).first()
        elif hasattr(instance, 'obra_id') and instance.obra_id:
            from .models import Obra
            obra = Obra.objects.filter(pk=instance.obra_id).first()
            if obra:
                empresa = obra.empresa
        
        # Obter usuário
        usuario = getattr(request, 'user', None)
        if not usuario or not usuario.is_authenticated:
            usuario = None
        
        ip, user_agent = cls.get_request_info(request)
        request_id = cls.get_request_id(request)
        
        # Criar registro de auditoria
        AuditEvent.objects.create(
            empresa=empresa,
            usuario=usuario,
            acao=acao,
            entidade_app=instance._meta.label,
            entidade_label=str(instance),
            objeto_id=instance.pk,
            antes=antes,
            depois=depois,
            ip_address=ip,
            user_agent=user_agent,
            request_id=request_id,
        )
    
    @classmethod
    def log_create(cls, request, instance: Model):
        """Loga criação de registro."""
        cls.log_event(request, 'CREATE', instance, depois=cls.instance_to_dict(instance))
    
    @classmethod
    def log_update(cls, request, instance: Model, before_dict: dict, after_dict: dict):
        """Loga atualização de registro."""
        cls.log_event(request, 'UPDATE', instance, before_dict, after_dict)
    
    @classmethod
    def log_delete(cls, request, instance: Model, before_dict: dict):
        """Loga exclusão de registro."""
        cls.log_event(request, 'DELETE', instance, antes=before_dict)
    
    @staticmethod
    def instance_to_dict(instance: Model) -> dict:
        """Converte instância do modelo para dict (sem relação)."""
        result = {}
        for field in instance._meta.get_fields():
            if hasattr(field, 'name') and not field.is_relation:
                value = getattr(instance, field.name, None)
                if value is not None:
                    result[field.name] = str(value)
        return result


class AuditMiddleware:
    """
    Middleware para adicionar request_id em todas as requisições.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Gerar request_id único
        request._audit_request_id = str(uuid.uuid4())[:8]
        set_current_request(request)
        try:
            response = self.get_response(request)
        finally:
            clear_current_request()

        # Adicionar request_id no header da resposta
        response['X-Request-ID'] = request._audit_request_id

        return response


def audit_post_save(sender, instance, created, **kwargs):
    """Signal handler para audit em post_save."""
    from django.db import connection
    from .audit import AuditService
    
    # Não auditar se for uma migração
    if connection.schema_editor().atomic_ddl:
        return
    
    # Obter request do thread local (se disponível)
    request = get_current_request()
    if not request:
        return
    
    if created:
        AuditService.log_create(request, instance)
    else:
        AuditService.log_event(
            request,
            'UPDATE',
            instance,
            depois=AuditService.instance_to_dict(instance),
        )


def audit_post_delete(sender, instance, **kwargs):
    """Signal handler para audit em post_delete."""
    from django.db import connection
    from .audit import AuditService
    
    if connection.schema_editor().atomic_ddl:
        return
    
    request = get_current_request()
    if not request:
        return
    
    before_dict = AuditService.instance_to_dict(instance)
    AuditService.log_delete(request, instance, before_dict)
