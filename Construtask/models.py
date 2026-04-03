from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Sum
from mptt.managers import TreeManager
from mptt.models import MPTTModel, TreeForeignKey

from .domain import (
    arredondar_moeda,
    calcular_total_item,
    gerar_numero_documento,
    hidratar_medicao_do_contrato,
    validar_compromisso_orcamento,
    validar_medicao_contrato,
    validar_nota_fiscal,
)


# Validators
cnpj_validator = RegexValidator(
    regex=r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$",
    message="CNPJ deve estar no formato XX.XXX.XXX/XXXX-XX",
)


class Empresa(models.Model):
    """
    Modelo Tenant para suportar múltiplas empresas (Multi-tenant).
    Cada empresa pode ter suas próprias obras e usuários.
    """
    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"

    nome = models.CharField(max_length=150)
    nome_fantasia = models.CharField(max_length=150, blank=True)
    cnpj = models.CharField(max_length=18, unique=True, validators=[cnpj_validator])
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    endereco = models.TextField(blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nome


class UserProfile(models.Model):
    """
    Perfil estendido do usuário com empresa (tenant) e papel.
    """
    class Meta:
        verbose_name = "Perfil de Usuário"
        verbose_name_plural = "Perfis de Usuário"

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, null=True, blank=True, related_name="usuarios")
    telefone = models.CharField(max_length=20, blank=True)
    cargo = models.CharField(max_length=100, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.usuario.username} - {self.empresa}"


class UsuarioEmpresa(models.Model):
    """
    Modelo para vincular usuários a empresas com permissões específicas.
    Cada usuário pertence a uma empresa e pode ser admin dessa empresa.
    """
    class Meta:
        verbose_name = "Usuário de Empresa"
        verbose_name_plural = "Usuários de Empresa"
        unique_together = ("usuario", "empresa")

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usuario_empresa")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="usuarios_empresa")
    is_admin_empresa = models.BooleanField(
        default=False,
        help_text="Se marcado, este usuário pode gerenciar usuários e liberar obras da empresa."
    )
    obras_permitidas = models.ManyToManyField(
        "Obra",
        related_name="usuarios_permitidos",
        blank=True,
        help_text="Obras que este usuário pode acessar. Se vazio e não for admin, não terá acesso a nenhuma obra."
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        admin_label = " (Admin)" if self.is_admin_empresa else ""
        return f"{self.usuario.username} - {self.empresa.nome}{admin_label}"

    def save(self, *args, **kwargs):
        # Admin da empresa automaticamente tem acesso a todas as obras da empresa
        if self.is_admin_empresa:
            # Não precisa salvar obras_permitidas para admin, pois ele vê todas
            pass
        super().save(*args, **kwargs)


class AuditEvent(models.Model):
    """
    Modelo de auditoria para conformidade ISO 9.2.
    Registra todas as operações Create/Update/Delete com diff.
    """
    class Meta:
        verbose_name = "Evento de Auditoria"
        verbose_name_plural = "Eventos de Auditoria"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["empresa", "timestamp"]),
            models.Index(fields=["usuario", "timestamp"]),
            models.Index(fields=["acao"]),
        ]

    ACAO_CHOICES = (
        ("CREATE", "Criação"),
        ("UPDATE", "Atualização"),
        ("DELETE", "Exclusão"),
        ("APPROVE", "Aprovação"),
        ("REJECT", "Rejeição"),
        ("UPLOAD", "Upload de arquivo"),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True, related_name="audits")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="audits")
    timestamp = models.DateTimeField(auto_now_add=True)
    acao = models.CharField(max_length=20, choices=ACAO_CHOICES)
    entidade_app = models.CharField(max_length=50)  # ex: 'Construtask.Obra'
    entidade_label = models.CharField(max_length=100)  # ex: 'Obra OBJ-001'
    objeto_id = models.PositiveIntegerField()
    antes = models.JSONField(null=True, blank=True)
    depois = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    request_id = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return f"{self.acao} - {self.entidade_label} por {self.usuario} em {self.timestamp:%d/%m/%Y %H:%M}"


