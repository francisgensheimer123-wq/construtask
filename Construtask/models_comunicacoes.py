from django.conf import settings
from django.db import models
from django.utils import timezone

from .domain import gerar_numero_documento
from .tenant_querysets import TenantScopedManager


class ParametroComunicacaoEmpresa(models.Model):
    class Meta:
        verbose_name = "Parametro de Comunicacao da Empresa"
        verbose_name_plural = "Parametros de Comunicacao da Empresa"

    empresa = models.OneToOneField(
        "Empresa",
        on_delete=models.CASCADE,
        related_name="parametros_comunicacao",
    )
    frequencia_curto_prazo_dias = models.PositiveIntegerField(default=7)
    frequencia_medio_prazo_dias = models.PositiveIntegerField(default=30)
    frequencia_longo_prazo_dias = models.PositiveIntegerField(default=90)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Parametros de comunicacao - {self.empresa.nome}"

    @classmethod
    def obter_ou_criar(cls, empresa):
        if not empresa:
            return cls(
                frequencia_curto_prazo_dias=7,
                frequencia_medio_prazo_dias=30,
                frequencia_longo_prazo_dias=90,
            )
        parametros, _ = cls.objects.get_or_create(empresa=empresa)
        return parametros


class ReuniaoComunicacao(models.Model):
    class Meta:
        verbose_name = "Reuniao de Comunicacao"
        verbose_name_plural = "Reunioes de Comunicacao"
        ordering = ["-data_prevista", "-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "obra", "tipo_reuniao", "status"]),
            models.Index(fields=["obra", "data_prevista"]),
        ]

    TIPO_REUNIAO_CHOICES = (
        ("CURTO_PRAZO", "Curto Prazo"),
        ("MEDIO_PRAZO", "Medio Prazo"),
        ("LONGO_PRAZO", "Longo Prazo"),
    )
    STATUS_CHOICES = (
        ("RASCUNHO", "Rascunho"),
        ("PAUTA_VALIDADA", "Pauta Validada"),
        ("EM_APROVACAO", "Em Aprovacao"),
        ("APROVADA", "Aprovada"),
    )

    empresa = models.ForeignKey("Empresa", on_delete=models.CASCADE, related_name="reunioes_comunicacao")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="reunioes_comunicacao")
    numero = models.CharField(max_length=30, unique=True, blank=True)
    titulo = models.CharField(max_length=200)
    tipo_reuniao = models.CharField(max_length=20, choices=TIPO_REUNIAO_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RASCUNHO")
    periodicidade_dias = models.PositiveIntegerField(default=7)
    data_prevista = models.DateField(default=timezone.localdate)
    data_realizada = models.DateField(null=True, blank=True)
    pauta_resumo = models.TextField(blank=True)
    ata_texto = models.TextField(blank=True)
    pauta_validada_em = models.DateTimeField(null=True, blank=True)
    pauta_validada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reunioes_comunicacao_validadas",
    )
    enviado_para_aprovacao_em = models.DateTimeField(null=True, blank=True)
    enviado_para_aprovacao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reunioes_comunicacao_enviadas_aprovacao",
    )
    parecer_aprovacao = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reunioes_comunicacao_aprovadas",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reunioes_comunicacao_criadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    def __str__(self):
        return self.numero or self.titulo

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = gerar_numero_documento(ReuniaoComunicacao, "COM-", "numero")
        if not self.empresa_id and self.obra_id:
            self.empresa = self.obra.empresa
        super().save(*args, **kwargs)

    @property
    def quantidade_itens_ativos(self):
        return self.itens_pauta.filter(ativo=True).count()


class ItemPautaReuniao(models.Model):
    class Meta:
        verbose_name = "Item de Pauta"
        verbose_name_plural = "Itens de Pauta"
        ordering = ["ordem", "id"]
        indexes = [
            models.Index(fields=["reuniao", "ativo", "categoria"]),
            models.Index(fields=["reuniao", "origem_tipo", "referencia_modelo", "referencia_id"]),
        ]

    ORIGEM_CHOICES = (
        ("AUTOMATICA", "Automatica"),
        ("MANUAL", "Manual"),
    )
    CATEGORIA_CHOICES = (
        ("ALERTA", "Alerta"),
        ("RISCO", "Risco"),
        ("NAO_CONFORMIDADE", "Nao Conformidade"),
        ("CRONOGRAMA", "Cronograma"),
        ("CONTRATO", "Contrato"),
        ("MEDICAO", "Medicao"),
        ("OUTRO", "Outro"),
    )

    reuniao = models.ForeignKey(ReuniaoComunicacao, on_delete=models.CASCADE, related_name="itens_pauta")
    ordem = models.PositiveIntegerField(default=0)
    ativo = models.BooleanField(default=True)
    origem_tipo = models.CharField(max_length=20, choices=ORIGEM_CHOICES, default="AUTOMATICA")
    categoria = models.CharField(max_length=30, choices=CATEGORIA_CHOICES, default="OUTRO")
    referencia_modelo = models.CharField(max_length=100, blank=True)
    referencia_id = models.PositiveIntegerField(null=True, blank=True)
    titulo = models.CharField(max_length=255)
    descricao = models.TextField(blank=True)
    contexto = models.JSONField(default=dict, blank=True)
    resposta_o_que = models.TextField(blank=True)
    resposta_quem = models.CharField(max_length=180, blank=True)
    resposta_quando = models.DateField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.reuniao.numero} - {self.titulo}"


class HistoricoReuniaoComunicacao(models.Model):
    class Meta:
        verbose_name = "Historico de Reuniao de Comunicacao"
        verbose_name_plural = "Historicos de Reunioes de Comunicacao"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["reuniao", "criado_em"]),
            models.Index(fields=["acao"]),
        ]

    ACAO_CHOICES = (
        ("CRIACAO", "Criacao"),
        ("PAUTA_VALIDADA", "Pauta Validada"),
        ("PAUTA_ATUALIZADA", "Pauta Atualizada"),
        ("ENVIO_APROVACAO", "Envio para Aprovacao"),
        ("APROVACAO", "Aprovacao"),
        ("AJUSTE", "Retorno para Ajuste"),
    )

    reuniao = models.ForeignKey(ReuniaoComunicacao, on_delete=models.CASCADE, related_name="historicos")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historicos_reunioes_comunicacao",
    )
    acao = models.CharField(max_length=30, choices=ACAO_CHOICES)
    observacao = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.reuniao.numero} - {self.get_acao_display()}"
