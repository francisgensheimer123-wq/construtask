from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import F, Max, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .importacao_cronograma import MapeamentoService
from .models import (
    AlertaOperacional,
    AlertaOperacionalHistorico,
    Compromisso,
    ExecucaoRegraOperacional,
    Medicao,
    NotaFiscal,
    NotaFiscalCentroCusto,
    ParametroAlertaEmpresa,
)
from .models_aquisicoes import SolicitacaoCompra
from .models_planejamento import MapaCorrespondencia, PlanoFisico, PlanoFisicoItem
from .models_qualidade import NaoConformidade
from .models_risco import Risco


CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS = "PLAN-SUP-001"
TITULO_ALERTA_PLANEJAMENTO_SUPRIMENTOS = "Atividade planejada sem solicitacao de compra antecipada"

CODIGO_ALERTA_CONTRATO_SEM_MEDICAO = "CONT-MED-001"
TITULO_ALERTA_CONTRATO_SEM_MEDICAO = "Contrato ativo sem medicao registrada"

CODIGO_ALERTA_MEDICAO_SEM_NOTA = "MED-NF-001"
TITULO_ALERTA_MEDICAO_SEM_NOTA = "Medicao sem nota fiscal vinculada"

CODIGO_ALERTA_NOTA_SEM_RATEIO = "NF-RAT-001"
TITULO_ALERTA_NOTA_SEM_RATEIO = "Nota fiscal sem rateio completo"

CODIGO_ALERTA_RISCO_VENCIDO = "RISK-DUE-001"
TITULO_ALERTA_RISCO_VENCIDO = "Risco com prazo vencido sem tratamento concluido"

CODIGO_ALERTA_NC_SEM_EVOLUCAO = "NC-EVO-001"
TITULO_ALERTA_NC_SEM_EVOLUCAO = "Nao conformidade sem evolucao recente"

CODIGO_ALERTA_ATIVIDADE_SEM_AVANCO = "PLAN-PROG-001"
TITULO_ALERTA_ATIVIDADE_SEM_AVANCO = "Atividade iniciada sem avanço fisico registrado"

CODIGO_ALERTA_DESVIO_PRAZO = "PLAN-PROG-002"
TITULO_ALERTA_DESVIO_PRAZO = "Avanco fisico abaixo do tempo decorrido"

CODIGO_ALERTA_ESTOURO_PRAZO = "PLAN-PROG-003"
TITULO_ALERTA_ESTOURO_PRAZO = "Projecao de termino alem do prazo da obra"

CODIGO_ALERTA_DESVIO_CUSTO = "COST-PROG-001"
TITULO_ALERTA_DESVIO_CUSTO = "Custo realizado acima do previsto proporcional"

CODIGO_ALERTA_CUSTO_SEM_AVANCO = "COST-PROG-002"
TITULO_ALERTA_CUSTO_SEM_AVANCO = "Lancamento de custo sem avanço fisico correspondente"

CODIGO_ALERTA_COMPROMISSO_ACIMA_ORCADO = "COST-BUD-001"
TITULO_ALERTA_COMPROMISSO_ACIMA_ORCADO = "Compromisso acima do valor orcado"

CODIGO_ALERTA_MULTIPLOS_RISCOS = "RISK-ACC-001"
TITULO_ALERTA_MULTIPLOS_RISCOS = "Acumulo de riscos operacionais nao tratados"

CODIGO_ALERTA_DESVIO_COMBINADO = "COMB-001"
TITULO_ALERTA_DESVIO_COMBINADO = "Desvio simultaneo de prazo e custo na atividade"


SEVERIDADE_ORDEM = {"CRITICA": 4, "ALTA": 3, "MEDIA": 2, "BAIXA": 1}


def _formatar_parametro_alerta(valor, tipo):
    if tipo == "dias":
        return f"{valor} dia(s)"
    if tipo == "percentual":
        return f"{valor}%"
    if tipo == "moeda":
        return f"R$ {valor}"
    return str(valor)


def _json_safe(valor):
    if isinstance(valor, dict):
        return {chave: _json_safe(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple, set)):
        return [_json_safe(item) for item in valor]
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


