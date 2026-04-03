"""
Módulo de Planejamento Físico - Controle de Cronogramas
Atende: ISO 6.1 (Planejamento) + PMBOK 6 (Cronograma)
"""

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse_lazy


class PlanoFisico(models.Model):
    """
    Cabeçalho do cronograma físico.
    Permite importação de cronograma externo (MPP/XLSX).
    """
    
    class Meta:
        verbose_name = "Cronograma Físico"
        verbose_name_plural = "Cronogramas Físicos"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["obra", "status"]),
            models.Index(fields=["obra", "is_baseline"]),
        ]

    STATUS_CHOICES = (
        ("RASCUNHO", "Rascunho"),
        ("ATIVO", "Ativo"),
        ("OBSOLETO", "Obsoleto"),
        ("BASELINE", "Baseline"),
    )

    TIPO_ARQUIVO_CHOICES = (
        ("XLSX", "Excel (.xlsx)"),
        ("MPP", "Microsoft Project (.mpp)"),
    )

    obra = models.ForeignKey(
        "Obra",
        on_delete=models.CASCADE,
        related_name="planos_fisicos"
    )
    
    titulo = models.CharField(
        max_length=200,
        help_text="Título do cronograma"
    )
    descricao = models.TextField(blank=True)
    
    # Arquivo importado
    arquivo_origem = models.FileField(
        upload_to="cronogramas/%Y/%m",
        blank=True,
        null=True,
        help_text="Arquivo original importado (MPP/XLSX)"
    )
    tipo_arquivo = models.CharField(
        max_length=10,
        choices=TIPO_ARQUIVO_CHOICES,
        blank=True
    )
    
    # Controle de versão
    versao = models.PositiveIntegerField(default=1)
    is_baseline = models.BooleanField(
        default=False,
        help_text="Indica se este cronograma é um baseline"
    )
    baseline_de = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="baselines",
        help_text="Qual baseline originou esta versão"
    )
    
    # Datas e responsáveis
    data_base = models.DateField(
        null=True,
        blank=True,
        help_text="Data base do cronograma"
    )
    data_importacao = models.DateTimeField(auto_now_add=True)
    responsavel_importacao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cronogramas_importados"
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="RASCUNHO"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.obra.codigo} - {self.titulo} (v{self.versao})"

    def get_absolute_url(self):
        return reverse_lazy("plano_fisico_detail", kwargs={"pk": self.pk})

    def save(self, *args, **kwargs):
        # Se is_baseline, forçar status BASELINE
        if self.is_baseline:
            self.status = "BASELINE"
        super().save(*args, **kwargs)

    @property
    def total_itens(self):
        return self.itens.count()

    @property
    def itens_nivel_raiz(self):
        return self.itens.filter(level=0).order_by("sort_order")

    @property
    def macros(self):
        return self.itens.filter(is_marco=True)

    @property
    def percentual_geral(self):
        """Calcula percentual geral de execução"""
        itens = self.itens.exclude(percentual_concluido__isnull=True)
        if not itens.exists():
            return 0
        total = sum(i.percentual_concluido or 0 for i in itens)
        return round(total / itens.count(), 1)


class PlanoFisicoItem(models.Model):
    """
    Itens/Atividades do cronograma físico.
    """
    
    class Meta:
        verbose_name = "Atividade do Cronograma"
        verbose_name_plural = "Atividades do Cronograma"
        ordering = ["plano", "sort_order"]
        indexes = [
            models.Index(fields=["plano", "codigo_atividade"]),
            models.Index(fields=["plano", "data_inicio_prevista"]),
        ]

    plano = models.ForeignKey(
        PlanoFisico,
        on_delete=models.CASCADE,
        related_name="itens"
    )
    
    # Vínculo opcional com EAP (orçamento)
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="itens_cronograma",
        help_text="Item da EAP vinculado (opcional)"
    )
    
    # Dados da atividade
    codigo_atividade = models.CharField(
        max_length=50,
        help_text="Código da atividade (do arquivo)"
    )
    atividade = models.CharField(
        max_length=500,
        help_text="Nome da atividade"
    )
    
    # Dependências
    predecessor = models.CharField(
        max_length=50,
        blank=True,
        help_text="Código da predecessora"
    )
    successor = models.CharField(
        max_length=50,
        blank=True,
        help_text="Código da sucessora"
    )
    
    # Datas previstas
    duracao = models.PositiveIntegerField(
        default=0,
        help_text="Duração em dias"
    )
    data_inicio_prevista = models.DateField(null=True, blank=True)
    data_fim_prevista = models.DateField(null=True, blank=True)
    
    # Datas realizadas
    data_inicio_real = models.DateField(null=True, blank=True)
    data_fim_real = models.DateField(null=True, blank=True)
    
    # Controle de execução
    percentual_concluido = models.PositiveSmallIntegerField(
        default=0,
        help_text="Percentual concluído (0-100)"
    )
    
    # Marco (milestone)
    is_marco = models.BooleanField(
        default=False,
        help_text="Indica se é um marco"
    )
    
    # Estrutura
    level = models.PositiveSmallIntegerField(default=0)
    wbs_code = models.CharField(max_length=50, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    
    # Dados financeiros do cronograma
    valor_planejado = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Valor planejado da atividade"
    )
    valor_realizado = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Valor realizado da atividade"
    )
    
    # Índices calculados
    dias_desvio = models.IntegerField(
        default=0,
        help_text="Dias de desvio (positivo = atraso)"
    )
    percent_desvio = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="% de desvio"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.codigo_atividade} - {self.atividade[:50]}"

    def save(self, *args, **kwargs):
        # Calcular desvios
        if self.data_inicio_prevista and self.data_inicio_real:
            from datetime import timedelta
            diff = self.data_inicio_real - self.data_inicio_prevista
            self.dias_desvio = diff.days
        elif self.data_inicio_prevista:
            from datetime import date
            if date.today() > self.data_inicio_prevista:
                diff = date.today() - self.data_inicio_prevista
                self.dias_desvio = diff.days
            else:
                self.dias_desvio = 0
        
        # Calcular % de desvio baseado no cronograma
        if self.percentual_concluido is not None:
            # Simplificado: se está com 30% de execução mas deveria estar com 50%
            expected_percent = self._calcular_percentual_esperado()
            if expected_percent > 0:
                self.percent_desvio = self.percentual_concluido - expected_percent
        
        super().save(*args, **kwargs)

    def _calcular_percentual_esperado(self):
        """Calcula percentual esperado baseado na data atual"""
        if not self.data_inicio_prevista or not self.data_fim_prevista:
            return 0
        
        from datetime import date
        today = date.today()
        
        if today < self.data_inicio_prevista:
            return 0
        if today > self.data_fim_prevista:
            return 100
        
        total_days = (self.data_fim_prevista - self.data_inicio_prevista).days
        if total_days <= 0:
            return 100
        
        elapsed_days = (today - self.data_inicio_prevista).days
        return round((elapsed_days / total_days) * 100, 1)


