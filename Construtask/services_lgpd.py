"""
Servicos de governanca LGPD, inventario minimo de tratamento e trilha de acesso.
"""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import ConsentimentoLGPD, RegistroAcessoDadoPessoal, RegistroTratamentoDadoPessoal
from .models_aquisicoes import Fornecedor
from .permissions import get_empresa_operacional


FIELD_METADATA_DEFAULT = {
    "email": {
        "categoria_titular": "USUARIO",
        "finalidade": "Comunicacao operacional, autenticacao e rastreabilidade",
        "base_legal": "Execucao de contrato e legitimo interesse",
        "retencao": "Enquanto durar o vinculo e pelo prazo prescricional aplicavel",
        "responsavel": "Administracao da empresa",
    },
    "telefone": {
        "categoria_titular": "COLABORADOR",
        "finalidade": "Contato operacional e coordenacao de obra",
        "base_legal": "Execucao de contrato e legitimo interesse",
        "retencao": "Enquanto durar a relacao operacional",
        "responsavel": "Administracao da empresa",
    },
    "cnpj": {
        "categoria_titular": "FORNECEDOR",
        "finalidade": "Contratacao, faturamento e prova fiscal",
        "base_legal": "Execucao de contrato e obrigacao legal",
        "retencao": "Prazo fiscal, contabil e contratual aplicavel",
        "responsavel": "Financeiro e suprimentos",
    },
    "cliente": {
        "categoria_titular": "CLIENTE",
        "finalidade": "Identificacao do empreendimento e relacao contratual",
        "base_legal": "Execucao de contrato",
        "retencao": "Prazo de vida util da obra e periodo prescricional",
        "responsavel": "Operacao da obra",
    },
    "responsavel": {
        "categoria_titular": "COLABORADOR",
        "finalidade": "Responsabilizacao operacional e trilha de decisao",
        "base_legal": "Execucao de contrato e legitimo interesse",
        "retencao": "Enquanto durar o contexto operacional",
        "responsavel": "Operacao da obra",
    },
    "contato": {
        "categoria_titular": "FORNECEDOR",
        "finalidade": "Contato comercial e operacional",
        "base_legal": "Execucao de contrato e exercicio regular de direitos",
        "retencao": "Durante a relacao comercial",
        "responsavel": "Suprimentos",
    },
    "user_agent": {
        "categoria_titular": "TERCEIRO",
        "finalidade": "Seguranca da informacao e auditoria",
        "base_legal": "Legitimo interesse",
        "retencao": "Conforme politica de seguranca",
        "responsavel": "Administracao da empresa",
    },
    "ip_address": {
        "categoria_titular": "TERCEIRO",
        "finalidade": "Seguranca da informacao e auditoria",
        "base_legal": "Legitimo interesse",
        "retencao": "Conforme politica de seguranca",
        "responsavel": "Administracao da empresa",
    },
}

PERSONAL_FIELD_KEYWORDS = {
    "email",
    "telefone",
    "celular",
    "whatsapp",
    "cnpj",
    "cpf",
    "cliente",
    "responsavel",
    "contato",
    "ip_address",
    "user_agent",
    "username",
    "first_name",
    "last_name",
    "nome_fantasia",
}


