from decimal import Decimal

from .numeric_utils import coerce_decimal


PAPEL_LIMITE_APROVACAO = {
    "GERENTE_OBRAS": None,
    "COORDENADOR_OBRAS": Decimal("100000.00"),
    "ENGENHEIRO_OBRAS": Decimal("50000.00"),
    "TECNICO_OBRAS": Decimal("0.00"),
}

PAPEIS_APROVACAO_ADITIVO = {"GERENTE_OBRAS", "COORDENADOR_OBRAS"}
PAPEIS_APROVACAO_DOCUMENTO = {"GERENTE_OBRAS", "COORDENADOR_OBRAS", "ENGENHEIRO_OBRAS"}
PAPEIS_GESTAO_QUALIDADE = {"GERENTE_OBRAS", "COORDENADOR_OBRAS", "ENGENHEIRO_OBRAS"}
PAPEIS_GESTAO_ALERTA = {"GERENTE_OBRAS", "COORDENADOR_OBRAS", "ENGENHEIRO_OBRAS"}
PAPEIS_ENCERRAMENTO_ALERTA = {"GERENTE_OBRAS", "COORDENADOR_OBRAS"}


def get_usuario_empresa(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "usuario_empresa", None)


def get_papel_aprovacao(user):
    usuario_empresa = get_usuario_empresa(user)
    if getattr(user, "is_superuser", False):
        return "GERENTE_OBRAS"
    if usuario_empresa and usuario_empresa.is_admin_empresa:
        return "GERENTE_OBRAS"
    if usuario_empresa:
        return usuario_empresa.papel_aprovacao
    return None


def get_limite_aprovacao(user):
    papel = get_papel_aprovacao(user)
    if not papel:
        return Decimal("0.00")
    return PAPEL_LIMITE_APROVACAO.get(papel, Decimal("0.00"))


def can_submit_for_approval(user):
    return bool(get_papel_aprovacao(user))


def can_approve_value(user, valor):
    papel = get_papel_aprovacao(user)
    if not papel or papel == "TECNICO_OBRAS":
        return False
    limite = get_limite_aprovacao(user)
    if limite is None:
        return True
    return coerce_decimal(valor) <= limite


def can_approve_aditivo(user, valor):
    papel = get_papel_aprovacao(user)
    if papel not in PAPEIS_APROVACAO_ADITIVO:
        return False
    return can_approve_value(user, valor)


def can_approve_document(user):
    return get_papel_aprovacao(user) in PAPEIS_APROVACAO_DOCUMENTO


def can_manage_quality(user):
    return get_papel_aprovacao(user) in PAPEIS_GESTAO_QUALIDADE


def can_assume_alert(user):
    return bool(get_papel_aprovacao(user))


def can_justify_alert(user):
    return get_papel_aprovacao(user) in PAPEIS_GESTAO_ALERTA


def can_close_alert(user):
    return get_papel_aprovacao(user) in PAPEIS_ENCERRAMENTO_ALERTA