class PlanoContas(MPTTModel):

    class Meta:
        verbose_name = "Plano de Contas"
        verbose_name_plural = "Plano de Contas"
        indexes = [models.Index(fields=["codigo"])]
        constraints = [
            models.UniqueConstraint(fields=["obra", "codigo"], name="uq_plano_contas_obra_codigo")
        ]

    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, null=True, blank=True, related_name="planos_contas")
    codigo = models.CharField(max_length=50, editable=False)
    descricao = models.CharField(max_length=255)
    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="filhos")
    unidade = models.CharField(max_length=20, null=True, blank=True)
    quantidade = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    valor_unitario = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    valor_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))

    objects = TreeManager()

    class MPTTMeta:
        order_insertion_by = ["codigo"]

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"

    def gerar_codigo(self):
        def formatar(numero):
            return str(numero).zfill(2)

        if self.parent:
            filhos = self.parent.get_children().order_by("-codigo")
            if filhos.exists():
                ultimo_codigo = filhos.first().codigo
                partes = ultimo_codigo.split(".")
                partes[-1] = formatar(int(partes[-1]) + 1)
                return ".".join(partes)
            return f"{self.parent.codigo}.01"

        raizes = PlanoContas.objects.filter(parent__isnull=True, obra=self.obra).order_by("-codigo")
        if raizes.exists():
            return formatar(int(raizes.first().codigo) + 1)
        return "01"

    def clean(self):
        if self.parent and self.obra_id != self.parent.obra_id:
            raise ValidationError("O centro de custo filho deve pertencer a mesma obra do pai.")
        nivel = self.level if self.pk else (self.parent.level + 1 if self.parent else 0)
        if nivel < 5 and (self.unidade or self.quantidade or self.valor_unitario):
            raise ValidationError("Unidade, quantidade e valor unitário só podem existir no nível 6.")

    def save(self, *args, **kwargs):
        if self.parent and not self.obra_id:
            self.obra = self.parent.obra
        if not self.codigo:
            self.codigo = self.gerar_codigo()
        if self.quantidade is not None and self.valor_unitario is not None:
            self.valor_total = (
                Decimal(self.quantidade) * Decimal(self.valor_unitario)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)

    @property
    def valor_total_consolidado(self):
        return self.get_descendants(include_self=True).aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")

    @property
    def valor_comprometido(self):
        return self.get_descendants(include_self=True).aggregate(total=Sum("compromissos__valor_contratado"))["total"] or Decimal("0.00")

    @property
    def valor_medido(self):
        return self.get_descendants(include_self=True).aggregate(total=Sum("medicoes__valor_medido"))["total"] or Decimal("0.00")

    @property
    def valor_executado(self):
        return self.get_descendants(include_self=True).aggregate(total=Sum("notafiscalcentrocusto__valor"))["total"] or Decimal("0.00")

    @property
    def saldo_a_comprometer(self):
        return arredondar_moeda(self.valor_total_consolidado - self.valor_comprometido)

    @property
    def saldo_a_medir(self):
        return arredondar_moeda(self.valor_comprometido - self.valor_medido)


STATUS_OBRA_CHOICES = (
    ("PLANEJADA", "Planejada"),
    ("EM_ANDAMENTO", "Em Andamento"),
    ("PARALISADA", "Paralisada"),
    ("CONCLUIDA", "Concluida"),
)


STATUS_COMPROMISSO_CHOICES = (
    ("RASCUNHO", "Rascunho"),
    ("APROVADO", "Aprovado"),
    ("EM_EXECUCAO", "Em Execucao"),
    ("ENCERRADO", "Encerrado"),
    ("CANCELADO", "Cancelado"),
)


STATUS_MEDICAO_CHOICES = (
    ("EM_ELABORACAO", "Em Elaboracao"),
    ("CONFERIDA", "Conferida"),
    ("APROVADA", "Aprovada"),
    ("FATURADA", "Faturada"),
)


STATUS_NOTA_CHOICES = (
    ("LANCADA", "Lancada"),
    ("CONFERIDA", "Conferida"),
    ("PAGA", "Paga"),
    ("FECHADA", "Fechada"),
)


class Obra(models.Model):
    class Meta:
        verbose_name = "Obra"
        verbose_name_plural = "Obras"

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, null=True, blank=True, related_name="obras")
    codigo = models.CharField(max_length=30, unique=True)
    nome = models.CharField(max_length=150)
    cliente = models.CharField(max_length=150, blank=True)
    responsavel = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_OBRA_CHOICES, default="EM_ANDAMENTO")
    data_inicio = models.DateField(null=True, blank=True)
    data_fim = models.DateField(null=True, blank=True)
    descricao = models.TextField(blank=True)

    def __str__(self):
        return f"{self.codigo} - {self.nome}"