CATALOGO_REGRAS_OPERACIONAIS = [
    {
        "codigo": CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
        "titulo": TITULO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
        "frente": "Suprimentos e mobilizacao",
        "gatilho": "Atividade futura sem solicitacao compativel vinculada.",
        "impacto": "Antecipa ruptura de suprimentos e atraso de mobilizacao.",
        "recomendacao": "Antecipar solicitacao, cotacao e contratacao das frentes proximas.",
        "tipo_parametro": "dias",
        "resolver_valor": lambda parametros: parametros.planejamento_suprimentos_janela_dias,
    },
    {
        "codigo": CODIGO_ALERTA_CONTRATO_SEM_MEDICAO,
        "titulo": TITULO_ALERTA_CONTRATO_SEM_MEDICAO,
        "frente": "Governanca contratual",
        "gatilho": "Contrato ativo sem medicao apos a tolerancia definida.",
        "impacto": "Mostra perda de ritmo contratual e falta de lastro de execucao.",
        "recomendacao": "Cobrar medicao, status fisico e evidencia de execucao do contrato.",
        "tipo_parametro": "dias",
        "resolver_valor": lambda parametros: parametros.contrato_sem_medicao_dias,
    },
    {
        "codigo": CODIGO_ALERTA_MEDICAO_SEM_NOTA,
        "titulo": TITULO_ALERTA_MEDICAO_SEM_NOTA,
        "frente": "Faturamento e documentos",
        "gatilho": "Medicao aprovada sem nota fiscal dentro do prazo operacional.",
        "impacto": "Expõe gargalo entre execucao, faturamento e documentacao fiscal.",
        "recomendacao": "Cobrar emissao da nota e amarrar o faturamento ao fluxo documental.",
        "tipo_parametro": "dias",
        "resolver_valor": lambda parametros: parametros.medicao_sem_nota_dias,
    },
    {
        "codigo": CODIGO_ALERTA_NOTA_SEM_RATEIO,
        "titulo": TITULO_ALERTA_NOTA_SEM_RATEIO,
        "frente": "Custos e apropriacao",
        "gatilho": "Percentual pendente de rateio acima do minimo definido.",
        "impacto": "Evita custo financeiro sem apropriacao completa na obra.",
        "recomendacao": "Completar rateio e conferir centro de custo antes do fechamento.",
        "tipo_parametro": "percentual",
        "resolver_valor": lambda parametros: parametros.nota_sem_rateio_percentual_minimo,
    },
    {
        "codigo": CODIGO_ALERTA_RISCO_VENCIDO,
        "titulo": TITULO_ALERTA_RISCO_VENCIDO,
        "frente": "Riscos",
        "gatilho": "Prazo de tratamento vencido acima da tolerancia.",
        "impacto": "Destaca riscos sem acao efetiva antes que virem problema real.",
        "recomendacao": "Atualizar plano de resposta, responsavel e nova data-meta.",
        "tipo_parametro": "dias",
        "resolver_valor": lambda parametros: parametros.risco_vencido_tolerancia_dias,
    },
    {
        "codigo": CODIGO_ALERTA_NC_SEM_EVOLUCAO,
        "titulo": TITULO_ALERTA_NC_SEM_EVOLUCAO,
        "frente": "Qualidade",
        "gatilho": "Nao conformidade aberta sem nova movimentacao.",
        "impacto": "Reforca governanca de qualidade e encerramento com evidencia.",
        "recomendacao": "Atualizar tratativa, evidencias e responsavel de encerramento.",
        "tipo_parametro": "dias",
        "resolver_valor": lambda parametros: parametros.nao_conformidade_sem_evolucao_dias,
    },
    {
        "codigo": CODIGO_ALERTA_ATIVIDADE_SEM_AVANCO,
        "titulo": TITULO_ALERTA_ATIVIDADE_SEM_AVANCO,
        "frente": "Prazo",
        "gatilho": "Atividade com inicio atingido sem progresso acima da tolerancia.",
        "impacto": "Aponta atraso imediato no cronograma e ajuda a agir cedo.",
        "recomendacao": "Reprogramar frente, reforcar recursos e validar apontamento fisico.",
        "tipo_parametro": "dias",
        "resolver_valor": lambda parametros: parametros.atividade_sem_avanco_tolerancia_dias,
    },
    {
        "codigo": CODIGO_ALERTA_DESVIO_PRAZO,
        "titulo": TITULO_ALERTA_DESVIO_PRAZO,
        "frente": "Prazo",
        "gatilho": "Desvio percentual de prazo apos percentual minimo previsto.",
        "impacto": "Mostra perda de ritmo antes do atraso definitivo da obra.",
        "recomendacao": "Ativar plano de recuperacao do cronograma da atividade.",
        "tipo_parametro": "texto",
        "resolver_valor": lambda parametros: (
            f"Minimo {parametros.desvio_prazo_percentual_minimo_previsto}% / "
            f"tolerancia {parametros.desvio_prazo_tolerancia_percentual}%"
        ),
    },
    {
        "codigo": CODIGO_ALERTA_ESTOURO_PRAZO,
        "titulo": TITULO_ALERTA_ESTOURO_PRAZO,
        "frente": "Prazo",
        "gatilho": "Data estimada de termino excede a folga de prazo definida.",
        "impacto": "Mostra risco de estouro global de prazo com base no ritmo atual.",
        "recomendacao": "Aprovar plano de recuperacao e rever sequenciamento da obra.",
        "tipo_parametro": "dias",
        "resolver_valor": lambda parametros: parametros.estouro_prazo_tolerancia_dias,
    },
    {
        "codigo": CODIGO_ALERTA_DESVIO_CUSTO,
        "titulo": TITULO_ALERTA_DESVIO_CUSTO,
        "frente": "Custo",
        "gatilho": "Custo acima da tolerancia percentual definida.",
        "impacto": "Ajuda a detectar estouro de custo antes de contaminar a obra inteira.",
        "recomendacao": "Revisar produtividade, contratacoes e apropriacoes do item.",
        "tipo_parametro": "percentual",
        "resolver_valor": lambda parametros: parametros.desvio_custo_tolerancia_percentual,
    },
    {
        "codigo": CODIGO_ALERTA_CUSTO_SEM_AVANCO,
        "titulo": TITULO_ALERTA_CUSTO_SEM_AVANCO,
        "frente": "Custo x execucao",
        "gatilho": "Valor realizado sem avanço fisico acima do minimo definido.",
        "impacto": "Identifica custo sem lastro fisico e possivel retrabalho.",
        "recomendacao": "Conferir apontamento fisico, competencia e documentacao da despesa.",
        "tipo_parametro": "moeda",
        "resolver_valor": lambda parametros: parametros.custo_sem_avanco_valor_minimo,
    },
    {
        "codigo": CODIGO_ALERTA_COMPROMISSO_ACIMA_ORCADO,
        "titulo": TITULO_ALERTA_COMPROMISSO_ACIMA_ORCADO,
        "frente": "Orcamento e compras",
        "gatilho": "Compromisso acima do orcado somando a tolerancia percentual.",
        "impacto": "Protege o orcamento contra contratacao ou compra acima do previsto.",
        "recomendacao": "Rever alcada, baseline e justificativa de contratacao.",
        "tipo_parametro": "percentual",
        "resolver_valor": lambda parametros: parametros.compromisso_acima_orcado_tolerancia_percentual,
    },
    {
        "codigo": CODIGO_ALERTA_MULTIPLOS_RISCOS,
        "titulo": TITULO_ALERTA_MULTIPLOS_RISCOS,
        "frente": "Riscos",
        "gatilho": "Quantidade de riscos ativos acima do limite e do nivel critico.",
        "impacto": "Evidencia perda sistêmica de controle na obra.",
        "recomendacao": "Executar revisao extraordinaria da matriz de riscos e dos responsaveis.",
        "tipo_parametro": "texto",
        "resolver_valor": lambda parametros: (
            f"Minimo {parametros.acumulo_riscos_quantidade_minima} / "
            f"critico {parametros.acumulo_riscos_quantidade_critica}"
        ),
    },
    {
        "codigo": CODIGO_ALERTA_DESVIO_COMBINADO,
        "titulo": TITULO_ALERTA_DESVIO_COMBINADO,
        "frente": "Prazo x custo",
        "gatilho": "Atividade com desvio simultaneo de prazo e custo.",
        "impacto": "Mostra perda conjunta de desempenho fisico-financeiro.",
        "recomendacao": "Executar plano integrado de recuperacao, suprimento e produtividade.",
        "tipo_parametro": "texto",
        "resolver_valor": lambda parametros: "Correlacao automatica entre regras de prazo e custo",
    },
]


def _parametros_alerta(obra):
    return ParametroAlertaEmpresa.obter_ou_criar(obra.empresa)


def catalogo_alertas_empresa(empresa=None, *, incluir_score=True):
    parametros = ParametroAlertaEmpresa.obter_ou_criar(empresa)
    catalogo = []
    for regra in CATALOGO_REGRAS_OPERACIONAIS:
        valor = regra["resolver_valor"](parametros)
        tipo_parametro = regra.get("tipo_parametro") or "texto"
        if tipo_parametro != "texto":
            valor = _formatar_parametro_alerta(valor, tipo_parametro)
        catalogo.append(
            {
                "codigo": regra["codigo"],
                "titulo": regra["titulo"],
                "frente": regra["frente"],
                "gatilho": regra["gatilho"],
                "impacto": regra["impacto"],
                "acao_recomendada": regra["recomendacao"],
                "valor_atual": valor,
            }
        )

    if incluir_score:
        catalogo.extend(
            [
                {
                    "codigo": "ALERT-SLA-001",
                    "titulo": "Alerta sem workflow recente",
                    "frente": "Governanca do tratamento",
                    "gatilho": "Alerta sem nova movimentacao acima da tolerancia operacional.",
                    "impacto": "Diferencia alerta ativo de alerta negligenciado.",
                    "acao_recomendada": "Cobrar responsavel e registrar avancos de tratamento.",
                    "valor_atual": _formatar_parametro_alerta(parametros.alerta_sem_workflow_dias, "dias"),
                },
                {
                    "codigo": "ALERT-SLA-002",
                    "titulo": "Prazo de solucao do alerta estourado",
                    "frente": "Governanca do tratamento",
                    "gatilho": "Alerta aberto alem do prazo padrao de solucao da empresa.",
                    "impacto": "Leva envelhecimento do alerta para a leitura executiva da obra.",
                    "acao_recomendada": "Escalar alerta em atraso e revisar prazo de solucao.",
                    "valor_atual": _formatar_parametro_alerta(parametros.alerta_prazo_solucao_dias, "dias"),
                },
            ]
        )

    return catalogo


def obter_regra_operacional(codigo_regra, empresa=None):
    return next((item for item in catalogo_alertas_empresa(empresa, incluir_score=True) if item["codigo"] == codigo_regra), None)


