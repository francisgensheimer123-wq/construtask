import posixpath
import re

from django.utils import timezone


SEGMENTO_FALLBACK = "sem-identificacao"


def _segmento(valor):
    texto = str(valor or "").strip()
    texto = re.sub(r"\s+", " ", texto)
    texto = texto.replace("/", "-").replace("\\", "-")
    return texto or SEGMENTO_FALLBACK


def _obter_empresa_obra(instancia):
    empresa = getattr(instancia, "empresa", None)
    obra = getattr(instancia, "obra", None)

    for atributo in (
        "cotacao",
        "solicitacao",
        "ordem_compra",
        "plano",
        "documento",
        "compromisso",
        "medicao",
        "nota_fiscal",
    ):
        relacionado = getattr(instancia, atributo, None)
        if relacionado is None:
            continue

        if empresa is None:
            empresa = getattr(relacionado, "empresa", None)
        if obra is None:
            obra = getattr(relacionado, "obra", None)

        if obra is None and hasattr(relacionado, "plano"):
            plano = getattr(relacionado, "plano", None)
            obra = getattr(plano, "obra", None)
        if empresa is None and obra is not None:
            empresa = getattr(obra, "empresa", None)

    if empresa is None and obra is not None:
        empresa = getattr(obra, "empresa", None)

    return empresa, obra


def caminho_anexo_hierarquico(instancia, filename, modulo, view):
    empresa, obra = _obter_empresa_obra(instancia)
    data = timezone.localtime(timezone.now())
    return posixpath.join(
        _segmento(getattr(empresa, "nome", empresa)),
        _segmento(getattr(obra, "nome", obra)),
        _segmento(modulo),
        _segmento(view),
        str(data.year),
        f"{data.month:02d}",
        filename,
    )


def upload_job_entrada(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "administração", "jobs-entrada")


def upload_job_resultado(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "administração", "jobs-resultado")


def upload_anexo_operacional(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "operacional", "anexos")


def upload_documento_revisao(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "documentos", "revisoes")


def upload_documento_aprovado(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "documentos", "aprovados")


def upload_cotacao_anexo(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "aquisições", "cotacoes")


def upload_cronograma_origem(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "planejamento", "cronogramas")


def upload_cronograma_baseline(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "planejamento", "baselines")


def upload_nao_conformidade_tratamento(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "qualidade", "nao-conformidades-tratamento")


def upload_nao_conformidade_encerramento(instancia, filename):
    return caminho_anexo_hierarquico(instancia, filename, "qualidade", "nao-conformidades-encerramento")


def _empresa_obra_do_request(request):
    empresa = None
    obra = None
    if request is None:
        return empresa, obra

    obra_id = getattr(request, "session", {}).get("obra_contexto_id")
    if obra_id:
        try:
            from .models import Obra

            obra = Obra.objects.select_related("empresa").filter(pk=obra_id).first()
        except Exception:
            obra = None

    if obra is not None:
        empresa = getattr(obra, "empresa", None)

    if empresa is None and getattr(request, "user", None) and request.user.is_authenticated:
        try:
            from .models import UsuarioEmpresa

            vinculo = UsuarioEmpresa.objects.select_related("empresa").filter(usuario=request.user).first()
            empresa = getattr(vinculo, "empresa", None)
        except Exception:
            empresa = None

    return empresa, obra


def _modulo_view_do_request(request):
    if request is None:
        return "sistema", "exportacoes"

    url_name = getattr(getattr(request, "resolver_match", None), "url_name", None) or ""
    view = _segmento(url_name.replace("_", "-") or getattr(request, "path", "").strip("/").replace("/", "-"))
    prefixo = url_name.split("_", 1)[0]
    mapa_modulos = {
        "alerta": "operacional",
        "ata": "comunicacoes",
        "compromisso": "financeiro",
        "cotacao": "aquisicoes",
        "curva": "financeiro",
        "fechamento": "financeiro",
        "medicao": "financeiro",
        "nao": "qualidade",
        "nota": "financeiro",
        "pauta": "comunicacoes",
        "plano": "administracao",
        "projecao": "financeiro",
        "reuniao": "comunicacoes",
        "solicitacao": "aquisicoes",
    }
    return mapa_modulos.get(prefixo, "sistema"), view


def caminho_exportacao_sistema(filename, request=None):
    empresa, obra = _empresa_obra_do_request(request)
    modulo, view = _modulo_view_do_request(request)
    data = timezone.localtime(timezone.now())
    return posixpath.join(
        _segmento(getattr(empresa, "nome", empresa)),
        _segmento(getattr(obra, "nome", obra)),
        _segmento(modulo),
        _segmento(view),
        str(data.year),
        f"{data.month:02d}",
        filename,
    )
