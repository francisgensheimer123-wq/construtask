from decimal import Decimal


def _calcular_percentual(valor, total):
    if not total:
        return 0
    return round((float(valor) / float(total)) * 100, 1)


def _nivel_resumo_alerta(total):
    if total >= 8:
        return "critico"
    if total >= 4:
        return "alto"
    if total >= 1:
        return "medio"
    return "baixo"


def _grafico_score_operacional(score_operacional):
    componentes = list(score_operacional.get("componentes") or [])
    cores = ["#a61e1e", "#d4a017", "#4f9a2f", "#3f6fd1"]
    fatias = []
    offset = Decimal("0.00")
    for indice, componente in enumerate(componentes):
        maximo = Decimal(str(componente.get("maximo") or 0))
        pontuacao = Decimal(str(componente.get("pontuacao") or 0))
        percentual = Decimal("0.00")
        if maximo:
            percentual = (pontuacao / maximo * Decimal("25.00")).quantize(Decimal("0.01"))
        inicio = offset
        fim = min(Decimal("100.00"), offset + Decimal("25.00"))
        fatias.append(
            {
                "cor": cores[indice % len(cores)],
                "inicio": inicio,
                "fim": fim,
                "percentual_componente": percentual,
                "indice": indice + 1,
                "nome": componente.get("nome"),
                "pontuacao": pontuacao,
                "nivel": componente.get("nivel"),
                "detalhe": componente.get("detalhe"),
            }
        )
        offset = fim
    gradiente = ", ".join(
        f"{item['cor']} {item['inicio']}% {item['fim']}%" for item in fatias
    ) or "#d1d5db 0 100%"
    return {
        "gradiente": gradiente,
        "fatias": fatias,
    }


def _obter_grupos_navegacao():
    return {
        "planejamento": {
            "slug": "planejamento",
            "titulo": "Planejamento",
            "descricao": "Organize o orcamento, acompanhe o cronograma e monitore os riscos da obra em um unico fluxo.",
            "itens": [
                {
                    "titulo": "Plano de Contas",
                    "descricao": "Estruture a EAP, acompanhe o orcado e mantenha as baselines de referencia.",
                    "url_name": "plano_contas_list",
                },
                {
                    "titulo": "Cronograma",
                    "descricao": "Importe, revise e acompanhe as atividades planejadas e realizadas da obra.",
                    "url_name": "plano_fisico_list",
                },
                {
                    "titulo": "Riscos",
                    "descricao": "Registre, trate e acompanhe os riscos operacionais e gerenciais da obra.",
                    "url_name": "risco_list",
                },
                {
                    "titulo": "Alertas Operacionais",
                    "descricao": "Centralize desvios, justificativas e encerramentos dos alertas automaticos da obra.",
                    "url_name": "alerta_operacional_list",
                },
            ],
        },
        "qualidade": {
            "slug": "qualidade",
            "titulo": "Qualidade",
            "descricao": "Concentre o controle documental, as nao conformidades e as evidencias formais da obra.",
            "itens": [
                {
                    "titulo": "Documentos",
                    "descricao": "Controle revisoes, aprovacoes e rastreabilidade dos documentos da obra.",
                    "url_name": "documento_list",
                },
                {
                    "titulo": "Nao Conformidades",
                    "descricao": "Gerencie tratativas, evidencias e encerramentos das ocorrencias de qualidade.",
                    "url_name": "nao_conformidade_list",
                },
                {
                    "titulo": "Central de Evidencias",
                    "descricao": "Acesse rapidamente os comprovantes formais e registros probatorios da operacao.",
                    "url_name": "central_evidencias",
                },
            ],
        },
        "aquisicoes": {
            "slug": "aquisicoes",
            "titulo": "Aquisicoes",
            "descricao": "Administre fornecedores, solicitacoes, cotacoes, ordens e compromissos em uma jornada unica.",
            "itens": [
                {
                    "titulo": "Fornecedores",
                    "descricao": "Cadastre e acompanhe os parceiros que participam da cadeia de suprimentos.",
                    "url_name": "fornecedor_list",
                },
                {
                    "titulo": "Solicitacoes",
                    "descricao": "Abra e acompanhe as demandas de compra originadas pela obra.",
                    "url_name": "solicitacao_compra_list",
                },
                {
                    "titulo": "Cotacoes",
                    "descricao": "Compare propostas e consolide o processo de aquisicao.",
                    "url_name": "cotacao_list",
                },
                {
                    "titulo": "Ordens de Compra",
                    "descricao": "Visualize as ordens emitidas a partir das cotacoes aprovadas.",
                    "url_name": "ordem_compra_list",
                },
            ],
        },
        "financeiro": {
            "slug": "financeiro",
            "titulo": "Financeiro",
            "descricao": "Mantenha contratos, medicoes, notas e projetado financeiro em uma leitura operacional unica.",
            "itens": [
                {
                    "titulo": "Contratos e Pedidos",
                    "descricao": "Acompanhe compromissos, contratos, pedidos e aditivos aprovados.",
                    "url_name": "compromisso_list",
                },
                {
                    "titulo": "Notas Fiscais",
                    "descricao": "Controle emissao, rateio e situacao financeira das notas da obra.",
                    "url_name": "nota_fiscal_list",
                },
                {
                    "titulo": "Medicoes",
                    "descricao": "Acompanhe lancamentos, aprovacoes e valores medidos.",
                    "url_name": "medicao_list",
                },
                {
                    "titulo": "Projecao Financeira",
                    "descricao": "Projete entradas e saidas futuras com base no andamento da obra.",
                    "url_name": "projecao_financeira",
                },
            ],
        },
    }
