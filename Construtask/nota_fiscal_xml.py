from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import xml.etree.ElementTree as ET

from .cnpj_utils import formatar_cnpj


class NotaFiscalXmlError(ValueError):
    pass


@dataclass
class DadosNotaFiscalXml:
    numero: str = ""
    serie: str = ""
    tipo: str = "MATERIAL"
    data_emissao: date | None = None
    data_vencimento: date | None = None
    fornecedor: str = ""
    cnpj: str = ""
    descricao: str = ""
    valor_total: Decimal | None = None

    def as_form_data(self):
        dados = {}
        for campo in ("numero", "serie", "tipo", "fornecedor", "cnpj", "descricao"):
            valor = getattr(self, campo)
            if valor:
                dados[campo] = valor
        if self.data_emissao:
            dados["data_emissao"] = self.data_emissao.isoformat()
        if self.data_vencimento:
            dados["data_vencimento"] = self.data_vencimento.isoformat()
        if self.valor_total is not None:
            dados["valor_total"] = f"{self.valor_total:.2f}"
        return dados


def importar_dados_nota_fiscal_xml(arquivo):
    try:
        raiz = ET.fromstring(arquivo.read())
    except ET.ParseError as exc:
        raise NotaFiscalXmlError("XML inválido. Verifique o arquivo da nota fiscal.") from exc

    tags = {_tag_local(elemento.tag).lower() for elemento in raiz.iter()}
    if _tem_tag(tags, "infcte"):
        dados = _extrair_cte(raiz)
    elif _tem_tag(tags, "infcfe"):
        dados = _extrair_cfe_sat(raiz)
    elif _eh_nfse(tags, raiz):
        dados = _extrair_nfse(raiz)
    else:
        dados = _extrair_nfe_nfce(raiz)

    if not dados.numero and not dados.valor_total and not dados.fornecedor:
        raise NotaFiscalXmlError("Não foi possível identificar os dados principais no XML informado.")
    return dados


def _extrair_nfe_nfce(raiz):
    inf = _primeiro_no(raiz, "infNFe") or raiz
    ide = _primeiro_filho(inf, "ide") or inf
    emit = _primeiro_filho(inf, "emit") or inf
    total = _primeiro_no(inf, "ICMSTot") or _primeiro_no(inf, "total") or inf
    cobr = _primeiro_no(inf, "dup") or _primeiro_no(inf, "fat")
    produtos = [_texto_filho(prod, "xProd") for prod in _nos(inf, "prod")]
    produtos = [produto for produto in produtos if produto]
    descricao = _texto_no(inf, "infCpl") or "; ".join(produtos[:6])
    return DadosNotaFiscalXml(
        numero=_texto_filho(ide, "nNF"),
        serie=_texto_filho(ide, "serie"),
        tipo="MATERIAL",
        data_emissao=_parse_data(_texto_filho(ide, "dhEmi") or _texto_filho(ide, "dEmi")),
        data_vencimento=_parse_data(_texto_filho(cobr, "dVenc") if cobr is not None else ""),
        fornecedor=_texto_filho(emit, "xNome") or _texto_filho(emit, "xFant"),
        cnpj=formatar_cnpj(_texto_filho(emit, "CNPJ") or _texto_filho(emit, "CPF")),
        descricao=descricao[:900],
        valor_total=_parse_decimal(_texto_filho(total, "vNF") or _texto_filho(total, "vProd")),
    )


def _extrair_nfse(raiz):
    inf_nfse = _primeiro_no(raiz, "infNFSe") or _primeiro_no(raiz, "InfNfse") or raiz
    inf_dps = _primeiro_no(raiz, "infDPS")
    prestador = (
        _primeiro_filho(inf_nfse, "emit")
        or _primeiro_no(inf_nfse, "PrestadorServico")
        or _primeiro_no(inf_nfse, "Prestador")
        or _primeiro_no(raiz, "PrestadorServico")
        or _primeiro_no(raiz, "Prestador")
        or inf_nfse
    )
    valores = (
        _primeiro_no(inf_nfse, "valores")
        or _primeiro_no(inf_nfse, "Valores")
        or _primeiro_no(raiz, "ValoresNfse")
        or inf_nfse
    )
    descricao = (
        _texto_no(inf_nfse, "xDescServ")
        or _texto_no(inf_nfse, "Discriminacao")
        or _texto_no(inf_nfse, "Descricao")
        or _texto_no(inf_nfse, "xTribMun")
        or _texto_no(inf_nfse, "xTribNac")
    )
    return DadosNotaFiscalXml(
        numero=(
            _texto_filho(inf_nfse, "nNFSe")
            or _texto_filho(inf_nfse, "Numero")
            or _texto_no(inf_nfse, "NumeroNfse")
            or _texto_filho(inf_nfse, "nDFSe")
        ),
        serie=_texto_filho(inf_dps, "serie") if inf_dps is not None else _texto_no(inf_nfse, "Serie"),
        tipo="SERVICO",
        data_emissao=_parse_data(
            _texto_no(inf_nfse, "dhEmi")
            or _texto_no(inf_nfse, "DataEmissao")
            or _texto_filho(inf_nfse, "dhProc")
            or _texto_no(inf_nfse, "dCompet")
        ),
        data_vencimento=_parse_data(_texto_no(inf_nfse, "dVenc") or _texto_no(inf_nfse, "DataVencimento")),
        fornecedor=(
            _texto_filho(prestador, "xNome")
            or _texto_filho(prestador, "RazaoSocial")
            or _texto_filho(prestador, "Nome")
        ),
        cnpj=formatar_cnpj(_texto_no(prestador, "CNPJ") or _texto_no(prestador, "Cnpj") or _texto_no(prestador, "Cpf")),
        descricao=descricao[:900],
        valor_total=_parse_decimal(
            _texto_no(valores, "vLiq")
            or _texto_no(valores, "ValorLiquidoNfse")
            or _texto_no(valores, "ValorServicos")
            or _texto_no(valores, "vServ")
            or _texto_no(valores, "vBC")
        ),
    )


