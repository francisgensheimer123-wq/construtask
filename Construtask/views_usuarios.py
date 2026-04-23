"""
Views para gerenciamento de usuarios e obras da empresa.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
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


def _empresa_redirect_kwargs(empresa):
    if not empresa:
        return {}
    return {"empresa": empresa.pk}


def _exigir_admin_sistema(request):
    if not is_admin_sistema(request.user):
        messages.error(request, "Apenas o superuser tecnico Construtask pode acessar o gerenciamento do sistema.")
        return False
    return True


def _exigir_admin_empresa_vinculado(request):
    if not is_admin_empresa_vinculado(request.user):
        messages.error(request, "Apenas a administracao da empresa pode acessar esta pagina.")
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
            "titulo": "Atividade planejada sem solicitacao de compra antecipada",
            "gatilho": "Atividade futura sem solicitacao compativel vinculada",
            "valor_atual": _formatar_parametro_alerta(parametros.planejamento_suprimentos_janela_dias, "dias"),
            "impacto": "Antecipa risco de suprimento e atraso de mobilizacao.",
        },
        {
            "codigo": "CONT-MED-001",
            "titulo": "Contrato ativo sem medicao registrada",
            "gatilho": "Contrato ativo sem medicao apos a tolerancia definida",
            "valor_atual": _formatar_parametro_alerta(parametros.contrato_sem_medicao_dias, "dias"),
            "impacto": "Sinaliza perda de ritmo contratual e falta de lastro de execucao.",
        },
        {
            "codigo": "MED-NF-001",
            "titulo": "Medicao sem nota fiscal vinculada",
            "gatilho": "Medicao aprovada sem nota fiscal dentro do prazo operacional",
            "valor_atual": _formatar_parametro_alerta(parametros.medicao_sem_nota_dias, "dias"),
            "impacto": "Ajuda a controlar faturamento, fluxo documental e competencia.",
        },
        {
            "codigo": "NF-RAT-001",
            "titulo": "Nota fiscal sem rateio completo",
            "gatilho": "Percentual pendente de rateio acima do minimo definido",
            "valor_atual": _formatar_parametro_alerta(parametros.nota_sem_rateio_percentual_minimo, "percentual"),
            "impacto": "Evita custo financeiro sem apropriacao completa na obra.",
        },
        {
            "codigo": "RISK-DUE-001",
            "titulo": "Risco com prazo vencido sem tratamento concluido",
            "gatilho": "Prazo de tratamento vencido acima da tolerancia",
            "valor_atual": _formatar_parametro_alerta(parametros.risco_vencido_tolerancia_dias, "dias"),
            "impacto": "Destaca riscos sem acao efetiva antes que virem problema real.",
        },
        {
            "codigo": "NC-EVO-001",
            "titulo": "Nao conformidade sem evolucao recente",
            "gatilho": "Nao conformidade aberta sem nova movimentacao",
            "valor_atual": _formatar_parametro_alerta(parametros.nao_conformidade_sem_evolucao_dias, "dias"),
            "impacto": "Reforca governanca de qualidade e encerramento com evidencia.",
        },
        {
            "codigo": "PLAN-PROG-001",
            "titulo": "Atividade iniciada sem avanço fisico registrado",
            "gatilho": "Atividade com inicio atingido sem progresso acima da tolerancia",
            "valor_atual": _formatar_parametro_alerta(parametros.atividade_sem_avanco_tolerancia_dias, "dias"),
            "impacto": "Aponta atraso imediato no cronograma e ajuda a agir cedo.",
        },
        {
            "codigo": "PLAN-PROG-002",
            "titulo": "Avanco fisico abaixo do tempo decorrido",
            "gatilho": "Desvio percentual de prazo apos percentual minimo previsto",
            "valor_atual": f"Minimo {parametros.desvio_prazo_percentual_minimo_previsto}% / tolerancia {parametros.desvio_prazo_tolerancia_percentual}%",
            "impacto": "Sinaliza tendencia de atraso crescente antes do estouro final.",
        },
        {
            "codigo": "PLAN-PROG-003",
            "titulo": "Projecao de termino alem do prazo da obra",
            "gatilho": "Data estimada de termino excede a folga de prazo definida",
            "valor_atual": _formatar_parametro_alerta(parametros.estouro_prazo_tolerancia_dias, "dias"),
            "impacto": "Mostra risco de estouro global de prazo com base no ritmo atual.",
        },
        {
            "codigo": "COST-PROG-001",
            "titulo": "Custo realizado acima do previsto proporcional",
            "gatilho": "Custo acima da tolerancia percentual definida",
            "valor_atual": _formatar_parametro_alerta(parametros.desvio_custo_tolerancia_percentual, "percentual"),
            "impacto": "Ajuda a detectar estouro de custo antes de contaminar a obra inteira.",
        },
        {
            "codigo": "COST-PROG-002",
            "titulo": "Lancamento de custo sem avanço fisico correspondente",
            "gatilho": "Valor realizado sem avanço fisico acima do minimo definido",
            "valor_atual": _formatar_parametro_alerta(parametros.custo_sem_avanco_valor_minimo, "moeda"),
            "impacto": "Identifica custo sem lastro fisico e possivel retrabalho ou baixa produtividade.",
        },
        {
            "codigo": "COST-BUD-001",
            "titulo": "Compromisso acima do valor orcado",
            "gatilho": "Compromisso acima do orcado somando a tolerancia percentual",
            "valor_atual": _formatar_parametro_alerta(parametros.compromisso_acima_orcado_tolerancia_percentual, "percentual"),
            "impacto": "Protege o orcamento contra contratacao ou compra acima do previsto.",
        },
        {
            "codigo": "RISK-ACC-001",
            "titulo": "Acumulo de riscos operacionais nao tratados",
            "gatilho": "Quantidade de riscos ativos acima do limite e do nivel critico",
            "valor_atual": f"Minimo {parametros.acumulo_riscos_quantidade_minima} / critico {parametros.acumulo_riscos_quantidade_critica}",
            "impacto": "Evidencia perda sistêmica de controle na obra.",
        },
        {
            "codigo": "ALERT-SLA-001",
            "titulo": "Alerta sem workflow recente",
            "gatilho": "Alerta sem nova movimentacao acima da tolerancia operacional",
            "valor_atual": _formatar_parametro_alerta(parametros.alerta_sem_workflow_dias, "dias"),
            "impacto": "O score so reduz quando o alerta envelhece sem tratamento efetivo.",
        },
        {
            "codigo": "ALERT-SLA-002",
            "titulo": "Prazo de solucao do alerta estourado",
            "gatilho": "Alerta aberto alem do prazo padrao de solucao da empresa",
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
    """Formulario de cadastro de empresa no painel tecnico do sistema."""

    class Meta:
        model = Empresa
        fields = ["nome", "nome_fantasia", "cnpj", "telefone", "email", "endereco", "ativo"]
        widgets = {
            "endereco": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "nome": "Razao social",
            "nome_fantasia": "Nome fantasia",
            "cnpj": "CNPJ",
            "telefone": "Telefone",
            "email": "Email principal",
            "endereco": "Endereco",
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
            "planejamento_suprimentos_janela_dias": "Dias futuros para alerta de atividade sem solicitacao",
            "contrato_sem_medicao_dias": "Dias sem medicao para alertar contrato ativo",
            "medicao_sem_nota_dias": "Dias sem nota fiscal para alertar medicao",
            "nota_sem_rateio_percentual_minimo": "Percentual minimo pendente para alertar nota sem rateio",
            "risco_vencido_tolerancia_dias": "Dias de tolerancia para risco vencido",
            "nao_conformidade_sem_evolucao_dias": "Dias sem evolucao para alertar nao conformidade",
            "atividade_sem_avanco_tolerancia_dias": "Dias de tolerancia apos inicio da atividade sem avancar",
            "desvio_prazo_percentual_minimo_previsto": "Percentual previsto minimo para avaliar desvio de prazo",
            "desvio_prazo_tolerancia_percentual": "Diferenca percentual minima para alertar desvio de prazo",
            "estouro_prazo_tolerancia_dias": "Dias de tolerancia para estourar prazo da obra",
            "desvio_custo_tolerancia_percentual": "Percentual acima do custo proporcional para alertar desvio",
            "custo_sem_avanco_valor_minimo": "Valor minimo sem avanço fisico para alertar custo sem lastro",
            "compromisso_acima_orcado_tolerancia_percentual": "Percentual de tolerancia acima do orcado para compromissos",
            "acumulo_riscos_quantidade_minima": "Quantidade minima de riscos ativos para gerar alerta",
            "acumulo_riscos_quantidade_critica": "Quantidade de riscos ativos para tornar o alerta critico",
            "alerta_sem_workflow_dias": "Dias sem workflow para o score penalizar um alerta",
            "alerta_prazo_solucao_dias": "Prazo padrao de solucao do alerta para o score penalizar",
        }

    def clean(self):
        cleaned = super().clean()
        quantidade_minima = cleaned.get("acumulo_riscos_quantidade_minima")
        quantidade_critica = cleaned.get("acumulo_riscos_quantidade_critica")
        if quantidade_minima and quantidade_critica and quantidade_critica < quantidade_minima:
            self.add_error("acumulo_riscos_quantidade_critica", "O nivel critico deve ser maior ou igual ao nivel minimo de disparo.")
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
            "frequencia_medio_prazo_dias": "Medio prazo (dias)",
            "frequencia_longo_prazo_dias": "Longo prazo (dias)",
        }
        help_texts = {
            "frequencia_curto_prazo_dias": "Frequencia padrao das reunioes de curto prazo da empresa.",
            "frequencia_medio_prazo_dias": "Frequencia padrao das reunioes de médio prazo da empresa.",
            "frequencia_longo_prazo_dias": "Frequencia padrao das reunioes de longo prazo da empresa.",
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
            messages.error(request, "Voce nao esta vinculado a nenhuma empresa.")
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
            finalidade="Gestao administrativa de usuarios e permissoes por empresa",
            detalhes="Consulta administrativa da area de usuarios da empresa.",
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
        """Processa acoes de usuarios ou obras."""
        if not _exigir_admin_empresa_vinculado(request):
            return redirect("home")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa:
            return redirect("home")
        
        if not empresa:
            messages.error(request, "Empresa nao encontrada.")
            return redirect("home")
        
        # Verificar qual acao
        acao = request.POST.get("acao")
        
        if acao == "atualizar_obras_usuario":
            return self._atualizar_obras_usuario(request, empresa)
        elif acao == "atualizar_permissoes_usuario":
            return self._atualizar_permissoes_usuario(request, empresa)
        elif acao == "criar_usuario":
            return self._criar_usuario(request, empresa)
        elif acao == "criar_obra":
            return self._criar_obra(request, empresa)
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
        """Atualizar obras permitidas de um usuario."""
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        obras_selecionadas = request.POST.getlist("obras")
        papel_aprovacao = request.POST.get("papel_aprovacao") or "TECNICO_OBRAS"
        
        try:
            usuario_empresa = UsuarioEmpresa.objects.get(pk=usuario_empresa_id)
            
            admin_empresa = get_empresa_do_usuario(request.user)
            if admin_empresa != usuario_empresa.empresa:
                messages.error(request, "Voce so pode gerenciar usuarios da sua empresa.")
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
            messages.error(request, "Usuario empresa nao encontrado.")
        
        return redirect("empresa_admin")

    def _atualizar_permissoes_usuario(self, request, empresa):
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        try:
            usuario_empresa = UsuarioEmpresa.objects.get(pk=usuario_empresa_id, empresa=empresa)
        except UsuarioEmpresa.DoesNotExist:
            messages.error(request, "Usuario da empresa nao encontrado.")
            return redirect("empresa_admin")

        permissoes = {}
        for modulo, acoes in PERMISSOES_MODULO_ACAO.items():
            modulo_permissoes = {}
            for acao in acoes:
                campo = f"perm_{modulo}_{acao}"
                modulo_permissoes[acao] = request.POST.get(campo) == "on"
            permissoes[modulo] = modulo_permissoes
        atualizar_permissoes_usuario_empresa(usuario_empresa, permissoes, concedido_por=request.user)
        messages.success(request, f"Permissoes de modulo atualizadas para {usuario_empresa.usuario.username}.")
        return redirect("empresa_admin")
    
    def _criar_usuario(self, request, empresa):
        """Criar novo usuario."""
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        obras_selecionadas = request.POST.getlist("obras_usuario")
        papel_aprovacao = request.POST.get("papel_aprovacao") or "TECNICO_OBRAS"
        
        if not username or not password:
            messages.error(request, "Username e senha sao obrigatorios.")
            return redirect("empresa_admin")
        
        if User.objects.filter(username=username).exists():
            messages.error(request, "Ja existe um usuario com este username.")
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
        
        messages.success(request, f"Usuario {username} criado com sucesso!")
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
                messages.error(request, "Nao foi possivel gerar um codigo unico para a obra.")
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


class UsuarioEmpresaCreateView(View):
    """
    Criar um novo usuario e vincula-lo a empresa do admin.
    """
    template_name = "app/usuario_empresa_form.html"
    
    def get(self, request):
        if not is_admin_empresa(request.user) and not request.user.is_superuser:
            messages.error(request, "Voce nao tem permissao para acessar esta pagina.")
            return redirect("home")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa and not request.user.is_superuser:
            messages.error(request, "Voce nao esta vinculado a nenhuma empresa.")
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
            messages.error(request, "Voce nao tem permissao.")
            return redirect("home")
        
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        obras_selecionadas = request.POST.getlist("obras")
        papel_aprovacao = request.POST.get("papel_aprovacao") or "TECNICO_OBRAS"
        empresa_id = request.POST.get("empresa_id")
        
        if not username or not password:
            messages.error(request, "Username e senha sao obrigatorios.")
            return redirect("usuario_empresa_create")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa and not request.user.is_superuser:
            return redirect("home")
        
        if request.user.is_superuser and empresa_id:
            empresa = Empresa.objects.filter(pk=empresa_id).first()
        
        if not empresa:
            messages.error(request, "Empresa nao encontrada.")
            return redirect("home")
        
        # Verificar se username ja existe
        if User.objects.filter(username=username).exists():
            messages.error(request, "Ja existe um usuario com este username.")
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
        
        messages.success(request, f"Usuario {username} criado com sucesso!")
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
            messages.error(request, "Selecione uma empresa valida para gerenciar o sistema.")
            return redirect("sistema_admin")

        if acao == "criar_admin_empresa":
            return self._criar_admin_empresa(request, empresa)
        
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

        messages.error(request, "Acao de sistema nao reconhecida.")
        return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

    def _build_context(self, *, empresa=None, empresa_create_form=None):
        plano_empresa = getattr(empresa, "plano", None) if empresa else None
        status_plano = TenantService.status_plano(empresa) if empresa else None

        contexto = {
            "empresa": empresa,
            "plano_empresa": plano_empresa,
            "status_plano": status_plano,
            "empresas": Empresa.objects.filter(ativo=True).order_by("nome"),
            "admins_empresa": self._admins_empresa_queryset(empresa),
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

    def _criar_empresa(self, request):
        form = SistemaEmpresaCreateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Revise os dados da empresa e tente novamente.")
            return render(request, self.template_name, self._build_context(empresa_create_form=form))

        empresa = form.save()
        PlanoEmpresa.objects.get_or_create(empresa=empresa)
        messages.success(request, f"Empresa {empresa.nome} criada com sucesso.")
        return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

    def _criar_admin_empresa(self, request, empresa):
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = (request.POST.get("password") or "").strip()

        if not username or not password:
            messages.error(request, "Username e senha sao obrigatorios para criar o admin da empresa.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Ja existe um usuario com este username.")
            return redirect(f"{reverse_lazy('sistema_admin')}?empresa={empresa.pk}")

        if UsuarioEmpresa.objects.filter(empresa=empresa, is_admin_empresa=True).exists():
            messages.info(request, "A empresa ja possui um admin cadastrado. Crie outro apenas se isso for realmente necessario.")

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