def _plano_referencia_obra(obra):
    plano = (
        PlanoFisico.objects.filter(obra=obra, is_baseline=True)
        .order_by("-versao", "-created_at")
        .first()
    )
    if plano:
        return plano
    return (
        PlanoFisico.objects.filter(obra=obra, status__in=["ATIVO", "BASELINE"])
        .order_by("-created_at")
        .first()
    )


def _severidade_por_proximidade(dias_para_inicio):
    if dias_para_inicio <= 15:
        return "CRITICA"
    if dias_para_inicio <= 30:
        return "ALTA"
    return "MEDIA"


def _severidade_por_idade(dias):
    if dias >= 30:
        return "CRITICA"
    if dias >= 15:
        return "ALTA"
    return "MEDIA"


def _ordenar_alertas(alertas):
    return sorted(
        alertas,
        key=lambda alerta: (
            -SEVERIDADE_ORDEM.get(alerta.severidade, 0),
            alerta.data_referencia or timezone.localdate(),
            alerta.criado_em,
        ),
    )


def _registrar_execucao_regra(alerta, *, obra, codigo_regra, referencia="", resultado, contexto=None):
    contexto_serializado = _json_safe(contexto or {})
    ExecucaoRegraOperacional.objects.create(
        obra=obra,
        alerta=alerta,
        codigo_regra=codigo_regra,
        referencia=referencia,
        entidade_tipo=getattr(alerta, "entidade_tipo", "") if alerta else contexto_serializado.get("entidade_tipo", ""),
        entidade_id=getattr(alerta, "entidade_id", None) if alerta else contexto_serializado.get("entidade_id"),
        severidade=getattr(alerta, "severidade", "") if alerta else contexto_serializado.get("severidade", ""),
        status_alerta=getattr(alerta, "status", "") if alerta else contexto_serializado.get("status", ""),
        resultado=resultado,
        contexto=contexto_serializado,
    )


def _centros_custo_item(item):
    centros = []
    vistos = set()

    for mapeamento in (
        MapaCorrespondencia.objects.filter(
            plano_fisico_item=item,
            status="ATIVO",
            plano_contas__isnull=False,
        )
        .select_related("plano_contas")
        .order_by("plano_contas__codigo", "id")
    ):
        if mapeamento.plano_contas_id in vistos:
            continue
        vistos.add(mapeamento.plano_contas_id)
        centros.append({"plano_contas": mapeamento.plano_contas, "origem": "MAPEAMENTO"})

    if item.plano_contas_id and item.plano_contas_id not in vistos:
        centros.append({"plano_contas": item.plano_contas, "origem": "VINCULO_DIRETO"})

    return centros


def _sync_registros_alerta(obra, codigo_regra, referencias_ativas, registros):
    alertas_ativos = []
    for referencia, payload in registros.items():
        referencia_str = str(referencia)
        referencias_ativas.add(referencia_str)
        alerta = AlertaOperacional.objects.filter(
            obra=obra,
            codigo_regra=codigo_regra,
            referencia=referencia_str,
        ).first()
        created = alerta is None
        status_anterior = alerta.status if alerta else ""
        payload_aplicado = dict(payload)

        if created:
            alerta = AlertaOperacional.objects.create(
                obra=obra,
                codigo_regra=codigo_regra,
                referencia=referencia_str,
                **payload_aplicado,
            )
            AlertaOperacionalHistorico.objects.create(
                alerta=alerta,
                acao="CRIACAO",
                status_novo=alerta.status,
                observacao=alerta.descricao,
            )
            _registrar_execucao_regra(
                alerta,
                obra=obra,
                codigo_regra=codigo_regra,
                referencia=referencia_str,
                resultado="CRIADO",
                contexto=payload_aplicado,
            )
        else:
            if status_anterior == "JUSTIFICADO" and payload_aplicado.get("status") == "ABERTO":
                payload_aplicado["status"] = "JUSTIFICADO"
            campos_alterados = []
            for campo, valor in payload_aplicado.items():
                if getattr(alerta, campo) != valor:
                    setattr(alerta, campo, valor)
                    campos_alterados.append(campo)
            if campos_alterados:
                alerta.save(update_fields=campos_alterados + ["atualizado_em"])
                resultado = "REATIVADO" if status_anterior == "ENCERRADO" and alerta.status != "ENCERRADO" else "ATUALIZADO"
                _registrar_execucao_regra(
                    alerta,
                    obra=obra,
                    codigo_regra=codigo_regra,
                    referencia=referencia_str,
                    resultado=resultado,
                    contexto=payload_aplicado,
                )
                if status_anterior == "ENCERRADO" and alerta.status != "ENCERRADO":
                    AlertaOperacionalHistorico.objects.create(
                        alerta=alerta,
                        acao="REABERTURA",
                        status_anterior=status_anterior,
                        status_novo=alerta.status,
                        observacao="Alerta reaberto automaticamente por nova ocorrencia da regra.",
                    )
        alertas_ativos.append(alerta)

    alertas_para_encerrar = list(
        AlertaOperacional.objects.filter(
            obra=obra,
            codigo_regra=codigo_regra,
            status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"],
        )
        .exclude(referencia__in=referencias_ativas)
        .select_related("obra")
    )
    for alerta in alertas_para_encerrar:
        status_anterior = alerta.status
        alerta.status = "ENCERRADO"
        alerta.encerrado_em = timezone.now()
        alerta.save(update_fields=["status", "encerrado_em", "atualizado_em"])
        AlertaOperacionalHistorico.objects.create(
            alerta=alerta,
            acao="ENCERRAMENTO",
            status_anterior=status_anterior,
            status_novo="ENCERRADO",
            observacao="Alerta encerrado automaticamente porque a regra deixou de encontrar ocorrencia ativa.",
        )
        _registrar_execucao_regra(
            alerta,
            obra=obra,
            codigo_regra=codigo_regra,
            referencia=alerta.referencia,
            resultado="ENCERRADO",
            contexto={"motivo": "regra_sem_ocorrencia_ativa"},
        )

    return alertas_ativos


def atualizar_status_alerta(
    alerta,
    *,
    novo_status,
    usuario=None,
    observacao="",
    responsavel=None,
    acao_historico=None,
    prazo_solucao_em=None,
):
    status_anterior = alerta.status
    alerta.status = novo_status
    alerta.observacao_status = observacao or ""
    if responsavel is not None:
        alerta.responsavel = responsavel
    if prazo_solucao_em is not None:
        alerta.prazo_solucao_em = prazo_solucao_em
    alerta.ultima_acao_por = usuario
    alerta.ultima_acao_em = timezone.now()
    if novo_status == "ENCERRADO":
        alerta.encerrado_em = timezone.now()
        alerta.prazo_solucao_em = None
    elif alerta.encerrado_em and novo_status != "ENCERRADO":
        alerta.encerrado_em = None
    alerta.save(
        update_fields=[
            "status",
            "observacao_status",
            "responsavel",
            "prazo_solucao_em",
            "ultima_acao_por",
            "ultima_acao_em",
            "encerrado_em",
            "atualizado_em",
        ]
    )
    AlertaOperacionalHistorico.objects.create(
        alerta=alerta,
        usuario=usuario,
        acao=acao_historico or "TRATAMENTO",
        status_anterior=status_anterior,
        status_novo=novo_status,
        observacao=observacao or "",
    )
    return alerta


