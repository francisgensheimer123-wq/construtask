from collections import defaultdict
from decimal import Decimal

from ..importacao_cronograma import MapeamentoService


def peso_item_planejado(item, inicio_previsto=None, fim_previsto=None):
    if inicio_previsto and fim_previsto:
        dias = (fim_previsto - inicio_previsto).days + 1
        if dias > 0:
            return Decimal(str(dias))
    return Decimal("1")


def itens_plano_carregados(plano):
    itens_prefetchados = getattr(plano, "itens_prefetchados", None)
    if itens_prefetchados is not None:
        return list(itens_prefetchados)
    return list(plano.itens.select_related("plano_contas", "parent").order_by("sort_order", "pk"))


def consolidar_arvore_cronograma(plano, analise_vinculos=None):
    analise_vinculos = analise_vinculos or MapeamentoService.analisar_vinculos(plano)
    valores_planejados = analise_vinculos["valores_item"]
    codigos_por_item = analise_vinculos["codigos_por_item"]
    mensagens_vinculo = analise_vinculos["mensagens"]
    itens = itens_plano_carregados(plano)
    filhos_por_parent = defaultdict(list)
    for item in itens:
        filhos_por_parent[item.parent_id].append(item)

    cache = {}

    def calcular(item):
        if item.pk in cache:
            return cache[item.pk]

        filhos = filhos_por_parent.get(item.pk, [])
        if not filhos:
            valor_planejado = valores_planejados.get(item.pk, item.valor_planejado or Decimal("0.00"))
            valor_realizado = item.valor_realizado
            inicio_previsto = item.data_inicio_prevista
            fim_previsto = item.data_fim_prevista
            inicio_real = item.data_inicio_real
            fim_real = item.data_fim_real
            percentual_previsto = Decimal(str(item._calcular_percentual_esperado() or 0))
            percentual_realizado = Decimal(str(item.percentual_concluido or 0))
            dias_desvio = item.dias_desvio
            peso = peso_item_planejado(item, inicio_previsto, fim_previsto)
        else:
            metricas_filhos = [calcular(filho) for filho in filhos]
            valor_planejado = sum((m["valor_planejado"] for m in metricas_filhos), Decimal("0.00"))
            valor_realizado = sum((m["valor_realizado"] for m in metricas_filhos), Decimal("0.00"))
            inicios_previstos = [m["inicio_previsto"] for m in metricas_filhos if m["inicio_previsto"]]
            fins_previstos = [m["fim_previsto"] for m in metricas_filhos if m["fim_previsto"]]
            inicios_reais = [m["inicio_real"] for m in metricas_filhos if m["inicio_real"]]
            fins_reais = [m["fim_real"] for m in metricas_filhos if m["fim_real"]]

            inicio_previsto = min(inicios_previstos) if inicios_previstos else item.data_inicio_prevista
            fim_previsto = max(fins_previstos) if fins_previstos else item.data_fim_prevista
            inicio_real = min(inicios_reais) if inicios_reais else None
            fim_real = max(fins_reais) if fins_reais else None
            dias_desvio = max((m["dias_desvio"] for m in metricas_filhos), default=0)

            filhos_validos = [
                (m["percentual_previsto"], m["peso"], m["percentual_realizado"])
                for m in metricas_filhos
                if m["peso"] > 0
            ]
            peso_total = sum(peso for _, peso, _ in filhos_validos)
            if peso_total > 0:
                percentual_previsto = sum(
                    Decimal(str(percentual)) * peso for percentual, peso, _ in filhos_validos
                ) / peso_total
                percentual_realizado = sum(
                    Decimal(str(percentual_realizado)) * peso
                    for _, peso, percentual_realizado in filhos_validos
                ) / peso_total
            else:
                percentual_previsto = Decimal("0")
                percentual_realizado = Decimal("0")

            peso = sum(m["peso"] for m in metricas_filhos)
            if peso <= 0:
                peso = peso_item_planejado(item, inicio_previsto, fim_previsto)

        if inicio_previsto and fim_previsto:
            duracao_calculada = max((fim_previsto - inicio_previsto).days + 1, 0)
        else:
            duracao_calculada = 0

        cache[item.pk] = {
            "inicio_previsto": inicio_previsto,
            "fim_previsto": fim_previsto,
            "inicio_real": inicio_real,
            "fim_real": fim_real,
            "percentual_previsto": round(float(percentual_previsto), 1),
            "percentual_realizado": round(float(percentual_realizado), 1),
            "dias_desvio": dias_desvio,
            "peso": Decimal(str(peso)),
            "duracao": duracao_calculada,
            "valor_planejado": valor_planejado,
            "valor_realizado": valor_realizado,
            "tem_filhos": bool(filhos),
        }
        return cache[item.pk]

    linhas = []

    def percorrer(parent_id=None):
        for item in filhos_por_parent.get(parent_id, []):
            metricas = calcular(item)
            item.inicio_previsto_exibicao = metricas["inicio_previsto"]
            item.fim_previsto_exibicao = metricas["fim_previsto"]
            item.inicio_real_exibicao = metricas["inicio_real"]
            item.fim_real_exibicao = metricas["fim_real"]
            item.percentual_previsto_exibicao = metricas["percentual_previsto"]
            item.percentual_realizado_exibicao = metricas["percentual_realizado"]
            item.dias_desvio_exibicao = metricas["dias_desvio"]
            item.duracao_exibicao = metricas["duracao"]
            item.valor_planejado_exibicao = metricas["valor_planejado"]
            item.valor_realizado_exibicao = metricas["valor_realizado"]
            item.tem_filhos_exibicao = metricas["tem_filhos"]
            item.nivel_exibicao = item.level
            item.codigos_eap_exibicao = codigos_por_item.get(item.pk) or (
                [item.codigo_eap_importado] if item.codigo_eap_importado else []
            )
            item.erro_vinculo_exibicao = mensagens_vinculo.get(item.pk) or item.erro_vinculo_eap
            linhas.append(item)
            percorrer(item.pk)

    percorrer()
    return linhas


