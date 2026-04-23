from decimal import Decimal

from django.conf import settings
from django.db import models

from .domain import arredondar_moeda, calcular_total_item, gerar_numero_documento
from .tenant_querysets import TenantScopedManager


class Fornecedor(models.Model):
    class Meta:
        verbose_name = "Fornecedor"
        verbose_name_plural = "Fornecedores"
        unique_together = ("empresa", "cnpj")
        ordering = ["razao_social"]

    empresa = models.ForeignKey("Empresa", on_delete=models.CASCADE, related_name="fornecedores")
    razao_social = models.CharField(max_length=180)
    nome_fantasia = models.CharField(max_length=180, blank=True)
    cnpj = models.CharField(max_length=18)
    contato = models.CharField(max_length=150, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    ativo = models.BooleanField(default=True)
    exclusao_logica_em = models.DateTimeField(null=True, blank=True)
    anonimizado_em = models.DateTimeField(null=True, blank=True)
    descartado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    @property
    def media_avaliacao(self):
        total = self.avaliacoes.aggregate(total=models.Sum("nota"))["total"] or Decimal("0.00")
        quantidade = self.avaliacoes.count()
        if not quantidade:
            return Decimal("0.00")
        return arredondar_moeda(total / quantidade)

    def __str__(self):
        return self.nome_fantasia or self.razao_social


class FornecedorAvaliacao(models.Model):
    class Meta:
        verbose_name = "AvaliaÃ§Ã£o de Fornecedor"
        verbose_name_plural = "AvaliaÃ§Ãµes de Fornecedor"
        ordering = ["-avaliado_em"]

    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.CASCADE, related_name="avaliacoes")
    obra = models.ForeignKey("Obra", on_delete=models.SET_NULL, null=True, blank=True, related_name="avaliacoes_fornecedores")
    nota = models.PositiveSmallIntegerField()
    comentario = models.TextField(blank=True)
    avaliado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    avaliado_em = models.DateTimeField(auto_now_add=True)

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.nota < 1 or self.nota > 5:
            raise ValidationError("A nota do fornecedor deve estar entre 1 e 5.")


class SolicitacaoCompra(models.Model):
    class Meta:
        verbose_name = "SolicitaÃ§Ã£o de Compra"
        verbose_name_plural = "SolicitaÃ§Ãµes de Compra"
        ordering = ["-data_solicitacao", "-id"]
        indexes = [
            models.Index(fields=["empresa", "obra", "status"]),
        ]

    STATUS_CHOICES = (
        ("RASCUNHO", "Rascunho"),
        ("EM_APROVACAO", "Em Aprovacao"),
        ("APROVADA", "Aprovada"),
        ("COTANDO", "Em CotaÃ§Ã£o"),
        ("ENCERRADA", "Encerrada"),
        ("CANCELADA", "Cancelada"),
    )

    empresa = models.ForeignKey("Empresa", on_delete=models.CASCADE, related_name="solicitacoes_compra")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="solicitacoes_compra")
    plano_contas = models.ForeignKey(
        "PlanoContas",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solicitacoes_compra",
    )
    numero = models.CharField(max_length=30, unique=True)
    titulo = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    solicitante = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="solicitacoes_compra")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RASCUNHO")
    data_solicitacao = models.DateField()
    observacoes = models.TextField(blank=True)
    enviado_para_aprovacao_em = models.DateTimeField(null=True, blank=True)
    enviado_para_aprovacao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solicitacoes_compra_enviadas_para_aprovacao",
    )
    parecer_aprovacao = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solicitacoes_compra_aprovadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = gerar_numero_documento(SolicitacaoCompra, "SC-", "numero")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero

    @property
    def valor_estimado_total(self):
        return self.itens.aggregate(total=models.Sum("valor_total_estimado"))["total"] or Decimal("0.00")


class SolicitacaoCompraItem(models.Model):
    solicitacao = models.ForeignKey(SolicitacaoCompra, on_delete=models.CASCADE, related_name="itens")
    plano_contas = models.ForeignKey("PlanoContas", on_delete=models.PROTECT, related_name="itens_solicitacao")
    descricao_tecnica = models.TextField(blank=True)
    unidade = models.CharField(max_length=20, blank=True)
    quantidade = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_estimado_unitario = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_total_estimado = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))

    def save(self, *args, **kwargs):
        if self.plano_contas_id and not self.unidade:
            self.unidade = self.plano_contas.unidade or ""
        self.valor_total_estimado = calcular_total_item(self.quantidade, self.valor_estimado_unitario)
        super().save(*args, **kwargs)


