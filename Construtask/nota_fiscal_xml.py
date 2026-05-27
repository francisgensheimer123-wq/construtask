from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
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
        conteudo = arquivo.read()
        raiz = ET.fromstring(conteudo)
    except ET.ParseError as exc:
        raise NotaFiscalXmlError("XML inválido. Verifique o arquivo da nota fiscal.") from exc

    elementos = _indexar_elementos(raiz)
    produtos = _textos(elementos, "xProd")
    descricao = _primeiro_texto(elementos, "infCpl") or "; ".join(produtos[:6])
    dados = DadosNotaFiscalXml(
        numero=_primeiro_texto(elementos, "nNF", "Numero", "NumeroNFe", "numero"),
        serie=_primeiro_texto(elementos, "serie", "Serie", "serieRPS"),
        tipo=_inferir_tipo(elementos),
        data_emissao=_parse_data(_primeiro_texto(elementos, "dhEmi", "dEmi", "DataEmissao", "dataEmissao")),
        data_vencimento=_parse_data(_primeiro_texto(elementos, "dVenc", "DataVencimento", "dataVencimento")),
        fornecedor=_primeiro_texto(elementos, "xNome", "RazaoSocial", "razaoSocial"),
        cnpj=formatar_cnpj(_primeiro_texto(elementos, "CNPJ", "Cnpj", "cnpj")),
        descricao=descricao[:900],
        valor_total=_parse_decimal(_primeiro_texto(elementos, "vNF", "ValorServicos", "ValorTotal", "valorTotal")),
    )
    if not dados.numero and not dados.valor_total and not dados.fornecedor:
        raise NotaFiscalXmlError("Não foi possível identificar os dados principais no XML informado.")
    return dados


def _indexar_elementos(raiz):
    elementos = {}
    for elemento in raiz.iter():
        tag = elemento.tag.rsplit("}", 1)[-1]
        texto = (elemento.text or "").strip()
        if texto:
            elementos.setdefault(tag, []).append(texto)
    return elementos


def _primeiro_texto(elementos, *tags):
    for tag in tags:
        valores = elementos.get(tag)
        if valores:
            return valores[0]
    return ""


def _textos(elementos, tag):
    return elementos.get(tag, [])


def _parse_data(valor):
    if not valor:
        return None
    texto = valor.strip()[:10]
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _parse_decimal(valor):
    if not valor:
        return None
    texto = valor.strip().replace(",", ".")
    try:
        return Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _inferir_tipo(elementos):
    if _primeiro_texto(elementos, "ValorServicos", "Discriminacao", "CodigoServico"):
        return "SERVICO"
    return "MATERIAL"
