import re
import unicodedata


MOJIBAKE_MARKERS = ("Ã", "Â", "â", "�")

TERM_REPLACEMENTS = {
    "acao": "ação",
    "acoes": "ações",
    "analise": "análise",
    "aprovacao": "aprovação",
    "aprovacoes": "aprovações",
    "ampliacao": "ampliação",
    "arquitetonico": "arquitetônico",
    "arquitetonicos": "arquitetônicos",
    "ceramico": "cerâmico",
    "ceramicos": "cerâmicos",
    "comparacao": "comparação",
    "concluida": "concluída",
    "concluidas": "concluídas",
    "contratacao": "contratação",
    "contratacoes": "contratações",
    "cotacao": "cotação",
    "cotacoes": "cotações",
    "descricao": "descrição",
    "duracao": "duração",
    "fabricacao": "fabricação",
    "historico": "histórico",
    "medicao": "medição",
    "medicoes": "medições",
    "nao": "não",
    "orcamento": "orçamento",
    "orcamentos": "orçamentos",
    "projecao": "projeção",
    "revisao": "revisão",
    "revisoes": "revisões",
    "servico": "serviço",
    "servicos": "serviços",
    "situacao": "situação",
    "solicitacao": "solicitação",
    "solicitacoes": "solicitações",
    "tecnica": "técnica",
    "tecnicas": "técnicas",
    "tecnico": "técnico",
    "tecnicos": "técnicos",
    "titulo": "título",
    "unico": "único",
    "versao": "versão",
    "verificacao": "verificação",
}

TEXT_NORMALIZATION_TARGETS = {
    "Construtask.PlanoContas": ("descricao", "unidade"),
    "Construtask.Obra": ("nome", "cliente", "responsavel", "descricao"),
    "Construtask.AnexoOperacional": ("descricao",),
    "Construtask.Compromisso": ("descricao", "fornecedor", "responsavel", "torre", "bloco", "etapa", "parecer_aprovacao"),
    "Construtask.Medicao": ("descricao", "fornecedor", "responsavel", "torre", "bloco", "etapa", "parecer_aprovacao"),
    "Construtask.NotaFiscal": ("fornecedor", "descricao", "torre", "bloco", "etapa"),
    "Construtask.OrcamentoBaseline": ("descricao", "parecer_aprovacao"),
    "Construtask.Documento": ("processo", "codigo_documento", "titulo"),
    "Construtask.DocumentoRevisao": ("parecer",),
    "Construtask.NaoConformidade": ("descricao", "causa", "acao_corretiva"),
    "Construtask.Fornecedor": ("razao_social", "nome_fantasia", "contato"),
    "Construtask.SolicitacaoCompra": ("titulo", "descricao", "observacoes"),
    "Construtask.SolicitacaoCompraItem": ("descricao_tecnica", "unidade"),
    "Construtask.Cotacao": ("observacoes", "justificativa_escolha"),
    "Construtask.CotacaoAnexo": ("descricao",),
    "Construtask.OrdemCompra": ("descricao",),
}


def _mojibake_score(texto):
    return sum(texto.count(marker) for marker in MOJIBAKE_MARKERS)


def corrigir_mojibake(texto):
    if texto is None:
        return texto
    texto = str(texto)
    candidates = {texto}
    best = texto
    best_score = _mojibake_score(texto)

    for _ in range(2):
        new_candidates = set(candidates)
        for candidate in list(candidates):
            for encoding in ("latin1", "cp1252"):
                try:
                    new_candidates.add(candidate.encode(encoding, "ignore").decode("utf-8", "ignore"))
                except Exception:
                    pass
        candidates = new_candidates
        for candidate in candidates:
            score = _mojibake_score(candidate)
            if score < best_score:
                best = candidate
                best_score = score
    return best


def _match_case(original, replacement):
    if original.isupper():
        return replacement.upper()
    if original.istitle():
        return replacement.capitalize()
    return replacement


def _replace_known_terms(texto):
    result = texto
    for source, target in TERM_REPLACEMENTS.items():
        pattern = re.compile(rf"\b{re.escape(source)}\b", re.IGNORECASE)
        result = pattern.sub(lambda match: _match_case(match.group(0), target), result)
    return result


def normalizar_texto_cadastral(valor):
    if valor is None:
        return valor
    texto = str(valor)
    texto = corrigir_mojibake(texto)
    texto = texto.replace("\xa0", " ")
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = _replace_known_terms(texto)
    return unicodedata.normalize("NFC", texto)