class Compromisso(models.Model):
    class Meta:
        verbose_name = "Compras e Contratações"
        verbose_name_plural = "Compras e Contratações"

    TIPO_CHOICES = (
        ("CONTRATO", "Contrato (Serviço)"),
        ("PEDIDO_COMPRA", "Pedido de Compra (Material)"),
    )

    numero = models.CharField(max_length=30, unique=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, null=True, blank=True, related_name="compromissos")
    torre = models.CharField(max_length=80, blank=True)
    bloco = models.CharField(max_length=80, blank=True)
    etapa = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_COMPROMISSO_CHOICES, default="RASCUNHO")
    centro_custo = models.ForeignKey(
        PlanoContas,
        on_delete=models.PROTECT,
        related_name="compromissos",
        null=True,
        blank=True,
    )
    descricao = models.CharField(max_length=500)
    fornecedor = models.CharField(max_length=150)
    cnpj = models.CharField(max_length=18, validators=[cnpj_validator])
    responsavel = models.CharField(max_length=150)
    telefone = models.CharField(max_length=20)
    valor_contratado = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    data_assinatura = models.DateField()
    data_prevista_inicio = models.DateField(null=True, blank=True)
    data_prevista_fim = models.DateField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    @property
    def valor_executado(self):
        if self.tipo == "CONTRATO":
            total = self.medicoes.aggregate(total=Sum("valor_medido"))["total"]
        else:
            total = self.notas_fiscais_material.aggregate(total=Sum("valor_total"))["total"]
        return total or Decimal("0.00")

    @property
    def saldo(self):
        return arredondar_moeda(self.valor_contratado - self.valor_executado)

    @property
    def quantidade_total(self):
        return self.itens.aggregate(total=Sum("quantidade"))["total"] or Decimal("0.00")

    @property
    def valor_unitario_medio(self):
        if not self.quantidade_total:
            return Decimal("0.00")
        return arredondar_moeda(self.valor_contratado / self.quantidade_total)

    def recalcular_totais_por_itens(self):
        total_base = self.itens.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")
        total_aditivos_valor = (
            self.aditivos.filter(tipo__in=["VALOR", "ESCOPO"])
            .aggregate(total=Sum("itens__valor"))["total"]
            or Decimal("0.00")
        )
        self.valor_contratado = arredondar_moeda(total_base + total_aditivos_valor)
        Compromisso.objects.filter(pk=self.pk).update(valor_contratado=self.valor_contratado)

    def clean(self):
        super().clean()
        validar_compromisso_orcamento(self)

    def save(self, *args, **kwargs):
        if self.centro_custo_id and not self.obra_id:
            self.obra = self.centro_custo.obra
        if not self.status:
            self.status = "RASCUNHO"
        prefixo = "CTR-" if self.tipo == "CONTRATO" else "PED-"
        if not self.numero or not self.numero.startswith(prefixo):
            self.numero = gerar_numero_documento(Compromisso, prefixo, "numero")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero


class AditivoContrato(models.Model):
    class Meta:
        verbose_name = "Aditivo Contratual"
        verbose_name_plural = "Aditivos Contratuais"
        ordering = ["-criado_em"]

    TIPO_CHOICES = (
        ("PRAZO", "Prazo"),
        ("VALOR", "Valor"),
        ("ESCOPO", "Escopo"),
    )

    contrato = models.ForeignKey(Compromisso, on_delete=models.CASCADE, related_name="aditivos")
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    descricao = models.CharField(max_length=500, blank=True)
    # Para PRAZO: incremento em dias.
    delta_dias = models.IntegerField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.contrato_id and self.contrato.tipo != "CONTRATO":
            raise ValidationError("Aditivos contratuais só podem ser vinculados a contratos.")

        if self.tipo == "PRAZO":
            if self.delta_dias in (None, ""):
                raise ValidationError("Para aditivo de prazo, informe delta de dias.")
        else:
            # VALOR/ESCOPO não usam delta_dias.
            if self.delta_dias not in (None, 0, ""):
                raise ValidationError("delta de dias deve ficar vazio apenas para aditivo de prazo.")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Adiciona/atualiza o valor_contratado sempre que houver aditivo de valor.
        if self.tipo in ("VALOR", "ESCOPO"):
            self.contrato.recalcular_totais_por_itens()

    def __str__(self):
        return f"Aditivo {self.get_tipo_display()} ({self.contrato.numero})"