def _itens_folha_plano_referencia(obra):
    plano = _plano_referencia_obra(obra)
    if not plano:
        return None, []
    itens = list(
        PlanoFisicoItem.objects.filter(plano=plano, filhos__isnull=True)
        .select_related("plano_contas")
        .order_by("data_inicio_prevista", "sort_order", "id")
    )
    return plano, itens


def sincronizar_alertas_planejamento_suprimentos(obra):
    """
    Regra PLAN-SUP-001:
    atividade dos proximos 60 dias sem solicitacao de compra compativel.
    """
    plano = _plano_referencia_obra(obra)
    parametros = _parametros_alerta(obra)
    hoje = timezone.localdate()
    limite = hoje + timedelta(days=parametros.planejamento_suprimentos_janela_dias)
    referencias_ativas = set()
    registros = {}

    if not plano:
        AlertaOperacional.objects.filter(
            obra=obra,
            codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
            status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"],
        ).update(status="ENCERRADO", encerrado_em=timezone.now())
        return []

    itens = (
        PlanoFisicoItem.objects.select_related("plano_contas")
        .filter(
            plano=plano,
            filhos__isnull=True,
            data_inicio_prevista__gte=hoje,
            data_inicio_prevista__lte=limite,
            percentual_concluido__lt=100,
        )
        .order_by("data_inicio_prevista", "sort_order", "id")
    )

    analise = MapeamentoService.analisar_vinculos(plano)
    centros_ids = {
        eap.pk
        for item in itens
        for eap in analise["item_to_eaps"].get(item.pk, [])
    }
    totais_realizados_eap = {}
    if centros_ids:
        totais_realizados_eap = dict(
            NotaFiscalCentroCusto.objects.filter(
                nota_fiscal__obra=obra,
                nota_fiscal__status__in=["LANCADA", "CONFERIDA", "PAGA"],
                centro_custo_id__in=centros_ids,
            ).values_list("centro_custo_id").annotate(total=Coalesce(Sum("valor"), Decimal("0.00")))
        )

    for item in itens:
        centros = _centros_custo_item(item)
        if item.is_marco and not centros:
            continue
        for centro_info in centros:
            centro = centro_info["plano_contas"]
            existe_solicitacao = SolicitacaoCompra.objects.filter(
                obra=obra,
                plano_contas=centro,
                status__in=["RASCUNHO", "APROVADA", "COTANDO"],
            ).exists()
            if existe_solicitacao:
                continue

            dias_para_inicio = max((item.data_inicio_prevista - hoje).days, 0)
            referencia = f"{item.pk}:{centro.pk}"
            evidencia = {
                "plano_numero": plano.numero or "",
                "atividade_id": item.pk,
                "codigo_atividade": item.codigo_atividade,
                "atividade": item.atividade,
                "data_inicio_prevista": item.data_inicio_prevista.strftime("%d/%m/%Y") if item.data_inicio_prevista else "",
                "dias_para_inicio": dias_para_inicio,
                "centro_custo_id": centro.pk,
                "centro_custo_codigo": centro.codigo,
                "centro_custo_descricao": centro.descricao,
                "origem_vinculo": centro_info["origem"],
                "mensagem": "Nenhuma solicitacao de compra compativel encontrada para o centro de custo vinculado.",
            }
            registros[referencia] = {
                "titulo": TITULO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
                "descricao": f"A atividade {item.codigo_atividade} - {item.atividade} inicia em {dias_para_inicio} dia(s) e nao possui solicitacao de compra compativel para o centro de custo {centro.codigo} - {centro.descricao}.",
                "severidade": _severidade_por_proximidade(dias_para_inicio),
                "status": "ABERTO",
                "entidade_tipo": "PlanoFisicoItem",
                "entidade_id": item.pk,
                "evidencias": evidencia,
                "data_referencia": item.data_inicio_prevista,
                "encerrado_em": None,
            }

    return _sync_registros_alerta(
        obra,
        CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
        referencias_ativas,
        registros,
    )


def sincronizar_alertas_contrato_sem_medicao(obra):
    parametros = _parametros_alerta(obra)
    hoje = timezone.localdate()
    referencias_ativas = set()
    registros = {}

    contratos = (
        Compromisso.objects.filter(
            obra=obra,
            tipo="CONTRATO",
            status__in=["APROVADO", "EM_EXECUCAO"],
            medicoes__isnull=True,
        )
        .select_related("obra")
        .distinct()
        .order_by("data_prevista_inicio", "data_assinatura", "numero")
    )

    for contrato in contratos:
        data_base = contrato.data_prevista_inicio or contrato.data_assinatura
        if not data_base:
            continue
        dias_sem_medicao = max((hoje - data_base).days, 0)
        if dias_sem_medicao < parametros.contrato_sem_medicao_dias:
            continue
        referencia = str(contrato.pk)
        registros[referencia] = {
            "titulo": TITULO_ALERTA_CONTRATO_SEM_MEDICAO,
            "descricao": f"O contrato {contrato.numero} esta ativo ha {dias_sem_medicao} dia(s) sem medicao registrada.",
            "severidade": _severidade_por_idade(dias_sem_medicao),
            "status": "ABERTO",
            "entidade_tipo": "Compromisso",
            "entidade_id": contrato.pk,
            "evidencias": {
                "contrato_numero": contrato.numero,
                "fornecedor": contrato.fornecedor,
                "data_base": data_base.strftime("%d/%m/%Y"),
                "dias_sem_medicao": dias_sem_medicao,
            },
            "data_referencia": data_base,
            "encerrado_em": None,
        }

    return _sync_registros_alerta(obra, CODIGO_ALERTA_CONTRATO_SEM_MEDICAO, referencias_ativas, registros)


def sincronizar_alertas_medicao_sem_nota(obra):
    parametros = _parametros_alerta(obra)
    hoje = timezone.localdate()
    referencias_ativas = set()
    registros = {}

    medicoes = (
        Medicao.objects.filter(
            obra=obra,
            status__in=["CONFERIDA", "APROVADA", "FATURADA"],
            notas_fiscais__isnull=True,
        )
        .select_related("contrato")
        .distinct()
        .order_by("data_medicao", "numero_da_medicao")
    )

    for medicao in medicoes:
        data_base = medicao.data_medicao
        dias_sem_nota = max((hoje - data_base).days, 0)
        if dias_sem_nota < parametros.medicao_sem_nota_dias:
            continue
        referencia = str(medicao.pk)
        registros[referencia] = {
            "titulo": TITULO_ALERTA_MEDICAO_SEM_NOTA,
            "descricao": f"A medicao {medicao.numero_da_medicao} esta em {medicao.get_status_display()} ha {dias_sem_nota} dia(s) sem nota fiscal vinculada.",
            "severidade": _severidade_por_idade(dias_sem_nota),
            "status": "ABERTO",
            "entidade_tipo": "Medicao",
            "entidade_id": medicao.pk,
            "evidencias": {
                "medicao_numero": medicao.numero_da_medicao,
                "contrato_numero": medicao.contrato.numero if medicao.contrato_id else "",
                "status": medicao.get_status_display(),
                "data_medicao": data_base.strftime("%d/%m/%Y"),
                "dias_sem_nota": dias_sem_nota,
            },
            "data_referencia": data_base,
            "encerrado_em": None,
        }

    return _sync_registros_alerta(obra, CODIGO_ALERTA_MEDICAO_SEM_NOTA, referencias_ativas, registros)


