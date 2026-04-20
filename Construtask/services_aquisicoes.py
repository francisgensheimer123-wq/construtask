from decimal import Decimal

from django.db import transaction

from .models import Compromisso, CompromissoItem
from .models_aquisicoes import OrdemCompra, OrdemCompraItem


class AquisicoesService:
    @classmethod
    @transaction.atomic
    def emitir_ordem_compra(cls, cotacao, usuario, descricao="", tipo_resultado="PEDIDO_COMPRA"):
        if cotacao.status != "APROVADA":
            raise ValueError("A cotacao precisa estar aprovada para gerar compras e contratacoes.")

        fornecedores_comparados = (
            cotacao.solicitacao.cotacoes.values_list("fornecedor_id", flat=True).distinct().count()
        )
        if fornecedores_comparados < 2:
            raise ValueError("A cotacao precisa comparar pelo menos 2 fornecedores antes de gerar compras e contratacoes.")

        if tipo_resultado not in {"PEDIDO_COMPRA", "CONTRATO"}:
            raise ValueError("Tipo de resultado invalido para a cotacao.")

        ordem = OrdemCompra.objects.create(
            empresa=cotacao.empresa,
            obra=cotacao.obra,
            solicitacao=cotacao.solicitacao,
            cotacao_aprovada=cotacao,
            fornecedor=cotacao.fornecedor,
            status="RASCUNHO",
            data_emissao=cotacao.data_cotacao,
            descricao=descricao or cotacao.solicitacao.titulo,
            emitido_por=usuario,
        )

        compromisso = Compromisso.objects.create(
            tipo=tipo_resultado,
            obra=cotacao.obra,
            centro_custo=cotacao.solicitacao.plano_contas or cotacao.solicitacao.itens.first().plano_contas,
            descricao=descricao or cotacao.solicitacao.titulo,
            fornecedor=cotacao.fornecedor.razao_social,
            cnpj=cotacao.fornecedor.cnpj,
            responsavel=getattr(usuario, "get_full_name", lambda: "")() or usuario.username,
            telefone=cotacao.fornecedor.telefone or "",
            data_assinatura=cotacao.data_cotacao,
            status="APROVADO",
        )

        total = Decimal("0.00")
        for item in cotacao.itens.select_related("item_solicitacao__plano_contas"):
            item_solic = item.item_solicitacao
            OrdemCompraItem.objects.create(
                ordem_compra=ordem,
                plano_contas=item_solic.plano_contas,
                unidade=item_solic.unidade,
                quantidade=item_solic.quantidade,
                valor_unitario=item.valor_unitario,
            )
            CompromissoItem.objects.create(
                compromisso=compromisso,
                centro_custo=item_solic.plano_contas,
                descricao_tecnica=item_solic.descricao_tecnica,
                unidade=item_solic.unidade,
                quantidade=item_solic.quantidade,
                valor_unitario=item.valor_unitario,
            )
            total += item.valor_total

        compromisso.recalcular_totais_por_itens()
        ordem.compromisso_relacionado = compromisso
        ordem.valor_total = total.quantize(Decimal("0.01"))
        ordem.save(update_fields=["compromisso_relacionado", "valor_total"])

        cotacao.solicitacao.status = "ENCERRADA"
        cotacao.solicitacao.save(update_fields=["status"])
        return ordem