class AditivoContratoItem(models.Model):
    class Meta:
        verbose_name = "Item de Aditivo"
        verbose_name_plural = "Itens de Aditivos"

    aditivo = models.ForeignKey(AditivoContrato, on_delete=models.CASCADE, related_name="itens")
    centro_custo = models.ForeignKey(
        PlanoContas,
        on_delete=models.PROTECT,
        related_name="aditivos_itens",
    )
    valor = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))

    def clean(self):
        if self.aditivo_id and self.aditivo.tipo == "PRAZO":
            raise ValidationError("Aditivo de prazo nao possui itens de valor.")

        if not self.centro_custo_id:
            return

        if self.valor in (None, ""):
            return

        if self.valor <= 0:
            raise ValidationError("Valor do aditivo deve ser maior que zero.")

        if self.aditivo_id and self.aditivo.contrato_id:
            contrato = self.aditivo.contrato
            if not contrato.itens.filter(centro_custo_id=self.centro_custo_id).exists():
                raise ValidationError("Centro de custo do aditivo deve estar no escopo do contrato.")

    def save(self, *args, **kwargs):
        if self.valor is not None:
            self.valor = arredondar_moeda(self.valor)
        super().save(*args, **kwargs)
        if self.aditivo_id:
            self.aditivo.contrato.recalcular_totais_por_itens()

    def delete(self, *args, **kwargs):
        aditivo = self.aditivo
        super().delete(*args, **kwargs)
        aditivo.contrato.recalcular_totais_por_itens()


class CompromissoItem(models.Model):
    compromisso = models.ForeignKey(Compromisso, on_delete=models.CASCADE, related_name="itens")
    centro_custo = models.ForeignKey(PlanoContas, on_delete=models.PROTECT, related_name="itens_compromisso")
    descricao_tecnica = models.TextField(blank=True)
    unidade = models.CharField(max_length=20, blank=True)
    quantidade = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_unitario = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        verbose_name = "Item da Compra/Contratação"
        verbose_name_plural = "Itens da Compra/Contratação"

    def __str__(self):
        return f"{self.compromisso.numero} - {self.centro_custo.codigo}"

    def clean(self):
        # Linhas incompletas (ex.: "linha extra" sem centro de custo)
        # nao devem disparar validacoes de valor/quantidade.
        if not self.centro_custo_id:
            return
        if self.compromisso_id and self.compromisso.obra_id and self.centro_custo_id:
            if self.centro_custo.obra_id != self.compromisso.obra_id:
                raise ValidationError("Centro de custo do item deve pertencer a mesma obra do compromisso.")
        quantidade = self.quantidade if self.quantidade is not None else Decimal("0.00")
        valor_unitario = self.valor_unitario if self.valor_unitario is not None else Decimal("0.00")
        if quantidade <= 0:
            raise ValidationError("A quantidade do item deve ser maior que zero.")
        if valor_unitario < 0:
            raise ValidationError("O valor unitário do item não pode ser negativo.")
        self.quantidade = quantidade
        self.valor_unitario = valor_unitario

    def save(self, *args, **kwargs):
        if self.centro_custo_id and self.compromisso_id and not self.compromisso.obra_id:
            self.compromisso.obra = self.centro_custo.obra
            self.compromisso.save(update_fields=["obra"])
        if self.centro_custo and not self.unidade:
            self.unidade = self.centro_custo.unidade or ""
        self.valor_total = calcular_total_item(self.quantidade, self.valor_unitario)
        super().save(*args, **kwargs)
        self.compromisso.recalcular_totais_por_itens()

    def delete(self, *args, **kwargs):
        compromisso = self.compromisso
        super().delete(*args, **kwargs)
        compromisso.recalcular_totais_por_itens()