def sincronizar_alertas_nota_sem_rateio(obra):
    parametros = _parametros_alerta(obra)
    referencias_ativas = set()
    registros = {}

    notas = (
        NotaFiscal.objects.filter(obra=obra)
        .annotate(rateio_total=Coalesce(Sum("centros_custo__valor"), Value(Decimal("0.00"))))
        .filter(rateio_total__lt=F("valor_total"))
        .order_by("-data_emissao", "-id")
    )

    for nota in notas:
        faltante = (nota.valor_total or Decimal("0.00")) - (nota.rateio_total or Decimal("0.00"))
        if faltante <= Decimal("0.00"):
            continue
        percentual_pendente = (faltante / nota.valor_total * Decimal("100")) if nota.valor_total else Decimal("0.00")
        if percentual_pendente < Decimal(str(parametros.nota_sem_rateio_percentual_minimo)):
            continue
        referencia = str(nota.pk)
        registros[referencia] = {
            "titulo": TITULO_ALERTA_NOTA_SEM_RATEIO,
            "descricao": f"A nota fiscal {nota.numero} possui rateio pendente de {faltante.quantize(Decimal('0.01'))}.",
            "severidade": "CRITICA" if percentual_pendente >= Decimal("50.00") else "ALTA",
            "status": "ABERTO",
            "entidade_tipo": "NotaFiscal",
            "entidade_id": nota.pk,
            "evidencias": {
                "nota_numero": nota.numero,
                "fornecedor": nota.fornecedor,
                "data_emissao": nota.data_emissao.strftime("%d/%m/%Y") if nota.data_emissao else "",
                "valor_total": str(nota.valor_total or Decimal("0.00")),
                "valor_rateado": str(nota.rateio_total or Decimal("0.00")),
                "valor_pendente": str(faltante.quantize(Decimal("0.01"))),
            },
            "data_referencia": nota.data_emissao,
            "encerrado_em": None,
        }

    return _sync_registros_alerta(obra, CODIGO_ALERTA_NOTA_SEM_RATEIO, referencias_ativas, registros)


def sincronizar_alertas_risco_vencido(obra):
    parametros = _parametros_alerta(obra)
    hoje = timezone.localdate()
    referencias_ativas = set()
    registros = {}

    riscos = (
        Risco.objects.filter(
            obra=obra,
            data_meta_tratamento__lt=hoje - timedelta(days=parametros.risco_vencido_tolerancia_dias),
        )
        .exclude(status__in=["MITIGADO", "FECHADO", "CANCELADO"])
        .select_related("responsavel")
        .order_by("data_meta_tratamento", "-nivel")
    )

    for risco in riscos:
        dias_vencido = max((hoje - risco.data_meta_tratamento).days, 0)
        referencia = str(risco.pk)
        registros[referencia] = {
            "titulo": TITULO_ALERTA_RISCO_VENCIDO,
            "descricao": f"O risco {risco.codigo} - {risco.titulo} esta com prazo vencido ha {dias_vencido} dia(s) e segue em {risco.get_status_display()}.",
            "severidade": "CRITICA" if risco.nivel > 15 else _severidade_por_idade(dias_vencido),
            "status": "ABERTO",
            "entidade_tipo": "Risco",
            "entidade_id": risco.pk,
            "evidencias": {
                "risco_codigo": risco.codigo,
                "titulo": risco.titulo,
                "responsavel": str(risco.responsavel) if risco.responsavel else "",
                "data_meta_tratamento": risco.data_meta_tratamento.strftime("%d/%m/%Y") if risco.data_meta_tratamento else "",
                "dias_vencido": dias_vencido,
                "nivel": risco.nivel,
            },
            "data_referencia": risco.data_meta_tratamento,
            "encerrado_em": None,
        }

    return _sync_registros_alerta(obra, CODIGO_ALERTA_RISCO_VENCIDO, referencias_ativas, registros)


def sincronizar_alertas_nc_sem_evolucao(obra):
    parametros = _parametros_alerta(obra)
    hoje = timezone.localdate()
    referencias_ativas = set()
    registros = {}

    nao_conformidades = (
        NaoConformidade.objects.filter(obra=obra)
        .exclude(status__in=["ENCERRADA", "CANCELADA"])
        .annotate(ultima_evolucao=Max("historico__timestamp"))
        .select_related("responsavel")
        .order_by("data_abertura", "numero")
    )

    for nc in nao_conformidades:
        data_base = (nc.ultima_evolucao.date() if nc.ultima_evolucao else nc.data_abertura)
        dias_sem_evolucao = max((hoje - data_base).days, 0)
        if dias_sem_evolucao < parametros.nao_conformidade_sem_evolucao_dias:
            continue
        referencia = str(nc.pk)
        registros[referencia] = {
            "titulo": TITULO_ALERTA_NC_SEM_EVOLUCAO,
            "descricao": f"A nao conformidade {nc.numero} esta sem evolucao registrada ha {dias_sem_evolucao} dia(s).",
            "severidade": _severidade_por_idade(dias_sem_evolucao),
            "status": "ABERTO",
            "entidade_tipo": "NaoConformidade",
            "entidade_id": nc.pk,
            "evidencias": {
                "numero": nc.numero,
                "status": nc.get_status_display(),
                "responsavel": str(nc.responsavel) if nc.responsavel else "",
                "data_abertura": nc.data_abertura.strftime("%d/%m/%Y") if nc.data_abertura else "",
                "dias_sem_evolucao": dias_sem_evolucao,
            },
            "data_referencia": data_base,
            "encerrado_em": None,
        }

    return _sync_registros_alerta(obra, CODIGO_ALERTA_NC_SEM_EVOLUCAO, referencias_ativas, registros)