INVENTARIO_DADOS_PESSOAIS = [
    {
        "categoria_titular": "Usuario",
        "entidade": "auth.User / UsuarioEmpresa / UserProfile",
        "dados_tratados": "Username, email, papel operacional, telefone, cargo, obras permitidas",
        "finalidade": "Controle de acesso, segregacao por empresa/obra, aprovacao operacional e rastreabilidade",
        "base_legal": "Execucao de contrato e legitimo interesse",
        "retencao": "Enquanto durar o vinculo operacional e pelo prazo prescricional aplicavel",
    },
    {
        "categoria_titular": "Fornecedor",
        "entidade": "Fornecedor / Compromisso / Cotacao / OrdemCompra / NotaFiscal",
        "dados_tratados": "Razao social, nome fantasia, CNPJ, contato, telefone, email e historico comercial",
        "finalidade": "Aquisicoes, contratacoes, pagamentos, medicao de desempenho e prova operacional",
        "base_legal": "Execucao de contrato e exercicio regular de direitos",
        "retencao": "Durante a relacao comercial e pelo prazo legal, contabil e contratual aplicavel",
    },
    {
        "categoria_titular": "Cliente",
        "entidade": "Obra",
        "dados_tratados": "Nome do cliente e referencias operacionais da obra",
        "finalidade": "Identificacao do empreendimento e gestao contratual da obra",
        "base_legal": "Execucao de contrato e legitimo interesse",
        "retencao": "Enquanto a obra permanecer ativa e pelo prazo de guarda do historico contratual",
    },
    {
        "categoria_titular": "Colaborador",
        "entidade": "Obra / Compromisso / NaoConformidade",
        "dados_tratados": "Nome do responsavel, telefone, cargo funcional e historico de acoes",
        "finalidade": "Responsabilizacao operacional, fluxo de aprovacao e rastreabilidade",
        "base_legal": "Execucao de contrato e cumprimento de obrigacao legal/regulatoria",
        "retencao": "Enquanto durar a relacao operacional e pelo prazo prescricional aplicavel",
    },
    {
        "categoria_titular": "Terceiro",
        "entidade": "Documento / AuditEvent / RegistroAcessoDadoPessoal",
        "dados_tratados": "Nome de usuario, endereco IP, user agent e identificadores de acesso",
        "finalidade": "Seguranca da informacao, auditoria, investigacao e defesa juridica",
        "base_legal": "Legitimo interesse e exercicio regular de direitos",
        "retencao": "Conforme politica de seguranca e prazo de guarda de trilhas de auditoria",
    },
]


POLITICA_RETENCAO_PADRAO = [
    {
        "registro": "Usuarios e perfis de acesso",
        "regra": "Manter enquanto o usuario possuir vinculo operacional ativo e, apos desligamento, preservar trilhas essenciais para auditoria.",
    },
    {
        "registro": "Fornecedores e cadastros comerciais",
        "regra": "Manter durante a relacao comercial e pelo prazo legal, contabil, fiscal e contratual aplicavel.",
    },
    {
        "registro": "Auditoria e acesso a dados pessoais",
        "regra": "Manter para seguranca, rastreabilidade, auditoria interna e defesa juridica, observando necessidade e proporcionalidade.",
    },
    {
        "registro": "Documentos, contratos, medicoes e evidencias operacionais",
        "regra": "Manter pelo prazo de vida util da obra e pelos prazos prescricionais e contratuais aplicaveis.",
    },
]


POLITICA_DESCARTE_ANONIMIZACAO = [
    {
        "entidade": "Fornecedor",
        "criterio": "Cadastro inativo e sem necessidade operacional de manter dados de contato identificaveis.",
        "acao_recomendada": "Anonimizar contato, telefone, email e nome fantasia, preservando historico contratual e fiscal.",
    },
    {
        "entidade": "Usuario operacional",
        "criterio": "Usuario inativo ou desligado, mantendo necessidade de trilha historica.",
        "acao_recomendada": "Anonimizar email e dados complementares do perfil, preservando identificador historico minimo.",
    },
    {
        "entidade": "Logs e auditoria",
        "criterio": "Decurso do prazo de retencao definido em politica interna e ausencia de litigo ou auditoria pendente.",
        "acao_recomendada": "Descarte controlado ou arquivamento conforme politica institucional.",
    },
]


def obter_inventario_dados_pessoais():
    return INVENTARIO_DADOS_PESSOAIS