class Medicao(models.Model):
    class Meta:
        verbose_name = "Medição"
        verbose_name_plural = "Medições"
        ordering = ["-data_medicao"]

    contrato = models.ForeignKey(Compromisso, on_delete=models.PROTECT, related_name="medicoes")
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, null=True, blank=True, related_name="medicoes")
    torre = models.CharField(max_length=80, blank=True)
    bloco = models.CharField(max_length=80, blank=True)
    etapa = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_MEDICAO_CHOICES, default="EM_ELABORACAO")
    fornecedor = models.CharField(max_length=150, blank=True)
    cnpj = models.CharField(max_length=18, blank=True)
    responsavel = models.CharField(max_length=150, blank=True)
    valor_contrato = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    centro_custo = models.ForeignKey(
        PlanoContas,
        on_delete=models.PROTECT,
        related_name="medicoes",
        null=True,
        blank=True,
    )
    numero_da_medicao = models.CharField(max_length=30, unique=True, null=True, blank=True)
    data_medicao = models.DateField()
    data_prevista_inicio = models.DateField(null=True, blank=True)
    data_prevista_fim = models.DateField(null=True, blank=True)
    descricao = models.CharField(max_length=900)
    valor_medido = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    criado_em = models.DateTimeField(auto_now_add=True)

    @property
    def quantidade_total(self):
        return self.itens.aggregate(total=Sum("quantidade"))["total"] or Decimal("0.00")

    @property
    def valor_unitario_medio(self):
        if not self.quantidade_total:
            return Decimal("0.00")
        return arredondar_moeda(self.valor_medido / self.quantidade_total)

    def recalcular_totais_por_itens(self):
        total = self.itens.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")
        self.valor_medido = arredondar_moeda(total)
        Medicao.objects.filter(pk=self.pk).update(valor_medido=self.valor_medido)

    def clean(self):
        super().clean()
        validar_medicao_contrato(self)

    def save(self, *args, **kwargs):
        if not self.status:
            self.status = "EM_ELABORACAO"
        if self.contrato_id and not self.obra_id:
            self.obra = self.contrato.obra
        if self.contrato_id and not self.torre:
            self.torre = self.contrato.torre
        if self.contrato_id and not self.bloco:
            self.bloco = self.contrato.bloco
        if self.contrato_id and not self.etapa:
            self.etapa = self.contrato.etapa
        hidratar_medicao_do_contrato(self)
        if not self.numero_da_medicao or not self.numero_da_medicao.startswith("MED-"):
            self.numero_da_medicao = gerar_numero_documento(Medicao, "MED-", "numero_da_medicao")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero_da_medicao or f"MED-{str(self.pk or 0).zfill(4)}"


class MedicaoItem(models.Model):
    medicao = models.ForeignKey(Medicao, on_delete=models.CASCADE, related_name="itens")
    centro_custo = models.ForeignKey(PlanoContas, on_delete=models.PROTECT, related_name="itens_medicao")
    unidade = models.CharField(max_length=20, blank=True)
    quantidade = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_unitario = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        verbose_name = "Item da Medição"
        verbose_name_plural = "Itens da Medição"

    def __str__(self):
        return f"{self.medicao.numero_da_medicao} - {self.centro_custo.codigo}"

    def clean(self):
        # Linhas incompletas (ex.: "linha extra" sem centro de custo)
        # nao devem disparar validacoes de valor/quantidade.
        if not self.centro_custo_id:
            return
        if self.medicao_id and self.medicao.obra_id and self.centro_custo_id:
            if self.centro_custo.obra_id != self.medicao.obra_id:
                raise ValidationError("Centro de custo do item deve pertencer a mesma obra da medicao.")
        quantidade = self.quantidade if self.quantidade is not None else Decimal("0.00")
        valor_unitario = self.valor_unitario if self.valor_unitario is not None else Decimal("0.00")
        if quantidade <= 0:
            raise ValidationError("A quantidade do item medido deve ser maior que zero.")
        if valor_unitario < 0:
            raise ValidationError("O valor unitário do item medido não pode ser negativo.")
        self.quantidade = quantidade
        self.valor_unitario = valor_unitario
        if self.medicao_id and self.medicao.contrato_id:
            item_contrato = self.medicao.contrato.itens.filter(centro_custo=self.centro_custo).first()
            if item_contrato:
                if self.unidade and self.unidade != item_contrato.unidade:
                    raise ValidationError("A unidade da medição deve ser igual à unidade definida no contrato.")
                if self.valor_unitario and self.valor_unitario != item_contrato.valor_unitario:
                    raise ValidationError("O valor unitário da medição deve ser igual ao valor unitário definido no contrato.")

    def save(self, *args, **kwargs):
        if self.medicao_id and self.medicao.contrato_id:
            item_contrato = self.medicao.contrato.itens.filter(centro_custo=self.centro_custo).first()
            if item_contrato:
                self.unidade = item_contrato.unidade or ""
                self.valor_unitario = item_contrato.valor_unitario
        elif self.centro_custo and not self.unidade:
            self.unidade = self.centro_custo.unidade or ""
        self.valor_total = calcular_total_item(self.quantidade, self.valor_unitario)
        super().save(*args, **kwargs)
        self.medicao.recalcular_totais_por_itens()

    def delete(self, *args, **kwargs):
        medicao = self.medicao
        super().delete(*args, **kwargs)
        medicao.recalcular_totais_por_itens()