def sincronizar_alertas_cronograma_desempenho(obra):
    parametros = _parametros_alerta(obra)
    hoje = timezone.localdate()
    referencias_ativas = set()
    registros = {}
    plano, itens = _itens_folha_plano_referencia(obra)

    if not plano:
        for codigo in [
            CODIGO_ALERTA_ATIVIDADE_SEM_AVANCO,
            CODIGO_ALERTA_DESVIO_PRAZO,
            CODIGO_ALERTA_ESTOURO_PRAZO,
            CODIGO_ALERTA_DESVIO_CUSTO,
            CODIGO_ALERTA_CUSTO_SEM_AVANCO,
            CODIGO_ALERTA_DESVIO_COMBINADO,
        ]:
            AlertaOperacional.objects.filter(
                obra=obra,
                codigo_regra=codigo,
                status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"],
            ).update(status="ENCERRADO", encerrado_em=timezone.now())
        return []

    analise = MapeamentoService.analisar_vinculos(plano)
    centros_ids = {
        eap.pk
        for item in itens
        for eap in analise["item_to_eaps"].get(item.pk, [])
    }
    totais_realizados_eap = {}
    if centros_ids:
        totais_realizados_eap = dict(
            NotaFiscalCentroCusto.objects.filter(
                nota_fiscal__obra=obra,
                nota_fiscal__status__in=["LANCADA", "CONFERIDA", "PAGA"],
                centro_custo_id__in=centros_ids,
            ).values_list("centro_custo_id").annotate(total=Coalesce(Sum("valor"), Decimal("0.00")))
        )

    for item in itens:
        progresso_previsto = Decimal(str(item.percentual_previsto_calculado or 0))
        progresso_real = Decimal(str(item.percentual_realizado_calculado or 0))
        valor_planejado = Decimal(str(item.valor_planejado or Decimal("0.00")))
        custo_realizado = Decimal("0.00")
        for eap in analise["item_to_eaps"].get(item.pk, []):
            valor_planejado_contrib = Decimal(str(analise["contribuicoes"].get((item.pk, eap.pk), Decimal("0.00"))))
            valor_total_eap = Decimal(str(eap.valor_total_consolidado or Decimal("0.00")))
            if valor_total_eap > Decimal("0.00"):
                custo_realizado += Decimal(str(totais_realizados_eap.get(eap.pk, Decimal("0.00")))) * (
                    valor_planejado_contrib / valor_total_eap
                )
        custo_realizado = custo_realizado.quantize(Decimal("0.01"))

        if (
            item.data_inicio_prevista
            and item.data_inicio_prevista + timedelta(days=parametros.atividade_sem_avanco_tolerancia_dias) <= hoje
            and progresso_real <= Decimal("0.00")
        ):
            referencia = str(item.pk)
            dias_sem_avanco = max((hoje - item.data_inicio_prevista).days, 0)
            registros[(CODIGO_ALERTA_ATIVIDADE_SEM_AVANCO, referencia)] = {
                "titulo": TITULO_ALERTA_ATIVIDADE_SEM_AVANCO,
                "descricao": f"A atividade {item.codigo_atividade} - {item.atividade} deveria ter iniciado e segue sem avanço fisico registrado.",
                "severidade": _severidade_por_idade(dias_sem_avanco),
                "status": "ABERTO",
                "entidade_tipo": "PlanoFisicoItem",
                "entidade_id": item.pk,
                "evidencias": {
                    "codigo_atividade": item.codigo_atividade,
                    "atividade": item.atividade,
                    "inicio_previsto": item.data_inicio_prevista.strftime("%d/%m/%Y"),
                    "dias_sem_avanco": dias_sem_avanco,
                },
                "data_referencia": item.data_inicio_prevista,
                "encerrado_em": None,
            }

        tolerancia_prazo = Decimal(str(parametros.desvio_prazo_tolerancia_percentual))
        percentual_minimo_previsto = Decimal(str(parametros.desvio_prazo_percentual_minimo_previsto))
        if progresso_previsto >= percentual_minimo_previsto and progresso_real + tolerancia_prazo < progresso_previsto:
            referencia = str(item.pk)
            diferenca = progresso_previsto - progresso_real
            registros[(CODIGO_ALERTA_DESVIO_PRAZO, referencia)] = {
                "titulo": TITULO_ALERTA_DESVIO_PRAZO,
                "descricao": f"A atividade {item.codigo_atividade} - {item.atividade} apresenta avanço fisico abaixo do tempo decorrido.",
                "severidade": "CRITICA" if diferenca >= Decimal("30.00") else "ALTA",
                "status": "ABERTO",
                "entidade_tipo": "PlanoFisicoItem",
                "entidade_id": item.pk,
                "evidencias": {
                    "codigo_atividade": item.codigo_atividade,
                    "atividade": item.atividade,
                    "percentual_previsto": float(progresso_previsto),
                    "percentual_realizado": float(progresso_real),
                    "desvio_percentual": float(diferenca),
                },
                "data_referencia": item.data_fim_prevista or item.data_inicio_prevista,
                "encerrado_em": None,
            }

        if obra.data_fim and item.data_inicio_prevista and hoje >= item.data_inicio_prevista and Decimal("0.00") < progresso_real < Decimal("100.00"):
            dias_decorridos = max((hoje - item.data_inicio_prevista).days + 1, 1)
            progresso_fracao = progresso_real / Decimal("100")
            dias_estimados_total = int((Decimal(dias_decorridos) / progresso_fracao).quantize(Decimal("1")))
            data_estimativa_fim = item.data_inicio_prevista + timedelta(days=max(dias_estimados_total - 1, 0))
            atraso_estimado = (data_estimativa_fim - obra.data_fim).days
            if atraso_estimado > parametros.estouro_prazo_tolerancia_dias:
                referencia = str(item.pk)
                registros[(CODIGO_ALERTA_ESTOURO_PRAZO, referencia)] = {
                    "titulo": TITULO_ALERTA_ESTOURO_PRAZO,
                    "descricao": f"A atividade {item.codigo_atividade} - {item.atividade} projeta termino alem do prazo contratual da obra.",
                    "severidade": "CRITICA" if atraso_estimado >= 15 else "ALTA",
                    "status": "ABERTO",
                    "entidade_tipo": "PlanoFisicoItem",
                    "entidade_id": item.pk,
                    "evidencias": {
                        "codigo_atividade": item.codigo_atividade,
                        "atividade": item.atividade,
                        "termino_previsto_obra": obra.data_fim.strftime("%d/%m/%Y"),
                        "termino_estimado": data_estimativa_fim.strftime("%d/%m/%Y"),
                        "atraso_estimado_dias": atraso_estimado,
                    },
                    "data_referencia": data_estimativa_fim,
                    "encerrado_em": None,
                }

        custo_esperado = (valor_planejado * (progresso_real / Decimal("100"))).quantize(Decimal("0.01")) if valor_planejado > 0 else Decimal("0.00")
        multiplicador_tolerancia_custo = Decimal("1.00") + (Decimal(str(parametros.desvio_custo_tolerancia_percentual)) / Decimal("100"))
        if custo_realizado > Decimal("0.00") and custo_esperado > Decimal("0.00") and custo_realizado > (custo_esperado * multiplicador_tolerancia_custo):
            referencia = str(item.pk)
            excesso = custo_realizado - custo_esperado
            registros[(CODIGO_ALERTA_DESVIO_CUSTO, referencia)] = {
                "titulo": TITULO_ALERTA_DESVIO_CUSTO,
                "descricao": f"A atividade {item.codigo_atividade} - {item.atividade} apresenta custo realizado acima do previsto proporcional ao avanço.",
                "severidade": "CRITICA" if excesso >= Decimal("10000.00") else "ALTA",
                "status": "ABERTO",
                "entidade_tipo": "PlanoFisicoItem",
                "entidade_id": item.pk,
                "evidencias": {
                    "codigo_atividade": item.codigo_atividade,
                    "atividade": item.atividade,
                    "valor_planejado": str(valor_planejado),
                    "custo_realizado": str(custo_realizado),
                    "custo_esperado_proporcional": str(custo_esperado),
                },
                "data_referencia": hoje,
                "encerrado_em": None,
            }

        if custo_realizado > Decimal(str(parametros.custo_sem_avanco_valor_minimo)) and progresso_real <= Decimal("0.00"):
            referencia = str(item.pk)
            registros[(CODIGO_ALERTA_CUSTO_SEM_AVANCO, referencia)] = {
                "titulo": TITULO_ALERTA_CUSTO_SEM_AVANCO,
                "descricao": f"A atividade {item.codigo_atividade} - {item.atividade} possui custo lancado sem lastro fisico registrado.",
                "severidade": "CRITICA" if custo_realizado >= Decimal("5000.00") else "ALTA",
                "status": "ABERTO",
                "entidade_tipo": "PlanoFisicoItem",
                "entidade_id": item.pk,
                "evidencias": {
                    "codigo_atividade": item.codigo_atividade,
                    "atividade": item.atividade,
                    "custo_realizado": str(custo_realizado),
                    "percentual_realizado": float(progresso_real),
                },
                "data_referencia": hoje,
                "encerrado_em": None,
            }

        if (
            (CODIGO_ALERTA_DESVIO_PRAZO, str(item.pk)) in registros
            and (CODIGO_ALERTA_DESVIO_CUSTO, str(item.pk)) in registros
        ):
            referencia = str(item.pk)
            registros[(CODIGO_ALERTA_DESVIO_COMBINADO, referencia)] = {
                "titulo": TITULO_ALERTA_DESVIO_COMBINADO,
                "descricao": f"A atividade {item.codigo_atividade} - {item.atividade} apresenta desvio simultaneo de prazo e custo.",
                "severidade": "CRITICA",
                "status": "ABERTO",
                "entidade_tipo": "PlanoFisicoItem",
                "entidade_id": item.pk,
                "evidencias": {
                    "codigo_atividade": item.codigo_atividade,
                    "atividade": item.atividade,
                },
                "data_referencia": item.data_fim_prevista or hoje,
                "encerrado_em": None,
            }

    alertas = []
    for codigo in [
        CODIGO_ALERTA_ATIVIDADE_SEM_AVANCO,
        CODIGO_ALERTA_DESVIO_PRAZO,
        CODIGO_ALERTA_ESTOURO_PRAZO,
        CODIGO_ALERTA_DESVIO_CUSTO,
        CODIGO_ALERTA_CUSTO_SEM_AVANCO,
        CODIGO_ALERTA_DESVIO_COMBINADO,
    ]:
        registros_codigo = {
            referencia: payload for (codigo_registro, referencia), payload in registros.items() if codigo_registro == codigo
        }
        alertas.extend(_sync_registros_alerta(obra, codigo, set(), registros_codigo))
    return alertas