def obter_inventario_modelos_dados_pessoais():
    inventario = []
    for model in apps.get_app_config("Construtask").get_models():
        campos_pessoais = []
        for field in model._meta.get_fields():
            if not getattr(field, "concrete", False) or getattr(field, "many_to_many", False):
                continue
            nome = getattr(field, "name", "")
            if nome in PERSONAL_FIELD_KEYWORDS:
                metadata = FIELD_METADATA_DEFAULT.get(nome, {})
                campos_pessoais.append(
                    {
                        "campo": nome,
                        "categoria_titular": metadata.get("categoria_titular", "TERCEIRO"),
                        "finalidade": metadata.get("finalidade", "Controle operacional e rastreabilidade"),
                        "base_legal": metadata.get("base_legal", "Legitimo interesse"),
                        "retencao": metadata.get("retencao", "Conforme politica de retencao da empresa"),
                        "responsavel": metadata.get("responsavel", "Administracao da empresa"),
                    }
                )
        inventario.append(
            {
                "modelo": model.__name__,
                "app_label": model._meta.app_label,
                "campos_pessoais": campos_pessoais,
                "possui_dados_pessoais": bool(campos_pessoais),
            }
        )
    return inventario


def obter_politica_retencao_padrao():
    return POLITICA_RETENCAO_PADRAO


def obter_politica_descarte_anonimizacao():
    return POLITICA_DESCARTE_ANONIMIZACAO


def obter_resumo_rotinas_lgpd():
    user_model = get_user_model()
    return {
        "fornecedores_inativos": Fornecedor.objects.filter(ativo=False).count(),
        "usuarios_inativos": user_model.objects.filter(is_active=False).count(),
        "tratamentos_registrados": RegistroTratamentoDadoPessoal.objects.count(),
        "consentimentos_ativos": ConsentimentoLGPD.objects.filter(revogado_em__isnull=True).count(),
    }


