from datetime import date, datetime, time
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .importacao_cronograma import CronogramaService
from .models import AlertaOperacional, ParametroAlertaEmpresa
from .models_qualidade import NaoConformidade
from .models_risco import Risco
from .services_alertas import resumo_alertas_operacionais
from .services_eva import EVAService
from .services_integracao import IntegracaoService


class IndicadoresService:
    @classmethod
    def resumo_obra(
        cls,
        obra,
        data_referencia=None,
        *,
        include_curva_s=True,
        consolidado=None,
        eva=None,
    ):
        data_referencia = data_referencia or date.today()
        ttl = max(30, int(getattr(settings, "CONSTRUTASK_HOME_CACHE_TTL", 120)))
        usar_cache = consolidado is None and eva is None
        cache_key = (
            f"indicadores:resumo:{obra.pk}:{data_referencia.isoformat()}:{int(include_curva_s)}"
            if usar_cache
            else None
        )
        if cache_key:
            resumo_cache = cache.get(cache_key)
            if resumo_cache is not None:
                return resumo_cache

        consolidado = consolidado or IntegracaoService.consolidar_obra(obra, data_referencia)
        eva = eva or EVAService.calcular(obra, data_referencia)

        planejado = consolidado["planejado"]
        executado = consolidado["executado"]
        orcado = consolidado["orcado"]

        resumo = {
            "percentual_planejado_vs_executado": cls._percentual(executado, planejado),
            "custo_previsto_vs_realizado": {
                "previsto": planejado,
                "realizado": executado,
            },
            "cpi": eva["CPI"],
            "spi": eva["SPI"],
            "orcado": orcado,
            "comprometido": consolidado["comprometido"],
            "medido": consolidado["medido"],
            "executado": executado,
            "score_operacional": cls.score_obra(obra, data_referencia, eva=eva),
        }
        if include_curva_s:
            resumo["curva_s"] = cls.curva_s(obra)
        if cache_key:
            cache.set(cache_key, resumo, ttl)
        return resumo

    @classmethod
    def score_obra(cls, obra, data_referencia=None, *, eva=None):
        data_referencia = data_referencia or date.today()
        eva = eva or EVAService.calcular(obra, data_referencia)
        resumo_alertas = resumo_alertas_operacionais(obra)
        parametros = ParametroAlertaEmpresa.obter_ou_criar(getattr(obra, "empresa", None))
        alertas_ativos = AlertaOperacional.objects.filter(
            obra=obra,
            status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"],
        )
        alertas_pendentes_score = cls._alertas_pendentes_para_score(
            alertas_ativos,
            dias_sem_workflow=parametros.alerta_sem_workflow_dias,
            prazo_solucao_dias=parametros.alerta_prazo_solucao_dias,
            data_referencia=data_referencia,
        )
        riscos_ativos = Risco.objects.filter(obra=obra).exclude(status__in=["FECHADO", "CANCELADO"])
        ncs_abertas = NaoConformidade.objects.filter(obra=obra).exclude(status__in=["ENCERRADA", "CANCELADA"])

        componentes = [
            cls._componente_prazo(eva, resumo_alertas, alertas_pendentes_score),
            cls._componente_custo(eva, resumo_alertas, alertas_pendentes_score),
            cls._componente_governanca(resumo_alertas, alertas_pendentes_score),
            cls._componente_riscos_qualidade(riscos_ativos, ncs_abertas, resumo_alertas, alertas_ativos, alertas_pendentes_score),
        ]

        pontuacao = sum((item["pontuacao"] for item in componentes), Decimal("0.00"))
        pontuacao = max(Decimal("0.00"), min(Decimal("100.00"), pontuacao))
        return {
            "pontuacao": pontuacao.quantize(Decimal("0.01")),
            "faixa": cls._faixa_score(pontuacao),
            "componentes": componentes,
            "total_alertas_ativos": alertas_ativos.count(),
            "total_alertas_pendentes_score": len(alertas_pendentes_score),
            "total_riscos_ativos": riscos_ativos.count(),
            "total_ncs_abertas": ncs_abertas.count(),
        }

    @staticmethod
    def curva_s(obra):
        baseline = IntegracaoService.obter_baseline_ativo(obra)
        if not baseline:
            return {"planejada": [], "realizada": []}
        return {
            "planejada": CronogramaService.gerar_curva_s_planejada(baseline.pk),
            "realizada": CronogramaService.gerar_curva_s_realizada(baseline.pk),
        }

    @staticmethod
    def _percentual(parte, total):
        if not total:
            return Decimal("0.00")
        return ((Decimal(parte) / Decimal(total)) * Decimal("100")).quantize(Decimal("0.01"))

    @classmethod
    def _componente_prazo(cls, eva, resumo_alertas, alertas_pendentes_score):
        maximo = Decimal("25.00")
        spi = Decimal(str(eva.get("SPI") or 0))
        penalidade_spi = Decimal("0.00")
        if spi < Decimal("1.00"):
            penalidade_spi = min((Decimal("1.00") - spi) * Decimal("20.00"), Decimal("15.00"))
        codigos_prazo = {"PLAN-PROG-001", "PLAN-PROG-002", "PLAN-PROG-003"}
        alertas_prazo = sum(1 for item in alertas_pendentes_score if item.codigo_regra in codigos_prazo)
        penalidade_alertas = min(Decimal(alertas_prazo) * Decimal("2.00"), Decimal("10.00"))
        pontuacao = max(Decimal("0.00"), maximo - penalidade_spi - penalidade_alertas)
        return {
            "nome": "Prazo",
            "pontuacao": pontuacao.quantize(Decimal("0.01")),
            "maximo": maximo,
            "nivel": cls._faixa_componente(pontuacao, maximo),
            "detalhe": f"SPI {spi.quantize(Decimal('0.01'))} e {alertas_prazo} alertas de prazo fora do SLA de tratamento.",
        }

    @classmethod
    def _componente_custo(cls, eva, resumo_alertas, alertas_pendentes_score):
        maximo = Decimal("25.00")
        cpi = Decimal(str(eva.get("CPI") or 0))
        penalidade_cpi = Decimal("0.00")
        if cpi < Decimal("1.00"):
            penalidade_cpi = min((Decimal("1.00") - cpi) * Decimal("20.00"), Decimal("15.00"))
        codigos_custo = {"COST-PROG-001", "COST-PROG-002", "COST-BUD-001"}
        alertas_custo = sum(1 for item in alertas_pendentes_score if item.codigo_regra in codigos_custo)
        penalidade_alertas = min(Decimal(alertas_custo) * Decimal("2.00"), Decimal("10.00"))
        pontuacao = max(Decimal("0.00"), maximo - penalidade_cpi - penalidade_alertas)
        return {
            "nome": "Custo",
            "pontuacao": pontuacao.quantize(Decimal("0.01")),
            "maximo": maximo,
            "nivel": cls._faixa_componente(pontuacao, maximo),
            "detalhe": f"CPI {cpi.quantize(Decimal('0.01'))} e {alertas_custo} alertas financeiros fora do SLA de tratamento.",
        }

    @classmethod
    def _componente_governanca(cls, resumo_alertas, alertas_pendentes_score):
        maximo = Decimal("25.00")
        codigos_governanca = {"CONT-MED-001", "MED-NF-001", "NF-RAT-001", "PLAN-SUP-001"}
        total = sum(1 for item in alertas_pendentes_score if item.codigo_regra in codigos_governanca)
        penalidade = min(Decimal(total) * Decimal("3.00"), Decimal("25.00"))
        pontuacao = max(Decimal("0.00"), maximo - penalidade)
        return {
            "nome": "Governanca",
            "pontuacao": pontuacao.quantize(Decimal("0.01")),
            "maximo": maximo,
            "nivel": cls._faixa_componente(pontuacao, maximo),
            "detalhe": f"{total} alertas de lastro operacional, medicoes, notas e suprimentos fora do SLA.",
        }

    @classmethod
    def _componente_riscos_qualidade(cls, riscos_ativos, ncs_abertas, resumo_alertas, alertas_ativos, alertas_pendentes_score):
        maximo = Decimal("25.00")
        riscos_criticos = riscos_ativos.filter(nivel__gt=15).count()
        riscos_altos = riscos_ativos.filter(nivel__gte=10, nivel__lte=15).count()
        alertas_criticos = sum(1 for item in alertas_pendentes_score if item.severidade == "CRITICA")
        penalidade_riscos = min(
            Decimal(riscos_criticos * 4 + riscos_altos * 2 + ncs_abertas.count() + alertas_criticos),
            Decimal("25.00"),
        )
        codigos_risco_qualidade = {"RISK-DUE-001", "RISK-ACC-001", "NC-EVO-001"}
        alertas_risco_qualidade = sum(1 for item in alertas_pendentes_score if item.codigo_regra in codigos_risco_qualidade)
        penalidade_operacional = min(Decimal(alertas_risco_qualidade) * Decimal("2.00"), Decimal("10.00"))
        pontuacao = max(Decimal("0.00"), maximo - penalidade_riscos - penalidade_operacional)
        return {
            "nome": "Riscos e qualidade",
            "pontuacao": pontuacao.quantize(Decimal("0.01")),
            "maximo": maximo,
            "nivel": cls._faixa_componente(pontuacao, maximo),
            "detalhe": f"{riscos_criticos} riscos criticos, {riscos_altos} riscos altos, {ncs_abertas.count()} NCs abertas, {alertas_criticos} alertas criticos fora do SLA e {alertas_risco_qualidade} alertas dessa frente fora do SLA.",
        }

    @staticmethod
    def _alertas_pendentes_para_score(alertas_ativos, *, dias_sem_workflow, prazo_solucao_dias, data_referencia):
        pendentes = []
        referencia_datetime = timezone.make_aware(datetime.combine(data_referencia, time.min))
        for alerta in alertas_ativos:
            ultima_movimentacao = alerta.ultima_acao_em or alerta.criado_em
            dias_sem_movimento = max(0, (referencia_datetime - ultima_movimentacao).days)
            dias_em_aberto = max(0, (referencia_datetime - alerta.criado_em).days)
            if dias_sem_movimento >= dias_sem_workflow or dias_em_aberto > prazo_solucao_dias:
                pendentes.append(alerta)
        return pendentes

    @staticmethod
    def _faixa_componente(pontuacao, maximo):
        percentual = (Decimal(pontuacao) / Decimal(maximo)) * Decimal("100") if maximo else Decimal("0.00")
        if percentual >= Decimal("85.00"):
            return "excelente"
        if percentual >= Decimal("70.00"):
            return "saudavel"
        if percentual >= Decimal("50.00"):
            return "atencao"
        return "critico"

    @staticmethod
    def _faixa_score(pontuacao):
        pontuacao = Decimal(pontuacao)
        if pontuacao >= Decimal("85.00"):
            return "Excelente"
        if pontuacao >= Decimal("70.00"):
            return "Saudavel"
        if pontuacao >= Decimal("50.00"):
            return "Atencao"
        return "Critico"