def sincronizar_alertas_compromissos_acima_orcado(obra):
    parametros = _parametros_alerta(obra)
    referencias_ativas = set()
    registros = {}

    compromissos = (
        Compromisso.objects.filter(obra=obra, status__in=["EM_APROVACAO", "APROVADO", "EM_EXECUCAO"])
        .prefetch_related("itens__centro_custo")
        .select_related("centro_custo")
        .order_by("numero")
    )

    for compromisso in compromissos:
        if compromisso.itens.exists():
            for item in compromisso.itens.all():
                orcado = item.centro_custo.valor_total_consolidado if item.centro_custo_id else Decimal("0.00")
                limite_item = orcado * (Decimal("1.00") + (Decimal(str(parametros.compromisso_acima_orcado_tolerancia_percentual)) / Decimal("100")))
                if orcado and item.valor_total > limite_item:
                    referencia = f"{compromisso.pk}:{item.pk}"
                    registros[referencia] = {
                        "titulo": TITULO_ALERTA_COMPROMISSO_ACIMA_ORCADO,
                        "descricao": f"O item {item.centro_custo.codigo} do compromisso {compromisso.numero} supera o valor orcado da EAP vinculada.",
                        "severidade": "CRITICA",
                        "status": "ABERTO",
                        "entidade_tipo": "CompromissoItem",
                        "entidade_id": item.pk,
                        "evidencias": {
                            "compromisso_numero": compromisso.numero,
                            "centro_custo_codigo": item.centro_custo.codigo,
                            "valor_item": str(item.valor_total),
                            "valor_orcado": str(orcado),
                        },
                        "data_referencia": compromisso.data_assinatura,
                        "encerrado_em": None,
                    }
        elif compromisso.centro_custo_id:
            orcado = compromisso.centro_custo.valor_total_consolidado or Decimal("0.00")
            limite_compromisso = orcado * (Decimal("1.00") + (Decimal(str(parametros.compromisso_acima_orcado_tolerancia_percentual)) / Decimal("100")))
            if orcado and compromisso.valor_contratado > limite_compromisso:
                referencia = str(compromisso.pk)
                registros[referencia] = {
                    "titulo": TITULO_ALERTA_COMPROMISSO_ACIMA_ORCADO,
                    "descricao": f"O compromisso {compromisso.numero} supera o valor orcado do centro de custo vinculado.",
                    "severidade": "CRITICA",
                    "status": "ABERTO",
                    "entidade_tipo": "Compromisso",
                    "entidade_id": compromisso.pk,
                    "evidencias": {
                        "compromisso_numero": compromisso.numero,
                        "centro_custo_codigo": compromisso.centro_custo.codigo,
                        "valor_compromisso": str(compromisso.valor_contratado),
                        "valor_orcado": str(orcado),
                    },
                    "data_referencia": compromisso.data_assinatura,
                    "encerrado_em": None,
                }

    return _sync_registros_alerta(obra, CODIGO_ALERTA_COMPROMISSO_ACIMA_ORCADO, referencias_ativas, registros)


def sincronizar_alertas_acumulo_riscos(obra):
    parametros = _parametros_alerta(obra)
    referencias_ativas = set()
    riscos_ativos = Risco.objects.filter(obra=obra).exclude(status__in=["MITIGADO", "FECHADO", "CANCELADO"]).count()
    registros = {}
    if riscos_ativos >= parametros.acumulo_riscos_quantidade_minima:
        registros["obra"] = {
            "titulo": TITULO_ALERTA_MULTIPLOS_RISCOS,
            "descricao": f"A obra possui {riscos_ativos} riscos ativos sem tratamento concluido, indicando perda de controle operacional.",
            "severidade": "CRITICA" if riscos_ativos >= parametros.acumulo_riscos_quantidade_critica else "ALTA",
            "status": "ABERTO",
            "entidade_tipo": "Obra",
            "entidade_id": obra.pk,
            "evidencias": {
                "quantidade_riscos_ativos": riscos_ativos,
            },
            "data_referencia": timezone.localdate(),
            "encerrado_em": None,
        }
    return _sync_registros_alerta(obra, CODIGO_ALERTA_MULTIPLOS_RISCOS, referencias_ativas, registros)


def sincronizar_alertas_operacionais_obra(obra):
    sincronizadores = [
        sincronizar_alertas_planejamento_suprimentos,
        sincronizar_alertas_contrato_sem_medicao,
        sincronizar_alertas_medicao_sem_nota,
        sincronizar_alertas_nota_sem_rateio,
        sincronizar_alertas_risco_vencido,
        sincronizar_alertas_nc_sem_evolucao,
        sincronizar_alertas_cronograma_desempenho,
        sincronizar_alertas_compromissos_acima_orcado,
        sincronizar_alertas_acumulo_riscos,
    ]
    alertas = []
    for sincronizador in sincronizadores:
        alertas.extend(sincronizador(obra))
    return _ordenar_alertas(alertas)


def listar_alertas_planejamento_suprimentos(obra, *, limit=10):
    return list(
        _ordenar_alertas(
            AlertaOperacional.objects.filter(
                obra=obra,
                codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
                status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"],
            )
        )[:limit]
    )


def listar_alertas_operacionais_ativos(obra, *, limit=10):
    return list(
        _ordenar_alertas(
            AlertaOperacional.objects.filter(
                obra=obra,
                status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"],
            )
        )[:limit]
    )


def listar_execucoes_regras_operacionais(obra, *, limit=15):
    return list(
        ExecucaoRegraOperacional.objects.filter(obra=obra)
        .select_related("alerta")
        .order_by("-executado_em")[:limit]
    )