def registrar_acesso_dado_pessoal(
    request,
    *,
    categoria_titular,
    entidade,
    objeto=None,
    identificador="",
    acao="VIEW",
    finalidade="Gestao operacional autorizada",
    detalhes="",
):
    """
    Registra acessos administrativos a dados pessoais sem interferir no fluxo operacional.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return None

    empresa = getattr(objeto, "empresa", None) if objeto is not None else get_empresa_operacional(request)
    ip_address = request.META.get("REMOTE_ADDR")
    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:1000]

    return RegistroAcessoDadoPessoal.objects.create(
        empresa=empresa,
        usuario=request.user,
        categoria_titular=categoria_titular,
        entidade=entidade,
        objeto_id=getattr(objeto, "pk", None),
        identificador=(identificador or (str(objeto) if objeto is not None else ""))[:255],
        acao=acao,
        finalidade=finalidade,
        detalhes=detalhes,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def registrar_tratamento_dado_pessoal(
    *,
    empresa=None,
    usuario=None,
    categoria_titular,
    entidade,
    objeto=None,
    identificador="",
    acao,
    finalidade,
    base_legal="",
    detalhes="",
    evidencia="",
):
    return RegistroTratamentoDadoPessoal.objects.create(
        empresa=empresa or getattr(objeto, "empresa", None),
        usuario=usuario,
        categoria_titular=categoria_titular,
        entidade=entidade,
        objeto_id=getattr(objeto, "pk", None),
        identificador=(identificador or (str(objeto) if objeto is not None else ""))[:255],
        acao=acao,
        finalidade=finalidade,
        base_legal=base_legal,
        detalhes=detalhes,
        evidencia=evidencia,
    )


def registrar_consentimento(request, *, categoria_titular, finalidade, texto_aceito, email_referencia=""):
    consentimento = ConsentimentoLGPD.objects.create(
        empresa=get_empresa_operacional(request),
        usuario=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
        categoria_titular=categoria_titular,
        email_referencia=email_referencia,
        finalidade=finalidade,
        texto_aceito=texto_aceito,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:1000],
    )
    registrar_tratamento_dado_pessoal(
        empresa=consentimento.empresa,
        usuario=consentimento.usuario,
        categoria_titular=categoria_titular,
        entidade="ConsentimentoLGPD",
        objeto=consentimento,
        identificador=email_referencia or str(consentimento.usuario or ""),
        acao="CONSENTIMENTO",
        finalidade=finalidade,
        base_legal="Consentimento",
        detalhes="Consentimento registrado pelo portal",
        evidencia=texto_aceito[:500],
    )
    return consentimento


def revogar_consentimento(consentimento, *, usuario=None):
    if consentimento.revogado_em:
        return False
    consentimento.revogado_em = timezone.now()
    consentimento.save(update_fields=["revogado_em"])
    registrar_tratamento_dado_pessoal(
        empresa=consentimento.empresa,
        usuario=usuario,
        categoria_titular=consentimento.categoria_titular,
        entidade="ConsentimentoLGPD",
        objeto=consentimento,
        identificador=consentimento.email_referencia or str(consentimento.usuario or ""),
        acao="REVOGACAO_CONSENTIMENTO",
        finalidade=consentimento.finalidade,
        base_legal="Consentimento",
        detalhes="Consentimento revogado",
    )
    return True


def anonimizar_fornecedor_inativo(fornecedor):
    """
    Anonimiza apenas campos potencialmente pessoais de fornecedores inativos,
    preservando integridade juridica e rastreabilidade fiscal do cadastro.
    """
    if fornecedor.ativo:
        return False

    atualizado = False
    campos = []
    if fornecedor.nome_fantasia:
        fornecedor.nome_fantasia = ""
        campos.append("nome_fantasia")
        atualizado = True
    if fornecedor.contato:
        fornecedor.contato = "Contato anonimizado"
        campos.append("contato")
        atualizado = True
    if fornecedor.telefone:
        fornecedor.telefone = ""
        campos.append("telefone")
        atualizado = True
    if fornecedor.email:
        fornecedor.email = ""
        campos.append("email")
        atualizado = True

    if atualizado:
        fornecedor.anonimizado_em = timezone.now()
        campos.append("anonimizado_em")
        fornecedor.save(update_fields=campos)
        registrar_tratamento_dado_pessoal(
            empresa=fornecedor.empresa,
            categoria_titular="FORNECEDOR",
            entidade="Fornecedor",
            objeto=fornecedor,
            identificador=fornecedor.razao_social,
            acao="ANONIMIZACAO",
            finalidade="Reducao de dados apos inatividade",
            base_legal="Legitimo interesse e exercicio regular de direitos",
            detalhes="Anonimizacao segura de dados de contato do fornecedor inativo",
        )
    return atualizado


def anonimizar_usuario_inativo(usuario):
    """
    Anonimiza dados complementares de usuario inativo, preservando o identificador
    historico minimo necessario para auditoria.
    """
    if usuario.is_active or usuario.is_superuser:
        return False

    atualizado = False
    campos_usuario = []
    email_anonimo = f"anonimizado+{usuario.pk}@construtask.local"
    if usuario.email != email_anonimo:
        usuario.email = email_anonimo
        campos_usuario.append("email")
        atualizado = True
    if getattr(usuario, "first_name", ""):
        usuario.first_name = ""
        campos_usuario.append("first_name")
        atualizado = True
    if getattr(usuario, "last_name", ""):
        usuario.last_name = ""
        campos_usuario.append("last_name")
        atualizado = True
    if campos_usuario:
        usuario.save(update_fields=campos_usuario)

    perfil = getattr(usuario, "perfil", None)
    if perfil is not None:
        campos_perfil = []
        if perfil.telefone:
            perfil.telefone = ""
            campos_perfil.append("telefone")
        if perfil.cargo:
            perfil.cargo = ""
            campos_perfil.append("cargo")
        if campos_perfil:
            perfil.save(update_fields=campos_perfil)
            atualizado = True

    usuario_empresa = getattr(usuario, "usuario_empresa", None)
    if usuario_empresa is not None and usuario_empresa.obras_permitidas.exists():
        usuario_empresa.obras_permitidas.clear()
        atualizado = True

    if atualizado:
        registrar_tratamento_dado_pessoal(
            empresa=getattr(usuario_empresa, "empresa", None),
            usuario=usuario,
            categoria_titular="USUARIO",
            entidade="auth.User",
            objeto=usuario,
            identificador=usuario.username,
            acao="ANONIMIZACAO",
            finalidade="Reducao de dados de usuario inativo",
            base_legal="Legitimo interesse e exercicio regular de direitos",
            detalhes="Anonimizacao de dados complementares do usuario inativo",
        )

    return atualizado


def excluir_logicamente_fornecedor(fornecedor, *, usuario=None, justificativa=""):
    if not fornecedor.ativo and fornecedor.exclusao_logica_em:
        return False
    fornecedor.ativo = False
    fornecedor.exclusao_logica_em = timezone.now()
    fornecedor.save(update_fields=["ativo", "exclusao_logica_em"])
    registrar_tratamento_dado_pessoal(
        empresa=fornecedor.empresa,
        usuario=usuario,
        categoria_titular="FORNECEDOR",
        entidade="Fornecedor",
        objeto=fornecedor,
        identificador=fornecedor.razao_social,
        acao="EXCLUSAO_LOGICA",
        finalidade="Atender solicitacao de restricao de uso do cadastro",
        base_legal="Exercicio regular de direitos",
        detalhes=justificativa or "Exclusao logica do cadastro",
    )
    return True


def descartar_fornecedor_anonimizado(fornecedor, *, usuario=None, justificativa=""):
    if fornecedor.descartado_em:
        return False
    fornecedor.descartado_em = timezone.now()
    fornecedor.save(update_fields=["descartado_em"])
    registrar_tratamento_dado_pessoal(
        empresa=fornecedor.empresa,
        usuario=usuario,
        categoria_titular="FORNECEDOR",
        entidade="Fornecedor",
        objeto=fornecedor,
        identificador=fornecedor.razao_social,
        acao="DESCARTE",
        finalidade="Encerramento definitivo do ciclo de retencao",
        base_legal="Exercicio regular de direitos",
        detalhes=justificativa or "Registro marcado como descartado apos anonimização",
    )
    return True


def buscar_titular(empresa, termo):
    termo = (termo or "").strip()
    if not termo:
        return []

    resultados = []
    user_model = get_user_model()
    usuarios = user_model.objects.filter(username__icontains=termo) | user_model.objects.filter(email__icontains=termo)
    for usuario in usuarios.distinct()[:20]:
        resultados.append(
            {
                "categoria_titular": "USUARIO",
                "entidade": "auth.User",
                "identificador": usuario.email or usuario.username,
                "objeto_id": usuario.pk,
                "descricao": usuario.username,
            }
        )

    fornecedores = Fornecedor.objects.filter(empresa=empresa) if empresa else Fornecedor.objects.all()
    fornecedores = fornecedores.filter(
        razao_social__icontains=termo
    ) | fornecedores.filter(
        email__icontains=termo
    ) | fornecedores.filter(
        contato__icontains=termo
    ) | fornecedores.filter(
        cnpj__icontains=termo
    )
    for fornecedor in fornecedores.distinct()[:20]:
        resultados.append(
            {
                "categoria_titular": "FORNECEDOR",
                "entidade": "Fornecedor",
                "identificador": fornecedor.email or fornecedor.razao_social,
                "objeto_id": fornecedor.pk,
                "descricao": fornecedor.razao_social,
            }
        )
    return resultados


def executar_rotinas_anonimizacao():
    """
    Executa rotinas seguras de anonimização em registros inequivocamente inativos.
    """
    user_model = get_user_model()
    fornecedores = 0
    usuarios = 0
    for fornecedor in Fornecedor.objects.filter(ativo=False):
        if anonimizar_fornecedor_inativo(fornecedor):
            fornecedores += 1
    for usuario in user_model.objects.filter(is_active=False):
        if anonimizar_usuario_inativo(usuario):
            usuarios += 1
    return {
        "executado_em": timezone.now(),
        "fornecedores_anonimizados": fornecedores,
        "usuarios_anonimizados": usuarios,
    }
