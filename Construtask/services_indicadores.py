from datetime import date
from decimal import Decimal

from .importacao_cronograma import CronogramaService
from .services_eva import EVAService
from .services_integracao import IntegracaoService


class IndicadoresService:
    @classmethod
    def resumo_obra(cls, obra, data_referencia=None):
        data_referencia = data_referencia or date.today()
        consolidado = IntegracaoService.consolidar_obra(obra, data_referencia)
        eva = EVAService.calcular(obra, data_referencia)

        planejado = consolidado["planejado"]
        executado = consolidado["executado"]
        orcado = consolidado["orcado"]

        return {
            "percentual_planejado_vs_executado": cls._percentual(executado, planejado),
            "custo_previsto_vs_realizado": {
                "previsto": planejado,
                "realizado": executado,
            },
            "cpi": eva["CPI"],
            "spi": eva["SPI"],
            "curva_s": cls.curva_s(obra),
            "orcado": orcado,
            "comprometido": consolidado["comprometido"],
            "medido": consolidado["medido"],
            "executado": executado,
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
