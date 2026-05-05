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
            "descricao": "Organize o orçamento, acompanhe o cronograma e monitore os riscos da obra em um único fluxo.",
            "itens": [
                {
                    "titulo": "Plano de Contas",
                    "descricao": "Estruture a EAP, acompanhe o orçado e mantenha as baselines de referência.",
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
            "descricao": "Concentre o controle documental, as não conformidades e as evidências formais da obra.",
            "itens": [
                {
                    "titulo": "Documentos",
                    "descricao": "Controle revisoes, aprovacoes e rastreabilidade dos documentos da obra.",
                    "url_name": "documento_list",
                },
                {
                    "titulo": "Não Conformidades",
                    "descricao": "Gerencie tratativas, evidências e encerramentos das ocorrencias de qualidade.",
                    "url_name": "nao_conformidade_list",
                },
                {
                    "titulo": "Central de Evidências",
                    "descricao": "Acesse rapidamente os comprovantes formais e registros probatorios da operação.",
                    "url_name": "central_evidencias",
                },
            ],
        },
        "aquisicoes": {
            "slug": "aquisicoes",
            "titulo": "Aquisições",
            "descricao": "Administre fornecedores, solicitações, cotações, ordens e compromissos em uma jornada unica.",
            "itens": [
                {
                    "titulo": "Fornecedores",
                    "descricao": "Cadastre e acompanhe os parceiros que participam da cadeia de suprimentos.",
                    "url_name": "fornecedor_list",
                },
                {
                    "titulo": "Solicitações",
                    "descricao": "Abra e acompanhe as demandas de compra originadas pela obra.",
                    "url_name": "solicitacao_compra_list",
                },
                {
                    "titulo": "Cotações",
                    "descricao": "Compare propostas e consolide o processo de aquisição.",
                    "url_name": "cotacao_list",
                },
                {
                    "titulo": "Ordens de Compra",
                    "descricao": "Visualize as ordens emitidas a partir das cotações aprovadas.",
                    "url_name": "ordem_compra_list",
                },
            ],
        },
        "comunicacoes": {
            "slug": "comunicacoes",
            "titulo": "Comunicacoes",
            "descricao": "Conduza pautas, atas e acompanhamentos formais da obra em um fluxo único e rastreavel.",
            "itens": [
                {
                    "titulo": "Reunioes da Obra",
                    "descricao": "Monte pautas, valide atas e acompanhe respostas pendentes do time.",
                    "url_name": "reuniao_comunicacao_list",
                },
                {
                    "titulo": "Alertas Operacionais",
                    "descricao": "Use os alertas como insumo prioritario para as reunioes de acompanhamento.",
                    "url_name": "alerta_operacional_list",
                },
            ],
        },
        "financeiro": {
            "slug": "financeiro",
            "titulo": "Financeiro",
            "descricao": "Mantenha contratos, medições, notas e projetado financeiro em uma leitura operacional unica.",
            "itens": [
                {
                    "titulo": "Contratos e Pedidos",
                    "descricao": "Acompanhe compromissos, contratos, pedidos e aditivos aprovados.",
                    "url_name": "compromisso_list",
                },
                {
                    "titulo": "Notas Fiscais",
                    "descricao": "Controle emissao, rateio e situação financeira das notas da obra.",
                    "url_name": "nota_fiscal_list",
                },
                {
                    "titulo": "Medições",
                    "descricao": "Acompanhe lancamentos, aprovacoes e valores medidos.",
                    "url_name": "medicao_list",
                },
                {
                    "titulo": "Projeção Financeira",
                    "descricao": "Projete entradas e saidas futuras com base no andamento da obra.",
                    "url_name": "projecao_financeira",
                },
            ],
        },
        "relatorios": {
            "slug": "relatorios",
            "titulo": "Relatórios",
            "descricao": "Acesse os consolidadores e visoes executivas mais usados para acompanhamento da obra.",
            "itens": [
                {
                    "titulo": "Dossiê da Obra",
                    "descricao": "Reuna documentos, evidências e registros formais em uma visão consolidada.",
                    "url_name": "dossie_obra",
                },
                {
                    "titulo": "Curva ABC",
                    "descricao": "Análise concentracao de custos e prioridades do orçamento em uma leitura gerencial.",
                    "url_name": "curva_abc",
                },
                {
                    "titulo": "Dashboard de Alertas",
                    "descricao": "Visualize desvios operacionais e criticidades da obra em painel executivo.",
                    "url_name": "alerta_operacional_dashboard",
                },
            ],
        },
        "juridico": {
            "slug": "juridico",
            "titulo": "Juridico",
            "descricao": "Centralize governanca legal, politicas publicas e orientacoes formais do ambiente SaaS.",
            "itens": [
                {
                    "titulo": "Governanca LGPD",
                    "descricao": "Consulte os controles e evidências de governanca de dados do sistema.",
                    "url_name": "lgpd_governanca",
                },
                {
                    "titulo": "Política de Privacidade",
                    "descricao": "Acesse o documento institucional de privacidade disponibilizado aos usuarios.",
                    "url_name": "politica_privacidade",
                },
                {
                    "titulo": "Termos de Uso",
                    "descricao": "Consulte os termos de uso vigentes aplicaveis ao ambiente e aos usuarios.",
                    "url_name": "termos_uso",
                },
            ],
        },
    }