def atribuir_metricas_resumo_planos(planos):
    for plano in planos:
        itens = itens_plano_carregados(plano)
        plano.total_itens_exibicao = len(itens)

        filhos_por_parent = defaultdict(list)
        for item in itens:
            filhos_por_parent[item.parent_id].append(item)

        cache = {}

        def calcular(item):
            if item.pk in cache:
                return cache[item.pk]

            filhos = filhos_por_parent.get(item.pk, [])
            if not filhos:
                inicio_previsto = item.data_inicio_prevista
                fim_previsto = item.data_fim_prevista
                percentual_realizado = Decimal(str(item.percentual_concluido or 0))
                peso = peso_item_planejado(item, inicio_previsto, fim_previsto)
            else:
                metricas_filhos = [calcular(filho) for filho in filhos]
                inicios_previstos = [m["inicio_previsto"] for m in metricas_filhos if m["inicio_previsto"]]
                fins_previstos = [m["fim_previsto"] for m in metricas_filhos if m["fim_previsto"]]
                inicio_previsto = min(inicios_previstos) if inicios_previstos else item.data_inicio_prevista
                fim_previsto = max(fins_previstos) if fins_previstos else item.data_fim_prevista
                filhos_validos = [
                    (m["percentual_realizado"], m["peso"]) for m in metricas_filhos if m["peso"] > 0
                ]
                peso_total = sum(peso for _, peso in filhos_validos)
                percentual_realizado = (
                    sum(Decimal(str(percentual)) * peso for percentual, peso in filhos_validos) / peso_total
                    if peso_total > 0
                    else Decimal("0")
                )
                peso = sum(m["peso"] for m in metricas_filhos)
                if peso <= 0:
                    peso = peso_item_planejado(item, inicio_previsto, fim_previsto)

            cache[item.pk] = {
                "inicio_previsto": inicio_previsto,
                "fim_previsto": fim_previsto,
                "percentual_realizado": percentual_realizado,
                "peso": Decimal(str(peso)),
                "tem_filhos": bool(filhos),
            }
            return cache[item.pk]

        metricas_raiz = [calcular(item) for item in filhos_por_parent.get(None, [])]
        peso_total = sum(m["peso"] for m in metricas_raiz)
        if peso_total > 0:
            percentual = sum(
                Decimal(str(m["percentual_realizado"])) * m["peso"] for m in metricas_raiz
            ) / peso_total
            plano.percentual_realizado_exibicao = round(float(percentual), 1)
        else:
            plano.percentual_realizado_exibicao = 0

        inicios = [m["inicio_previsto"] for m in metricas_raiz if m["inicio_previsto"]]
        fins = [m["fim_previsto"] for m in metricas_raiz if m["fim_previsto"]]
        plano.inicio_previsto_exibicao = min(inicios) if inicios else None
        plano.fim_previsto_exibicao = max(fins) if fins else None