class NotaFiscal(models.Model):
    class Meta:
        verbose_name = "Nota Fiscal"
        verbose_name_plural = "Notas Fiscais"
        ordering = ["-data_emissao"]

    TIPO_CHOICES = (
        ("SERVICO", "Nota de Serviço"),
        ("MATERIAL", "Nota de Material"),
    )

    numero = models.CharField(max_length=50)
    serie = models.CharField(max_length=10, blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT, null=True, blank=True, related_name="notas_fiscais")
    torre = models.CharField(max_length=80, blank=True)
    bloco = models.CharField(max_length=80, blank=True)
    etapa = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_NOTA_CHOICES, default="LANCADA")
    data_emissao = models.DateField()
    fornecedor = models.CharField(max_length=150)
    cnpj = models.CharField(max_length=18, validators=[cnpj_validator])
    descricao = models.CharField(max_length=500)
    valor_total = models.DecimalField(max_digits=15, decimal_places=2)
    medicao = models.ForeignKey(Medicao, on_delete=models.PROTECT, null=True, blank=True, related_name="notas_fiscais")
    pedido_compra = models.ForeignKey(
        Compromisso,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="notas_fiscais_material",
        limit_choices_to={"tipo": "PEDIDO_COMPRA"},
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"NF {self.numero}"

    def clean(self):
        super().clean()
        validar_nota_fiscal(self)

    def save(self, *args, **kwargs):
        if not self.status:
            self.status = "LANCADA"
        origem = self.medicao or self.pedido_compra
        if origem and not self.obra_id:
            self.obra = getattr(origem, "obra", None)
        if origem and not self.torre:
            self.torre = getattr(origem, "torre", "")
        if origem and not self.bloco:
            self.bloco = getattr(origem, "bloco", "")
        if origem and not self.etapa:
            self.etapa = getattr(origem, "etapa", "")
        super().save(*args, **kwargs)


class NotaFiscalCentroCusto(models.Model):
    class Meta:
        verbose_name = "Centro de Custo da Nota"
        verbose_name_plural = "Centros de Custo da Nota"

    nota_fiscal = models.ForeignKey(NotaFiscal, on_delete=models.CASCADE, related_name="centros_custo")
    centro_custo = models.ForeignKey(PlanoContas, on_delete=models.PROTECT)
    valor = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.nota_fiscal.numero} - {self.centro_custo.codigo}"

    def clean(self):
        super().clean()
        if not self.nota_fiscal_id:
            return

        permitidos_ids = None
        if self.nota_fiscal.pedido_compra:
            permitidos_ids = set(self.nota_fiscal.pedido_compra.itens.values_list("centro_custo_id", flat=True))
            if not permitidos_ids and self.nota_fiscal.pedido_compra.centro_custo_id:
                permitidos_ids = {self.nota_fiscal.pedido_compra.centro_custo_id}
        elif self.nota_fiscal.medicao:
            permitidos_ids = set(self.nota_fiscal.medicao.itens.values_list("centro_custo_id", flat=True))
            if not permitidos_ids and self.nota_fiscal.medicao.centro_custo_id:
                permitidos_ids = {self.nota_fiscal.medicao.centro_custo_id}

        if permitidos_ids is not None and self.centro_custo_id not in permitidos_ids:
            raise ValidationError("Centro de custo não pertence à origem desta nota fiscal.")

        if self.valor:
            total_rateado = (
                NotaFiscalCentroCusto.objects
                .filter(nota_fiscal_id=self.nota_fiscal_id)
                .exclude(pk=self.pk)
                .aggregate(total=Sum("valor"))["total"]
                or Decimal("0.00")
            )
            total_rateado += self.valor
            if total_rateado > self.nota_fiscal.valor_total:
                raise ValidationError("A soma do rateio ultrapassa o valor da nota fiscal.")


