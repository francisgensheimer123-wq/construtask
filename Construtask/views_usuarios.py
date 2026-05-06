"""
Views para gerenciamento de usuarios e obras da empresa.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import ProtectedError
from django.utils.crypto import get_random_string
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View

from .models import Empresa, Obra, ParametroAlertaEmpresa, ParametroComunicacaoEmpresa, UsuarioEmpresa, PlanoEmpresa
from .permissions import (
    PERMISSOES_MODULO_ACAO,
    atualizar_permissoes_usuario_empresa,
    get_empresa_do_usuario,
    get_permissoes_modulo_usuario,
    is_admin_empresa,
    is_admin_empresa_vinculado,
    is_admin_sistema,
)
from .domain import gerar_numero_documento
from .application.saas import contexto_base_saas
from .services_alertas import catalogo_alertas_empresa
from .services_lgpd import registrar_acesso_dado_pessoal

from .services_tenant import TenantService, LimitePlanoExcedido

User = get_user_model()


def _gerar_senha_temporaria():
    return get_random_string(12, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789@$#")


def _excluir_usuario_ou_desativar(user):
    username = user.get_username()
    try:
        user.delete()
        return f"Usuário {username} excluído com sucesso."
    except ProtectedError:
        user.is_active = False
        user.save(update_fields=["is_active"])
        return f"Usuário {username} possui vínculos históricos e foi desativado."


def _empresa_redirect_kwargs(empresa):
    if not empresa:
        return {}
    return {"empresa": empresa.pk}


def _exigir_admin_sistema(request):
    if not is_admin_sistema(request.user):
        messages.error(request, "Apenas o superuser técnico Construtask pode acessar o gerenciamento do sistema.")
        return False
    return True


def _exigir_admin_empresa_vinculado(request):
    if not is_admin_empresa_vinculado(request.user):
        messages.error(request, "Apenas a administracao da empresa pode acessar esta página.")
        return False
    return True


def _formatar_parametro_alerta(valor, tipo):
    if tipo == "dias":
        return f"{valor} dia(s)"
    if tipo == "percentual":
        return f"{valor}%"
    if tipo == "moeda":
        return f"R$ {valor}"
    return str(valor)


def _catalogo_alertas_empresa(parametros):
    return [
        {
            "codigo": "PLAN-SUP-001",
            "titulo": "Atividade planejada sem solicitação de compra antecipada",
            "gatilho": "Atividade futura sem solicitação compatível vinculada",
            "valor_atual": _formatar_parametro_alerta(parametros.planejamento_suprimentos_janela_dias, "dias"),
            "impacto": "Antecipa risco de suprimento e atraso de mobilizacao.",
        },
        {
            "codigo": "CONT-MED-001",
            "titulo": "Contrato ativo sem medição registrada",
            "gatilho": "Contrato ativo sem medição após a tolerância definida",
            "valor_atual": _formatar_parametro_alerta(parametros.contrato_sem_medicao_dias, "dias"),
            "impacto": "Sinaliza perda de ritmo contratual e falta de lastro de execução.",
        },
        {
            "codigo": "MED-NF-001",
            "titulo": "Medição sem nota fiscal vinculada",
            "gatilho": "Medição aprovada sem nota fiscal dentro do prazo operacional",
            "valor_atual": _formatar_parametro_alerta(parametros.medicao_sem_nota_dias, "dias"),
            "impacto": "Ajuda a controlar faturamento, fluxo documental e competencia.",
        },
        {
            "codigo": "NF-RAT-001",
            "titulo": "Nota fiscal sem rateio completo",
            "gatilho": "Percentual pendente de rateio acima do mínimo definido",
            "valor_atual": _formatar_parametro_alerta(parametros.nota_sem_rateio_percentual_minimo, "percentual"),
            "impacto": "Evita custo financeiro sem apropriacao completa na obra.",
        },
        {
            "codigo": "RISK-DUE-001",
            "titulo": "Risco com prazo vencido sem tratamento concluído",
            "gatilho": "Prazo de tratamento vencido acima da tolerância",
            "valor_atual": _formatar_parametro_alerta(parametros.risco_vencido_tolerancia_dias, "dias"),
            "impacto": "Destaca riscos sem ação efetiva antes que virem problema real.",
        },
        {
            "codigo": "NC-EVO-001",
            "titulo": "Não conformidade sem evolução recente",
            "gatilho": "Não conformidade aberta sem nova movimentação",
            "valor_atual": _formatar_parametro_alerta(parametros.nao_conformidade_sem_evolucao_dias, "dias"),
            "impacto": "Reforca governanca de qualidade e encerramento com evidência.",
        },
        {
            "codigo": "PLAN-PROG-001",
            "titulo": "Atividade iniciada sem avanço físico registrado",
            "gatilho": "Atividade com inicio atingido sem progresso acima da tolerância",
            "valor_atual": _formatar_parametro_alerta(parametros.atividade_sem_avanco_tolerancia_dias, "dias"),
            "impacto": "Aponta atraso imediato no cronograma e ajuda a agir cedo.",
        },
        {
            "codigo": "PLAN-PROG-002",
            "titulo": "Avanço físico abaixo do tempo decorrido",
            "gatilho": "Desvio percentual de prazo após percentual mínimo previsto",
            "valor_atual": f"Mínimo {parametros.desvio_prazo_percentual_minimo_previsto}% / tolerância {parametros.desvio_prazo_tolerancia_percentual}%",
            "impacto": "Sinaliza tendencia de atraso crescente antes do estouro final.",
        },
        {
            "codigo": "PLAN-PROG-003",
            "titulo": "Projeção de término além do prazo da obra",
            "gatilho": "Data estimada de término excede a folga de prazo definida",
            "valor_atual": _formatar_parametro_alerta(parametros.estouro_prazo_tolerancia_dias, "dias"),
            "impacto": "Mostra risco de estouro global de prazo com base no ritmo atual.",
        },
        {
            "codigo": "COST-PROG-001",
            "titulo": "Custo realizado acima do previsto proporcional",
            "gatilho": "Custo acima da tolerância percentual definida",
            "valor_atual": _formatar_parametro_alerta(parametros.desvio_custo_tolerancia_percentual, "percentual"),
            "impacto": "Ajuda a detectar estouro de custo antes de contaminar a obra inteira.",
        },
        {
            "codigo": "COST-PROG-002",
            "titulo": "Lançamento de custo sem avanço físico correspondente",
            "gatilho": "Valor realizado sem avanço físico acima do mínimo definido",
            "valor_atual": _formatar_parametro_alerta(parametros.custo_sem_avanco_valor_minimo, "moeda"),
            "impacto": "Identifica custo sem lastro físico e possível retrabalho ou baixa produtividade.",
        },
        {
            "codigo": "COST-BUD-001",
            "titulo": "Compromisso acima do valor orçado",
            "gatilho": "Compromisso acima do orçado somando a tolerância percentual",
            "valor_atual": _formatar_parametro_alerta(parametros.compromisso_acima_orcado_tolerancia_percentual, "percentual"),
            "impacto": "Protege o orçamento contra contratação ou compra acima do previsto.",
        },
        {
            "codigo": "RISK-ACC-001",
            "titulo": "Acúmulo de riscos operacionais não tratados",
            "gatilho": "Quantidade de riscos ativos acima do limite e do nível crítico",
            "valor_atual": f"Mínimo {parametros.acumulo_riscos_quantidade_minima} / crítico {parametros.acumulo_riscos_quantidade_critica}",
            "impacto": "Evidência perda sistêmica de controle na obra.",
        },
        {
            "codigo": "ALERT-SLA-001",
            "titulo": "Alerta sem workflow recente",
            "gatilho": "Alerta sem nova movimentação acima da tolerância operacional",
            "valor_atual": _formatar_parametro_alerta(parametros.alerta_sem_workflow_dias, "dias"),
            "impacto": "O score so reduz quando o alerta envelhece sem tratamento efetivo.",
        },
        {
            "codigo": "ALERT-SLA-002",
            "titulo": "Prazo de solução do alerta estourado",
            "gatilho": "Alerta aberto além do prazo padrão de solução da empresa",
            "valor_atual": _formatar_parametro_alerta(parametros.alerta_prazo_solucao_dias, "dias"),
            "impacto": "Distingue alerta ativo de alerta negligenciado na leitura executiva da obra.",
        },
    ]


class EmpresaAdminForm(forms.Form):
    """Formulario para criar obra."""
    codigo = forms.CharField(max_length=30, required=False, disabled=True)
    nome = forms.CharField(max_length=150, required=True)
    cliente = forms.CharField(max_length=150, required=False)
    responsavel = forms.CharField(max_length=150, required=False)
    status = forms.ChoiceField(choices=Obra._meta.get_field("status").choices, initial="PLANEJADA")
    data_inicio = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    data_fim = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    descricao = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


class SistemaEmpresaCreateForm(forms.ModelForm):
    """Formulario de cadastro de empresa no painel técnico do sistema."""

    class Meta:
        model = Empresa
        fields = ["nome", "nome_fantasia", "cnpj", "telefone", "email", "endereco", "ativo"]
        widgets = {
            "endereco": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "nome": "Razão social",
            "nome_fantasia": "Nome fantasia",
            "cnpj": "CNPJ",
            "telefone": "Telefone",
            "email": "Email principal",
            "endereco": "Endereço",
            "ativo": "Empresa ativa",
        }

    def clean_nome(self):
        return (self.cleaned_data.get("nome") or "").strip()

    def clean_nome_fantasia(self):
        return (self.cleaned_data.get("nome_fantasia") or "").strip()

    def clean_cnpj(self):
        return (self.cleaned_data.get("cnpj") or "").strip()

    def clean_telefone(self):
        return (self.cleaned_data.get("telefone") or "").strip()

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip()

    def clean_endereco(self):
        return (self.cleaned_data.get("endereco") or "").strip()


class ParametroAlertaEmpresaForm(forms.ModelForm):
    class Meta:
        model = ParametroAlertaEmpresa
        fields = [
            "planejamento_suprimentos_janela_dias",
            "contrato_sem_medicao_dias",
            "medicao_sem_nota_dias",
            "nota_sem_rateio_percentual_minimo",
            "risco_vencido_tolerancia_dias",
            "nao_conformidade_sem_evolucao_dias",
            "atividade_sem_avanco_tolerancia_dias",
            "desvio_prazo_percentual_minimo_previsto",
            "desvio_prazo_tolerancia_percentual",
            "estouro_prazo_tolerancia_dias",
            "desvio_custo_tolerancia_percentual",
            "custo_sem_avanco_valor_minimo",
            "compromisso_acima_orcado_tolerancia_percentual",
            "acumulo_riscos_quantidade_minima",
            "acumulo_riscos_quantidade_critica",
            "alerta_sem_workflow_dias",
            "alerta_prazo_solucao_dias",
        ]
        widgets = {
            "planejamento_suprimentos_janela_dias": forms.NumberInput(attrs={"min": 1}),
            "contrato_sem_medicao_dias": forms.NumberInput(attrs={"min": 0}),
            "medicao_sem_nota_dias": forms.NumberInput(attrs={"min": 0}),
            "nota_sem_rateio_percentual_minimo": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "risco_vencido_tolerancia_dias": forms.NumberInput(attrs={"min": 0}),
            "nao_conformidade_sem_evolucao_dias": forms.NumberInput(attrs={"min": 0}),
            "atividade_sem_avanco_tolerancia_dias": forms.NumberInput(attrs={"min": 0}),
            "desvio_prazo_percentual_minimo_previsto": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "desvio_prazo_tolerancia_percentual": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "estouro_prazo_tolerancia_dias": forms.NumberInput(attrs={"min": 0}),
            "desvio_custo_tolerancia_percentual": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "custo_sem_avanco_valor_minimo": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "compromisso_acima_orcado_tolerancia_percentual": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "acumulo_riscos_quantidade_minima": forms.NumberInput(attrs={"min": 1}),
            "acumulo_riscos_quantidade_critica": forms.NumberInput(attrs={"min": 1}),
            "alerta_sem_workflow_dias": forms.NumberInput(attrs={"min": 1}),
            "alerta_prazo_solucao_dias": forms.NumberInput(attrs={"min": 1}),
        }
        labels = {
            "planejamento_suprimentos_janela_dias": "Dias futuros para alerta de atividade sem solicitação",
            "contrato_sem_medicao_dias": "Dias sem medição para alertar contrato ativo",
            "medicao_sem_nota_dias": "Dias sem nota fiscal para alertar medição",
            "nota_sem_rateio_percentual_minimo": "Percentual mínimo pendente para alertar nota sem rateio",
            "risco_vencido_tolerancia_dias": "Dias de tolerância para risco vencido",
            "nao_conformidade_sem_evolucao_dias": "Dias sem evolução para alertar não conformidade",
            "atividade_sem_avanco_tolerancia_dias": "Dias de tolerância após início da atividade sem avançar",
            "desvio_prazo_percentual_minimo_previsto": "Percentual previsto mínimo para avaliar desvio de prazo",
            "desvio_prazo_tolerancia_percentual": "Diferença percentual mínima para alertar desvio de prazo",
            "estouro_prazo_tolerancia_dias": "Dias de tolerância para estourar prazo da obra",
            "desvio_custo_tolerancia_percentual": "Percentual acima do custo proporcional para alertar desvio",
            "custo_sem_avanco_valor_minimo": "Valor mínimo sem avanço físico para alertar custo sem lastro",
            "compromisso_acima_orcado_tolerancia_percentual": "Percentual de tolerância acima do orçado para compromissos",
            "acumulo_riscos_quantidade_minima": "Quantidade minima de riscos ativos para gerar alerta",
            "acumulo_riscos_quantidade_critica": "Quantidade de riscos ativos para tornar o alerta crítico",
            "alerta_sem_workflow_dias": "Dias sem workflow para o score penalizar um alerta",
            "alerta_prazo_solucao_dias": "Prazo padrão de solução do alerta para o score penalizar",
        }

    def clean(self):
        cleaned = super().clean()
        quantidade_minima = cleaned.get("acumulo_riscos_quantidade_minima")
        quantidade_critica = cleaned.get("acumulo_riscos_quantidade_critica")
        if quantidade_minima and quantidade_critica and quantidade_critica < quantidade_minima:
            self.add_error("acumulo_riscos_quantidade_critica", "O nível crítico deve ser maior ou igual ao nível mínimo de disparo.")
        return cleaned


class ParametroComunicacaoEmpresaForm(forms.ModelForm):
    class Meta:
        model = ParametroComunicacaoEmpresa
        fields = [
            "frequencia_curto_prazo_dias",
            "frequencia_medio_prazo_dias",
            "frequencia_longo_prazo_dias",
        ]
        widgets = {
            "frequencia_curto_prazo_dias": forms.NumberInput(attrs={"min": 1}),
            "frequencia_medio_prazo_dias": forms.NumberInput(attrs={"min": 1}),
            "frequencia_longo_prazo_dias": forms.NumberInput(attrs={"min": 1}),
        }
        labels = {
            "frequencia_curto_prazo_dias": "Curto prazo (dias)",
            "frequencia_medio_prazo_dias": "Médio prazo (dias)",
            "frequencia_longo_prazo_dias": "Longo prazo (dias)",
        }
        help_texts = {
            "frequencia_curto_prazo_dias": "Frequência padrão das reunioes de curto prazo da empresa.",
            "frequencia_medio_prazo_dias": "Frequência padrão das reuniões de médio prazo da empresa.",
            "frequencia_longo_prazo_dias": "Frequência padrão das reunioes de longo prazo da empresa.",
        }


class UsuarioEmpresaListView(View):
    """
    Gerencia usuarios e obras da empresa do admin logado.
    Uma unica tela para ambas as funcoes.
    """
    template_name = "app/empresa_admin.html"
    
    def get(self, request):
        if not _exigir_admin_empresa_vinculado(request):
            return redirect("home")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa:
            messages.error(request, "Você não está vinculado a nenhuma empresa.")
            return redirect("home")
        
        # Usuarios da empresa
        usuarios_empresa = (
            UsuarioEmpresa.objects
            .filter(empresa=empresa)
            .select_related("usuario")
            .prefetch_related("obras_permitidas")
            .order_by("usuario__username")
        )
        for usuario_empresa in usuarios_empresa:
            usuario_empresa.permissoes_checks = {
                f"{modulo}:{acao}"
                for modulo, acoes in get_permissoes_modulo_usuario(usuario_empresa.usuario).items()
                for acao in acoes
            }
        
        # Obras da empresa
        obras_da_empresa = (
            Obra.objects
            .filter(empresa=empresa)
            .order_by("codigo")
        )
        
        # Form para nova obra
        obra_form = EmpresaAdminForm()
        parametros_alerta = ParametroAlertaEmpresa.obter_ou_criar(empresa)
        parametros_alerta_form = ParametroAlertaEmpresaForm(instance=parametros_alerta)
        parametros_comunicacao = ParametroComunicacaoEmpresa.obter_ou_criar(empresa)
        parametros_comunicacao_form = ParametroComunicacaoEmpresaForm(instance=parametros_comunicacao)

        registrar_acesso_dado_pessoal(
            request,
            categoria_titular="USUARIO",
            entidade="UsuarioEmpresa",
            identificador=f"Empresa {empresa.nome}",
            acao="ADMIN_LIST",
            finalidade="Gestão administrativa de usuários e permissões por empresa",
            detalhes="Consulta administrativa da área de usuários da empresa.",
        )
        
        status_plano = TenantService.status_plano(empresa)

        contexto = {
            "empresa": empresa,
            "status_plano": status_plano,
            "usuarios_empresa": usuarios_empresa,
            "obras_da_empresa": obras_da_empresa,
            "obra_form": obra_form,
            "parametros_alerta_form": parametros_alerta_form,
            "parametros_comunicacao_form": parametros_comunicacao_form,
            "catalogo_alertas": catalogo_alertas_empresa(empresa),
            "catalogo_permissoes_modulo": PERMISSOES_MODULO_ACAO,
        }
        return render(request, self.template_name, contexto)
    
    def post(self, request):
        """Processa ações de usuarios ou obras."""
        if not _exigir_admin_empresa_vinculado(request):
            return redirect("home")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa:
            return redirect("home")
        
        if not empresa:
            messages.error(request, "Empresa não encontrada.")
            return redirect("home")
        
        # Verificar qual acao
        acao = request.POST.get("acao")
        
        if acao == "atualizar_obras_usuario":
            return self._atualizar_obras_usuario(request, empresa)
        elif acao == "atualizar_permissoes_usuario":
            return self._atualizar_permissoes_usuario(request, empresa)
        elif acao == "resetar_senha_usuario":
            return self._resetar_senha_usuario(request, empresa)
        elif acao == "excluir_usuario":
            return self._excluir_usuario(request, empresa)
        elif acao == "criar_usuario":
            return self._criar_usuario(request, empresa)
        elif acao == "criar_obra":
            return self._criar_obra(request, empresa)
        elif acao == "excluir_obra":
            return self._excluir_obra(request, empresa)
        elif acao == "salvar_parametros_alerta":
            return self._salvar_parametros_alerta(request, empresa)
        elif acao == "salvar_parametros_comunicacao":
            return self._salvar_parametros_comunicacao(request, empresa)
        
        return redirect("empresa_admin")

    def _salvar_parametros_alerta(self, request, empresa):
        parametros = ParametroAlertaEmpresa.obter_ou_criar(empresa)
        form = ParametroAlertaEmpresaForm(request.POST, instance=parametros)
        if form.is_valid():
            form.save()
            messages.success(request, "Gatilhos operacionais da empresa atualizados com sucesso.")
        else:
            for campo, erros in form.errors.items():
                for erro in erros:
                    messages.error(request, f"{campo}: {erro}")
        return redirect("empresa_admin")

    def _salvar_parametros_comunicacao(self, request, empresa):
        parametros = ParametroComunicacaoEmpresa.obter_ou_criar(empresa)
        form = ParametroComunicacaoEmpresaForm(request.POST, instance=parametros)
        if form.is_valid():
            form.save()
            messages.success(request, "Frequencias das reunioes da empresa atualizadas com sucesso.")
        else:
            for campo, erros in form.errors.items():
                for erro in erros:
                    messages.error(request, f"{campo}: {erro}")
        return redirect("empresa_admin")
    
    def _atualizar_obras_usuario(self, request, empresa):
        """Atualizar obras permitidas de um usuário."""
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        obras_selecionadas = request.POST.getlist("obras")
        papel_aprovacao = request.POST.get("papel_aprovacao") or "TECNICO_OBRAS"
        
        try:
            usuario_empresa = UsuarioEmpresa.objects.get(pk=usuario_empresa_id)
            
            admin_empresa = get_empresa_do_usuario(request.user)
            if admin_empresa != usuario_empresa.empresa:
                messages.error(request, "Você so pode gerenciar usuarios da sua empresa.")
                return redirect("empresa_admin")
            
            if not usuario_empresa.is_admin_empresa:
                obras = Obra.objects.filter(pk__in=obras_selecionadas, empresa=usuario_empresa.empresa)
                usuario_empresa.obras_permitidas.set(obras)
                usuario_empresa.papel_aprovacao = papel_aprovacao
                usuario_empresa.save(update_fields=["papel_aprovacao", "atualizado_em"])
                messages.success(request, f"Obras atualizadas para {usuario_empresa.usuario.username}.")
            else:
                messages.info(request, "Admin da empresa ja tem acesso a todas as obras.")
            
        except UsuarioEmpresa.DoesNotExist:
            messages.error(request, "Usuário empresa não encontrado.")
        
        return redirect("empresa_admin")

    def _atualizar_permissoes_usuario(self, request, empresa):
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        try:
            usuario_empresa = UsuarioEmpresa.objects.get(pk=usuario_empresa_id, empresa=empresa)
        except UsuarioEmpresa.DoesNotExist:
            messages.error(request, "Usuário da empresa não encontrado.")
            return redirect("empresa_admin")

        permissoes = {}
        for modulo, acoes in PERMISSOES_MODULO_ACAO.items():
            modulo_permissoes = {}
            for acao in acoes:
                campo = f"perm_{modulo}_{acao}"
                modulo_permissoes[acao] = request.POST.get(campo) == "on"
            permissoes[modulo] = modulo_permissoes
        atualizar_permissoes_usuario_empresa(usuario_empresa, permissoes, concedido_por=request.user)
        messages.success(request, f"Permissões de módulo atualizadas para {usuario_empresa.usuario.username}.")
        return redirect("empresa_admin")
    
    def _resetar_senha_usuario(self, request, empresa):
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        try:
            usuario_empresa = UsuarioEmpresa.objects.select_related("usuario").get(pk=usuario_empresa_id, empresa=empresa)
        except UsuarioEmpresa.DoesNotExist:
            messages.error(request, "Usuário da empresa não encontrado.")
            return redirect("empresa_admin")

        nova_senha = _gerar_senha_temporaria()
        usuario_empresa.usuario.set_password(nova_senha)
        usuario_empresa.usuario.save(update_fields=["password"])
        messages.success(
            request,
            f"Senha temporária de {usuario_empresa.usuario.username}: {nova_senha}. Informe ao usuário e peça a troca no próximo acesso.",
        )
        return redirect("empresa_admin")

    def _excluir_usuario(self, request, empresa):
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        try:
            usuario_empresa = UsuarioEmpresa.objects.select_related("usuario").get(pk=usuario_empresa_id, empresa=empresa)
        except UsuarioEmpresa.DoesNotExist:
            messages.error(request, "Usuário da empresa não encontrado.")
            return redirect("empresa_admin")

        if usuario_empresa.usuario_id == request.user.id:
            messages.error(request, "Você não pode excluir o próprio usuário logado.")
            return redirect("empresa_admin")

        with transaction.atomic():
            usuario = usuario_empresa.usuario
            usuario_empresa.delete()
            mensagem = _excluir_usuario_ou_desativar(usuario)
        messages.success(request, mensagem)
        return redirect("empresa_admin")

    def _criar_usuario(self, request, empresa):
        """Criar novo usuário."""
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        obras_selecionadas = request.POST.getlist("obras_usuario")
        papel_aprovacao = request.POST.get("papel_aprovacao") or "TECNICO_OBRAS"
        
        if not username or not password:
            messages.error(request, "Username e senha são obrigatórios.")
            return redirect("empresa_admin")
        
        if User.objects.filter(username=username).exists():
            messages.error(request, "Já existe um usuário com este username.")
            return redirect("empresa_admin")
            
        try:
            TenantService.verificar_limite_usuario(empresa)
        except LimitePlanoExcedido as e:
            messages.error(request, str(e))
            return redirect("empresa_admin")            
        
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=False,
            is_active=True,
        )
        
        usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=user,
            empresa=empresa,
            is_admin_empresa=False,
            papel_aprovacao=papel_aprovacao,
        )
        
        if obras_selecionadas:
            obras = Obra.objects.filter(pk__in=obras_selecionadas, empresa=empresa)
            usuario_empresa.obras_permitidas.set(obras)
        
        messages.success(request, f"Usuário {username} criado com sucesso!")
        return redirect("empresa_admin")
    
    def _criar_obra(self, request, empresa):
        """Criar nova obra para a empresa."""
        form = EmpresaAdminForm(request.POST)
        
        if form.is_valid():
            try:
                TenantService.verificar_limite_obra(empresa)
            except LimitePlanoExcedido as e:
                messages.error(request, str(e))
                return redirect("empresa_admin")
                        
            # Gerar codigo unico (loop para garantir unicidade)
            codigo = None
            for tentativa in range(100):
                temp_codigo = gerar_numero_documento(Obra, "OBRA-", "codigo")
                if not Obra.objects.filter(codigo=temp_codigo).exists():
                    codigo = temp_codigo
                    break
            
            if not codigo:
                messages.error(request, "Não foi possível gerar um código único para a obra.")
                return redirect("empresa_admin")
            
            try:
                obra = Obra.objects.create(
                    empresa=empresa,
                    codigo=codigo,
                    nome=form.cleaned_data["nome"],
                    cliente=form.cleaned_data.get("cliente", ""),
                    responsavel=form.cleaned_data.get("responsavel", ""),
                    status=form.cleaned_data["status"],
                    data_inicio=form.cleaned_data.get("data_inicio"),
                    data_fim=form.cleaned_data.get("data_fim"),
                    descricao=form.cleaned_data.get("descricao", ""),
                )
                
                messages.success(request, f"Obra {obra.codigo} - {obra.nome} criada com sucesso!")
            except Exception as e:
                messages.error(request, f"Erro ao criar obra: {str(e)}")
        else:
            messages.error(request, "Erro ao criar obra. Verifique os dados.")
        
        return redirect("empresa_admin")

    def _excluir_obra(self, request, empresa):
        obra_id = request.POST.get("obra_id")
        try:
            obra = Obra.objects.get(pk=obra_id, empresa=empresa)
        except Obra.DoesNotExist:
            messages.error(request, "Obra não encontrada.")
            return redirect("empresa_admin")

        identificador = f"{obra.codigo} - {obra.nome}"
        try:
            obra.delete()
            messages.success(request, f"Obra {identificador} excluída com sucesso.")
        except ProtectedError:
            messages.error(request, f"Obra {identificador} não pode ser excluída porque possui vínculos operacionais.")
        return redirect("empresa_admin")


class UsuarioEmpresaCreateView(View):
    """
    Criar um novo usuário e vincula-lo a empresa do admin.
    """
    template_name = "app/usuario_empresa_form.html"
    
    def get(self, request):
        if not is_admin_empresa(request.user) and not request.user.is_superuser:
            messages.error(request, "Você não tem permissão para acessar esta página.")
            return redirect("home")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa and not request.user.is_superuser:
            messages.error(request, "Você não está vinculado a nenhuma empresa.")
            return redirect("home")
        
        if request.user.is_superuser:
            empresa_id = request.GET.get("empresa")
            if empresa_id:
                empresa = Empresa.objects.filter(pk=empresa_id).first()
        
        return render(request, self.template_name, {
            "empresa": empresa,
        })
    
    def post(self, request):
        if not is_admin_empresa(request.user) and not request.user.is_superuser:
            messages.error(request, "Você não tem permissão.")
            return redirect("home")
        
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        obras_selecionadas = request.POST.getlist("obras")
        papel_aprovacao = request.POST.get("papel_aprovacao") or "TECNICO_OBRAS"
        empresa_id = request.POST.get("empresa_id")
        
        if not username or not password:
            messages.error(request, "Username e senha são obrigatórios.")
            return redirect("usuario_empresa_create")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa and not request.user.is_superuser:
            return redirect("home")
        
        if request.user.is_superuser and empresa_id:
            empresa = Empresa.objects.filter(pk=empresa_id).first()
        
        if not empresa:
            messages.error(request, "Empresa não encontrada.")
            return redirect("home")
        
        # Verificar se username ja existe
        if User.objects.filter(username=username).exists():
            messages.error(request, "Já existe um usuário com este username.")
            return redirect("usuario_empresa_create")
        
        # Criar usuario
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=False,
            is_active=True,
        )
        
        # Criar vinculo com empresa
        usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=user,
            empresa=empresa,
            is_admin_empresa=False,
            papel_aprovacao=papel_aprovacao,
        )
        
        # Liberar obras
        if obras_selecionadas:
            obras = Obra.objects.filter(pk__in=obras_selecionadas, empresa=empresa)
            usuario_empresa.obras_permitidas.set(obras)
        
        messages.success(request, f"Usuário {username} criado com sucesso!")
        return redirect("usuario_empresa_list")


class SistemaAdminView(View):
    template_name = "app/sistema_admin.html"

    def get(self, request):
        if not _exigir_admin_sistema(request):
            return redirect("home")

        empresa = self._resolver_empresa(request)
        return render(request, self.template_name, self._build_context(empresa=empresa))

    def post(self, request):
        if not _exigir_admin_sistema(request):
            return redirect("home")

        acao = request.POST.get("acao")
        if acao == "criar_empresa":
            return self._criar_empresa(request)

        empresa = self._resolver_empresa(request, source="post")
        if not empresa:
            messages.error(request, "Selecione uma empresa válida para gerenciar o sistema.")
            return redirect("sistema_admin")

        if acao == "criar_admin_empresa":
            return self._criar_admin_empresa(request, empresa)
        if acao == "resetar_senha_usuario":
            return self._resetar_senha_usuario(request, empresa)
        if acao == "excluir_usuario":
            return self._excluir_usuario(request, empresa)
        if acao == "excluir_empresa":
            return self._excluir_empresa(request, empresa)
        if acao == "alternar_bloqueio_empresa":
            return self._alternar_bloqueio_empresa(request, empresa)
        
        if acao == "definir_plano":
            plano_nome = request.POST.get("plano_nome", "STARTER")
            max_usuarios = request.POST.get("max_usuarios") or None
            max_obras = request.POST.get("max_obras") or None

            plano, _ = PlanoEmpresa.objects.get_or_create(empresa=empresa)
            plano.nome = plano_nome
            plano.max_usuarios = int(max_usuarios) if max_usuarios else None
            plano.max_obras = int(max_obras) if max_obras else None
            plano.save()

            messages.success(request, f"Plano {plano.get_nome_display()} salvo com sucesso.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

        messages.error(request, "Ação de sistema não reconhecida.")
        return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

    def _build_context(self, *, empresa=None, empresa_create_form=None):
        plano_empresa = getattr(empresa, "plano", None) if empresa else None
        status_plano = TenantService.status_plano(empresa) if empresa else None

        contexto = {
            "empresa": empresa,
            "plano_empresa": plano_empresa,
            "status_plano": status_plano,
            "empresas": Empresa.objects.all().order_by("nome"),
            "admins_empresa": self._admins_empresa_queryset(empresa),
            "usuarios_empresa": self._usuarios_empresa_queryset(empresa),
            "empresa_create_form": empresa_create_form or SistemaEmpresaCreateForm(initial={"ativo": True}),
        }
        contexto.update(contexto_base_saas())
        return contexto

    def _resolver_empresa(self, request, source="get"):
        empresa_id = request.GET.get("empresa") if source == "get" else request.POST.get("empresa_id")
        if empresa_id:
            empresa = Empresa.objects.filter(pk=empresa_id).first()
            if empresa:
                return empresa
        return Empresa.objects.filter(ativo=True).order_by("nome").first()

    def _admins_empresa_queryset(self, empresa):
        if not empresa:
            return UsuarioEmpresa.objects.none()
        return (
            UsuarioEmpresa.objects.filter(empresa=empresa, is_admin_empresa=True)
            .select_related("usuario")
            .order_by("usuario__username")
        )

    def _usuarios_empresa_queryset(self, empresa):
        if not empresa:
            return UsuarioEmpresa.objects.none()
        return (
            UsuarioEmpresa.objects.filter(empresa=empresa)
            .select_related("usuario")
            .order_by("-is_admin_empresa", "usuario__username")
        )

    def _criar_empresa(self, request):
        form = SistemaEmpresaCreateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Revise os dados da empresa e tente novamente.")
            return render(request, self.template_name, self._build_context(empresa_create_form=form))

        empresa = form.save()
        PlanoEmpresa.objects.get_or_create(empresa=empresa)
        messages.success(request, f"Empresa {empresa.nome} criada com sucesso.")
        return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

    def _resetar_senha_usuario(self, request, empresa):
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        try:
            usuario_empresa = UsuarioEmpresa.objects.select_related("usuario").get(pk=usuario_empresa_id, empresa=empresa)
        except UsuarioEmpresa.DoesNotExist:
            messages.error(request, "Usuário da empresa não encontrado.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

        nova_senha = _gerar_senha_temporaria()
        usuario_empresa.usuario.set_password(nova_senha)
        usuario_empresa.usuario.save(update_fields=["password"])
        messages.success(
            request,
            f"Senha temporária de {usuario_empresa.usuario.username}: {nova_senha}. Informe ao usuário e peça a troca no próximo acesso.",
        )
        return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

    def _excluir_usuario(self, request, empresa):
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        try:
            usuario_empresa = UsuarioEmpresa.objects.select_related("usuario").get(pk=usuario_empresa_id, empresa=empresa)
        except UsuarioEmpresa.DoesNotExist:
            messages.error(request, "Usuário da empresa não encontrado.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

        if usuario_empresa.usuario_id == request.user.id:
            messages.error(request, "Você não pode excluir o próprio usuário logado.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

        with transaction.atomic():
            usuario = usuario_empresa.usuario
            usuario_empresa.delete()
            mensagem = _excluir_usuario_ou_desativar(usuario)
        messages.success(request, mensagem)
        return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

    def _alternar_bloqueio_empresa(self, request, empresa):
        empresa.ativo = not empresa.ativo
        empresa.save(update_fields=["ativo", "atualizado_em"])
        if not empresa.ativo:
            User.objects.filter(usuario_empresa__empresa=empresa, is_superuser=False).update(is_active=False)
            messages.success(request, f"Empresa {empresa.nome} bloqueada e usuários desativados no sistema.")
        else:
            messages.success(request, f"Empresa {empresa.nome} desbloqueada. Reative os usuários que devem voltar a acessar.")
        return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

    def _excluir_empresa(self, request, empresa):
        nome = empresa.nome
        try:
            empresa.delete()
            messages.success(request, f"Empresa {nome} excluída com sucesso.")
            return redirect("sistema_admin")
        except ProtectedError:
            messages.error(request, f"Empresa {nome} não pode ser excluída porque possui vínculos operacionais. Use o bloqueio para impedir acesso.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

    def _criar_admin_empresa(self, request, empresa):
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = (request.POST.get("password") or "").strip()

        if not username or not password:
            messages.error(request, "Username e senha são obrigatórios para criar o admin da empresa.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Já existe um usuário com este username.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

        if UsuarioEmpresa.objects.filter(empresa=empresa, is_admin_empresa=True).exists():
            messages.info(request, "A empresa ja possui um admin cadastrado. Crie outro apenas se isso for realmente necessário.")

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=False,
            is_active=True,
        )
        UsuarioEmpresa.objects.create(
            usuario=user,
            empresa=empresa,
            is_admin_empresa=True,
            papel_aprovacao="GERENTE_OBRAS",
        )
        messages.success(request, f"Admin da empresa {empresa.nome} criado com sucesso.")
        return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")
