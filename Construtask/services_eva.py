from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum

from .models import PlanoContas
from .models_planejamento import PlanoFisico, PlanoFisicoItem
from .services_integracao import IntegracaoService


class EVAService:
    @classmethod
    def calcular(cls, obra, data_referencia=None):
        data_referencia = data_referencia or date.today()
        zero = Decimal("0.00")

        bac = (
            PlanoContas.objects
            .filter(obra=obra)
            .annotate(filhos_count=Count("filhos"))
            .filter(filhos_count=0)
            .aggregate(total=Sum("valor_total"))["total"] or zero
        )

        if bac == zero:
            bac = (
                PlanoContas.objects
                .filter(obra=obra)
                .annotate(n_filhos=Count("filhos"))
                .filter(n_filhos=0)
                .aggregate(total=Sum("valor_total"))["total"] or zero
            )

        pv = cls._calcular_pv(obra, data_referencia)
        ev = IntegracaoService.calcular_valor_agregado_operacional(obra, data_referencia)
        ac = IntegracaoService.calcular_custo_real_operacional(obra, data_referencia)

        cv = ev - ac
        sv = ev - pv
        if pv == zero:
            cpi = Decimal("1.0000")
            spi = Decimal("1.0000")
        else:
            cpi = cls._safe_div(ev, ac)
            spi = cls._safe_div(ev, pv)
        eac = cls._safe_div(bac, cpi) if cpi > zero else bac
        etc = eac - ac
        vac = bac - eac
        tcpi = cls._safe_div(bac - ev, bac - ac)

        return {
            "BAC": bac,
            "PV": pv,
            "EV": ev,
            "AC": ac,
            "CV": cv.quantize(Decimal("0.01")),
            "SV": sv.quantize(Decimal("0.01")),
            "CPI": cpi,
            "SPI": spi,
            "EAC": eac,
            "ETC": etc,
            "VAC": vac,
            "TCPI": tcpi,
            "percentual_planejado": cls._safe_div(pv, bac) * Decimal("100"),
            "percentual_executado": cls._safe_div(ev, bac) * Decimal("100"),
            "percentual_pago": cls._safe_div(ac, bac) * Decimal("100"),
            "status_semaforo": cls._semaforo(cpi, spi),
            "data_corte": data_referencia.isoformat(),
        }

    @classmethod
    def _calcular_pv(cls, obra, data_referencia):
        plano = (
            PlanoFisico.objects
            .filter(obra=obra)
            .order_by("-is_baseline", "-created_at")
            .first()
        )
        if not plano:
            return cls._pv_linear_legado(obra, data_referencia)
        return IntegracaoService.calcular_valor_planejado_ate_data(obra, data_referencia)

    @classmethod
    def _pv_linear_legado(cls, obra, data_referencia):
        zero = Decimal("0.00")
        if not obra.data_inicio or not obra.data_fim:
            return zero

        bac = (
            PlanoContas.objects
            .filter(obra=obra)
            .annotate(n_filhos=Count("filhos"))
            .filter(n_filhos=0)
            .aggregate(total=Sum("valor_total"))["total"] or zero
        )

        total_dias = (obra.data_fim - obra.data_inicio).days or 1
        dias_decorridos = max(0, (data_referencia - obra.data_inicio).days)
        proporcao = Decimal(str(min(dias_decorridos / total_dias, 1.0)))
        return bac * proporcao

    @staticmethod
    def _safe_div(numerador, denominador):
        zero = Decimal("0.00")
        if not denominador or denominador == zero:
            return zero
        return (Decimal(str(numerador)) / Decimal(str(denominador))).quantize(Decimal("0.0001"))

    @staticmethod
    def _semaforo(cpi, spi):
        limite_vermelho = Decimal("0.85")
        limite_amarelo = Decimal("0.95")
        if cpi < limite_vermelho or spi < limite_vermelho:
            return "VERMELHO"
        if cpi < limite_amarelo or spi < limite_amarelo:
            return "AMARELO"
        return "VERDE"
