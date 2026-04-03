from datetime import date
from decimal import Decimal

from .models_planejamento import PlanoFisicoItem
from .services_integracao import IntegracaoService


class EVAService:
    @classmethod
    def calcular(cls, obra, data_referencia=None):
        data_referencia = data_referencia or date.today()
        baseline = IntegracaoService.obter_baseline_ativo(obra)
        pv = IntegracaoService.calcular_valor_planejado_ate_data(obra, data_referencia)
        ev = cls._calcular_earned_value(baseline)
        ac = IntegracaoService.consolidar_obra(obra, data_referencia)["executado"]

        cv = ev - ac
        sv = ev - pv
        cpi = cls._safe_div(ev, ac)
        spi = cls._safe_div(ev, pv)

        return {
            "PV": pv,
            "EV": ev,
            "AC": ac,
            "CV": cv.quantize(Decimal("0.01")),
            "SV": sv.quantize(Decimal("0.01")),
            "CPI": cpi,
            "SPI": spi,
        }

    @staticmethod
    def _calcular_earned_value(baseline):
        if not baseline:
            return Decimal("0.00")

        total = Decimal("0.00")
        for item in baseline.itens.all():
            percentual = Decimal(item.percentual_concluido or 0) / Decimal("100")
            total += (item.valor_planejado or Decimal("0.00")) * percentual
        return total.quantize(Decimal("0.01"))

    @staticmethod
    def _safe_div(numerador, denominador):
        if not denominador:
            return Decimal("0.00")
        return (Decimal(numerador) / Decimal(denominador)).quantize(Decimal("0.01"))
