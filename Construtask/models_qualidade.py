from django.conf import settings
from django.db import models
from django.utils import timezone

from .domain import gerar_numero_documento
from .tenant_querysets import TenantScopedManager
from .upload_paths import upload_nao_conformidade_encerramento, upload_nao_conformidade_tratamento


class NaoConformidade(models.Model):
    class Meta:
        verbose_name = "Não Conformidade"
        verbose_name_plural = "Não Conformidades"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "obra", "status"]),
            models.Index(fields=["obra", "data_abertura"]),
        ]

    STATUS_CHOICES = (
        ("ABERTA", "Aberta"),
        ("EM_TRATAMENTO", "Em Tratamento"),
        ("EM_VERIFICACAO", "Em Verificação"),
        ("ENCERRADA", "Encerrada"),
        ("CANCELADA", "Cancelada"),
    )

    empresa = models.ForeignKey("Empresa", on_delete=models.CASCADE, related_name="nao_conformidades")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="nao_conformidades")
    numero = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nao_conformidades",
    )
    descricao = models.TextField()
    causa = models.TextField(blank=True)
    acao_corretiva = models.TextField(blank=True)
    evidencia_tratamento = models.TextField(blank=True)
    evidencia_tratamento_anexo = models.FileField(
        upload_to=upload_nao_conformidade_tratamento,
        blank=True,
        null=True,
    )
    evidencia_encerramento = models.TextField(blank=True)
    evidencia_encerramento_anexo = models.FileField(
        upload_to=upload_nao_conformidade_encerramento,
        blank=True,
        null=True,
    )
    eficacia_observacao = models.TextField(blank=True)
    eficacia_verificada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nao_conformidades_eficacia_verificadas",
    )
    eficacia_verificada_em = models.DateTimeField(null=True, blank=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nao_conformidades_responsaveis",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ABERTA")
    data_abertura = models.DateField(default=timezone.localdate)
    data_encerramento = models.DateField(null=True, blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nao_conformidades_criadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = gerar_numero_documento(NaoConformidade, "NC-", "numero")
        if self.status == "ENCERRADA" and not self.data_encerramento:
            self.data_encerramento = timezone.localdate()
        if self.status != "ENCERRADA":
            self.data_encerramento = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.numero or 'NC-NOVA'} - {self.get_status_display()}"


class NaoConformidadeHistorico(models.Model):
    class Meta:
        verbose_name = "Histórico de Não Conformidade"
        verbose_name_plural = "Históricos de Não Conformidade"
        ordering = ["-timestamp"]

    ACAO_CHOICES = (
        ("ABERTURA", "Abertura"),
        ("TRATAMENTO", "Tratamento"),
        ("VERIFICACAO", "Verificação"),
        ("ENCERRAMENTO", "Encerramento"),
        ("CANCELAMENTO", "Cancelamento"),
        ("ATUALIZACAO", "Atualização"),
    )

    nao_conformidade = models.ForeignKey(
        NaoConformidade,
        on_delete=models.CASCADE,
        related_name="historico",
    )
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    acao = models.CharField(max_length=20, choices=ACAO_CHOICES)
    observacao = models.TextField(blank=True)
    dados_anteriores = models.JSONField(null=True, blank=True)
    dados_novos = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nao_conformidade} - {self.get_acao_display()}"