class PlanoFisicoBaseline(models.Model):
    """
    Histórico de versões de baseline.
    """
    
    class Meta:
        verbose_name = "Baseline do Cronograma"
        verbose_name_plural = "Baselines dos Cronogramas"
        ordering = ["-versao"]
        unique_together = ("plano", "versao")

    plano = models.ForeignKey(
        PlanoFisico,
        on_delete=models.CASCADE,
        related_name="historico_baseline"
    )
    versao = models.PositiveIntegerField()
    
    arquivo = models.FileField(
        upload_to="cronogramas/baselines/%Y/%m",
        blank=True,
        null=True
    )
    observacao = models.TextField(blank=True)
    
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT
    )
    data_criacao = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.plano.titulo} - Baseline v{self.versao}"


class MapaCorrespondencia(models.Model):
    """
    Mapeia itens do cronograma para itens do orçamento (EAP).
    Resolve a diferença entre EAP do orçamento e EAP do cronograma.
    """
    
    class Meta:
        verbose_name = "Mapeamento Cronograma ↔ Orçamento"
        verbose_name_plural = "Mapeamentos Cronograma ↔ Orçamento"
        ordering = ["plano_fisico_item"]
        indexes = [
            models.Index(fields=["plano_fisico_item", "status"]),
            models.Index(fields=["plano_contas", "status"]),
        ]

    STATUS_CHOICES = (
        ("ATIVO", "Ativo"),
        ("INATIVO", "Inativo"),
    )

    empresa = models.ForeignKey(
        "Empresa",
        on_delete=models.CASCADE,
        related_name="mapeamentos"
    )
    obra = models.ForeignKey(
        "Obra",
        on_delete=models.CASCADE,
        related_name="mapeamentos"
    )
    
    # Origem: Cronograma
    plano_fisico_item = models.ForeignKey(
        PlanoFisicoItem,
        on_delete=models.CASCADE,
        related_name="mapeamentos"
    )
    
    # Destino: Orçamento (EAP)
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mapeamentos",
        help_text="Centro de custo da EAP (opcional)"
    )
    
    # Rateio
    percentual_rateio = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=100,
        help_text="% do item do cronograma que corresponde ao centro de custo"
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="ATIVO"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mapeamentos_criados"
    )

    def __str__(self):
        return f"{self.plano_fisico_item.codigo_atividade} → {self.plano_contas.codigo if self.plano_contas else 'Não vinculado'} ({self.percentual_rateio}%)"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.percentual_rateio and (self.percentual_rateio < 0 or self.percentual_rateio > 100):
            raise ValidationError("Percentual de rateio deve estar entre 0 e 100")


class MapaCorrespondenciaRateio(models.Model):
    """
    Permite ratear um item do cronograma entre múltiplos centros de custo.
    Ex: "Estrutura" = 70% EAP 01.02 + 30% EAP 01.03
    """
    
    class Meta:
        verbose_name = "Rateio de Mapeamento"
        verbose_name_plural = "Rateios de Mapeamento"
        unique_together = ("correspondencia", "plano_contas")

    correspondencia = models.ForeignKey(
        MapaCorrespondencia,
        on_delete=models.CASCADE,
        related_name="rateios"
    )
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.CASCADE,
        related_name="rateios_recebidos"
    )
    percentual = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="% de rateio para este centro de custo"
    )

    def __str__(self):
        return f"{self.correspondencia.plano_fisico_item.codigo_atividade} → {self.plano_contas.codigo}: {self.percentual}%"
