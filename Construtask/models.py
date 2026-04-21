from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone
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


class PlanoEmpresa(models.Model):
    """
    Define os limites SaaS de cada empresa (tier de assinatura).
    Criado/editado exclusivamente pelo superuser Construtask via sistema_admin.
    """

    class Meta:
        verbose_name = "Plano da Empresa"
        verbose_name_plural = "Planos das Empresas"

    PLANO_CHOICES = (
        ("STARTER", "Starter"),
        ("PROFESSIONAL", "Professional"),
        ("BUSINESS", "Business"),
        ("ENTERPRISE", "Enterprise"),
    )

    # Limites por plano conforme tabela de preços Construtask
    LIMITES_PADRAO = {
        "STARTER":      {"max_usuarios": 8,   "max_obras": 3,   "preco_mensal": 490},
        "PROFESSIONAL": {"max_usuarios": 23,  "max_obras": 9,   "preco_mensal": 1190},
        "BUSINESS":     {"max_usuarios": 50,  "max_obras": 18,  "preco_mensal": 2490},
        "ENTERPRISE":   {"max_usuarios": None, "max_obras": None, "preco_mensal": 4990},
    }

    nome = models.CharField(
        max_length=20,
        choices=PLANO_CHOICES,
        default="STARTER",
        unique=False,   # cada empresa tem sua própria linha
        verbose_name="Plano",
    )
    empresa = models.OneToOneField(
        "Empresa",
        on_delete=models.CASCADE,
        related_name="plano",
        verbose_name="Empresa",
    )
    # Limites customizados — None = ilimitado
    max_usuarios = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Máximo de usuários ativos. Nulo = ilimitado (Enterprise).",
    )
    max_obras = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Máximo de obras ativas. Nulo = ilimitado (Enterprise).",
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.empresa.nome} — {self.get_nome_display()}"

    def save(self, *args, **kwargs):
        """Preenche os limites padrão do plano se não informados."""
        limites = self.LIMITES_PADRAO.get(self.nome, {})
        if self.max_usuarios is None and "max_usuarios" in limites:
            self.max_usuarios = limites["max_usuarios"]
        if self.max_obras is None and "max_obras" in limites:
            self.max_obras = limites["max_obras"]
        super().save(*args, **kwargs)

    # ── helpers de verificação ──────────────────────────────

    def usuarios_ativos(self):
        """Conta usuários ativos vinculados à empresa (exclui superusers)."""
        return self.empresa.usuarios_empresa.filter(
            usuario__is_active=True,
            usuario__is_superuser=False,
        ).count()

    def obras_ativas(self):
        """Conta obras com status diferente de encerrado/cancelado."""
        return self.empresa.obras.exclude(status__in=["ENCERRADA", "CANCELADA"]).count()

    def pode_criar_usuario(self):
        """Retorna True se ainda há vaga no plano."""
        if self.max_usuarios is None:
            return True
        return self.usuarios_ativos() < self.max_usuarios

    def pode_criar_obra(self):
        """Retorna True se ainda há vaga no plano."""
        if self.max_obras is None:
            return True
        return self.obras_ativas() < self.max_obras

    def mensagem_limite_usuario(self):
        return (
            f"Você chegou ao limite de {self.max_usuarios} usuário(s) para seu plano atual "
            f"({self.get_nome_display()}). Entre em contato com a Construtask e faça o upgrade do seu plano!"
        )

    def mensagem_limite_obra(self):
        return (
            f"Você chegou ao limite de {self.max_obras} obra(s) para seu plano atual "
            f"({self.get_nome_display()}). Entre em contato com a Construtask e faça o upgrade do seu plano!"
        )


