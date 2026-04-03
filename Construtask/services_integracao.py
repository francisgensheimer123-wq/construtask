from datetime import date
from decimal import Decimal

from django.db.models import Count, F, Sum

from .models import Compromisso, Medicao, NotaFiscalCentroCusto, PlanoContas
from .models_planejamento import PlanoFisico, PlanoFisicoItem


class IntegracaoService:
    @staticmethod
    def _folhas_eap(obra):
        return (
            PlanoContas.objects.filter(obra=obra)
            .annotate(filhos_count=Count("filhos"))
            .filter(filhos_count=0)
        )

    @staticmethod
    def obter_baseline_ativo(obra):
        return (
            PlanoFisico.objects.filter(obra=obra, is_baseline=True)
            .order_by("-versao", "-created_at")
            .first()
        )

    @classmethod
    def calcular_valor_planejado_ate_data(cls, obra, data_referencia=None):
        data_referencia = data_referencia or date.today()
        baseline = cls.obter_baseline_ativo(obra)
        if not baseline:
            return Decimal("0.00")

        total = Decimal("0.00")
        for item in baseline.itens.all():
            total += cls._valor_planejado_item_ate_data(item, data_referencia)
        return total.quantize(Decimal("0.01"))

    @staticmethod
    def _valor_planejado_item_ate_data(item, data_referencia):
        valor = item.valor_planejado or Decimal("0.00")
        if not valor:
            return Decimal("0.00")
        if not item.data_inicio_prevista or not item.data_fim_prevista:
            return valor if item.data_inicio_prevista and item.data_inicio_prevista <= data_referencia else Decimal("0.00")
        if data_referencia <= item.data_inicio_prevista:
            return Decimal("0.00")
        if data_referencia >= item.data_fim_prevista:
            return valor

        total_dias = max((item.data_fim_prevista - item.data_inicio_prevista).days, 1)
        dias_decorridos = max((data_referencia - item.data_inicio_prevista).days, 0)
        percentual = Decimal(dias_decorridos) / Decimal(total_dias)
        return (valor * percentual).quantize(Decimal("0.01"))

    @classmethod
    def consolidar_obra(cls, obra, data_referencia=None):
        data_referencia = data_referencia or date.today()
        folhas = cls._folhas_eap(obra)
        orcado = folhas.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00")
        comprometido = Compromisso.objects.filter(obra=obra).aggregate(total=Sum("valor_contratado"))["total"] or Decimal("0.00")
        medido = Medicao.objects.filter(obra=obra).aggregate(total=Sum("valor_medido"))["total"] or Decimal("0.00")
        executado = (
            NotaFiscalCentroCusto.objects.filter(centro_custo__obra=obra, nota_fiscal__data_emissao__lte=data_referencia)
            .aggregate(total=Sum("valor"))["total"]
            or Decimal("0.00")
        )
        planejado = cls.calcular_valor_planejado_ate_data(obra, data_referencia)
        return {
            "orcado": orcado,
            "comprometido": comprometido,
            "medido": medido,
            "executado": executado,
            "planejado": planejado,
        }

    @classmethod
    def consolidar_plano_contas(cls, plano_contas, data_referencia=None):
        data_referencia = data_referencia or date.today()
        centros = plano_contas.get_descendants(include_self=True)
        centros_ids = list(centros.values_list("id", flat=True))
        return {
            "orcado": centros.aggregate(total=Sum("valor_total"))["total"] or Decimal("0.00"),
            "comprometido": Compromisso.objects.filter(itens__centro_custo_id__in=centros_ids).aggregate(total=Sum("itens__valor_total"))["total"] or Decimal("0.00"),
            "medido": Medicao.objects.filter(itens__centro_custo_id__in=centros_ids).aggregate(total=Sum("itens__valor_total"))["total"] or Decimal("0.00"),
            "executado": NotaFiscalCentroCusto.objects.filter(
                centro_custo_id__in=centros_ids,
                nota_fiscal__data_emissao__lte=data_referencia,
            ).aggregate(total=Sum("valor"))["total"] or Decimal("0.00"),
            "planejado": (
                PlanoFisicoItem.objects.filter(plano__obra=plano_contas.obra, plano_contas_id__in=centros_ids)
                .aggregate(total=Sum("valor_planejado"))["total"]
                or Decimal("0.00")
            ),
        }
