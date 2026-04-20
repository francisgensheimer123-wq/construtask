"""
Modulo de planejamento fisico e controle de cronogramas.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.urls import reverse_lazy

from .domain import gerar_numero_documento


class PlanoFisico(models.Model):
    """
    Cabecalho do cronograma fisico.
    """

    class Meta:
        verbose_name = "Cronograma Fisico"
        verbose_name_plural = "Cronogramas Fisicos"
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

    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="planos_fisicos")
    numero = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    titulo = models.CharField(max_length=200, help_text="Titulo do cronograma")
    descricao = models.TextField(blank=True)
    arquivo_origem = models.FileField(
        upload_to="cronogramas/%Y/%m",
        blank=True,
        null=True,
        help_text="Arquivo original importado (MPP/XLSX)",
    )
    tipo_arquivo = models.CharField(max_length=10, choices=TIPO_ARQUIVO_CHOICES, blank=True)
    versao = models.PositiveIntegerField(default=1)
    is_baseline = models.BooleanField(default=False, help_text="Indica se este cronograma e um baseline")
    baseline_de = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="baselines",
        help_text="Qual baseline originou esta versao",
    )
    data_base = models.DateField(null=True, blank=True, help_text="Data base do cronograma")
    data_importacao = models.DateTimeField(auto_now_add=True)
    responsavel_importacao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cronogramas_importados",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RASCUNHO")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.numero or 'CRN-NOVO'} - {self.obra.codigo} - {self.titulo} (v{self.versao})"

    def get_absolute_url(self):
        return reverse_lazy("plano_fisico_detail", kwargs={"pk": self.pk})

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = gerar_numero_documento(PlanoFisico, "CRN-", "numero")
        if self.is_baseline:
            self.status = "BASELINE"
        super().save(*args, **kwargs)

    @property
    def total_itens(self):
        return self.itens.count()

    @property
    def itens_nivel_raiz(self):
        return self.itens.filter(parent__isnull=True).order_by("sort_order", "pk")

    @property
    def macros(self):
        return self.itens.filter(is_marco=True)

    @property
    def percentual_geral(self):
        raizes = list(self.itens.filter(parent__isnull=True))
        if not raizes:
            return 0
        pesos = []
        for item in raizes:
            peso = item.peso_planejado
            pesos.append((item.percentual_realizado_calculado, peso))
        total_peso = sum(peso for _, peso in pesos)
        if total_peso <= 0:
            return 0
        total = sum(Decimal(str(percentual)) * peso for percentual, peso in pesos)
        return round(total / total_peso, 1)


class PlanoFisicoItem(models.Model):
    """
    Item do cronograma. A consolidacao de pais sempre prevalece sobre valores manuais.
    """

    class Meta:
        verbose_name = "Atividade do Cronograma"
        verbose_name_plural = "Atividades do Cronograma"
        ordering = ["plano", "sort_order", "pk"]
        indexes = [
            models.Index(fields=["plano", "codigo_atividade"]),
            models.Index(fields=["plano", "data_inicio_prevista"]),
            models.Index(fields=["plano", "parent", "sort_order"]),
        ]

    plano = models.ForeignKey(PlanoFisico, on_delete=models.CASCADE, related_name="itens")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="filhos",
        help_text="Item pai na hierarquia do cronograma",
    )
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="itens_cronograma",
        help_text="Item da EAP vinculado (opcional)",
    )
    codigo_eap_importado = models.CharField(
        max_length=100,
        blank=True,
        help_text="Codigo da EAP informado no arquivo importado",
    )
    erro_vinculo_eap = models.CharField(
        max_length=255,
        blank=True,
        help_text="Erro de vinculo quando o codigo da EAP nao existir",
    )
    codigo_atividade = models.CharField(max_length=50, help_text="Codigo da atividade (do arquivo)")
    atividade = models.CharField(max_length=500, help_text="Nome da atividade")
    predecessor = models.CharField(max_length=50, blank=True, help_text="Codigo da predecessora")
    successor = models.CharField(max_length=50, blank=True, help_text="Codigo da sucessora")
    duracao = models.PositiveIntegerField(default=0, help_text="Duracao planejada em dias")
    data_inicio_prevista = models.DateField(null=True, blank=True)
    data_fim_prevista = models.DateField(null=True, blank=True)
    data_inicio_real = models.DateField(null=True, blank=True)
    data_fim_real = models.DateField(null=True, blank=True)
    percentual_concluido = models.PositiveSmallIntegerField(
        default=0,
        help_text="Percentual realizado informado para a atividade (0-100)",
    )
    is_marco = models.BooleanField(default=False, help_text="Indica se e um marco")
    level = models.PositiveSmallIntegerField(default=0)
    wbs_code = models.CharField(max_length=50, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    valor_planejado = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valor_realizado = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    dias_desvio = models.IntegerField(default=0, help_text="Dias de desvio (positivo = atraso)")
    percent_desvio = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.codigo_atividade} - {self.atividade[:50]}"

    def aplicar_percentual_realizado(self, percentual, *, data_lancamento=None, commit=True):
        if self.tem_filhos:
            return self

        data_referencia = data_lancamento or timezone.localdate()
        percentual_decimal = Decimal(str(percentual or 0))
        percentual_decimal = max(Decimal("0.00"), min(percentual_decimal, Decimal("100.00")))
        percentual_inteiro = int(percentual_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        percentual_anterior = int(self.percentual_concluido or 0)

        if percentual_inteiro <= 0:
            self.percentual_concluido = 0
            self.data_inicio_real = None
            self.data_fim_real = None
        else:
            self.percentual_concluido = percentual_inteiro
            if not self.data_inicio_real:
                self.data_inicio_real = data_referencia
            if percentual_anterior == 0 and percentual_inteiro == 100:
                self.data_inicio_real = data_referencia
                self.data_fim_real = data_referencia
            elif percentual_inteiro == 100:
                self.data_fim_real = data_referencia
            elif self.data_fim_real:
                self.data_fim_real = None

        if commit:
            self.save()
        return self

    def save(self, *args, **kwargs):
        if self.parent_id and self.level <= self.parent.level:
            self.level = self.parent.level + 1
        elif not self.parent_id:
            self.level = 0

        if self.data_inicio_prevista and self.data_inicio_real:
            diff = self.data_inicio_real - self.data_inicio_prevista
            self.dias_desvio = diff.days
        elif self.data_inicio_prevista:
            from datetime import date

            if date.today() > self.data_inicio_prevista:
                diff = date.today() - self.data_inicio_prevista
                self.dias_desvio = diff.days
            else:
                self.dias_desvio = 0

        if self.percentual_concluido is not None:
            expected_percent = self._calcular_percentual_esperado()
            if expected_percent > 0:
                self.percent_desvio = self.percentual_concluido - expected_percent

        if self.pk and self.filhos.exists():
            self.valor_realizado = Decimal("0.00")
        else:
            percentual = Decimal(str(self.percentual_concluido or 0))
            percentual = max(Decimal("0.00"), min(percentual, Decimal("100.00")))
            valor_planejado = self.valor_planejado or Decimal("0.00")
            self.valor_realizado = (valor_planejado * percentual / Decimal("100.00")).quantize(Decimal("0.01"))

        super().save(*args, **kwargs)

    def _calcular_percentual_esperado(self):
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

    @property
    def tem_filhos(self):
        return self.filhos.exists()

    @property
    def codigo_eap_exibicao(self):
        if self.plano_contas_id and self.plano_contas:
            return self.plano_contas.codigo
        return (self.codigo_eap_importado or "").strip()

    @property
    def duracao_calculada(self):
        if self.tem_filhos:
            inicio = self.data_inicio_prevista_calculada
            fim = self.data_fim_prevista_calculada
        else:
            inicio = self.data_inicio_prevista
            fim = self.data_fim_prevista

        if inicio and fim:
            dias = (fim - inicio).days + 1
            return max(dias, 0)
        return 0

    @property
    def peso_planejado(self):
        dias = self.duracao_calculada
        if dias > 0:
            return Decimal(str(dias))

        if self.data_inicio_prevista and self.data_fim_prevista:
            dias = (self.data_fim_prevista - self.data_inicio_prevista).days + 1
            if dias > 0:
                return Decimal(str(dias))

        return Decimal("1")

    @property
    def data_inicio_prevista_calculada(self):
        if not self.tem_filhos:
            return self.data_inicio_prevista
        datas = [filho.data_inicio_prevista_calculada for filho in self.filhos.all() if filho.data_inicio_prevista_calculada]
        return min(datas) if datas else None

    @property
    def data_fim_prevista_calculada(self):
        if not self.tem_filhos:
            return self.data_fim_prevista
        datas = [filho.data_fim_prevista_calculada for filho in self.filhos.all() if filho.data_fim_prevista_calculada]
        return max(datas) if datas else None

    @property
    def data_inicio_real_calculada(self):
        if not self.tem_filhos:
            return self.data_inicio_real
        datas = [filho.data_inicio_real_calculada for filho in self.filhos.all() if filho.data_inicio_real_calculada]
        return min(datas) if datas else None

    @property
    def data_fim_real_calculada(self):
        if not self.tem_filhos:
            return self.data_fim_real
        datas = [filho.data_fim_real_calculada for filho in self.filhos.all() if filho.data_fim_real_calculada]
        return max(datas) if datas else None

    @property
    def percentual_previsto_calculado(self):
        if not self.tem_filhos:
            return round(float(self._calcular_percentual_esperado() or 0), 1)

        filhos_validos = []
        for filho in self.filhos.all():
            peso = filho.peso_planejado
            if peso > 0:
                filhos_validos.append((filho.percentual_previsto_calculado, peso))

        if not filhos_validos:
            return 0

        peso_total = sum(peso for _, peso in filhos_validos)
        if peso_total <= 0:
            return 0

        total = sum(Decimal(str(percentual)) * peso for percentual, peso in filhos_validos)
        return round(total / peso_total, 1)

    @property
    def percentual_realizado_calculado(self):
        if not self.tem_filhos:
            return round(float(self.percentual_concluido or 0), 1)

        filhos_validos = []
        for filho in self.filhos.all():
            peso = filho.peso_planejado
            if peso > 0:
                filhos_validos.append((filho.percentual_realizado_calculado, peso))

        if not filhos_validos:
            return 0

        peso_total = sum(peso for _, peso in filhos_validos)
        if peso_total <= 0:
            return 0

        total = sum(Decimal(str(percentual)) * peso for percentual, peso in filhos_validos)
        return round(total / peso_total, 1)


class PlanoFisicoBaseline(models.Model):
    """
    Historico de versoes de baseline.
    """

    class Meta:
        verbose_name = "Baseline do Cronograma"
        verbose_name_plural = "Baselines dos Cronogramas"
        ordering = ["-versao"]
        unique_together = ("plano", "versao")

    plano = models.ForeignKey(PlanoFisico, on_delete=models.CASCADE, related_name="historico_baseline")
    versao = models.PositiveIntegerField()
    arquivo = models.FileField(upload_to="cronogramas/baselines/%Y/%m", blank=True, null=True)
    observacao = models.TextField(blank=True)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    data_criacao = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.plano.titulo} - Baseline v{self.versao}"


class MapaCorrespondencia(models.Model):
    """
    Mapeia itens do cronograma para itens do orcamento (EAP).
    """

    class Meta:
        verbose_name = "Mapeamento Cronograma ↔ Orcamento"
        verbose_name_plural = "Mapeamentos Cronograma ↔ Orcamento"
        ordering = ["plano_fisico_item"]
        indexes = [
            models.Index(fields=["plano_fisico_item", "status"]),
            models.Index(fields=["plano_contas", "status"]),
        ]

    STATUS_CHOICES = (
        ("ATIVO", "Ativo"),
        ("INATIVO", "Inativo"),
    )

    empresa = models.ForeignKey("Empresa", on_delete=models.CASCADE, related_name="mapeamentos")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="mapeamentos")
    plano_fisico_item = models.ForeignKey(PlanoFisicoItem, on_delete=models.CASCADE, related_name="mapeamentos")
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mapeamentos",
        help_text="Centro de custo da EAP (opcional)",
    )
    percentual_rateio = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=100,
        help_text="% do item do cronograma que corresponde ao centro de custo",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ATIVO")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mapeamentos_criados",
    )

    def __str__(self):
        destino = self.plano_contas.codigo if self.plano_contas else "Nao vinculado"
        return f"{self.plano_fisico_item.codigo_atividade} → {destino} ({self.percentual_rateio}%)"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.percentual_rateio and (self.percentual_rateio < 0 or self.percentual_rateio > 100):
            raise ValidationError("Percentual de rateio deve estar entre 0 e 100")


class MapaCorrespondenciaRateio(models.Model):
    """
    Permite ratear um item do cronograma entre multiplos centros de custo.
    """

    class Meta:
        verbose_name = "Rateio de Mapeamento"
        verbose_name_plural = "Rateios de Mapeamento"
        unique_together = ("correspondencia", "plano_contas")

    correspondencia = models.ForeignKey(
        MapaCorrespondencia,
        on_delete=models.CASCADE,
        related_name="rateios",
    )
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.CASCADE,
        related_name="rateios_recebidos",
    )
    percentual = models.DecimalField(max_digits=5, decimal_places=2, help_text="% de rateio para este centro de custo")

    def __str__(self):
        return f"{self.correspondencia.plano_fisico_item.codigo_atividade} → {self.plano_contas.codigo}: {self.percentual}%"