def resumo_executivo_alertas_operacionais(obra):
    resumo_alertas = resumo_alertas_operacionais(obra)

    prioridades = [
        {
            "frente": "Prazo e custo combinados",
            "total": resumo_alertas["desvio_combinado"],
            "nivel": "critico" if resumo_alertas["desvio_combinado"] else "baixo",
            "acao": "Priorizar plano de recuperacao fisico-financeiro da atividade impactada.",
        },
        {
            "frente": "Suprimentos e mobilizacao",
            "total": resumo_alertas["planejamento_suprimentos"] + resumo_alertas["contrato_sem_medicao"],
            "nivel": "critico"
            if (resumo_alertas["planejamento_suprimentos"] + resumo_alertas["contrato_sem_medicao"]) >= 8
            else "alto"
            if (resumo_alertas["planejamento_suprimentos"] + resumo_alertas["contrato_sem_medicao"]) >= 4
            else "medio"
            if (resumo_alertas["planejamento_suprimentos"] + resumo_alertas["contrato_sem_medicao"]) >= 1
            else "baixo",
            "acao": "Antecipar compras, contratos e frentes criticas dos proximos ciclos.",
        },
        {
            "frente": "Financeiro sem lastro fisico",
            "total": resumo_alertas["custo_sem_avanco"] + resumo_alertas["medicao_sem_nota"] + resumo_alertas["nota_sem_rateio"],
            "nivel": "critico"
            if (resumo_alertas["custo_sem_avanco"] + resumo_alertas["medicao_sem_nota"] + resumo_alertas["nota_sem_rateio"]) >= 8
            else "alto"
            if (resumo_alertas["custo_sem_avanco"] + resumo_alertas["medicao_sem_nota"] + resumo_alertas["nota_sem_rateio"]) >= 4
            else "medio"
            if (resumo_alertas["custo_sem_avanco"] + resumo_alertas["medicao_sem_nota"] + resumo_alertas["nota_sem_rateio"]) >= 1
            else "baixo",
            "acao": "Revisar medicao, nota e apropriacao para garantir evidencias de execucao.",
        },
        {
            "frente": "Riscos e qualidade",
            "total": resumo_alertas["risco_vencido"] + resumo_alertas["acumulo_riscos"] + resumo_alertas["nc_sem_evolucao"],
            "nivel": "critico"
            if (resumo_alertas["risco_vencido"] + resumo_alertas["acumulo_riscos"] + resumo_alertas["nc_sem_evolucao"]) >= 8
            else "alto"
            if (resumo_alertas["risco_vencido"] + resumo_alertas["acumulo_riscos"] + resumo_alertas["nc_sem_evolucao"]) >= 4
            else "medio"
            if (resumo_alertas["risco_vencido"] + resumo_alertas["acumulo_riscos"] + resumo_alertas["nc_sem_evolucao"]) >= 1
            else "baixo",
            "acao": "Atacar riscos vencidos e nao conformidades paradas antes de ampliar impacto sistemico.",
        },
    ]
    correlacoes = [
        {
            "titulo": "Prazo x Custo",
            "quantidade": resumo_alertas["desvio_prazo"] + resumo_alertas["estouro_prazo"] + resumo_alertas["desvio_custo"] + resumo_alertas["desvio_combinado"],
            "nivel": "critico"
            if (resumo_alertas["desvio_prazo"] + resumo_alertas["estouro_prazo"] + resumo_alertas["desvio_custo"] + resumo_alertas["desvio_combinado"]) >= 8
            else "alto"
            if (resumo_alertas["desvio_prazo"] + resumo_alertas["estouro_prazo"] + resumo_alertas["desvio_custo"] + resumo_alertas["desvio_combinado"]) >= 4
            else "medio"
            if (resumo_alertas["desvio_prazo"] + resumo_alertas["estouro_prazo"] + resumo_alertas["desvio_custo"] + resumo_alertas["desvio_combinado"]) >= 1
            else "baixo",
            "descricao": "Atividades com perda simultanea de desempenho fisico e financeiro.",
        },
        {
            "titulo": "Suprimentos x Execucao",
            "quantidade": resumo_alertas["planejamento_suprimentos"] + resumo_alertas["contrato_sem_medicao"] + resumo_alertas["medicao_sem_nota"],
            "nivel": "critico"
            if (resumo_alertas["planejamento_suprimentos"] + resumo_alertas["contrato_sem_medicao"] + resumo_alertas["medicao_sem_nota"]) >= 8
            else "alto"
            if (resumo_alertas["planejamento_suprimentos"] + resumo_alertas["contrato_sem_medicao"] + resumo_alertas["medicao_sem_nota"]) >= 4
            else "medio"
            if (resumo_alertas["planejamento_suprimentos"] + resumo_alertas["contrato_sem_medicao"] + resumo_alertas["medicao_sem_nota"]) >= 1
            else "baixo",
            "descricao": "Frentes futuras, contratos e medicoes com risco de ruptura operacional.",
        },
        {
            "titulo": "Riscos x Qualidade",
            "quantidade": resumo_alertas["risco_vencido"] + resumo_alertas["acumulo_riscos"] + resumo_alertas["nc_sem_evolucao"],
            "nivel": "critico"
            if (resumo_alertas["risco_vencido"] + resumo_alertas["acumulo_riscos"] + resumo_alertas["nc_sem_evolucao"]) >= 8
            else "alto"
            if (resumo_alertas["risco_vencido"] + resumo_alertas["acumulo_riscos"] + resumo_alertas["nc_sem_evolucao"]) >= 4
            else "medio"
            if (resumo_alertas["risco_vencido"] + resumo_alertas["acumulo_riscos"] + resumo_alertas["nc_sem_evolucao"]) >= 1
            else "baixo",
            "descricao": "Pendencias acumuladas que podem ampliar atraso, retrabalho e perda de controle.",
        },
    ]
    return {
        "resumo_alertas": resumo_alertas,
        "prioridades_executivas": prioridades,
        "correlacoes_operacionais": correlacoes,
        "execucoes_recentes": listar_execucoes_regras_operacionais(obra, limit=10),
        "catalogo_regras": catalogo_alertas_empresa(getattr(obra, "empresa", None), incluir_score=False),
    }


def resumo_alertas_operacionais(obra):
    alertas = AlertaOperacional.objects.filter(
        obra=obra,
        status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"],
    )
    return {
        "planejamento_suprimentos": alertas.filter(codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS).count(),
        "contrato_sem_medicao": alertas.filter(codigo_regra=CODIGO_ALERTA_CONTRATO_SEM_MEDICAO).count(),
        "medicao_sem_nota": alertas.filter(codigo_regra=CODIGO_ALERTA_MEDICAO_SEM_NOTA).count(),
        "nota_sem_rateio": alertas.filter(codigo_regra=CODIGO_ALERTA_NOTA_SEM_RATEIO).count(),
        "risco_vencido": alertas.filter(codigo_regra=CODIGO_ALERTA_RISCO_VENCIDO).count(),
        "nc_sem_evolucao": alertas.filter(codigo_regra=CODIGO_ALERTA_NC_SEM_EVOLUCAO).count(),
        "atividade_sem_avanco": alertas.filter(codigo_regra=CODIGO_ALERTA_ATIVIDADE_SEM_AVANCO).count(),
        "desvio_prazo": alertas.filter(codigo_regra=CODIGO_ALERTA_DESVIO_PRAZO).count(),
        "estouro_prazo": alertas.filter(codigo_regra=CODIGO_ALERTA_ESTOURO_PRAZO).count(),
        "desvio_custo": alertas.filter(codigo_regra=CODIGO_ALERTA_DESVIO_CUSTO).count(),
        "custo_sem_avanco": alertas.filter(codigo_regra=CODIGO_ALERTA_CUSTO_SEM_AVANCO).count(),
        "compromisso_acima_orcado": alertas.filter(codigo_regra=CODIGO_ALERTA_COMPROMISSO_ACIMA_ORCADO).count(),
        "acumulo_riscos": alertas.filter(codigo_regra=CODIGO_ALERTA_MULTIPLOS_RISCOS).count(),
        "desvio_combinado": alertas.filter(codigo_regra=CODIGO_ALERTA_DESVIO_COMBINADO).count(),
        "total": alertas.count(),
    }