class AnexoOperacional(models.Model):
    class Meta:
        verbose_name = "Anexo Operacional"
        verbose_name_plural = "Anexos Operacionais"

    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, null=True, blank=True, related_name="anexos")
    compromisso = models.ForeignKey(Compromisso, on_delete=models.CASCADE, null=True, blank=True, related_name="anexos")
    medicao = models.ForeignKey(Medicao, on_delete=models.CASCADE, null=True, blank=True, related_name="anexos")
    nota_fiscal = models.ForeignKey(NotaFiscal, on_delete=models.CASCADE, null=True, blank=True, related_name="anexos")
    descricao = models.CharField(max_length=255)
    arquivo = models.FileField(upload_to="anexos/%Y/%m", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.descricao


class HistoricoOperacional(models.Model):
    class Meta:
        verbose_name = "Historico Operacional"
        verbose_name_plural = "Historicos Operacionais"
        ordering = ["-criado_em"]

    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, null=True, blank=True, related_name="historicos")
    compromisso = models.ForeignKey(Compromisso, on_delete=models.CASCADE, null=True, blank=True, related_name="historicos")
    medicao = models.ForeignKey(Medicao, on_delete=models.CASCADE, null=True, blank=True, related_name="historicos")
    nota_fiscal = models.ForeignKey(NotaFiscal, on_delete=models.CASCADE, null=True, blank=True, related_name="historicos")
    acao = models.CharField(max_length=40)
    descricao = models.CharField(max_length=255)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.acao} - {self.descricao}"


class FechamentoMensal(models.Model):
    class Meta:
        verbose_name = "Fechamento Mensal"
        verbose_name_plural = "Fechamentos Mensais"
        unique_together = ("obra", "ano", "mes")
        ordering = ["-ano", "-mes"]

    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, related_name="fechamentos")
    ano = models.PositiveIntegerField()
    mes = models.PositiveIntegerField()
    valor_comprometido = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_medido = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    valor_notas = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    fechado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.obra} - {self.mes:02d}/{self.ano}"


# =============================================================================
# FASE 2 - ISO 7.5 CONTROLE DOCUMENTAL
# =============================================================================