class Cotacao(models.Model):
    class Meta:
        verbose_name = "CotaÃ§Ã£o"
        verbose_name_plural = "CotaÃ§Ãµes"
        ordering = ["-data_cotacao", "-id"]

    STATUS_CHOICES = (
        ("RASCUNHO", "Rascunho"),
        ("EM_APROVACAO", "Em Aprovacao"),
        ("EM_ANALISE", "Em Análise"),
        ("APROVADA", "Aprovada"),
        ("REJEITADA", "Rejeitada"),
        ("CANCELADA", "Cancelada"),
    )

    empresa = models.ForeignKey("Empresa", on_delete=models.CASCADE, related_name="cotacoes")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="cotacoes")
    solicitacao = models.ForeignKey(SolicitacaoCompra, on_delete=models.CASCADE, related_name="cotacoes")
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name="cotacoes")
    numero = models.CharField(max_length=30, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RASCUNHO")
    data_cotacao = models.DateField()
    validade_ate = models.DateField(null=True, blank=True)
    observacoes = models.TextField(blank=True)
    justificativa_escolha = models.TextField(blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cotacoes_criadas")
    enviado_para_aprovacao_em = models.DateTimeField(null=True, blank=True)
    enviado_para_aprovacao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cotacoes_enviadas_para_aprovacao",
    )
    parecer_aprovacao = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cotacoes_aprovadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = gerar_numero_documento(Cotacao, "COT-", "numero")
        if not self.empresa_id and self.solicitacao_id:
            self.empresa = self.solicitacao.empresa
        if not self.obra_id and self.solicitacao_id:
            self.obra = self.solicitacao.obra
        super().save(*args, **kwargs)

    @property
    def valor_total(self):
        return self.itens.aggregate(total=models.Sum("valor_total"))["total"] or Decimal("0.00")

    def __str__(self):
        return self.numero


class CotacaoItem(models.Model):
    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name="itens")
    item_solicitacao = models.ForeignKey(SolicitacaoCompraItem, on_delete=models.PROTECT, related_name="cotacoes")
    valor_unitario = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    prazo_entrega_dias = models.PositiveIntegerField(default=0)

    def save(self, *args, **kwargs):
        self.valor_total = calcular_total_item(self.item_solicitacao.quantidade, self.valor_unitario)
        super().save(*args, **kwargs)


class CotacaoAnexo(models.Model):
    class Meta:
        verbose_name = "Anexo da CotaÃ§Ã£o"
        verbose_name_plural = "Anexos das CotaÃ§Ãµes"
        ordering = ["id"]

    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name="anexos")
    descricao = models.CharField(max_length=255, blank=True)
    arquivo = models.FileField(upload_to="cotacoes/%Y/%m")
    criado_em = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.descricao and self.arquivo:
            self.descricao = self.arquivo.name.split("/")[-1]
        super().save(*args, **kwargs)


class OrdemCompra(models.Model):
    class Meta:
        verbose_name = "Ordem de Compra"
        verbose_name_plural = "Ordens de Compra"
        ordering = ["-data_emissao", "-id"]

    STATUS_CHOICES = (
        ("RASCUNHO", "Rascunho"),
        ("EM_APROVACAO", "Em Aprovação"),
        ("APROVADA", "Aprovada"),
        ("PARCIAL", "Parcial"),
        ("CONCLUIDA", "ConcluÃ­da"),
        ("CANCELADA", "Cancelada"),
    )

    empresa = models.ForeignKey("Empresa", on_delete=models.CASCADE, related_name="ordens_compra")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="ordens_compra")
    solicitacao = models.ForeignKey(SolicitacaoCompra, on_delete=models.PROTECT, related_name="ordens_compra")
    cotacao_aprovada = models.ForeignKey(Cotacao, on_delete=models.PROTECT, related_name="ordens_compra")
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name="ordens_compra")
    compromisso_relacionado = models.ForeignKey(
        "Compromisso",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordens_compra_estruturadas",
    )
    numero = models.CharField(max_length=30, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RASCUNHO")
    data_emissao = models.DateField()
    descricao = models.TextField(blank=True)
    emitido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ordens_compra_emitidas")
    valor_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    enviado_para_aprovacao_em = models.DateTimeField(null=True, blank=True)
    enviado_para_aprovacao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordens_compra_enviadas_aprovacao",
    )
    parecer_aprovacao = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ordens_compra_aprovadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = gerar_numero_documento(OrdemCompra, "OC-", "numero")
        if not self.empresa_id and self.solicitacao_id:
            self.empresa = self.solicitacao.empresa
        if not self.obra_id and self.solicitacao_id:
            self.obra = self.solicitacao.obra
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero


class OrdemCompraItem(models.Model):
    ordem_compra = models.ForeignKey(OrdemCompra, on_delete=models.CASCADE, related_name="itens")
    plano_contas = models.ForeignKey("PlanoContas", on_delete=models.PROTECT, related_name="itens_ordem_compra")
    unidade = models.CharField(max_length=20, blank=True)
    quantidade = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_unitario = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))

    def save(self, *args, **kwargs):
        if self.plano_contas_id and not self.unidade:
            self.unidade = self.plano_contas.unidade or ""
        self.valor_total = calcular_total_item(self.quantidade, self.valor_unitario)
        super().save(*args, **kwargs)