class Empresa(models.Model):
    """
    Modelo Tenant para suportar mÃºltiplas empresas (Multi-tenant).
    Cada empresa pode ter suas prÃ³prias obras e usuÃ¡rios.
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


class ParametroAlertaEmpresa(models.Model):
    class Meta:
        verbose_name = "Parametro de Alerta da Empresa"
        verbose_name_plural = "Parametros de Alertas da Empresa"

    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE, related_name="parametros_alerta")
    planejamento_suprimentos_janela_dias = models.PositiveIntegerField(default=60)
    contrato_sem_medicao_dias = models.PositiveIntegerField(default=15)
    medicao_sem_nota_dias = models.PositiveIntegerField(default=7)
    nota_sem_rateio_percentual_minimo = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.01"))
    risco_vencido_tolerancia_dias = models.PositiveIntegerField(default=0)
    nao_conformidade_sem_evolucao_dias = models.PositiveIntegerField(default=15)
    atividade_sem_avanco_tolerancia_dias = models.PositiveIntegerField(default=0)
    desvio_prazo_percentual_minimo_previsto = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10.00"))
    desvio_prazo_tolerancia_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10.00"))
    estouro_prazo_tolerancia_dias = models.PositiveIntegerField(default=0)
    desvio_custo_tolerancia_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10.00"))
    custo_sem_avanco_valor_minimo = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    compromisso_acima_orcado_tolerancia_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    acumulo_riscos_quantidade_minima = models.PositiveIntegerField(default=5)
    acumulo_riscos_quantidade_critica = models.PositiveIntegerField(default=8)
    alerta_sem_workflow_dias = models.PositiveIntegerField(default=7)
    alerta_prazo_solucao_dias = models.PositiveIntegerField(default=14)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Parametros de alerta - {self.empresa.nome}"

    @classmethod
    def obter_ou_criar(cls, empresa):
        if not empresa:
            return cls(
                planejamento_suprimentos_janela_dias=60,
                contrato_sem_medicao_dias=15,
                medicao_sem_nota_dias=7,
                nota_sem_rateio_percentual_minimo=Decimal("0.01"),
                risco_vencido_tolerancia_dias=0,
                nao_conformidade_sem_evolucao_dias=15,
                atividade_sem_avanco_tolerancia_dias=0,
                desvio_prazo_percentual_minimo_previsto=Decimal("10.00"),
                desvio_prazo_tolerancia_percentual=Decimal("10.00"),
                estouro_prazo_tolerancia_dias=0,
                desvio_custo_tolerancia_percentual=Decimal("10.00"),
                custo_sem_avanco_valor_minimo=Decimal("0.00"),
                compromisso_acima_orcado_tolerancia_percentual=Decimal("0.00"),
                acumulo_riscos_quantidade_minima=5,
                acumulo_riscos_quantidade_critica=8,
                alerta_sem_workflow_dias=7,
                alerta_prazo_solucao_dias=14,
            )
        parametros, _ = cls.objects.get_or_create(empresa=empresa)
        return parametros


class UserProfile(models.Model):
    """
    Perfil estendido do usuÃ¡rio com empresa (tenant) e papel.
    """
    class Meta:
        verbose_name = "Perfil de UsuÃ¡rio"
        verbose_name_plural = "Perfis de UsuÃ¡rio"

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
    Modelo para vincular usuÃ¡rios a empresas com permissÃµes especÃ­ficas.
    Cada usuÃ¡rio pertence a uma empresa e pode ser admin dessa empresa.
    """
    class Meta:
        verbose_name = "UsuÃ¡rio de Empresa"
        verbose_name_plural = "UsuÃ¡rios de Empresa"
        unique_together = ("usuario", "empresa")
        indexes = [
            models.Index(fields=["empresa", "is_admin_empresa"]),
            models.Index(fields=["empresa", "papel_aprovacao"]),
        ]

    PAPEL_APROVACAO_CHOICES = (
        ("GERENTE_OBRAS", "Gerente de Obras"),
        ("COORDENADOR_OBRAS", "Coordenador de Obras"),
        ("ENGENHEIRO_OBRAS", "Engenheiro de Obras"),
        ("TECNICO_OBRAS", "Tecnico de Obras"),
    )

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usuario_empresa")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="usuarios_empresa")
    is_admin_empresa = models.BooleanField(
        default=False,
        help_text="Se marcado, este usuÃ¡rio pode gerenciar usuÃ¡rios e liberar obras da empresa."
    )
    papel_aprovacao = models.CharField(
        max_length=30,
        choices=PAPEL_APROVACAO_CHOICES,
        default="TECNICO_OBRAS",
        help_text="Define a alcada de aprovacao operacional do usuario.",
    )
    obras_permitidas = models.ManyToManyField(
        "Obra",
        related_name="usuarios_permitidos",
        blank=True,
        help_text="Obras que este usuÃ¡rio pode acessar. Se vazio e nÃ£o for admin, nÃ£o terÃ¡ acesso a nenhuma obra."
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        admin_label = " (Admin)" if self.is_admin_empresa else ""
        return f"{self.usuario.username} - {self.empresa.nome}{admin_label}"

    def save(self, *args, **kwargs):
        # Admin da empresa automaticamente tem acesso a todas as obras da empresa
        if self.is_admin_empresa:
            # NÃ£o precisa salvar obras_permitidas para admin, pois ele vÃª todas
            pass
        super().save(*args, **kwargs)


class PermissaoModuloAcao(models.Model):
    class Meta:
        verbose_name = "Permissao por Modulo"
        verbose_name_plural = "Permissoes por Modulo"
        unique_together = ("usuario_empresa", "modulo", "acao")
        ordering = ["modulo", "acao"]

    usuario_empresa = models.ForeignKey(
        UsuarioEmpresa,
        on_delete=models.CASCADE,
        related_name="permissoes_modulo",
    )
    modulo = models.CharField(max_length=50)
    acao = models.CharField(max_length=50)
    permitido = models.BooleanField(default=True)
    concedido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="permissoes_modulo_concedidas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.usuario_empresa} | {self.modulo}:{self.acao}"


class AuditEvent(models.Model):
    """
    Modelo de auditoria para conformidade ISO 9.2.
    Registra todas as operaÃ§Ãµes Create/Update/Delete com diff.
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
    entidade_label = models.CharField(max_length=900)  # ex: 'Obra OBJ-001'
    objeto_id = models.PositiveIntegerField()
    antes = models.JSONField(null=True, blank=True)
    depois = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    request_id = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return f"{self.acao} - {self.entidade_label} por {self.usuario} em {self.timestamp:%d/%m/%Y %H:%M}"


class JobAssincrono(models.Model):
    class Meta:
        verbose_name = "Job Assincrono"
        verbose_name_plural = "Jobs Assincronos"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["status", "tipo", "criado_em"]),
            models.Index(fields=["empresa", "obra", "criado_em"]),
        ]

    TIPO_CHOICES = (
        ("SINCRONIZAR_ALERTAS_OBRA", "Sincronizar alertas da obra"),
        ("IMPORTAR_PLANO_CONTAS", "Importar plano de contas"),
        ("GERAR_RELATORIO_FINANCEIRO", "Gerar relatorio financeiro"),
    )
    STATUS_CHOICES = (
        ("PENDENTE", "Pendente"),
        ("EM_EXECUCAO", "Em execucao"),
        ("CONCLUIDO", "Concluido"),
        ("FALHOU", "Falhou"),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True, related_name="jobs")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, null=True, blank=True, related_name="jobs")
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs_assincronos",
    )
    tipo = models.CharField(max_length=40, choices=TIPO_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDENTE")
    descricao = models.CharField(max_length=900)
    parametros = models.JSONField(default=dict, blank=True)
    resultado = models.JSONField(default=dict, blank=True)
    erro = models.TextField(blank=True)
    tentativas = models.PositiveIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    concluido_em = models.DateTimeField(null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    arquivo_entrada = models.FileField(upload_to="jobs/input/", blank=True, null=True)
    arquivo_resultado = models.FileField(upload_to="jobs/output/", blank=True, null=True)

    def __str__(self):
        return f"{self.get_tipo_display()} [{self.get_status_display()}]"


class OperacaoBackupSaaS(models.Model):
    class Meta:
        verbose_name = "Operacao de Backup SaaS"
        verbose_name_plural = "Operacoes de Backup SaaS"
        ordering = ["-executado_em"]
        indexes = [
            models.Index(fields=["tipo", "status", "executado_em"]),
            models.Index(fields=["ambiente", "executado_em"]),
            models.Index(fields=["provedor", "executado_em"]),
        ]

    TIPO_CHOICES = (
        ("BACKUP", "Backup"),
        ("TESTE_RESTAURACAO", "Teste de recuperacao"),
    )
    STATUS_CHOICES = (
        ("SUCESSO", "Sucesso"),
        ("FALHOU", "Falhou"),
        ("PARCIAL", "Parcial"),
    )

    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="SUCESSO")
    ambiente = models.CharField(max_length=30, default="development")
    provedor = models.CharField(max_length=80, blank=True)
    identificador_artefato = models.CharField(max_length=255, blank=True)
    checksum = models.CharField(max_length=128, blank=True)
    tamanho_bytes = models.BigIntegerField(default=0)
    detalhes = models.JSONField(default=dict, blank=True)
    observacao = models.TextField(blank=True)
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operacoes_backup_saas",
    )
    backup_referencia = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="testes_restauracao",
    )
    executado_em = models.DateTimeField(default=timezone.now)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.tipo} - {self.status} - {self.executado_em:%d/%m/%Y %H:%M}"


class MetricaRequisicao(models.Model):
    class Meta:
        verbose_name = "Metrica de Requisicao"
        verbose_name_plural = "Metricas de Requisicao"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["metodo", "status_code", "criado_em"]),
            models.Index(fields=["empresa", "obra", "criado_em"]),
            models.Index(fields=["request_id"]),
            models.Index(fields=["path", "criado_em"]),
        ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True, related_name="metricas_requisicao")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, null=True, blank=True, related_name="metricas_requisicao")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="metricas_requisicao",
    )
    request_id = models.CharField(max_length=50, blank=True)
    metodo = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    status_code = models.PositiveIntegerField()
    duracao_ms = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.metodo} {self.path} [{self.status_code}]"


class RastroErroAplicacao(models.Model):
    class Meta:
        verbose_name = "Rastro de Erro da Aplicacao"
        verbose_name_plural = "Rastros de Erro da Aplicacao"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["classe_erro", "criado_em"]),
            models.Index(fields=["empresa", "obra", "criado_em"]),
            models.Index(fields=["request_id"]),
            models.Index(fields=["resolvido", "criado_em"]),
            models.Index(fields=["path", "criado_em"]),
        ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True, related_name="rastros_erro")
    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, null=True, blank=True, related_name="rastros_erro")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rastros_erro",
    )
    request_id = models.CharField(max_length=50, blank=True)
    metodo = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    status_code = models.PositiveIntegerField(default=500)
    classe_erro = models.CharField(max_length=120)
    mensagem = models.TextField()
    stacktrace = models.TextField(blank=True)
    resolvido = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    resolvido_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.classe_erro} em {self.path}"


class RegistroAcessoDadoPessoal(models.Model):
    """
    Registro minimo de acesso administrativo a dados pessoais para governanca LGPD.
    """
    class Meta:
        verbose_name = "Registro de Acesso a Dado Pessoal"
        verbose_name_plural = "Registros de Acesso a Dados Pessoais"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "criado_em"]),
            models.Index(fields=["usuario", "criado_em"]),
            models.Index(fields=["categoria_titular", "acao"]),
        ]

    CATEGORIA_TITULAR_CHOICES = (
        ("USUARIO", "Usuario"),
        ("COLABORADOR", "Colaborador"),
        ("FORNECEDOR", "Fornecedor"),
        ("CLIENTE", "Cliente"),
        ("TERCEIRO", "Terceiro"),
    )

    ACAO_CHOICES = (
        ("VIEW", "Visualizacao"),
        ("EXPORT", "Exportacao"),
        ("DOWNLOAD", "Download"),
        ("ADMIN_LIST", "Consulta Administrativa"),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True, related_name="logs_dados_pessoais")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acessos_dados_pessoais",
    )
    categoria_titular = models.CharField(max_length=20, choices=CATEGORIA_TITULAR_CHOICES)
    entidade = models.CharField(max_length=120)
    objeto_id = models.PositiveIntegerField(null=True, blank=True)
    identificador = models.CharField(max_length=255, blank=True)
    acao = models.CharField(max_length=20, choices=ACAO_CHOICES, default="VIEW")
    finalidade = models.CharField(max_length=255)
    detalhes = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_acao_display()} - {self.entidade} - {self.identificador or self.objeto_id or '-'}"


class RegistroTratamentoDadoPessoal(models.Model):
    class Meta:
        verbose_name = "Registro de Tratamento LGPD"
        verbose_name_plural = "Registros de Tratamento LGPD"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "criado_em"]),
            models.Index(fields=["categoria_titular", "acao"]),
            models.Index(fields=["entidade", "objeto_id"]),
        ]

    ACAO_CHOICES = (
        ("CONSULTA", "Consulta"),
        ("ANONIMIZACAO", "Anonimizacao"),
        ("EXCLUSAO_LOGICA", "Exclusao logica"),
        ("DESCARTE", "Descarte"),
        ("CONSENTIMENTO", "Consentimento"),
        ("REVOGACAO_CONSENTIMENTO", "Revogacao de consentimento"),
    )

    CATEGORIA_TITULAR_CHOICES = RegistroAcessoDadoPessoal.CATEGORIA_TITULAR_CHOICES

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True, related_name="tratamentos_lgpd")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tratamentos_lgpd_executados",
    )
    categoria_titular = models.CharField(max_length=20, choices=CATEGORIA_TITULAR_CHOICES)
    entidade = models.CharField(max_length=120)
    objeto_id = models.PositiveIntegerField(null=True, blank=True)
    identificador = models.CharField(max_length=255, blank=True)
    acao = models.CharField(max_length=30, choices=ACAO_CHOICES)
    finalidade = models.CharField(max_length=255)
    base_legal = models.CharField(max_length=255, blank=True)
    detalhes = models.TextField(blank=True)
    evidencia = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_acao_display()} - {self.entidade} - {self.identificador or self.objeto_id or '-'}"


class ConsentimentoLGPD(models.Model):
    class Meta:
        verbose_name = "Consentimento LGPD"
        verbose_name_plural = "Consentimentos LGPD"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "categoria_titular"]),
            models.Index(fields=["email_referencia", "revogado_em"]),
        ]

    CATEGORIA_TITULAR_CHOICES = RegistroAcessoDadoPessoal.CATEGORIA_TITULAR_CHOICES

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True, related_name="consentimentos_lgpd")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consentimentos_lgpd",
    )
    categoria_titular = models.CharField(max_length=20, choices=CATEGORIA_TITULAR_CHOICES)
    email_referencia = models.EmailField(blank=True)
    finalidade = models.CharField(max_length=255)
    texto_aceito = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    revogado_em = models.DateTimeField(null=True, blank=True)

    @property
    def ativo(self):
        return self.revogado_em is None


class AlertaOperacional(models.Model):
    """
    Registro persistente de alertas operacionais e preventivos da obra.
    """
    class Meta:
        verbose_name = "Alerta Operacional"
        verbose_name_plural = "Alertas Operacionais"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["obra", "codigo_regra", "status"]),
            models.Index(fields=["obra", "severidade", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["obra", "codigo_regra", "referencia"], name="uq_alerta_operacional_regra_ref")
        ]

    SEVERIDADE_CHOICES = (
        ("BAIXA", "Baixa"),
        ("MEDIA", "Media"),
        ("ALTA", "Alta"),
        ("CRITICA", "Critica"),
    )

    STATUS_CHOICES = (
        ("ABERTO", "Aberto"),
        ("EM_TRATAMENTO", "Em Tratamento"),
        ("JUSTIFICADO", "Justificado"),
        ("ENCERRADO", "Encerrado"),
    )

    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="alertas_operacionais")
    codigo_regra = models.CharField(max_length=40)
    titulo = models.CharField(max_length=180)
    descricao = models.TextField(max_length=900)
    severidade = models.CharField(max_length=20, choices=SEVERIDADE_CHOICES, default="MEDIA")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ABERTO")
    entidade_tipo = models.CharField(max_length=80, blank=True)
    entidade_id = models.PositiveIntegerField(null=True, blank=True)
    referencia = models.CharField(max_length=120)
    evidencias = models.JSONField(default=dict, blank=True)
    data_referencia = models.DateField(null=True, blank=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alertas_operacionais_responsavel",
    )
    observacao_status = models.TextField(blank=True)
    prazo_solucao_em = models.DateField(null=True, blank=True)
    ultima_acao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alertas_operacionais_movimentados",
    )
    ultima_acao_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    encerrado_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.codigo_regra} - {self.obra.codigo} - {self.referencia}"


class AlertaOperacionalHistorico(models.Model):
    class Meta:
        verbose_name = "Historico de Alerta Operacional"
        verbose_name_plural = "Historicos de Alertas Operacionais"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["alerta", "criado_em"]),
            models.Index(fields=["acao"]),
        ]

    ACAO_CHOICES = (
        ("CRIACAO", "Criacao"),
        ("TRATAMENTO", "Em Tratamento"),
        ("JUSTIFICATIVA", "Justificativa"),
        ("ENCERRAMENTO", "Encerramento"),
        ("REABERTURA", "Reabertura"),
    )

    alerta = models.ForeignKey(
        AlertaOperacional,
        on_delete=models.CASCADE,
        related_name="historico",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historicos_alertas_operacionais",
    )
    acao = models.CharField(max_length=20, choices=ACAO_CHOICES)
    status_anterior = models.CharField(max_length=20, blank=True)
    status_novo = models.CharField(max_length=20, blank=True)
    observacao = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.alerta.codigo_regra} - {self.get_acao_display()}"


class ExecucaoRegraOperacional(models.Model):
    class Meta:
        verbose_name = "Execucao de Regra Operacional"
        verbose_name_plural = "Execucoes de Regras Operacionais"
        ordering = ["-executado_em"]
        indexes = [
            models.Index(fields=["obra", "codigo_regra", "executado_em"]),
            models.Index(fields=["resultado", "executado_em"]),
            models.Index(fields=["alerta", "executado_em"]),
        ]

    RESULTADO_CHOICES = (
        ("CRIADO", "Criado"),
        ("ATUALIZADO", "Atualizado"),
        ("ENCERRADO", "Encerrado"),
        ("REATIVADO", "Reativado"),
    )

    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="execucoes_regras_operacionais")
    alerta = models.ForeignKey(
        AlertaOperacional,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="execucoes_automacao",
    )
    codigo_regra = models.CharField(max_length=40)
    referencia = models.CharField(max_length=120, blank=True)
    entidade_tipo = models.CharField(max_length=80, blank=True)
    entidade_id = models.PositiveIntegerField(null=True, blank=True)
    severidade = models.CharField(max_length=20, blank=True)
    status_alerta = models.CharField(max_length=20, blank=True)
    resultado = models.CharField(max_length=20, choices=RESULTADO_CHOICES)
    contexto = models.JSONField(default=dict, blank=True)
    executado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        referencia = f" - {self.referencia}" if self.referencia else ""
        return f"{self.codigo_regra} - {self.get_resultado_display()}{referencia}"


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
    descricao = models.CharField(max_length=900)
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
            raise ValidationError("Unidade, quantidade e valor unitÃ¡rio sÃ³ podem existir no nÃ­vel 6.")

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


class OrcamentoBaseline(models.Model):
    STATUS_CHOICES = (
        ("RASCUNHO", "Rascunho"),
        ("EM_APROVACAO", "Em Aprovacao"),
        ("APROVADA", "Aprovada"),
    )

    class Meta:
        verbose_name = "Baseline de Orcamento"
        verbose_name_plural = "Baselines de Orcamento"
        ordering = ["-criado_em"]

    obra = models.ForeignKey("Obra", on_delete=models.CASCADE, related_name="baselines_orcamento")
    descricao = models.CharField(max_length=900)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="RASCUNHO")
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="baselines_orcamento_criadas",
    )
    enviado_para_aprovacao_em = models.DateTimeField(null=True, blank=True)
    enviado_para_aprovacao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="baselines_orcamento_enviadas_para_aprovacao",
    )
    parecer_aprovacao = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="baselines_orcamento_aprovadas",
    )
    is_ativa = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.obra.codigo} - {self.descricao}"

    @property
    def valor_total(self):
        return self.itens.filter(level=0).aggregate(total=Sum("valor_total_consolidado"))["total"] or Decimal("0.00")


class OrcamentoBaselineItem(models.Model):
    class Meta:
        verbose_name = "Item da Baseline de Orcamento"
        verbose_name_plural = "Itens da Baseline de Orcamento"
        ordering = ["codigo"]

    baseline = models.ForeignKey(OrcamentoBaseline, on_delete=models.CASCADE, related_name="itens")
    codigo = models.CharField(max_length=50)
    descricao = models.CharField(max_length=900)
    parent_codigo = models.CharField(max_length=50, blank=True)
    level = models.PositiveIntegerField(default=0)
    unidade = models.CharField(max_length=20, blank=True)
    quantidade = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    valor_unitario = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    valor_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    valor_total_consolidado = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return f"{self.baseline} - {self.codigo}"


STATUS_OBRA_CHOICES = (
    ("PLANEJADA", "Planejada"),
    ("EM_ANDAMENTO", "Em Andamento"),
    ("PARALISADA", "Paralisada"),
    ("CONCLUIDA", "Concluida"),
)


STATUS_COMPROMISSO_CHOICES = (
    ("RASCUNHO", "Rascunho"),
    ("EM_APROVACAO", "Em Aprovacao"),
    ("APROVADO", "Aprovado"),
    ("EM_EXECUCAO", "Em Execucao"),
    ("ENCERRADO", "Encerrado"),
    ("CANCELADO", "Cancelado"),
)


STATUS_MEDICAO_CHOICES = (
    ("EM_ELABORACAO", "Em Elaboracao"),
    ("EM_APROVACAO", "Em Aprovacao"),
    ("CONFERIDA", "Conferida"),
    ("APROVADA", "Aprovada"),
    ("FATURADA", "Faturada"),
)


STATUS_ADITIVO_CHOICES = (
    ("RASCUNHO", "Rascunho"),
    ("EM_APROVACAO", "Em Aprovacao"),
    ("APROVADO", "Aprovado"),
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
        indexes = [
            models.Index(fields=["empresa", "status"]),
        ]

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
        verbose_name = "Compras e ContrataÃ§Ãµes"
        verbose_name_plural = "Compras e ContrataÃ§Ãµes"
        indexes = [
            models.Index(fields=["obra", "tipo", "status"]),
            models.Index(fields=["obra", "data_assinatura"]),
            models.Index(fields=["obra", "fornecedor"]),
            models.Index(fields=["obra", "cnpj"]),
        ]

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
    descricao = models.CharField(max_length=900)
    fornecedor = models.CharField(max_length=150)
    cnpj = models.CharField(max_length=18, validators=[cnpj_validator])
    responsavel = models.CharField(max_length=150)
    telefone = models.CharField(max_length=20)
    valor_contratado = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    data_assinatura = models.DateField()
    data_prevista_inicio = models.DateField(null=True, blank=True)
    data_prevista_fim = models.DateField(null=True, blank=True)
    enviado_para_aprovacao_em = models.DateTimeField(null=True, blank=True)
    enviado_para_aprovacao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compromissos_enviados_para_aprovacao",
    )
    parecer_aprovacao = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compromissos_aprovados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    @property
    def valor_executado(self):
        valor_anotado = getattr(self, "valor_executado_anotado", None)
        if valor_anotado is not None:
            return valor_anotado or Decimal("0.00")
        if self.tipo == "CONTRATO":
            total = self.medicoes.aggregate(total=Sum("valor_medido"))["total"]
        else:
            total = self.notas_fiscais_material.aggregate(total=Sum("valor_total"))["total"]
        return total or Decimal("0.00")

    @property
    def saldo(self):
        saldo_anotado = getattr(self, "saldo_anotado", None)
        if saldo_anotado is not None:
            return arredondar_moeda(saldo_anotado)
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
    status = models.CharField(max_length=20, choices=STATUS_ADITIVO_CHOICES, default="RASCUNHO")
    descricao = models.CharField(max_length=900, blank=True)
    motivo_mudanca = models.TextField(blank=True)
    impacto_resumido = models.CharField(max_length=255, blank=True)
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="aditivos_solicitados",
    )
    solicitado_em = models.DateTimeField(null=True, blank=True)
    # Para PRAZO: incremento em dias.
    delta_dias = models.IntegerField(null=True, blank=True)
    enviado_para_aprovacao_em = models.DateTimeField(null=True, blank=True)
    enviado_para_aprovacao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="aditivos_enviados_para_aprovacao",
    )
    parecer_aprovacao = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="aditivos_aprovados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.contrato_id and self.contrato.tipo != "CONTRATO":
            raise ValidationError("Aditivos contratuais só podem ser vinculados a contratos.")

        if self.tipo == "PRAZO":
            if self.delta_dias in (None, ""):
                raise ValidationError("Para aditivo de prazo, informe delta de dias.")
        else:
            # VALOR/ESCOPO nÃ£o usam delta_dias.
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
        verbose_name = "Item da Compra/ContrataÃ§Ã£o"
        verbose_name_plural = "Itens da Compra/ContrataÃ§Ã£o"

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
            raise ValidationError("O valor unitÃ¡rio do item nÃ£o pode ser negativo.")
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
        indexes = [
            models.Index(fields=["obra", "status", "data_medicao"]),
            models.Index(fields=["contrato", "data_medicao"]),
            models.Index(fields=["obra", "fornecedor"]),
        ]

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
    enviado_para_aprovacao_em = models.DateTimeField(null=True, blank=True)
    enviado_para_aprovacao_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="medicoes_enviadas_para_aprovacao",
    )
    parecer_aprovacao = models.TextField(blank=True)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="medicoes_aprovadas",
    )
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
        verbose_name = "Item da MediÃ§Ã£o"
        verbose_name_plural = "Itens da MediÃ§Ã£o"

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
            raise ValidationError("O valor unitÃ¡rio do item medido nÃ£o pode ser negativo.")
        self.quantidade = quantidade
        self.valor_unitario = valor_unitario
        if self.medicao_id and self.medicao.contrato_id:
            item_contrato = self.medicao.contrato.itens.filter(centro_custo=self.centro_custo).first()
            if item_contrato:
                if self.unidade and self.unidade != item_contrato.unidade:
                    raise ValidationError("A unidade da medição deve ser igual à  unidade definida no contrato.")
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
        indexes = [
            models.Index(fields=["obra", "status", "data_emissao"]),
            models.Index(fields=["medicao", "data_emissao"]),
            models.Index(fields=["pedido_compra", "data_emissao"]),
            models.Index(fields=["obra", "fornecedor"]),
        ]

    TIPO_CHOICES = (
        ("SERVICO", "Nota de ServiÃ§o"),
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
    data_vencimento = models.DateField(null=True, blank=True)
    fornecedor = models.CharField(max_length=150)
    cnpj = models.CharField(max_length=18, validators=[cnpj_validator])
    descricao = models.CharField(max_length=900)
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
            raise ValidationError("Centro de custo nÃ£o pertence Ã  origem desta nota fiscal.")

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
    descricao = models.CharField(max_length=900)
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
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="historicos_operacionais")
    acao = models.CharField(max_length=40)
    descricao = models.CharField(max_length=900)
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
    Documentos controlados com workflow de aprovaÃ§Ã£o e versionamento.
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
        ("EM_REVISAO", "Em RevisÃ£o"),
        ("APROVADO", "Aprovado"),
        ("OBSOLETO", "Obsoleto"),
    )

    STATUS_SEMANTICO = {
        "RASCUNHO": ("EM_ELABORACAO", "Em elaboraÃ§Ã£o", "secondary"),
        "EM_REVISAO": ("SUBMETIDO_VALIDACAO", "Submetido para validaÃ§Ã£o", "warning"),
        "APROVADO": ("VALIDADO", "Validado", "success"),
        "OBSOLETO": ("ENCERRADO", "Encerrado", "dark"),
    }

    TIPO_CHOICES = (
        ("PROCEDIMENTO", "Procedimento"),
        ("INSTRUCAO", "InstruÃ§Ã£o de Trabalho"),
        ("REGISTRO", "Registro de Qualidade"),
        ("MANUAL", "Manual"),
        ("POLITICA", "PolÃ­tica"),
        ("ROTEIRO", "Roteiro/Checklist"),
        ("FORMULARIO", "FormulÃ¡rio"),
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
        help_text="Vincular Ã  EAP nÃ­vel 5"
    )
    
    tipo_documento = models.CharField(max_length=20, choices=TIPO_CHOICES)
    codigo_documento = models.CharField(max_length=30, help_text="CÃ³digo Ãºnico do documento")
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
        """Gera cÃ³digo Ãºnico para o documento."""
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
        return self.status == "RASCUNHO"

    def pode_aprovar(self):
        return self.status == "EM_REVISAO" and self.revisoes.exists()

    def pode_tornar_obsoleto(self):
        return self.status in ["RASCUNHO", "EM_REVISAO", "APROVADO"]

    def get_versao_aprovada(self):
        return self.revisoes.filter(status="APROVADO").order_by("-versao").first()

    @property
    def ultima_revisao(self):
        return self.revisoes.order_by("-versao").first()

    @property
    def status_semantico(self):
        return self.STATUS_SEMANTICO.get(self.status, ("OUTRO", self.get_status_display(), "secondary"))[0]

    @property
    def status_semantico_display(self):
        return self.STATUS_SEMANTICO.get(self.status, ("OUTRO", self.get_status_display(), "secondary"))[1]

    @property
    def status_badge_class(self):
        return self.STATUS_SEMANTICO.get(self.status, ("OUTRO", self.get_status_display(), "secondary"))[2]


class DocumentoRevisao(models.Model):
    """
    Modelo para revisÃµes de documentos (imutÃ¡veis apÃ³s aprovaÃ§Ã£o).
    ISO 7.5 - Controle de versÃµes de documentos.
    """
    class Meta:
        verbose_name = "RevisÃ£o de Documento"
        verbose_name_plural = "RevisÃµes de Documentos"
        ordering = ["-versao"]
        unique_together = ("documento", "versao")
        indexes = [
            models.Index(fields=["documento", "status"]),
        ]

    STATUS_CHOICES = (
        ("ELABORACAO", "Em ElaboraÃ§Ã£o"),
        ("REVISAO", "Em RevisÃ£o"),
        ("APROVADO", "Aprovado"),
    )

    STATUS_SEMANTICO = {
        "ELABORACAO": ("EM_ELABORACAO", "Em elaboraÃ§Ã£o", "secondary"),
        "REVISAO": ("SUBMETIDO_VALIDACAO", "Submetido para validaÃ§Ã£o", "warning"),
        "APROVADO": ("VALIDADO", "Validado", "success"),
    }

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
    
    parecer = models.TextField(blank=True, help_text="Parecer sobre a revisÃ£o")
    arquivo_aprovado = models.FileField(
        upload_to="documentos/aprovados/%Y/%m",
        blank=True,
        help_text="CÃ³pia imutÃ¡vel do arquivo aprovado"
    )

    def __str__(self):
        return f"{self.documento.codigo_documento} - Rev. {self.versao:02d}"

    def save(self, *args, **kwargs):
        if not self.pk and not self.versao:
            self.versao = 1
        super().save(*args, **kwargs)

    def pode_aprovar(self):
        return self.status == "REVISAO"

    @property
    def status_semantico_display(self):
        return self.STATUS_SEMANTICO.get(self.status, ("OUTRO", self.get_status_display(), "secondary"))[1]

    @property
    def status_badge_class(self):
        return self.STATUS_SEMANTICO.get(self.status, ("OUTRO", self.get_status_display(), "secondary"))[2]


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
from .models_comunicacoes import (  # noqa: E402,F401
    HistoricoReuniaoComunicacao,
    ItemPautaReuniao,
    ParametroComunicacaoEmpresa,
    ReuniaoComunicacao,
)