class Documento(models.Model):
    """
    Modelo para controle documental ISO 7.5.
    Documentos controlados com workflow de aprovação e versionamento.
    """
    class Meta:
        verbose_name = "Documento Controlado"
        verbose_name_plural = "Documentos Controlados"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "status"]),
            models.Index(fields=["codigo_documento"]),
        ]

    STATUS_CHOICES = (
        ("RASCUNHO", "Rascunho"),
        ("EM_REVISAO", "Em Revisão"),
        ("APROVADO", "Aprovado"),
        ("OBSOLETO", "Obsoleto"),
    )

    TIPO_CHOICES = (
        ("PROCEDIMENTO", "Procedimento"),
        ("INSTRUCAO", "Instrução de Trabalho"),
        ("REGISTRO", "Registro de Qualidade"),
        ("MANUAL", "Manual"),
        ("POLITICA", "Política"),
        ("ROTEIRO", "Roteiro/Checklist"),
        ("FORMULARIO", "Formulário"),
        ("OUTRO", "Outro"),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="documentos")
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE, null=True, blank=True, related_name="documentos")
    processo = models.CharField(max_length=100, blank=True, help_text="Processo/Atividade ISO relacionado")
    plano_contas = models.ForeignKey(
        PlanoContas,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="documentos",
        help_text="Vincular à EAP nível 5"
    )
    
    tipo_documento = models.CharField(max_length=20, choices=TIPO_CHOICES)
    codigo_documento = models.CharField(max_length=30, help_text="Código único do documento")
    titulo = models.CharField(max_length=255)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RASCUNHO")
    versao_atual = models.PositiveIntegerField(default=1)
    
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="documentos_criados")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.codigo_documento} - {self.titulo}"

    def save(self, *args, **kwargs):
        if not self.codigo_documento:
            self.codigo_documento = self.gerar_codigo()
        super().save(*args, **kwargs)

    def gerar_codigo(self):
        """Gera código único para o documento."""
        from datetime import datetime
        prefixos = {
            "PROCEDIMENTO": "PRO",
            "INSTRUCAO": "INS",
            "REGISTRO": "REG",
            "MANUAL": "MAN",
            "POLITICA": "POL",
            "ROTEIRO": "ROT",
            "FORMULARIO": "FOR",
            "OUTRO": "OUT",
        }
        prefixo = prefixos.get(self.tipo_documento, "DOC")
        ano = datetime.now().year
        ultimos = Documento.objects.filter(
            codigo_documento__startswith=f"{prefixo}-{ano}"
        ).order_by("-codigo_documento")
        
        if ultimos.exists():
            ultimo_codigo = ultimos.first().codigo_documento
            partes = ultimo_codigo.split("-")
            if len(partes) >= 3:
                try:
                    numero = int(partes[2]) + 1
                    return f"{prefixo}-{ano}-{numero:04d}"
                except ValueError:
                    pass
        return f"{prefixo}-{ano}-0001"

    def pode_revisar(self):
        return self.status in ["RASCUNHO", "EM_REVISAO"]

    def pode_aprovar(self):
        return self.status == "EM_REVISAO" and self.revisoes.exists()

    def pode_tornar_obsoleto(self):
        return self.status in ["RASCUNHO", "EM_REVISAO", "APROVADO"]

    def get_versao_aprovada(self):
        return self.revisoes.filter(status="APROVADO").order_by("-versao").first()


class DocumentoRevisao(models.Model):
    """
    Modelo para revisões de documentos (imutáveis após aprovação).
    ISO 7.5 - Controle de versões de documentos.
    """
    class Meta:
        verbose_name = "Revisão de Documento"
        verbose_name_plural = "Revisões de Documentos"
        ordering = ["-versao"]
        unique_together = ("documento", "versao")
        indexes = [
            models.Index(fields=["documento", "status"]),
        ]

    STATUS_CHOICES = (
        ("ELABORACAO", "Em Elaboração"),
        ("REVISAO", "Em Revisão"),
        ("APROVADO", "Aprovado"),
    )

    documento = models.ForeignKey(Documento, on_delete=models.CASCADE, related_name="revisoes")
    versao = models.PositiveIntegerField()
    
    arquivo = models.FileField(upload_to="documentos/%Y/%m", help_text="Arquivo do documento (PDF, DOCX)")
    checksum = models.CharField(max_length=64, blank=True, help_text="Hash SHA-256 do arquivo para integridade")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ELABORACAO")
    
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="revisoes_criadas")
    criado_em = models.DateTimeField(auto_now_add=True)
    
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="revisoes_revisadas"
    )
    data_revisao = models.DateTimeField(null=True, blank=True)
    
    aprobador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="revisoes_aprovadas"
    )
    data_aprovacao = models.DateTimeField(null=True, blank=True)
    
    parecer = models.TextField(blank=True, help_text="Parecer sobre a revisão")
    arquivo_aprovado = models.FileField(
        upload_to="documentos/aprovados/%Y/%m",
        blank=True,
        help_text="Cópia imutável do arquivo aprovado"
    )

    def __str__(self):
        return f"{self.documento.codigo_documento} - Rev. {self.versao:02d}"

    def save(self, *args, **kwargs):
        if not self.pk and not self.versao:
            self.versao = 1
        super().save(*args, **kwargs)

    def pode_aprovar(self):
        return self.status == "REVISAO"


# Reexports incrementais para manter compatibilidade de imports centralizados.
from .models_qualidade import NaoConformidade, NaoConformidadeHistorico  # noqa: E402,F401
from .models_aquisicoes import (  # noqa: E402,F401
    Cotacao,
    CotacaoAnexo,
    CotacaoItem,
    Fornecedor,
    FornecedorAvaliacao,
    OrdemCompra,
    OrdemCompraItem,
    SolicitacaoCompra,
    SolicitacaoCompraItem,
)
