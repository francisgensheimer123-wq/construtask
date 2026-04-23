"""
Módulo de Gestão de Riscos - ISO 6.1 / PMBOK Riscos
"""

from django.conf import settings
from django.db import models

from .domain import gerar_numero_documento
from .tenant_querysets import TenantScopedManager


class Risco(models.Model):
    """
    Gestão de riscos conforme ISO 6.1 e PMBOK.
    Registra identificação, análise e resposta a riscos por obra.
    """
    
    class Meta:
        verbose_name = "Risco"
        verbose_name_plural = "Riscos"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "obra", "status"]),
            models.Index(fields=["nivel"]),
        ]

    # Categorias de risco
    CATEGORIA_CHOICES = (
        ("TECNICO", "Técnico"),
        ("AMBIENTAL", "Ambiental"),
        ("SEGURANCA", "Segurança do Trabalho"),
        ("FINANCEIRO", "Financeiro"),
        ("PRAZO", "Prazo"),
        ("QUALIDADE", "Qualidade"),
        ("FORNECEDOR", "Fornecedor"),
        ("REGULATORIO", "Regulatório"),
        ("OUTRO", "Outro"),
    )

    # Níveis de probabilidade
    PROBABILIDADE_CHOICES = (
        (1, "1 - Rara"),
        (2, "2 - Improvável"),
        (3, "3 - Possível"),
        (4, "4 - Provável"),
        (5, "5 - Quase Certa"),
    )

    # Níveis de impacto
    IMPACTO_CHOICES = (
        (1, "1 - Insignificante"),
        (2, "2 - Menor"),
        (3, "3 - Moderado"),
        (4, "4 - Maior"),
        (5, "5 - Catastrófico"),
    )

    # Status do risco
    STATUS_CHOICES = (
        ("IDENTIFICADO", "Identificado"),
        ("EM_ANALISE", "Em Análise"),
        ("EM_TRATAMENTO", "Em Tratamento"),
        ("MITIGADO", "Mitigado"),
        ("FECHADO", "Fechado"),
        ("CANCELADO", "Cancelado"),
    )

    # Campos principais
    empresa = models.ForeignKey(
        "Empresa",
        on_delete=models.CASCADE,
        related_name="riscos"
    )
    obra = models.ForeignKey(
        "Obra",
        on_delete=models.CASCADE,
        related_name="riscos"
    )
    codigo = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="riscos",
        help_text="EAP nível 5 associado ao risco (opcional)"
    )
    processo = models.CharField(
        max_length=100,
        blank=True,
        help_text="Processo/Atividade relacionado"
    )
    
    # Identificação
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES)
    titulo = models.CharField(max_length=200)
    descricao = models.TextField("Descrição detalhada do risco")
    causa = models.TextField("Causa raiz identificada", blank=True)
    
    # Análise quantitativa
    probabilidade = models.PositiveSmallIntegerField(
        choices=PROBABILIDADE_CHOICES,
        help_text="1=Rara a 5=Quase Certa"
    )
    impacto = models.PositiveSmallIntegerField(
        choices=IMPACTO_CHOICES,
        help_text="1=Insignificante a 5=Catastrófico"
    )
    nivel = models.PositiveSmallIntegerField(
        editable=False,
        blank=True,
        help_text="Calculado: Probabilidade × Impacto"
    )
    
    # Resposta ao risco
    plano_resposta = models.TextField(
        "Plano de ação de resposta",
        blank=True,
        help_text="Estratégia e ações para mitigar o risco"
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="riscos_responsaveis"
    )
    data_meta_tratamento = models.DateField(
        null=True,
        blank=True,
        help_text="Data meta para tratamento do risco"
    )
    
    # Status e controle
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="IDENTIFICADO"
    )
    data_fechamento = models.DateField(null=True, blank=True)
    observacoes = models.TextField(blank=True)
    
    # Auditoria
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="riscos_criados"
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    def __str__(self):
        return f"{self.codigo or 'RIS-NOVO'} - {self.titulo} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        """Calcula o nível do risco automaticamente."""
        if not self.codigo:
            self.codigo = gerar_numero_documento(Risco, "RIS-", "codigo")
        self.nivel = self.probabilidade * self.impacto
        super().save(*args, **kwargs)

    def pode_editar(self):
        """Verifica se o risco pode ser editado."""
        return self.status not in ["FECHADO", "CANCELADO"]

    def pode_tratar(self):
        """Verifica se o risco pode entrar em tratamento."""
        return self.status in ["IDENTIFICADO", "EM_ANALISE"]

    def pode_fechar(self):
        """Verifica se o risco pode ser fechado."""
        return self.status in ["EM_TRATAMENTO", "MITIGADO"]

    @property
    def nivel_texto(self):
        """Retorna texto do nível de risco."""
        if self.nivel <= 4:
            return "BAIXO"
        elif self.nivel <= 9:
            return "MÉDIO"
        elif self.nivel <= 15:
            return "ALTO"
        else:
            return "CRÍTICO"

    @property
    def nivel_cor(self):
        """Retorna cor do nível de risco para UI."""
        if self.nivel <= 4:
            return "success"
        elif self.nivel <= 9:
            return "warning"
        elif self.nivel <= 15:
            return "danger"
        else:
            return "dark"


class RiscoHistorico(models.Model):
    """
    Histórico de alterações do risco (ISO 9.2 - Rastreabilidade).
    Mantém registro de todas as mudanças no risco.
    """
    
    class Meta:
        verbose_name = "Histórico de Risco"
        verbose_name_plural = "Históricos de Riscos"
        ordering = ["-timestamp"]

    ACAO_CHOICES = (
        ("CRIACAO", "Criação"),
        ("ALTERACAO", "Alteração"),
        ("STATUS", "Mudança de Status"),
        ("TRATAMENTO", "Plano de Tratamento"),
        ("FECHAMENTO", "Fechamento"),
        ("REABERTURA", "Reabertura"),
    )

    risco = models.ForeignKey(
        Risco,
        on_delete=models.CASCADE,
        related_name="historico"
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT
    )
    acao = models.CharField(max_length=20, choices=ACAO_CHOICES)
    dados_anteriores = models.JSONField(null=True, blank=True)
    dados_novos = models.JSONField(null=True, blank=True)
    observacao = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.risco.titulo} - {self.get_acao_display()} em {self.timestamp:%d/%m/%Y %H:%M}"