def _extrair_cte(raiz):
    inf = _primeiro_no(raiz, "infCte") or raiz
    ide = _primeiro_filho(inf, "ide") or inf
    emit = _primeiro_filho(inf, "emit") or inf
    valor = _primeiro_no(inf, "vPrest") or inf
    descricao = _texto_no(inf, "proPred") or _texto_no(inf, "xObs") or "Conhecimento de Transporte Eletrônico"
    return DadosNotaFiscalXml(
        numero=_texto_filho(ide, "nCT"),
        serie=_texto_filho(ide, "serie"),
        tipo="SERVICO",
        data_emissao=_parse_data(_texto_filho(ide, "dhEmi")),
        fornecedor=_texto_filho(emit, "xNome") or _texto_filho(emit, "xFant"),
        cnpj=formatar_cnpj(_texto_filho(emit, "CNPJ") or _texto_filho(emit, "CPF")),
        descricao=descricao[:900],
        valor_total=_parse_decimal(_texto_no(valor, "vTPrest") or _texto_no(valor, "vRec")),
    )


def _extrair_cfe_sat(raiz):
    inf = _primeiro_no(raiz, "infCFe") or raiz
    ide = _primeiro_filho(inf, "ide") or inf
    emit = _primeiro_filho(inf, "emit") or inf
    total = _primeiro_no(inf, "total") or inf
    produtos = [_texto_filho(prod, "xProd") for prod in _nos(inf, "prod")]
    produtos = [produto for produto in produtos if produto]
    return DadosNotaFiscalXml(
        numero=_texto_filho(ide, "nCFe") or _numero_cfe_por_id(inf.attrib.get("Id", "")),
        serie=_texto_filho(ide, "nserieSAT"),
        tipo="MATERIAL",
        data_emissao=_parse_data(_texto_filho(ide, "dEmi")),
        fornecedor=_texto_filho(emit, "xNome") or _texto_filho(emit, "xFant"),
        cnpj=formatar_cnpj(_texto_filho(emit, "CNPJ") or _texto_filho(emit, "CPF")),
        descricao="; ".join(produtos[:6])[:900],
        valor_total=_parse_decimal(_texto_no(total, "vCFe") or _texto_no(total, "vCFeLei12741")),
    )


def _eh_nfse(tags, raiz):
    tag_raiz = _tag_local(raiz.tag).lower()
    return tag_raiz in {"nfse", "compnfse"} or any(
        _tem_tag(tags, tag)
        for tag in ("infnfse", "infdeclaracaoprestacaoservico", "xdescserv", "discriminacao", "valorservicos")
    )


def _tem_tag(tags, tag):
    return tag.lower() in tags


def _tag_local(tag):
    return tag.rsplit("}", 1)[-1]


def _nos(raiz, tag):
    tag_normalizada = tag.lower()
    return [elemento for elemento in raiz.iter() if _tag_local(elemento.tag).lower() == tag_normalizada]


def _primeiro_no(raiz, tag):
    for elemento in raiz.iter():
        if _tag_local(elemento.tag).lower() == tag.lower():
            return elemento
    return None


def _primeiro_filho(no, tag):
    if no is None:
        return None
    for filho in list(no):
        if _tag_local(filho.tag).lower() == tag.lower():
            return filho
    return None


def _texto_no(raiz, tag):
    no = _primeiro_no(raiz, tag)
    return (no.text or "").strip() if no is not None and no.text else ""


def _texto_filho(no, tag):
    filho = _primeiro_filho(no, tag)
    return (filho.text or "").strip() if filho is not None and filho.text else ""


def _parse_data(valor):
    if not valor:
        return None
    texto = valor.strip()[:10]
    if re.fullmatch(r"\d{8}", texto):
        texto = f"{texto[:4]}-{texto[4:6]}-{texto[6:8]}"
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _parse_decimal(valor):
    if not valor:
        return None
    texto = valor.strip().replace(".", "").replace(",", ".") if "," in valor else valor.strip()
    try:
        return Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _numero_cfe_por_id(valor):
    digitos = re.sub(r"\D", "", valor or "")
    return digitos[-6:] if len(digitos) >= 6 else ""
