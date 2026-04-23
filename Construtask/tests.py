from datetime import date
from decimal import Decimal
from datetime import timedelta
from io import BytesIO
from io import StringIO
import json
import os
from unittest.mock import patch

import pandas as pd
from celery.exceptions import SoftTimeLimitExceeded
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .domain import (
    arredondar_moeda,
    calcular_total_item,
    calcular_saldo_disponivel_compromisso,
    gerar_numero_documento,
    hidratar_medicao_do_contrato,
    validar_itens_compromisso_orcamento,
    validar_itens_medicao_contrato,
)
from .models import (
    AditivoContrato,
    AlertaOperacional,
    AlertaOperacionalHistorico,
    AditivoContratoItem,
    AuditEvent,
    Compromisso,
    CompromissoItem,
    ConsentimentoLGPD,
    Cotacao,
    CotacaoItem,
    Empresa,
    ExecucaoRegraOperacional,
    Fornecedor,
    FornecedorAvaliacao,
    HistoricoReuniaoComunicacao,
    ItemPautaReuniao,
    JobAssincrono,
    MetricaRequisicao,
    Obra,
    OrcamentoBaseline,
    OrcamentoBaselineItem,
    OrdemCompra,
    OperacaoBackupSaaS,
    ParametroAlertaEmpresa,
    ParametroComunicacaoEmpresa,
    PermissaoModuloAcao,
    RegistroAcessoDadoPessoal,
    RegistroTratamentoDadoPessoal,
    ReuniaoComunicacao,
    RastroErroAplicacao,
    Medicao,
    MedicaoItem,
    NaoConformidade,
    NotaFiscal,
    NotaFiscalCentroCusto,
    Documento,
    DocumentoRevisao,
    PlanoContas,
    SolicitacaoCompra,
    SolicitacaoCompraItem,
    UsuarioEmpresa,
)
from .models_planejamento import MapaCorrespondencia, PlanoFisico, PlanoFisicoItem
from .models_risco import Risco
from .forms import AditivoContratoItemFormSet, MedicaoForm, NotaFiscalForm
from .importacao_cronograma import CronogramaService, MapeamentoService
from .queries.financeiro import construir_fluxo_financeiro_contratual
from .views import ContratoDetailView, HomeView
from .services import importar_plano_contas_excel, obter_dados_contrato, validar_rateio_nota
from .services_aquisicoes import AquisicoesService
from .services_eva import EVAService
from .services_indicadores import IndicadoresService
from .services_integracao import IntegracaoService
from .services_qualidade import QualidadeWorkflowService
from .services_lgpd import (
    anonimizar_fornecedor_inativo,
    anonimizar_usuario_inativo,
    descartar_fornecedor_anonimizado,
    excluir_logicamente_fornecedor,
)
from .services_alertas import (
    CODIGO_ALERTA_CONTRATO_SEM_MEDICAO,
    CODIGO_ALERTA_MEDICAO_SEM_NOTA,
    CODIGO_ALERTA_NC_SEM_EVOLUCAO,
    CODIGO_ALERTA_NOTA_SEM_RATEIO,
    CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
    CODIGO_ALERTA_RISCO_VENCIDO,
    catalogo_alertas_empresa,
    sincronizar_alertas_contrato_sem_medicao,
    sincronizar_alertas_medicao_sem_nota,
    sincronizar_alertas_nc_sem_evolucao,
    sincronizar_alertas_nota_sem_rateio,
    sincronizar_alertas_operacionais_obra,
    sincronizar_alertas_planejamento_suprimentos,
    sincronizar_alertas_risco_vencido,
)
from .templatetags.formatters import money_br, trunc2
from .text_normalization import corrigir_mojibake, normalizar_texto_cadastral


class PlanoContasTests(TestCase):
    def test_gera_codigo_hierarquico_com_parent(self):
        raiz = PlanoContas.objects.create(descricao="Raiz")
        filho = PlanoContas.objects.create(descricao="Filho", parent=raiz)

        self.assertEqual(raiz.codigo, "01")
        self.assertEqual(filho.codigo, "01.01")


class NumericParsingTests(TestCase):
    def test_arredondar_moeda_aceita_formato_brasileiro(self):
        self.assertEqual(arredondar_moeda("1.234,56"), Decimal("1234.56"))

    def test_calcular_total_item_aceita_virgula_brasileira(self):
        self.assertEqual(calcular_total_item("10,5", "1.234,56"), Decimal("12962.88"))


class BaseFinanceTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.empresa = Empresa.objects.create(
            nome="Empresa Teste",
            nome_fantasia="Empresa Teste",
            cnpj="11.111.111/0001-11",
        )
        self.obra = Obra.objects.create(
            empresa=self.empresa,
            codigo="OBR-001",
            nome="Obra Teste",
            status="EM_ANDAMENTO",
        )
        self.grupo = PlanoContas.objects.create(codigo="01", descricao="Estrutura", obra=self.obra)
        self.analitico = PlanoContas.objects.create(
            codigo="01.01.01.01.01.01",
            descricao="Fundacao",
            parent=self.grupo,
            obra=self.obra,
            unidade="m3",
            quantidade=Decimal("100.00"),
            valor_unitario=Decimal("10.00"),
        )
        self.analitico_2 = PlanoContas.objects.create(
            codigo="01.01.01.01.01.02",
            descricao="Estrutura Metalica",
            parent=self.grupo,
            obra=self.obra,
            unidade="kg",
            quantidade=Decimal("200.00"),
            valor_unitario=Decimal("5.00"),
        )


class RegrasFinanceirasTests(BaseFinanceTestCase):
    def test_compromisso_nao_pode_exceder_orcamento(self):
        Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato base",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("600.00"),
            data_assinatura="2026-03-01",
        )

        compromisso = Compromisso(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato excedente",
            fornecedor="Fornecedor B",
            cnpj="98.765.432/0001-10",
            responsavel="Joao",
            telefone="11888888888",
            valor_contratado=Decimal("500.00"),
            data_assinatura="2026-03-02",
        )

        with self.assertRaises(ValidationError):
            compromisso.clean()

    def test_nota_fiscal_exibe_saldo_da_medicao_na_mensagem(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Estrutura",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("1000.00"),
            data_assinatura="2026-03-01",
        )
        medicao = Medicao.objects.create(
            contrato=contrato,
            descricao="Primeira medicao",
            valor_medido=Decimal("600.00"),
            data_medicao="2026-03-10",
        )
        NotaFiscal.objects.create(
            numero="NF-BASE",
            tipo="SERVICO",
            data_emissao="2026-03-11",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota base",
            valor_total=Decimal("500.00"),
            medicao=medicao,
        )

        nota = NotaFiscal(
            numero="NF-EXC",
            tipo="SERVICO",
            data_emissao="2026-03-12",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota excedente",
            valor_total=Decimal("150.00"),
            medicao=medicao,
        )

        with self.assertRaises(ValidationError) as exc:
            nota.clean()

        self.assertIn("Saldo da medição: 100.00", str(exc.exception))

    def test_rateio_nao_pode_ultrapassar_valor_da_nota(self):
        pedido = Compromisso.objects.create(
            tipo="PEDIDO_COMPRA",
            centro_custo=self.analitico,
            descricao="Aco",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("1000.00"),
            data_assinatura="2026-03-01",
        )
        nota = NotaFiscal.objects.create(
            numero="NF-1",
            tipo="MATERIAL",
            data_emissao="2026-03-20",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota material",
            valor_total=Decimal("1000.00"),
            pedido_compra=pedido,
        )

        NotaFiscalCentroCusto.objects.create(
            nota_fiscal=nota,
            centro_custo=self.analitico,
            valor=Decimal("700.00"),
        )

        rateio = NotaFiscalCentroCusto(
            nota_fiscal=nota,
            centro_custo=self.analitico,
            valor=Decimal("400.00"),
        )

        with self.assertRaises(ValidationError):
            rateio.clean()


class ItemizacaoTests(BaseFinanceTestCase):
    def test_compromisso_recalcula_total_pelos_itens(self):
        compromisso = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato itemizado",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            data_assinatura="2026-03-01",
        )

        CompromissoItem.objects.create(
            compromisso=compromisso,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("10.00"),
            valor_unitario=Decimal("15.00"),
        )
        CompromissoItem.objects.create(
            compromisso=compromisso,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("5.00"),
            valor_unitario=Decimal("20.00"),
        )

        compromisso.refresh_from_db()
        self.assertEqual(compromisso.valor_contratado, Decimal("250.00"))
        self.assertEqual(compromisso.quantidade_total, Decimal("15.00"))
        self.assertEqual(compromisso.valor_unitario_medio, Decimal("16.67"))

    def test_medicao_recalcula_total_pelos_itens(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato itemizado",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("1000.00"),
            data_assinatura="2026-03-01",
        )

        medicao = Medicao.objects.create(
            contrato=contrato,
            descricao="Medicao itemizada",
            data_medicao="2026-03-10",
        )

        MedicaoItem.objects.create(
            medicao=medicao,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("8.00"),
            valor_unitario=Decimal("25.00"),
        )

        medicao.refresh_from_db()
        self.assertEqual(medicao.valor_medido, Decimal("200.00"))
        self.assertEqual(medicao.quantidade_total, Decimal("8.00"))
        self.assertEqual(medicao.valor_unitario_medio, Decimal("25.00"))

    def test_validacao_itemizada_do_compromisso_considera_orcamento(self):
        Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato base",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("600.00"),
            data_assinatura="2026-03-01",
        )
        compromisso = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato novo",
            fornecedor="Fornecedor B",
            cnpj="98.765.432/0001-10",
            responsavel="Joao",
            telefone="11888888888",
            data_assinatura="2026-03-02",
        )

        with self.assertRaises(ValidationError):
            validar_itens_compromisso_orcamento(
                compromisso,
                [
                    {
                        "centro_custo": self.analitico,
                        "quantidade": Decimal("50.00"),
                        "valor_unitario": Decimal("10.00"),
                    }
                ],
            )

    def test_validacao_itemizada_da_medicao_considera_saldo_do_contrato(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato com itens",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            data_assinatura="2026-03-01",
        )
        CompromissoItem.objects.create(
            compromisso=contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("20.00"),
            valor_unitario=Decimal("20.00"),
        )
        medicao_existente = Medicao.objects.create(
            contrato=contrato,
            descricao="Medicao anterior",
            data_medicao="2026-03-10",
        )
        MedicaoItem.objects.create(
            medicao=medicao_existente,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("10.00"),
            valor_unitario=Decimal("20.00"),
        )

        medicao = Medicao.objects.create(
            contrato=contrato,
            descricao="Nova medicao",
            data_medicao="2026-03-12",
        )

        with self.assertRaises(ValidationError):
            validar_itens_medicao_contrato(
                medicao,
                [
                    {
                        "centro_custo": self.analitico,
                        "quantidade": Decimal("15.00"),
                        "valor_unitario": Decimal("20.00"),
                    }
                ],
            )


class ServicesTests(BaseFinanceTestCase):
    def setUp(self):
        super().setUp()
        self.compromisso = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato servico",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("1000.00"),
            data_assinatura="2026-03-01",
        )

    def test_obter_dados_contrato_retorna_payload(self):
        CompromissoItem.objects.create(
            compromisso=self.compromisso,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("5.00"),
            valor_unitario=Decimal("20.00"),
        )
        data = obter_dados_contrato(self.compromisso)

        self.assertEqual(data["fornecedor"], "Fornecedor A")
        self.assertEqual(data["cnpj"], "12.345.678/0001-90")
        self.assertEqual(data["valor_contrato"], "100.00")
        self.assertEqual(data["centro_custo"], self.analitico.codigo)
        self.assertEqual(len(data["centros_custo"]), 1)
        self.assertEqual(data["centros_custo"][0]["id"], self.analitico.id)
        self.assertEqual(data["centros_custo"][0]["valor_unitario"], "20.00")

    def test_validar_rateio_nota_reaproveita_regra_fora_do_admin(self):
        nota = NotaFiscal.objects.create(
            numero="NF-2",
            tipo="MATERIAL",
            data_emissao="2026-03-20",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota material",
            valor_total=Decimal("1000.00"),
            pedido_compra=Compromisso.objects.create(
                tipo="PEDIDO_COMPRA",
                centro_custo=self.analitico,
                descricao="Pedido",
                fornecedor="Fornecedor A",
                cnpj="12.345.678/0001-90",
                responsavel="Maria",
                telefone="11999999999",
                valor_contratado=Decimal("1000.00"),
                data_assinatura="2026-03-01",
            ),
        )

        with self.assertRaises(ValidationError):
            validar_rateio_nota(
                nota,
                [(self.analitico, Decimal("600.00")), (self.analitico, Decimal("500.00"))],
            )

    def test_rateio_da_nota_nao_pode_ser_menor_que_valor_total(self):
        pedido = Compromisso.objects.create(
            tipo="PEDIDO_COMPRA",
            centro_custo=self.analitico,
            descricao="Pedido rateio",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("1000.00"),
            data_assinatura="2026-03-01",
        )
        nota = NotaFiscal.objects.create(
            numero="NF-3",
            tipo="MATERIAL",
            data_emissao="2026-03-20",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota material",
            valor_total=Decimal("100.00"),
            pedido_compra=pedido,
        )
        formset_class = __import__("Construtask.forms", fromlist=["NotaFiscalCentroCustoFormSet"]).NotaFiscalCentroCustoFormSet
        formset = formset_class(
            data={
                "rateio-TOTAL_FORMS": "1",
                "rateio-INITIAL_FORMS": "0",
                "rateio-MIN_NUM_FORMS": "0",
                "rateio-MAX_NUM_FORMS": "1000",
                "rateio-0-centro_custo": str(self.analitico.pk),
                "rateio-0-valor": "90.00",
            },
            instance=nota,
            prefix="rateio",
            centros_queryset=PlanoContas.objects.filter(pk=self.analitico.pk),
        )
        self.assertFalse(formset.is_valid())
        self.assertIn("exatamente igual", str(formset.non_form_errors()))

    def test_importar_plano_contas_excel_bloqueia_quando_ha_dependencias(self):
        arquivo = BytesIO()
        df = pd.DataFrame(
            [
                {"ITEM": "1", "DESCRIÃ‡ÃƒO": "Estrutura", "UN": None, "QTD": None, "VALOR UNIT.": None},
                {"ITEM": "1.1", "DESCRIÃ‡ÃƒO": "Concreto", "UN": "m3", "QTD": "2", "VALOR UNIT.": "50"},
            ]
        )
        df.to_excel(arquivo, index=False)
        arquivo.seek(0)

        with self.assertRaises(ValidationError):
            importar_plano_contas_excel(arquivo)


class ImportacaoPlanoContasServiceTests(TestCase):
    def test_importar_plano_contas_excel_via_service(self):
        arquivo = BytesIO()
        df = pd.DataFrame(
            [
                {"ITEM": "1", "DESCRIÃ‡ÃƒO": "Estrutura", "UN": None, "QTD": None, "VALOR UNIT.": None},
                {"ITEM": "1.1", "DESCRIÃ‡ÃƒO": "Concreto", "UN": "m3", "QTD": "2", "VALOR UNIT.": "50"},
            ]
        )
        df.to_excel(arquivo, index=False)
        arquivo.seek(0)

        importar_plano_contas_excel(arquivo)

        self.assertTrue(PlanoContas.objects.filter(codigo="1").exists())
        folha = PlanoContas.objects.get(codigo="1.1.1.1.1.1")
        self.assertEqual(folha.descricao, "Concreto")
        self.assertEqual(folha.valor_total, Decimal("100.00"))


class DomainTests(BaseFinanceTestCase):
    def test_calcula_saldo_disponivel_do_compromisso(self):
        Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato base",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("400.00"),
            data_assinatura="2026-03-01",
        )

        compromisso = Compromisso(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato novo",
            fornecedor="Fornecedor B",
            cnpj="98.765.432/0001-10",
            responsavel="Joao",
            telefone="11888888888",
            valor_contratado=Decimal("200.00"),
            data_assinatura="2026-03-02",
        )

        self.assertEqual(calcular_saldo_disponivel_compromisso(compromisso), Decimal("600.00"))

    def test_gera_numero_documento_com_prefixo(self):
        Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato base",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("10.00"),
            data_assinatura="2026-03-01",
        )

        numero = gerar_numero_documento(Compromisso, "CTR-", "numero")
        self.assertEqual(numero, f"CTR-{date.today().year}-0002")

    def test_gera_numero_documento_reinicia_sequencia_em_novo_ano(self):
        Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato legado",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("10.00"),
            data_assinatura="2025-03-01",
            numero=f"CTR-{date.today().year - 1}-0042",
        )

        numero = gerar_numero_documento(Compromisso, "CTR-", "numero")
        self.assertEqual(numero, f"CTR-{date.today().year}-0001")

    def test_hidrata_medicao_a_partir_do_contrato(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato base",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("800.00"),
            data_assinatura="2026-03-01",
        )
        medicao = Medicao(
            contrato=contrato,
            descricao="Medicao 1",
            valor_medido=Decimal("100.00"),
            data_medicao="2026-03-05",
        )

        hidratar_medicao_do_contrato(medicao)

        self.assertEqual(medicao.fornecedor, contrato.fornecedor)
        self.assertEqual(medicao.cnpj, contrato.cnpj)
        self.assertEqual(medicao.centro_custo, contrato.centro_custo)

    def test_medicao_str_retorna_numero_da_medicao(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato base",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("800.00"),
            data_assinatura="2026-03-01",
        )
        medicao = Medicao.objects.create(
            contrato=contrato,
            descricao="Medicao 1",
            valor_medido=Decimal("100.00"),
            data_medicao="2026-03-05",
        )
        self.assertEqual(str(medicao), medicao.numero_da_medicao)

    def test_trunc2_formata_com_duas_casas_truncadas(self):
        self.assertEqual(trunc2(Decimal("12.349")), "12,34")

    def test_money_br_formata_com_cifrao_virgula_e_duas_casas(self):
        self.assertEqual(money_br(Decimal("1234.569")), "$ 1.234,56")


class AppViewsTests(BaseFinanceTestCase):
    def setUp(self):
        super().setUp()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tester", password="senhaforte123")
        self.usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=self.user,
            empresa=self.empresa,
            is_admin_empresa=True,
        )
        self.usuario_empresa.obras_permitidas.add(self.obra)
        self.client.force_login(self.user)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()
        self.contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato web",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("100.00"),
            data_assinatura="2026-03-01",
        )
        self.pedido = Compromisso.objects.create(
            tipo="PEDIDO_COMPRA",
            centro_custo=self.analitico,
            descricao="Pedido web",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("100.00"),
            data_assinatura="2026-03-01",
        )

    def _aprovar_contrato(self, contrato=None):
        contrato = contrato or self.contrato
        contrato.status = "APROVADO"
        contrato.enviado_para_aprovacao_por = self.user
        contrato.aprovado_por = self.user
        contrato.parecer_aprovacao = "Contrato aprovado para testes."
        contrato.save()
        return contrato

    def _aprovar_pedido(self, pedido=None):
        pedido = pedido or self.pedido
        pedido.status = "APROVADO"
        pedido.enviado_para_aprovacao_por = self.user
        pedido.aprovado_por = self.user
        pedido.parecer_aprovacao = "Pedido aprovado para testes."
        pedido.save()
        return pedido

    def _criar_usuario_operacional(self, username, papel_aprovacao):
        user_model = get_user_model()
        user = user_model.objects.create_user(username=username, password="senhaforte123")
        usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=user,
            empresa=self.empresa,
            is_admin_empresa=False,
            papel_aprovacao=papel_aprovacao,
        )
        usuario_empresa.obras_permitidas.add(self.obra)
        return user

    def test_home_view_responde(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Painel Operacional")

    def test_exclusao_plano_contas_com_id_formatado_nao_gera_erro_tecnico(self):
        response = self.client.post(
            reverse("plano_contas_delete"),
            {"id": "10.518"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nao foi possivel identificar o registro para exclusao.")

    def test_exclusao_plano_contas_protegida_mostra_motivo_ao_usuario(self):
        response = self.client.post(
            reverse("plano_contas_delete"),
            {"id": str(self.analitico.pk)},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nao pode ser excluido porque possui vinculos em outras operacoes do sistema")

    def test_grupo_planejamento_exibe_links_agrupados(self):
        response = self.client.get(reverse("modulo_grupo", args=["planejamento"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Planejamento")
        self.assertContains(response, "Plano de Contas")
        self.assertContains(response, "Cronograma")
        self.assertContains(response, "Riscos")
        self.assertContains(response, "Alertas Operacionais")

    def test_home_exibe_menu_agrupado(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("modulo_grupo", args=["planejamento"]))
        self.assertContains(response, reverse("modulo_grupo", args=["qualidade"]))
        self.assertContains(response, reverse("modulo_grupo", args=["aquisicoes"]))
        self.assertContains(response, reverse("modulo_grupo", args=["comunicacoes"]))
        self.assertContains(response, reverse("modulo_grupo", args=["relatorios"]))
        self.assertContains(response, reverse("modulo_grupo", args=["juridico"]))
        self.assertContains(response, reverse("modulo_grupo", args=["financeiro"]))

    def test_edicao_de_item_do_cronograma_salva_datas_percentual_e_calcula_valor_realizado(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma para edicao",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="CRN-001",
            atividade="Alvenaria",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 10),
            percentual_concluido=0,
            valor_planejado=Decimal("1000.00"),
            valor_realizado=Decimal("0.00"),
        )

        response = self.client.post(
            reverse("plano_fisico_item_update", args=[plano.pk, item.pk]),
            {
                "data_inicio_real": "2026-04-02",
                "data_fim_real": "2026-04-08",
                "percentual_concluido": "25",
                "plano_contas": str(self.analitico.pk),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.data_inicio_real, date(2026, 4, 2))
        self.assertEqual(item.data_fim_real, date(2026, 4, 8))
        self.assertEqual(item.percentual_concluido, 25)
        self.assertEqual(item.valor_realizado, Decimal("250.00"))

    def test_cronograma_detail_atualiza_realizado_de_folha_e_define_inicio_real(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma lancamento direto",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="CRN-DIR-001",
            atividade="Alvenaria",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 10),
            percentual_concluido=0,
            valor_planejado=Decimal("1000.00"),
        )

        response = self.client.post(
            reverse("plano_fisico_detail", args=[plano.pk]),
            {
                "acao": "atualizar_cronograma",
                f"realizado_{item.pk}": "25",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.percentual_concluido, 25)
        self.assertEqual(item.data_inicio_real, timezone.localdate())
        self.assertIsNone(item.data_fim_real)
        self.assertEqual(item.valor_realizado, Decimal("250.00"))

    def test_cronograma_detail_lancamento_direto_100_define_inicio_e_fim_no_mesmo_momento(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma cem por cento",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="CRN-DIR-100",
            atividade="Estrutura",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 10),
            percentual_concluido=0,
            valor_planejado=Decimal("500.00"),
        )

        response = self.client.post(
            reverse("plano_fisico_detail", args=[plano.pk]),
            {
                "acao": "atualizar_cronograma",
                f"realizado_{item.pk}": "100",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.percentual_concluido, 100)
        self.assertEqual(item.data_inicio_real, timezone.localdate())
        self.assertEqual(item.data_fim_real, timezone.localdate())
        self.assertEqual(item.valor_realizado, Decimal("500.00"))

    def test_cronograma_detail_mantem_pai_consolidado_e_atualiza_somente_filho(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma pai consolidado",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        pai = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="PAI-001",
            atividade="Pacote principal",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 10),
            valor_planejado=Decimal("0.00"),
            percentual_concluido=0,
        )
        filho = PlanoFisicoItem.objects.create(
            plano=plano,
            parent=pai,
            plano_contas=self.analitico,
            codigo_atividade="FILHO-001",
            atividade="Atividade filha",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 5),
            valor_planejado=Decimal("1000.00"),
            percentual_concluido=0,
        )

        response = self.client.post(
            reverse("plano_fisico_detail", args=[plano.pk]),
            {
                "acao": "atualizar_cronograma",
                f"realizado_{pai.pk}": "90",
                f"realizado_{filho.pk}": "40",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        pai.refresh_from_db()
        filho.refresh_from_db()
        self.assertEqual(pai.percentual_concluido, 0)
        self.assertEqual(filho.percentual_concluido, 40)
        self.assertEqual(filho.data_inicio_real, timezone.localdate())
        response_detail = self.client.get(reverse("plano_fisico_detail", args=[plano.pk]))
        self.assertEqual(response_detail.status_code, 200)
        item_pai = next(item_ctx for item_ctx in response_detail.context["itens"] if item_ctx.pk == pai.pk)
        self.assertEqual(item_pai.percentual_realizado_exibicao, 40.0)

    def test_atualizacao_de_obra_registra_usuario_no_historico(self):
        response = self.client.post(
            reverse("obra_update", args=[self.obra.pk]),
            {
                "codigo": self.obra.codigo,
                "nome": "Obra Atualizada",
                "cliente": "Cliente Revisado",
                "responsavel": "Responsavel Revisado",
                "status": "EM_ANDAMENTO",
                "data_inicio": "",
                "data_fim": "",
                "descricao": "Descricao atualizada",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        historico = self.obra.historicos.order_by("-criado_em").first()
        self.assertIsNotNone(historico)
        self.assertEqual(historico.usuario, self.user)

    def test_atualizacao_de_obra_registra_before_after_na_auditoria(self):
        response = self.client.post(
            reverse("obra_update", args=[self.obra.pk]),
            {
                "codigo": self.obra.codigo,
                "nome": "Obra Auditada",
                "cliente": self.obra.cliente,
                "responsavel": self.obra.responsavel,
                "status": self.obra.status,
                "data_inicio": "",
                "data_fim": "",
                "descricao": self.obra.descricao,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        evento = AuditEvent.objects.filter(entidade_app="Construtask.Obra", objeto_id=self.obra.pk, acao="UPDATE").order_by("-timestamp").first()
        self.assertIsNotNone(evento)
        self.assertEqual(evento.antes.get("nome"), "Obra Teste")
        self.assertEqual(evento.depois.get("nome"), "Obra Auditada")
        self.assertEqual(evento.usuario, self.user)

    def test_empresa_admin_exibe_parametros_de_alerta(self):
        response = self.client.get(reverse("empresa_admin"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gatilhos Operacionais dos Alertas")
        self.assertContains(response, "PLAN-SUP-001")
        self.assertContains(response, "COST-BUD-001")
        self.assertContains(response, "Dias sem nota fiscal para alertar medicao")

    def test_usuario_nao_lista_obras_de_outra_empresa(self):
        empresa_2 = Empresa.objects.create(
            nome="Empresa Paralela",
            nome_fantasia="Empresa Paralela",
            cnpj="55.555.555/0001-55",
        )
        Obra.objects.create(
            empresa=empresa_2,
            codigo="OBR-999",
            nome="Obra Restrita",
            status="EM_ANDAMENTO",
        )

        response = self.client.get(reverse("obra_list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "OBR-999")

    def test_selecao_de_obra_rejeita_obra_sem_permissao(self):
        empresa_2 = Empresa.objects.create(
            nome="Empresa Externa",
            nome_fantasia="Empresa Externa",
            cnpj="66.666.666/0001-66",
        )
        obra_externa = Obra.objects.create(
            empresa=empresa_2,
            codigo="OBR-888",
            nome="Obra Externa",
            status="EM_ANDAMENTO",
        )

        response = self.client.post(
            reverse("selecionar_obra_contexto"),
            {"obra_contexto": obra_externa.pk, "next": reverse("home")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertEqual(session.get("obra_contexto_id"), self.obra.pk)

    def test_documento_de_outra_empresa_retorna_404(self):
        empresa_2 = Empresa.objects.create(
            nome="Empresa Documental",
            nome_fantasia="Empresa Documental",
            cnpj="77.777.777/0001-77",
        )
        obra_2 = Obra.objects.create(
            empresa=empresa_2,
            codigo="OBR-777",
            nome="Obra Documental",
            status="EM_ANDAMENTO",
        )
        documento = Documento.objects.create(
            empresa=empresa_2,
            obra=obra_2,
            tipo_documento="PROCEDIMENTO",
            codigo_documento="PRO-2026-0001",
            titulo="Procedimento Restrito",
            criado_por=self.user,
        )

        response = self.client.get(reverse("documento_detail", args=[documento.pk]))

        self.assertEqual(response.status_code, 404)

    def test_documento_workflow_devolve_para_ajuste_com_auditoria(self):
        documento = Documento.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            tipo_documento="PROCEDIMENTO",
            codigo_documento="PRO-2026-0100",
            titulo="Procedimento Operacional",
            criado_por=self.user,
        )
        revisao = DocumentoRevisao.objects.create(
            documento=documento,
            versao=1,
            arquivo=SimpleUploadedFile("procedimento.pdf", b"%PDF-1.4 teste", content_type="application/pdf"),
            checksum="abc123",
            status="ELABORACAO",
            criado_por=self.user,
        )

        response_envio = self.client.post(
            reverse("documento_detail", args=[documento.pk]),
            {"workflow_action": "1", "acao": "ENVIAR_REVISAO", "parecer": "Pronto para validar."},
        )
        self.assertEqual(response_envio.status_code, 302)

        documento.refresh_from_db()
        revisao.refresh_from_db()
        self.assertEqual(documento.status, "EM_REVISAO")
        self.assertEqual(revisao.status, "REVISAO")

        response_ajuste = self.client.post(
            reverse("documento_detail", args=[documento.pk]),
            {"workflow_action": "1", "acao": "DEVOLVER_AJUSTE", "parecer": "Ajustar cabeçalho."},
        )
        self.assertEqual(response_ajuste.status_code, 302)

        documento.refresh_from_db()
        revisao.refresh_from_db()
        self.assertEqual(documento.status, "RASCUNHO")
        self.assertEqual(revisao.status, "ELABORACAO")
        self.assertEqual(revisao.parecer, "Ajustar cabeçalho.")
        self.assertTrue(
            AuditEvent.objects.filter(
                entidade_app="Construtask.Documento",
                objeto_id=documento.pk,
                acao="REJECT",
            ).exists()
        )

    def test_documento_workflow_aprovacao_bloqueia_tecnico(self):
        tecnico = self._criar_usuario_operacional("tecnico_docs", "TECNICO_OBRAS")
        documento = Documento.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            tipo_documento="PROCEDIMENTO",
            codigo_documento="PRO-2026-0101",
            titulo="Procedimento Tecnico",
            criado_por=self.user,
            status="EM_REVISAO",
        )
        revisao = DocumentoRevisao.objects.create(
            documento=documento,
            versao=1,
            arquivo=SimpleUploadedFile("procedimento-tecnico.pdf", b"%PDF-1.4 teste", content_type="application/pdf"),
            checksum="def456",
            status="REVISAO",
            criado_por=self.user,
        )

        self.client.force_login(tecnico)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("documento_detail", args=[documento.pk]),
            {"workflow_action": "1", "acao": "APROVAR", "parecer": "Aprovado."},
        )
        self.assertEqual(response.status_code, 302)

        documento.refresh_from_db()
        revisao.refresh_from_db()
        self.assertEqual(documento.status, "EM_REVISAO")
        self.assertEqual(revisao.status, "REVISAO")
        self.assertFalse(
            AuditEvent.objects.filter(
                entidade_app="Construtask.Documento",
                objeto_id=documento.pk,
                acao="APPROVE",
            ).exists()
        )

    def test_documento_update_bloqueado_fora_de_rascunho(self):
        documento = Documento.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            tipo_documento="PROCEDIMENTO",
            codigo_documento="PRO-2026-0102",
            titulo="Documento Em Revisao",
            criado_por=self.user,
            status="EM_REVISAO",
        )

        response = self.client.get(reverse("documento_update", args=[documento.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("documento_detail", args=[documento.pk]))

    def test_tecnico_nao_pode_encerrar_nao_conformidade(self):
        tecnico = self._criar_usuario_operacional("tecnico_qualidade", "TECNICO_OBRAS")
        nc = QualidadeWorkflowService.abrir(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            descricao="Falha de inspeção",
            responsavel=tecnico,
            criado_por=self.user,
        )
        self.client.force_login(tecnico)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("nao_conformidade_detail", args=[nc.pk]),
            {"acao": "ENCERRAMENTO", "observacao": "Tentativa indevida."},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        nc.refresh_from_db()
        self.assertEqual(nc.status, "ABERTA")

    def test_cronograma_de_outra_obra_retorna_404(self):
        obra_2 = Obra.objects.create(
            empresa=self.empresa,
            codigo="OBR-222",
            nome="Obra Sem Acesso",
            status="EM_ANDAMENTO",
        )
        plano = PlanoFisico.objects.create(
            obra=obra_2,
            titulo="Cronograma Restrito",
            responsavel_importacao=self.user,
            status="ATIVO",
        )

        response = self.client.get(reverse("plano_fisico_detail", args=[plano.pk]))

        self.assertEqual(response.status_code, 404)

    def test_exportacao_do_plano_contas_retorna_excel(self):
        response = self.client.get(reverse("plano_contas_export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_busca_em_compromissos_por_fornecedor(self):
        response = self.client.get(reverse("compromisso_list"), {"q": "Fornecedor A"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.contrato.numero)

    def test_empresa_admin_registra_acesso_lgpd_de_usuarios(self):
        response = self.client.get(reverse("empresa_admin"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            RegistroAcessoDadoPessoal.objects.filter(
                usuario=self.user,
                categoria_titular="USUARIO",
                entidade="UsuarioEmpresa",
                acao="ADMIN_LIST",
            ).exists()
        )

    def test_sistema_admin_exibe_base_operacional_saas(self):
        user_model = get_user_model()
        admin_sistema = user_model.objects.create_superuser(
            username="Construtask",
            email="sistema@construtask.com",
            password="senhaforte123",
        )
        self.client.force_login(admin_sistema)
        response = self.client.get(reverse("sistema_admin"), {"empresa": self.empresa.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Base Operacional SaaS")
        self.assertContains(response, "Backup e recuperacao")
        self.assertContains(response, "Storage de arquivos")

    def test_admin_empresa_nao_acessa_sistema_admin(self):
        response = self.client.get(reverse("sistema_admin"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apenas o superuser tecnico Construtask pode acessar o gerenciamento do sistema.")

    def test_superuser_construtask_nao_acessa_empresa_admin_sem_vinculo(self):
        user_model = get_user_model()
        admin_sistema = user_model.objects.create_superuser(
            username="Construtask",
            email="sistema@construtask.com",
            password="senhaforte123",
        )
        self.client.force_login(admin_sistema)

        response = self.client.get(reverse("empresa_admin"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apenas a administracao da empresa pode acessar esta pagina.")

    def test_sistema_admin_cria_admin_da_empresa(self):
        user_model = get_user_model()
        admin_sistema = user_model.objects.create_superuser(
            username="Construtask",
            email="sistema@construtask.com",
            password="senhaforte123",
        )
        self.client.force_login(admin_sistema)

        response = self.client.post(
            reverse("sistema_admin"),
            {
                "acao": "criar_admin_empresa",
                "empresa_id": str(self.empresa.pk),
                "username": "admin_empresa_novo",
                "email": "admin@empresa.com",
                "password": "senhaforte123",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            UsuarioEmpresa.objects.filter(
                empresa=self.empresa,
                usuario__username="admin_empresa_novo",
                is_admin_empresa=True,
            ).exists()
        )
        self.assertContains(response, "Admin da empresa")

    def test_fornecedor_list_registra_acesso_lgpd(self):
        Fornecedor.objects.create(
            empresa=self.empresa,
            razao_social="Fornecedor LGPD LTDA",
            nome_fantasia="Fornecedor LGPD",
            cnpj="66.666.666/0001-66",
            telefone="1133333333",
            email="contato@fornecedorlgpd.com",
        )
        response = self.client.get(reverse("fornecedor_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            RegistroAcessoDadoPessoal.objects.filter(
                usuario=self.user,
                categoria_titular="FORNECEDOR",
                entidade="Fornecedor",
                acao="ADMIN_LIST",
            ).exists()
        )

    def test_lgpd_governanca_exibe_inventario_e_trilha(self):
        RegistroAcessoDadoPessoal.objects.create(
            empresa=self.empresa,
            usuario=self.user,
            categoria_titular="USUARIO",
            entidade="UsuarioEmpresa",
            acao="ADMIN_LIST",
            identificador="Empresa Teste",
            finalidade="Teste de governanca",
        )
        response = self.client.get(reverse("lgpd_governanca"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Governanca LGPD")
        self.assertContains(response, "Base Legal")
        self.assertContains(response, "Teste de governanca")

    def test_politica_privacidade_publica_exibe_conteudo(self):
        response = self.client.get(reverse("politica_privacidade"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Politica de Privacidade")
        self.assertContains(response, "Inventario de Dados Pessoais")

    def test_termos_de_uso_publico_exibe_conteudo(self):
        response = self.client.get(reverse("termos_uso"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Termos de Uso")
        self.assertContains(response, "Responsabilidade e Rastreabilidade")

    def test_lgpd_governanca_pdf_retorna_pdf(self):
        response = self.client.get(reverse("lgpd_governanca_pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_contrato_dados_registra_acesso_lgpd(self):
        response = self.client.get(reverse("contrato_dados", args=[self.contrato.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            RegistroAcessoDadoPessoal.objects.filter(
                usuario=self.user,
                categoria_titular="FORNECEDOR",
                entidade="Compromisso",
                objeto_id=self.contrato.pk,
                acao="VIEW",
            ).exists()
        )

    def test_nota_fiscal_export_registra_acesso_lgpd(self):
        NotaFiscal.objects.create(
            numero="NF-LGPD",
            tipo="SERVICO",
            data_emissao="2026-03-16",
            fornecedor="Fornecedor Export",
            cnpj="12.345.678/0001-90",
            descricao="Nota para log de exportacao",
            valor_total=Decimal("10.00"),
            pedido_compra=self.pedido,
        )
        response = self.client.get(reverse("nota_fiscal_export"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            RegistroAcessoDadoPessoal.objects.filter(
                usuario=self.user,
                categoria_titular="FORNECEDOR",
                entidade="NotaFiscal",
                acao="EXPORT",
            ).exists()
        )

    def test_busca_em_compromissos_por_cnpj(self):
        response = self.client.get(reverse("compromisso_list"), {"q": "12.345.678/0001-90"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.contrato.numero)

    def test_busca_em_compromissos_por_nome_do_responsavel(self):
        response = self.client.get(reverse("compromisso_list"), {"q": "Maria"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.contrato.numero)

    def test_compromisso_contrato_exibe_valor_executado_por_medicoes(self):
        Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao executada",
            valor_medido=Decimal("35.00"),
            data_medicao="2026-03-12",
        )
        response = self.client.get(reverse("compromisso_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "$ 35,00")
        self.assertContains(response, "$ 65,00")

    def test_compromisso_pedido_exibe_valor_executado_por_notas(self):
        NotaFiscal.objects.create(
            numero="NF-PEDIDO",
            tipo="MATERIAL",
            data_emissao="2026-03-16",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota do pedido",
            valor_total=Decimal("40.00"),
            pedido_compra=self.pedido,
        )
        response = self.client.get(reverse("compromisso_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "$ 40,00")
        self.assertContains(response, "$ 60,00")

    def test_busca_em_medicoes_por_numero(self):
        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao busca",
            data_medicao="2026-03-12",
        )
        response = self.client.get(reverse("medicao_list"), {"q": medicao.numero_da_medicao})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, medicao.numero_da_medicao)

    def test_busca_em_medicoes_por_cnpj(self):
        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao por cnpj",
            data_medicao="2026-03-12",
        )
        response = self.client.get(reverse("medicao_list"), {"q": self.contrato.cnpj})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, medicao.numero_da_medicao)

    def test_compromisso_list_pagina_resultados_e_preserva_filtros(self):
        for indice in range(24):
            Compromisso.objects.create(
                tipo="PEDIDO_COMPRA",
                centro_custo=self.analitico,
                descricao=f"Pedido paginado {indice}",
                fornecedor="Fornecedor A",
                cnpj="12.345.678/0001-90",
                responsavel="Maria",
                telefone="11999999999",
                valor_contratado=Decimal("100.00"),
                data_assinatura="2026-03-01",
            )

        response = self.client.get(reverse("compromisso_list"), {"fornecedor": "Fornecedor A"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["compromissos"]), 20)
        self.assertContains(response, "Pagina 1 de 2")
        self.assertContains(response, "fornecedor=Fornecedor+A")
        self.assertContains(response, "page=2")

    def test_medicao_list_pagina_resultados(self):
        for indice in range(24):
            Medicao.objects.create(
                contrato=self.contrato,
                descricao=f"Medicao paginada {indice}",
                valor_medido=Decimal("10.00"),
                data_medicao="2026-03-12",
            )

        response = self.client.get(reverse("medicao_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["medicoes"]), 20)
        self.assertContains(response, "Pagina 1 de 2")
        self.assertContains(response, "page=2")

    def test_nota_fiscal_list_pagina_resultados(self):
        for indice in range(24):
            NotaFiscal.objects.create(
                numero=f"NF-PAG-{indice}",
                tipo="MATERIAL",
                data_emissao="2026-03-16",
                fornecedor="Fornecedor A",
                cnpj="12.345.678/0001-90",
                descricao=f"Nota paginada {indice}",
                valor_total=Decimal("40.00"),
                pedido_compra=self.pedido,
            )

        response = self.client.get(reverse("nota_fiscal_list"), {"fornecedor": "Fornecedor A"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["notas_fiscais"]), 20)
        self.assertContains(response, "Pagina 1 de 2")
        self.assertContains(response, "page=2")

    def test_fluxo_financeiro_contratual_considera_medicao_deslocada_por_aditivo_de_prazo(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato com prazo deslocado",
            fornecedor="Fornecedor B",
            cnpj="98.765.432/0001-10",
            responsavel="Carlos",
            telefone="11999999998",
            valor_contratado=Decimal("300.00"),
            data_assinatura="2026-02-10",
        )
        AditivoContrato.objects.create(
            contrato=contrato,
            tipo="PRAZO",
            descricao="Ajuste de prazo para janela corrente",
            delta_dias=40,
        )
        Medicao.objects.create(
            contrato=contrato,
            descricao="Medicao deslocada para abril",
            valor_medido=Decimal("90.00"),
            data_medicao="2026-02-15",
            data_prevista_inicio="2026-02-20",
            data_prevista_fim="2026-03-05",
        )
        Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao fora da janela",
            valor_medido=Decimal("55.00"),
            data_medicao="2025-01-10",
            data_prevista_inicio="2025-01-10",
            data_prevista_fim="2025-01-20",
        )

        dados = construir_fluxo_financeiro_contratual(obra=self.obra, meses_qtd=6)

        self.assertEqual(dados["total_saidas"], Decimal("90.00"))
        self.assertEqual(dados["series"][0]["saida"], Decimal("90.00"))

    def test_plano_contas_view_responde(self):
        response = self.client.get(reverse("plano_contas_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plano de Contas")
        self.assertContains(response, "Quantidade")
        self.assertContains(response, "Valor Unitário")

    def test_cria_baseline_de_orcamento_pela_interface(self):
        response = self.client.post(
            reverse("plano_contas_criar_baseline"),
            {"descricao_baseline": "Orcamento Aprovado v1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        baseline = OrcamentoBaseline.objects.get(descricao="Orcamento Aprovado v1")
        self.assertEqual(baseline.obra, self.obra)
        self.assertEqual(baseline.criado_por, self.user)
        self.assertTrue(OrcamentoBaselineItem.objects.filter(baseline=baseline, codigo=self.analitico.codigo).exists())
        self.assertContains(response, "Orcamento Aprovado v1")

    def test_baseline_de_orcamento_mantem_snapshot_apos_edicao_do_plano(self):
        self.client.post(
            reverse("plano_contas_criar_baseline"),
            {"descricao_baseline": "Baseline congelada"},
            follow=True,
        )
        baseline = OrcamentoBaseline.objects.get(descricao="Baseline congelada")
        snapshot_item = OrcamentoBaselineItem.objects.get(baseline=baseline, codigo=self.analitico.codigo)
        valor_snapshot = snapshot_item.valor_total_consolidado

        self.analitico.quantidade = Decimal("999.00")
        self.analitico.valor_unitario = Decimal("999.00")
        self.analitico.save()

        snapshot_item.refresh_from_db()
        self.assertEqual(snapshot_item.valor_total_consolidado, valor_snapshot)

    def test_baseline_de_orcamento_pode_ser_aprovada_e_ativada(self):
        gerente = self._criar_usuario_operacional("gerente_baseline", "GERENTE_OBRAS")
        self.client.post(
            reverse("plano_contas_criar_baseline"),
            {"descricao_baseline": "Baseline para aprovar"},
            follow=True,
        )
        baseline = OrcamentoBaseline.objects.get(descricao="Baseline para aprovar")

        self.client.force_login(gerente)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response_envio = self.client.post(
            reverse("plano_contas_baseline_workflow", args=[baseline.pk]),
            {"acao": "enviar_para_aprovacao"},
            follow=True,
        )
        self.assertEqual(response_envio.status_code, 200)

        response_aprovacao = self.client.post(
            reverse("plano_contas_baseline_workflow", args=[baseline.pk]),
            {"acao": "aprovar", "parecer_aprovacao": "Orcamento aprovado para execucao."},
            follow=True,
        )

        self.assertEqual(response_aprovacao.status_code, 200)
        baseline.refresh_from_db()
        self.assertEqual(baseline.status, "APROVADA")
        self.assertTrue(baseline.is_ativa)
        self.assertEqual(baseline.aprovado_por, gerente)
        self.assertEqual(baseline.parecer_aprovacao, "Orcamento aprovado para execucao.")

    def test_retorno_de_baseline_para_ajuste_exige_parecer(self):
        gerente = self._criar_usuario_operacional("gerente_baseline_ajuste", "GERENTE_OBRAS")
        baseline = OrcamentoBaseline.objects.create(
            obra=self.obra,
            descricao="Baseline em analise",
            criado_por=self.user,
            status="EM_APROVACAO",
        )

        self.client.force_login(gerente)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("plano_contas_baseline_workflow", args=[baseline.pk]),
            {"acao": "retornar_para_ajuste", "parecer_aprovacao": ""},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        baseline.refresh_from_db()
        self.assertEqual(baseline.status, "EM_APROVACAO")
        self.assertContains(response, "Informe um parecer para devolver a baseline para ajuste.")

    def test_relatorios_probatorios_pdf_retorna_pdf_para_contrato_medicao_e_baseline(self):
        self.contrato.status = "APROVADO"
        self.contrato.enviado_para_aprovacao_por = self.user
        self.contrato.aprovado_por = self.user
        self.contrato.parecer_aprovacao = "Contrato aprovado."
        self.contrato.save()

        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao probatoria",
            data_medicao="2026-03-12",
            valor_medido=Decimal("2500.00"),
            status="APROVADA",
            enviado_para_aprovacao_por=self.user,
            aprovado_por=self.user,
            parecer_aprovacao="Medicao aprovada.",
        )
        baseline = OrcamentoBaseline.objects.create(
            obra=self.obra,
            descricao="Baseline probatoria",
            criado_por=self.user,
            status="APROVADA",
            enviado_para_aprovacao_por=self.user,
            aprovado_por=self.user,
            parecer_aprovacao="Baseline aprovada.",
            is_ativa=True,
        )
        OrcamentoBaselineItem.objects.create(
            baseline=baseline,
            codigo=self.analitico.codigo,
            descricao=self.analitico.descricao,
            parent_codigo=self.grupo.codigo,
            level=self.analitico.level,
            unidade=self.analitico.unidade or "",
            quantidade=self.analitico.quantidade,
            valor_unitario=self.analitico.valor_unitario,
            valor_total=self.analitico.valor_total,
            valor_total_consolidado=self.analitico.valor_total,
        )

        response_contrato = self.client.get(reverse("compromisso_aprovacao_pdf", args=[self.contrato.pk]))
        response_medicao = self.client.get(reverse("medicao_aprovacao_pdf", args=[medicao.pk]))
        response_baseline = self.client.get(reverse("plano_contas_baseline_aprovacao_pdf", args=[baseline.pk]))

        self.assertEqual(response_contrato.status_code, 200)
        self.assertEqual(response_contrato["Content-Type"], "application/pdf")
        self.assertEqual(response_medicao.status_code, 200)
        self.assertEqual(response_medicao["Content-Type"], "application/pdf")
        self.assertEqual(response_baseline.status_code, 200)
        self.assertEqual(response_baseline["Content-Type"], "application/pdf")
        self.assertIn(b"/Subtype /Image", response_contrato.content)
        self.assertIn(b"/Subtype /Image", response_medicao.content)
        self.assertIn(b"/Subtype /Image", response_baseline.content)

    def test_relatorio_pdf_de_compras_e_contratacoes_usa_layout_tabular_com_itens(self):
        aditivo = AditivoContrato.objects.create(
            contrato=self.contrato,
            tipo="VALOR",
            descricao="Aditivo de escopo complementar",
        )
        AditivoContratoItem.objects.create(
            aditivo=aditivo,
            centro_custo=self.analitico,
            valor=Decimal("350.00"),
        )

        response = self.client.get(reverse("compromisso_pdf", args=[self.contrato.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(b"/Subtype /Image", response.content)
        self.assertIn(b"ITENS DO PEDIDO/CONTRATO", response.content)
        self.assertIn(b"ADITIVOS DO CONTRATO", response.content)
        self.assertIn("HISTÓRICO DOS ADITIVOS".encode("cp1252"), response.content)

    def test_pdf_do_contrato_distingue_devolucao_de_aditivo_no_historico(self):
        coordenador = self._criar_usuario_operacional("coord_hist_aditivo", "COORDENADOR_OBRAS")
        aditivo = AditivoContrato.objects.create(
            contrato=self.contrato,
            tipo="VALOR",
            descricao="Aditivo para historico",
            status="EM_APROVACAO",
        )
        AditivoContratoItem.objects.create(
            aditivo=aditivo,
            centro_custo=self.analitico,
            valor=Decimal("250.00"),
        )

        self.client.force_login(coordenador)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        self.client.post(
            reverse("aditivo_contrato_workflow", args=[aditivo.pk]),
            {"acao": "retornar_para_ajuste", "parecer_aprovacao": "Ajustar composicao."},
            follow=True,
        )

        response = self.client.get(reverse("compromisso_pdf", args=[self.contrato.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Devolvido para", response.content)
        self.assertIn(b"Ajuste", response.content)

    def test_relatorios_probatorios_excel_retorna_excel_para_contrato_medicao_e_baseline(self):
        self.contrato.status = "APROVADO"
        self.contrato.enviado_para_aprovacao_por = self.user
        self.contrato.aprovado_por = self.user
        self.contrato.parecer_aprovacao = "Contrato aprovado."
        self.contrato.save()

        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao probatoria excel",
            data_medicao="2026-03-12",
            valor_medido=Decimal("1800.00"),
            status="APROVADA",
            enviado_para_aprovacao_por=self.user,
            aprovado_por=self.user,
            parecer_aprovacao="Medicao aprovada.",
        )
        baseline = OrcamentoBaseline.objects.create(
            obra=self.obra,
            descricao="Baseline probatoria excel",
            criado_por=self.user,
            status="APROVADA",
            enviado_para_aprovacao_por=self.user,
            aprovado_por=self.user,
            parecer_aprovacao="Baseline aprovada.",
            is_ativa=True,
        )
        OrcamentoBaselineItem.objects.create(
            baseline=baseline,
            codigo=self.analitico.codigo,
            descricao=self.analitico.descricao,
            parent_codigo=self.grupo.codigo,
            level=self.analitico.level,
            unidade=self.analitico.unidade or "",
            quantidade=self.analitico.quantidade,
            valor_unitario=self.analitico.valor_unitario,
            valor_total=self.analitico.valor_total,
            valor_total_consolidado=self.analitico.valor_total,
        )

        response_contrato = self.client.get(reverse("compromisso_aprovacao_excel", args=[self.contrato.pk]))
        response_medicao = self.client.get(reverse("medicao_aprovacao_excel", args=[medicao.pk]))
        response_baseline = self.client.get(reverse("plano_contas_baseline_aprovacao_excel", args=[baseline.pk]))

        expected = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        self.assertEqual(response_contrato.status_code, 200)
        self.assertEqual(response_contrato["Content-Type"], expected)
        self.assertEqual(response_medicao.status_code, 200)
        self.assertEqual(response_medicao["Content-Type"], expected)
        self.assertEqual(response_baseline.status_code, 200)
        self.assertEqual(response_baseline["Content-Type"], expected)

    def test_central_de_evidencias_lista_contrato_medicao_e_baseline(self):
        self.contrato.status = "APROVADO"
        self.contrato.enviado_para_aprovacao_por = self.user
        self.contrato.aprovado_por = self.user
        self.contrato.parecer_aprovacao = "Contrato aprovado."
        self.contrato.save()

        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao central evidencias",
            data_medicao="2026-03-12",
            valor_medido=Decimal("900.00"),
            status="APROVADA",
            enviado_para_aprovacao_por=self.user,
            aprovado_por=self.user,
            parecer_aprovacao="Medicao aprovada.",
        )
        baseline = OrcamentoBaseline.objects.create(
            obra=self.obra,
            descricao="Baseline central evidencias",
            criado_por=self.user,
            status="APROVADA",
            enviado_para_aprovacao_por=self.user,
            aprovado_por=self.user,
            parecer_aprovacao="Baseline aprovada.",
            is_ativa=True,
        )
        OrcamentoBaselineItem.objects.create(
            baseline=baseline,
            codigo=self.analitico.codigo,
            descricao=self.analitico.descricao,
            parent_codigo=self.grupo.codigo,
            level=self.analitico.level,
            unidade=self.analitico.unidade or "",
            quantidade=self.analitico.quantidade,
            valor_unitario=self.analitico.valor_unitario,
            valor_total=self.analitico.valor_total,
            valor_total_consolidado=self.analitico.valor_total,
        )

        response = self.client.get(reverse("central_evidencias"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central de Evidencias")
        self.assertContains(response, self.contrato.numero)
        self.assertContains(response, medicao.numero_da_medicao)
        self.assertContains(response, "Baseline central evidencias")
        self.assertContains(response, "EVD-COMPROMISSO")
        self.assertContains(response, "EVD-MEDICAO")
        self.assertContains(response, "EVD-BASELINE")

    def test_dossie_da_obra_exibe_relatorio_padronizado(self):
        self.contrato.status = "APROVADO"
        self.contrato.enviado_para_aprovacao_por = self.user
        self.contrato.aprovado_por = self.user
        self.contrato.parecer_aprovacao = "Contrato aprovado para compor o dossie."
        self.contrato.save()

        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao dossie obra",
            data_medicao="2026-03-12",
            valor_medido=Decimal("900.00"),
            status="APROVADA",
            enviado_para_aprovacao_por=self.user,
            aprovado_por=self.user,
            parecer_aprovacao="Medicao aprovada no dossie.",
        )
        baseline = OrcamentoBaseline.objects.create(
            obra=self.obra,
            descricao="Baseline dossie obra",
            criado_por=self.user,
            status="APROVADA",
            enviado_para_aprovacao_por=self.user,
            aprovado_por=self.user,
            parecer_aprovacao="Baseline aprovada no dossie.",
            is_ativa=True,
        )
        OrcamentoBaselineItem.objects.create(
            baseline=baseline,
            codigo=self.analitico.codigo,
            descricao=self.analitico.descricao,
            parent_codigo=self.grupo.codigo,
            level=self.analitico.level,
            unidade=self.analitico.unidade or "",
            quantidade=self.analitico.quantidade,
            valor_unitario=self.analitico.valor_unitario,
            valor_total=self.analitico.valor_total,
            valor_total_consolidado=self.analitico.valor_total,
        )

        response = self.client.get(reverse("dossie_obra"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Relatórios de Acompanhamento de Obra")
        self.assertContains(response, "Relatório 01 - Gerencial")
        self.assertContains(response, "Relatório 03 - Análise de Plano de Contas")
        self.assertContains(response, "Relatório 04 - Evidências de Aprovação")
        self.assertContains(response, self.obra.nome)
        self.assertContains(response, self.contrato.numero)
        self.assertContains(response, medicao.numero_da_medicao)
        self.assertContains(response, "Baseline dossie obra")

    def test_endpoint_de_notas_do_plano_contas(self):
        nota = NotaFiscal.objects.create(
            numero="NF-PLANO",
            tipo="MATERIAL",
            data_emissao="2026-03-18",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota do centro de custo",
            valor_total=Decimal("50.00"),
            pedido_compra=self.pedido,
        )
        NotaFiscalCentroCusto.objects.create(
            nota_fiscal=nota,
            centro_custo=self.analitico,
            valor=Decimal("50.00"),
        )

        response = self.client.get(reverse("plano_contas_notas", args=[self.grupo.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "centro_custo": f"{self.grupo.codigo} - {self.grupo.descricao}",
                "quantidade_notas": 1,
                "notas": [
                    {
                        "id": nota.id,
                        "numero": "NF-PLANO",
                        "fornecedor": "Fornecedor A",
                        "cnpj": "12.345.678/0001-90",
                        "descricao": "Nota do centro de custo",
                        "centro_custo": f"{self.analitico.codigo} - {self.analitico.descricao}",
                        "valor": "$ 50,00",
                        "data": "18/03/2026",
                    }
                ],
            },
        )

    def test_endpoint_de_dados_do_contrato(self):
        CompromissoItem.objects.create(
            compromisso=self.contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("2.00"),
            valor_unitario=Decimal("20.00"),
        )
        CompromissoItem.objects.create(
            compromisso=self.contrato,
            centro_custo=self.analitico_2,
            unidade="kg",
            quantidade=Decimal("4.00"),
            valor_unitario=Decimal("5.00"),
        )
        response = self.client.get(reverse("contrato_dados", args=[self.contrato.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "fornecedor": self.contrato.fornecedor,
                "cnpj": self.contrato.cnpj,
                "responsavel": self.contrato.responsavel,
                "valor_contrato": "60.00",
                "centro_custo": self.analitico.codigo,
                "centros_custo": [
                    {
                        "id": self.analitico.id,
                        "codigo": self.analitico.codigo,
                        "descricao": self.analitico.descricao,
                        "unidade": self.analitico.unidade,
                        "valor_unitario": "20.00",
                    },
                    {
                        "id": self.analitico_2.id,
                        "codigo": self.analitico_2.codigo,
                        "descricao": self.analitico_2.descricao,
                        "unidade": self.analitico_2.unidade,
                        "valor_unitario": "5.00",
                    },
                ],
            },
        )

    def test_endpoint_de_dados_da_medicao(self):
        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao endpoint",
            data_medicao="2026-03-12",
        )
        MedicaoItem.objects.create(
            medicao=medicao,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("2.00"),
            valor_unitario=Decimal("20.00"),
        )
        response = self.client.get(reverse("medicao_dados", args=[medicao.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "fornecedor": self.contrato.fornecedor,
                "cnpj": self.contrato.cnpj,
                "responsavel": self.contrato.responsavel,
                "centros_custo": [
                    {
                        "id": self.analitico.id,
                        "codigo": self.analitico.codigo,
                        "descricao": self.analitico.descricao,
                        "unidade": self.analitico.unidade,
                        "valor_unitario": "20.00",
                    }
                ],
            },
        )

    def test_cria_compromisso_pela_interface_com_itens(self):
        response = self.client.post(
            reverse("compromisso_create"),
            data={
                "tipo": "PEDIDO_COMPRA",
                "status": "APROVADO",
                "centro_custo": self.analitico.pk,
                "descricao": "Novo pedido",
                "fornecedor": "Fornecedor B",
                "cnpj": "98.765.432/0001-10",
                "responsavel": "Joao",
                "telefone": "11888888888",
                "data_assinatura": "2026-03-10",
                "itens-TOTAL_FORMS": "2",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "0",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-centro_custo": str(self.analitico.pk),
                "itens-0-unidade": "m3",
                "itens-0-quantidade": "5.00",
                "itens-0-valor_unitario": "20.00",
                "itens-1-centro_custo": str(self.analitico_2.pk),
                "itens-1-unidade": "kg",
                "itens-1-quantidade": "10.00",
                "itens-1-valor_unitario": "5.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        compromisso = Compromisso.objects.get(descricao="Novo pedido")
        self.assertEqual(compromisso.valor_contratado, Decimal("150.00"))
        self.assertEqual(compromisso.itens.count(), 2)
        self.assertEqual(compromisso.status, "RASCUNHO")

    def test_tecnico_envia_contrato_para_aprovacao_mas_nao_aprova(self):
        tecnico = self._criar_usuario_operacional("tecnico", "TECNICO_OBRAS")
        self.client.force_login(tecnico)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response_envio = self.client.post(
            reverse("contrato_detail", args=[self.contrato.pk]),
            {"acao": "enviar_para_aprovacao"},
            follow=True,
        )

        self.assertEqual(response_envio.status_code, 200)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.status, "EM_APROVACAO")
        self.assertEqual(self.contrato.enviado_para_aprovacao_por, tecnico)

        response_aprovacao = self.client.post(
            reverse("contrato_detail", args=[self.contrato.pk]),
            {"acao": "aprovar"},
            follow=True,
        )

        self.assertEqual(response_aprovacao.status_code, 200)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.status, "EM_APROVACAO")
        self.assertIsNone(self.contrato.aprovado_por)

    def test_engenheiro_aprova_contrato_ate_cinquenta_mil(self):
        engenheiro = self._criar_usuario_operacional("engenheiro", "ENGENHEIRO_OBRAS")
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato dentro da alcada",
            fornecedor="Fornecedor Engenharia",
            cnpj="12.345.678/0001-90",
            responsavel="Carlos",
            telefone="11999999999",
            valor_contratado=Decimal("50000.00"),
            data_assinatura="2026-03-01",
            status="EM_APROVACAO",
        )
        self.client.force_login(engenheiro)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("contrato_detail", args=[contrato.pk]),
            {"acao": "aprovar"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        contrato.refresh_from_db()
        self.assertEqual(contrato.status, "APROVADO")
        self.assertEqual(contrato.aprovado_por, engenheiro)

    def test_coordenador_nao_aprova_contrato_acima_de_cem_mil(self):
        coordenador = self._criar_usuario_operacional("coordenador", "COORDENADOR_OBRAS")
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato acima da alcada",
            fornecedor="Fornecedor Coordenacao",
            cnpj="12.345.678/0001-90",
            responsavel="Ana",
            telefone="11999999999",
            valor_contratado=Decimal("100000.01"),
            data_assinatura="2026-03-01",
            status="EM_APROVACAO",
        )
        self.client.force_login(coordenador)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("contrato_detail", args=[contrato.pk]),
            {"acao": "aprovar"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        contrato.refresh_from_db()
        self.assertEqual(contrato.status, "EM_APROVACAO")
        self.assertIsNone(contrato.aprovado_por)

    def test_coordenador_retorna_contrato_para_ajuste_com_parecer(self):
        coordenador = self._criar_usuario_operacional("coord_ajuste", "COORDENADOR_OBRAS")
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato para ajuste",
            fornecedor="Fornecedor Ajuste",
            cnpj="12.345.678/0001-90",
            responsavel="Ana",
            telefone="11999999999",
            valor_contratado=Decimal("90000.00"),
            data_assinatura="2026-03-01",
            status="EM_APROVACAO",
        )
        self.client.force_login(coordenador)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("contrato_detail", args=[contrato.pk]),
            {"acao": "retornar_para_ajuste", "parecer_aprovacao": "Revisar composicao de custos."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        contrato.refresh_from_db()
        self.assertEqual(contrato.status, "RASCUNHO")
        self.assertEqual(contrato.parecer_aprovacao, "Revisar composicao de custos.")

    def test_engenheiro_nao_aprova_aditivo_contratual(self):
        engenheiro = self._criar_usuario_operacional("engenheiro_aditivo", "ENGENHEIRO_OBRAS")
        aditivo = AditivoContrato.objects.create(
            contrato=self.contrato,
            tipo="VALOR",
            descricao="Aditivo para aprovacao",
            status="EM_APROVACAO",
        )
        AditivoContratoItem.objects.create(
            aditivo=aditivo,
            centro_custo=self.analitico,
            valor=Decimal("1000.00"),
        )
        self.client.force_login(engenheiro)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("aditivo_contrato_workflow", args=[aditivo.pk]),
            {"acao": "aprovar"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        aditivo.refresh_from_db()
        self.assertEqual(aditivo.status, "EM_APROVACAO")
        self.assertIsNone(aditivo.aprovado_por)

    def test_coordenador_aprova_aditivo_contratual(self):
        coordenador = self._criar_usuario_operacional("coord_aditivo", "COORDENADOR_OBRAS")
        aditivo = AditivoContrato.objects.create(
            contrato=self.contrato,
            tipo="VALOR",
            descricao="Aditivo aprovado pelo coordenador",
            status="EM_APROVACAO",
        )
        AditivoContratoItem.objects.create(
            aditivo=aditivo,
            centro_custo=self.analitico,
            valor=Decimal("1000.00"),
        )
        self.client.force_login(coordenador)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("aditivo_contrato_workflow", args=[aditivo.pk]),
            {"acao": "aprovar", "parecer_aprovacao": "Aditivo aprovado."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        aditivo.refresh_from_db()
        self.assertEqual(aditivo.status, "APROVADO")
        self.assertEqual(aditivo.aprovado_por, coordenador)

    def test_criacao_de_aditivo_registra_trilha_formal_de_mudanca(self):
        CompromissoItem.objects.create(
            compromisso=self.contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("10.00"),
            valor_unitario=Decimal("10.00"),
        )
        response = self.client.post(
            reverse("aditivo_contrato_create", args=[self.contrato.pk]),
            {
                "tipo": "VALOR",
                "descricao": "Ampliação de escopo estrutural",
                "motivo_mudanca": "Necessidade de reforço estrutural identificada em campo.",
                "impacto_resumido": "Acréscimo financeiro controlado.",
                "aditivos_itens-TOTAL_FORMS": "1",
                "aditivos_itens-INITIAL_FORMS": "0",
                "aditivos_itens-MIN_NUM_FORMS": "0",
                "aditivos_itens-MAX_NUM_FORMS": "1000",
                "aditivos_itens-0-centro_custo": str(self.analitico.pk),
                "aditivos_itens-0-valor": "50.00",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        aditivo = self.contrato.aditivos.order_by("-criado_em").first()
        self.assertIsNotNone(aditivo)
        self.assertEqual(aditivo.solicitado_por, self.user)
        self.assertIsNotNone(aditivo.solicitado_em)
        self.assertEqual(aditivo.motivo_mudanca, "Necessidade de reforço estrutural identificada em campo.")
        self.assertEqual(aditivo.impacto_resumido, "Acréscimo financeiro controlado.")

    def test_exclui_compromisso_pela_interface(self):
        response = self.client.post(reverse("compromisso_delete"), data={"id": self.pedido.pk})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Compromisso.objects.filter(pk=self.pedido.pk).exists())

    def test_exclusao_protegida_de_compromisso_retorna_mensagem(self):
        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao vinculada",
            data_medicao="2026-03-20",
        )
        response = self.client.post(reverse("compromisso_delete"), data={"id": self.contrato.pk}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Compromisso.objects.filter(pk=self.contrato.pk).exists())
        self.assertTrue(Medicao.objects.filter(pk=medicao.pk).exists())
        self.assertContains(response, "possui vinculos em outras operacoes do sistema")

    def test_cria_medicao_pela_interface_com_itens(self):
        self._aprovar_contrato()
        CompromissoItem.objects.create(
            compromisso=self.contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("50.00"),
            valor_unitario=Decimal("20.00"),
        )

        response = self.client.post(
            reverse("medicao_create"),
            data={
                "contrato": self.contrato.pk,
                "status": "APROVADA",
                "descricao": "Medicao via app",
                "data_medicao": "2026-03-12",
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "0",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-centro_custo": str(self.analitico.pk),
                "itens-0-unidade": "m3",
                "itens-0-quantidade": "10.00",
                "itens-0-valor_unitario": "20.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        medicao = Medicao.objects.get(descricao="Medição via app")
        self.assertEqual(medicao.valor_medido, Decimal("200.00"))
        self.assertEqual(medicao.itens.count(), 1)
        self.assertEqual(medicao.status, "EM_ELABORACAO")

    def test_gerente_aprova_medicao_sem_limite(self):
        gerente = self._criar_usuario_operacional("gerente", "GERENTE_OBRAS")
        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao para gerente",
            data_medicao="2026-03-12",
            valor_medido=Decimal("250000.00"),
            status="EM_APROVACAO",
        )
        self.client.force_login(gerente)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("medicao_detail", args=[medicao.pk]),
            {"acao": "aprovar"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        medicao.refresh_from_db()
        self.assertEqual(medicao.status, "APROVADA")
        self.assertEqual(medicao.aprovado_por, gerente)

    def test_devolucao_de_medicao_para_ajuste_exige_parecer(self):
        gerente = self._criar_usuario_operacional("gerente_ajuste", "GERENTE_OBRAS")
        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao para devolver",
            data_medicao="2026-03-12",
            valor_medido=Decimal("2500.00"),
            status="EM_APROVACAO",
        )
        self.client.force_login(gerente)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("medicao_detail", args=[medicao.pk]),
            {"acao": "retornar_para_ajuste", "parecer_aprovacao": ""},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        medicao.refresh_from_db()
        self.assertEqual(medicao.status, "EM_APROVACAO")
        self.assertContains(response, "Informe um parecer para devolver o registro para ajuste.")

    def test_edicao_medicao_nao_cria_item_extra_vazio(self):
        # Garante que a linha extra do formset (extra=1) nao gere erro
        # nem crie um segundo item quando o centro de custo fica vazio.
        self._aprovar_contrato()
        CompromissoItem.objects.create(
            compromisso=self.contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("50.00"),
            valor_unitario=Decimal("20.00"),
        )
        medicao = Medicao.objects.create(
            contrato=self.contrato,
            descricao="Medicao para update",
            data_medicao="2026-03-12",
        )
        MedicaoItem.objects.create(
            medicao=medicao,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("10.00"),
            valor_unitario=Decimal("20.00"),
        )

        item = medicao.itens.first()

        response = self.client.post(
            reverse("medicao_update", args=[medicao.pk]),
            data={
                "contrato": self.contrato.pk,
                "descricao": "Medicao para update - alterada",
                "data_medicao": "2026-03-12",
                # Formset: 1 item inicial + 1 linha extra vazia
                "itens-TOTAL_FORMS": "2",
                "itens-INITIAL_FORMS": "1",
                "itens-MIN_NUM_FORMS": "0",
                "itens-MAX_NUM_FORMS": "1000",
                # Item existente
                "itens-0-id": item.pk,
                "itens-0-centro_custo": str(self.analitico.pk),
                "itens-0-unidade": "m3",
                "itens-0-quantidade": "10.00",
                "itens-0-valor_unitario": "20.00",
                # Linha extra vazia
                "itens-1-centro_custo": "",
                "itens-1-unidade": "",
                "itens-1-quantidade": "",
                "itens-1-valor_unitario": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        medicao.refresh_from_db()
        self.assertEqual(medicao.itens.count(), 1)
        self.assertEqual(medicao.descricao, "Medição para update - alterada")

    def test_exportacao_de_medicoes_retorna_excel(self):
        response = self.client.get(reverse("medicao_export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_medicao_herda_unidade_e_valor_unitario_do_contrato(self):
        self._aprovar_contrato()
        CompromissoItem.objects.create(
            compromisso=self.contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("50.00"),
            valor_unitario=Decimal("20.00"),
        )

        response = self.client.post(
            reverse("medicao_create"),
            data={
                "contrato": self.contrato.pk,
                "descricao": "Medicao travada ao contrato",
                "data_medicao": "2026-03-14",
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "0",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-centro_custo": str(self.analitico.pk),
                "itens-0-unidade": "kg",
                "itens-0-quantidade": "3.00",
                "itens-0-valor_unitario": "999.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        item = MedicaoItem.objects.get(medicao__descricao="Medição travada ao contrato")
        self.assertEqual(item.unidade, "m3")
        self.assertEqual(item.valor_unitario, Decimal("20.00"))
        self.assertEqual(item.valor_total, Decimal("60.00"))

    def test_cria_nota_fiscal_pela_interface(self):
        self._aprovar_pedido()
        CompromissoItem.objects.create(
            compromisso=self.pedido,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("3.00"),
            valor_unitario=Decimal("10.00"),
        )
        response = self.client.post(
            reverse("nota_fiscal_create"),
            data={
                "numero": "NF-100",
                "serie": "1",
                "tipo": "MATERIAL",
                "data_emissao": "2026-03-15",
                "fornecedor": "Fornecedor A",
                "cnpj": "12.345.678/0001-90",
                "descricao": "Nota via app",
                "valor_total": "30.00",
                "pedido_compra": self.pedido.pk,
                "medicao": "",
                "origem_info": "",
                "rateio-TOTAL_FORMS": "1",
                "rateio-INITIAL_FORMS": "0",
                "rateio-MIN_NUM_FORMS": "0",
                "rateio-MAX_NUM_FORMS": "1000",
                "rateio-0-centro_custo": str(self.analitico.pk),
                "rateio-0-valor": "30.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(NotaFiscal.objects.filter(numero="NF-100").exists())
        self.assertEqual(NotaFiscalCentroCusto.objects.filter(nota_fiscal__numero="NF-100").count(), 1)

    def test_exportacao_de_notas_fiscais_retorna_excel(self):
        response = self.client.get(reverse("nota_fiscal_export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_busca_em_notas_fiscais_por_numero(self):
        NotaFiscal.objects.create(
            numero="NF-BUSCA",
            tipo="MATERIAL",
            data_emissao="2026-03-16",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota para busca",
            valor_total=Decimal("10.00"),
            pedido_compra=self.pedido,
        )
        response = self.client.get(reverse("nota_fiscal_list"), {"q": "NF-BUSCA"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NF-BUSCA")

    def test_busca_em_notas_fiscais_por_fornecedor(self):
        NotaFiscal.objects.create(
            numero="NF-FORN",
            tipo="MATERIAL",
            data_emissao="2026-03-16",
            fornecedor="Fornecedor Busca",
            cnpj="12.345.678/0001-90",
            descricao="Nota para busca por fornecedor",
            valor_total=Decimal("10.00"),
            pedido_compra=self.pedido,
        )
        response = self.client.get(reverse("nota_fiscal_list"), {"q": "Fornecedor Busca"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NF-FORN")

    def test_nota_fiscal_rejeita_rateio_menor_e_mantem_origem_selecionada(self):
        self._aprovar_pedido()
        CompromissoItem.objects.create(
            compromisso=self.pedido,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("3.00"),
            valor_unitario=Decimal("10.00"),
        )
        response = self.client.post(
            reverse("nota_fiscal_create"),
            data={
                "numero": "NF-102",
                "serie": "1",
                "tipo": "MATERIAL",
                "data_emissao": "2026-03-15",
                "fornecedor": "Fornecedor A",
                "cnpj": "12.345.678/0001-90",
                "descricao": "Nota com rateio menor",
                "valor_total": "30.00",
                "pedido_compra": self.pedido.pk,
                "medicao": "",
                "origem_info": "",
                "rateio-TOTAL_FORMS": "1",
                "rateio-INITIAL_FORMS": "0",
                "rateio-MIN_NUM_FORMS": "0",
                "rateio-MAX_NUM_FORMS": "1000",
                "rateio-0-centro_custo": str(self.analitico.pk),
                "rateio-0-valor": "20.00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "exatamente igual ao valor total da nota fiscal")
        self.assertContains(response, f'value="{self.pedido.pk}" selected')
        self.assertContains(response, 'value="Fornecedor A"')
        self.assertContains(response, 'value="12.345.678/0001-90"')

    def test_nota_fiscal_rejeita_rateio_fora_da_origem(self):
        self._aprovar_pedido()
        CompromissoItem.objects.create(
            compromisso=self.pedido,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("3.00"),
            valor_unitario=Decimal("10.00"),
        )
        response = self.client.post(
            reverse("nota_fiscal_create"),
            data={
                "numero": "NF-101",
                "serie": "1",
                "tipo": "MATERIAL",
                "data_emissao": "2026-03-15",
                "fornecedor": "Fornecedor A",
                "cnpj": "12.345.678/0001-90",
                "descricao": "Nota com rateio invalido",
                "valor_total": "30.00",
                "pedido_compra": self.pedido.pk,
                "medicao": "",
                "origem_info": "",
                "rateio-TOTAL_FORMS": "1",
                "rateio-INITIAL_FORMS": "0",
                "rateio-MIN_NUM_FORMS": "0",
                "rateio-MAX_NUM_FORMS": "1000",
                "rateio-0-centro_custo": str(self.analitico_2.pk),
                "rateio-0-valor": "30.00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("escolha", response.content.decode("utf-8").lower())

class EvolucaoArquiteturalTests(BaseFinanceTestCase):
    def setUp(self):
        super().setUp()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="gestor", password="senhaforte123")
        self.usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=self.user,
            empresa=self.empresa,
            is_admin_empresa=True,
        )
        self.fornecedor = Fornecedor.objects.create(
            empresa=self.empresa,
            razao_social="Fornecedor Estruturado LTDA",
            nome_fantasia="Fornecedor Estruturado",
            cnpj="22.222.222/0001-22",
            telefone="1133333333",
        )
        self.client.login(username="gestor", password="senhaforte123")
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

    def test_tenant_scoped_manager_filtra_fornecedor_por_empresa_do_usuario(self):
        empresa_externa = Empresa.objects.create(
            nome="Empresa Externa",
            nome_fantasia="Empresa Externa",
            cnpj="98.765.432/0001-10",
        )
        Fornecedor.objects.create(
            empresa=empresa_externa,
            razao_social="Fornecedor Externo LTDA",
            nome_fantasia="Fornecedor Externo",
            cnpj="33.333.333/0001-33",
        )

        fornecedores = Fornecedor.objects.for_user(self.user)

        self.assertQuerySetEqual(
            fornecedores.order_by("id"),
            [self.fornecedor.pk],
            transform=lambda fornecedor: fornecedor.pk,
        )

    def test_tenant_scoped_manager_documentos_respeita_obras_permitidas_e_globais(self):
        usuario_operacional = self._criar_usuario_operacional("documental", "ENGENHEIRO_OBRAS")
        empresa_externa = Empresa.objects.create(
            nome="Empresa Documental Externa",
            nome_fantasia="Empresa Documental Externa",
            cnpj="77.777.777/0001-77",
        )
        obra_externa = Obra.objects.create(
            empresa=empresa_externa,
            codigo="EXT-001",
            nome="Obra Externa",
        )
        documento_obra = Documento.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            tipo_documento="PROCEDIMENTO",
            codigo_documento="PRO-TESTE-0001",
            titulo="Documento da obra permitida",
            criado_por=self.user,
        )
        documento_global = Documento.objects.create(
            empresa=self.empresa,
            obra=None,
            tipo_documento="PROCEDIMENTO",
            codigo_documento="PRO-TESTE-0002",
            titulo="Documento global",
            criado_por=self.user,
        )
        Documento.objects.create(
            empresa=empresa_externa,
            obra=obra_externa,
            tipo_documento="PROCEDIMENTO",
            codigo_documento="PRO-TESTE-0003",
            titulo="Documento externo",
            criado_por=self.user,
        )

        documentos = Documento.objects.for_user(usuario_operacional)

        self.assertQuerySetEqual(
            documentos.order_by("codigo_documento"),
            [documento_obra.pk, documento_global.pk],
            transform=lambda documento: documento.pk,
        )

    def _criar_usuario_operacional(self, username, papel_aprovacao):
        user_model = get_user_model()
        user = user_model.objects.create_user(username=username, password="senhaforte123")
        usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=user,
            empresa=self.empresa,
            is_admin_empresa=False,
            papel_aprovacao=papel_aprovacao,
        )
        usuario_empresa.obras_permitidas.add(self.obra)
        return user

    def _aprovar_pedido(self, pedido=None):
        pedido = pedido or Compromisso.objects.create(
            tipo="PEDIDO_COMPRA",
            centro_custo=self.analitico,
            descricao="Pedido aprovado temporario",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("100.00"),
            data_assinatura="2026-03-01",
        )
        pedido.status = "APROVADO"
        pedido.enviado_para_aprovacao_por = self.user
        pedido.aprovado_por = self.user
        pedido.parecer_aprovacao = "Pedido aprovado para testes."
        pedido.save()
        return pedido

    def test_workflow_de_nao_conformidade(self):
        nc = QualidadeWorkflowService.abrir(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            descricao="Falha de execução identificada em campo",
            responsavel=self.user,
            criado_por=self.user,
            causa="Procedimento não seguido",
        )
        self.assertEqual(nc.status, "ABERTA")

        QualidadeWorkflowService.iniciar_tratamento(nc, self.user, "Equipe orientada.")
        QualidadeWorkflowService.enviar_para_verificacao(nc, self.user, "Aguardando conferência.")
        QualidadeWorkflowService.encerrar(nc, self.user, "Correção validada.")

        nc.refresh_from_db()
        self.assertEqual(nc.status, "ENCERRADA")
        self.assertEqual(nc.historico.count(), 4)

    def test_workflow_de_nao_conformidade_registra_evidencia_e_eficacia(self):
        nc = QualidadeWorkflowService.abrir(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            descricao="Falha de controle de qualidade",
            responsavel=self.user,
            criado_por=self.user,
            acao_corretiva="Refazer verificação final.",
        )
        nc.evidencia_tratamento = "Checklist de correção anexado."
        nc.evidencia_encerramento = "Inspeção final concluída."
        nc.eficacia_observacao = "Medida corretiva eficaz."
        nc.save()

        QualidadeWorkflowService.iniciar_tratamento(nc, self.user, "Tratamento iniciado.")
        QualidadeWorkflowService.enviar_para_verificacao(nc, self.user, "Evidência conferida.")
        QualidadeWorkflowService.encerrar(nc, self.user, "Encerrado com eficácia confirmada.")

        nc.refresh_from_db()
        self.assertEqual(nc.status, "ENCERRADA")
        self.assertEqual(nc.evidencia_tratamento, "Checklist de correção anexado.")
        self.assertEqual(nc.evidencia_encerramento, "Inspeção final concluída.")
        self.assertEqual(nc.eficacia_observacao, "Medida corretiva eficaz.")
        self.assertEqual(nc.eficacia_verificada_por, self.user)
        self.assertIsNotNone(nc.eficacia_verificada_em)

    def test_medicao_form_oculta_obra_e_nota_form_remove_campos_redundantes(self):
        medicao_form = MedicaoForm(obra_contexto=self.obra)
        self.assertNotIn("obra", medicao_form.fields)
        self.assertNotIn("torre", medicao_form.fields)
        self.assertNotIn("bloco", medicao_form.fields)
        self.assertNotIn("etapa", medicao_form.fields)
        self.assertEqual(medicao_form.fields["data_prevista_inicio"].label, "Data de Início do Período")
        self.assertEqual(medicao_form.fields["data_prevista_fim"].label, "Data de Fim do Período")

        nota_form = NotaFiscalForm(obra_contexto=self.obra)
        self.assertNotIn("obra", nota_form.fields)
        self.assertNotIn("serie", nota_form.fields)
        self.assertNotIn("torre", nota_form.fields)
        self.assertNotIn("bloco", nota_form.fields)
        self.assertNotIn("etapa", nota_form.fields)

    def test_medicao_nao_pode_ser_criada_para_contrato_nao_aprovado(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato bloqueado",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("100.00"),
            data_assinatura="2026-03-01",
        )
        CompromissoItem.objects.create(
            compromisso=contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("10.00"),
            valor_unitario=Decimal("20.00"),
        )

        response = self.client.post(
            reverse("medicao_create"),
            data={
                "contrato": contrato.pk,
                "descricao": "Medicao bloqueada",
                "data_medicao": "2026-03-12",
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "0",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-centro_custo": str(self.analitico.pk),
                "itens-0-unidade": "m3",
                "itens-0-quantidade": "2.00",
                "itens-0-valor_unitario": "20.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Só é possível emitir medição para contratos aprovados.")
        self.assertFalse(Medicao.objects.filter(descricao="Medicao bloqueada").exists())

    def test_nota_fiscal_nao_pode_ser_criada_para_origem_nao_aprovada(self):
        pedido = Compromisso.objects.create(
            tipo="PEDIDO_COMPRA",
            centro_custo=self.analitico,
            descricao="Pedido bloqueado",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("100.00"),
            data_assinatura="2026-03-01",
        )
        CompromissoItem.objects.create(
            compromisso=pedido,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("3.00"),
            valor_unitario=Decimal("10.00"),
        )

        response = self.client.post(
            reverse("nota_fiscal_create"),
            data={
                "numero": "NF-BLOQ-1",
                "tipo": "MATERIAL",
                "data_emissao": "2026-03-15",
                "fornecedor": "Fornecedor A",
                "cnpj": "12.345.678/0001-90",
                "descricao": "Nota bloqueada",
                "valor_total": "30.00",
                "pedido_compra": pedido.pk,
                "medicao": "",
                "origem_info": "",
                "rateio-TOTAL_FORMS": "1",
                "rateio-INITIAL_FORMS": "0",
                "rateio-MIN_NUM_FORMS": "0",
                "rateio-MAX_NUM_FORMS": "1000",
                "rateio-0-centro_custo": str(self.analitico.pk),
                "rateio-0-valor": "30.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Só é possível emitir nota fiscal para pedidos aprovados.")
        self.assertFalse(NotaFiscal.objects.filter(numero="NF-BLOQ-1").exists())

    def test_nota_fiscal_de_servico_aceita_tipo_com_acento(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato servico",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("100.00"),
            data_assinatura="2026-03-01",
            status="APROVADO",
            aprovado_por=self.user,
        )
        medicao = Medicao.objects.create(
            contrato=contrato,
            descricao="Medicao de servico",
            data_medicao="2026-03-15",
            status="APROVADA",
            aprovado_por=self.user,
        )
        MedicaoItem.objects.create(
            medicao=medicao,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("3.00"),
            valor_unitario=Decimal("10.00"),
        )

        response = self.client.post(
            reverse("nota_fiscal_create"),
            data={
                "numero": "NF-SERV-1",
                "tipo": "SERVIÇO",
                "data_emissao": "2026-03-15",
                "fornecedor": "Fornecedor A",
                "cnpj": "12.345.678/0001-90",
                "descricao": "Nota de servico",
                "valor_total": "30.00",
                "pedido_compra": "",
                "medicao": medicao.pk,
                "origem_info": "",
                "rateio-TOTAL_FORMS": "1",
                "rateio-INITIAL_FORMS": "0",
                "rateio-MIN_NUM_FORMS": "0",
                "rateio-MAX_NUM_FORMS": "1000",
                "rateio-0-centro_custo": str(self.analitico.pk),
                "rateio-0-valor": "30.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(NotaFiscal.objects.filter(numero="NF-SERV-1", tipo="SERVICO").exists())

    def test_regra_planejamento_suprimentos_gera_alerta_sem_solicitacao(self):
        hoje = timezone.localdate()
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline Suprimentos",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base=hoje,
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-001",
            atividade="Estrutura metalica da cobertura",
            duracao=10,
            data_inicio_prevista=hoje + timedelta(days=20),
            data_fim_prevista=hoje + timedelta(days=30),
            percentual_concluido=0,
        )

        alertas = sincronizar_alertas_planejamento_suprimentos(self.obra)

        self.assertEqual(len(alertas), 1)
        alerta = AlertaOperacional.objects.get(
            obra=self.obra,
            codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
            referencia=f"{item.pk}:{self.analitico.pk}",
        )
        self.assertEqual(alerta.status, "ABERTO")
        self.assertEqual(alerta.severidade, "ALTA")
        self.assertEqual(alerta.evidencias["centro_custo_codigo"], self.analitico.codigo)

    def test_regra_planejamento_suprimentos_respeita_janela_parametrizada(self):
        ParametroAlertaEmpresa.obter_ou_criar(self.empresa)
        ParametroAlertaEmpresa.objects.filter(empresa=self.empresa).update(
            planejamento_suprimentos_janela_dias=90
        )
        hoje = timezone.localdate()
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline 90 dias",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base=hoje,
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-090",
            atividade="Compra futura parametrizada",
            duracao=10,
            data_inicio_prevista=hoje + timedelta(days=75),
            data_fim_prevista=hoje + timedelta(days=85),
            percentual_concluido=0,
        )

        alertas = sincronizar_alertas_planejamento_suprimentos(self.obra)

        self.assertEqual(len(alertas), 1)
        self.assertTrue(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
                entidade_id=item.pk,
            ).exists()
        )

    def test_regra_planejamento_suprimentos_nao_gera_alerta_com_solicitacao(self):
        hoje = timezone.localdate()
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline Coberta",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base=hoje,
        )
        PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-002",
            atividade="Instalacao de esquadrias",
            duracao=8,
            data_inicio_prevista=hoje + timedelta(days=12),
            data_fim_prevista=hoje + timedelta(days=20),
            percentual_concluido=0,
        )
        SolicitacaoCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            titulo="Compra de esquadrias",
            descricao="Cobertura de suprimento",
            solicitante=self.user,
            data_solicitacao=hoje,
            status="COTANDO",
        )

        alertas = sincronizar_alertas_planejamento_suprimentos(self.obra)

        self.assertEqual(alertas, [])
        self.assertFalse(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
            ).exists()
        )

    def test_regra_contrato_sem_medicao_gera_alerta_para_contrato_ativo(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato sem medicao",
            fornecedor="Fornecedor Contrato",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("15000.00"),
            data_assinatura=timezone.localdate() - timedelta(days=20),
            status="EM_EXECUCAO",
        )

        alertas = sincronizar_alertas_contrato_sem_medicao(self.obra)

        self.assertEqual(len(alertas), 1)
        self.assertTrue(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra=CODIGO_ALERTA_CONTRATO_SEM_MEDICAO,
                entidade_id=contrato.pk,
                status="ABERTO",
            ).exists()
        )

    def test_regra_medicao_sem_nota_gera_alerta(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato para medicao sem nota",
            fornecedor="Fornecedor Medicao",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("18000.00"),
            data_assinatura=timezone.localdate() - timedelta(days=30),
            status="EM_EXECUCAO",
        )
        medicao = Medicao.objects.create(
            contrato=contrato,
            obra=self.obra,
            centro_custo=self.analitico,
            numero_da_medicao="MED-2026-9999",
            data_medicao=timezone.localdate() - timedelta(days=10),
            descricao="Medicao pronta para faturamento",
            status="APROVADA",
        )

        alertas = sincronizar_alertas_medicao_sem_nota(self.obra)

        self.assertEqual(len(alertas), 1)
        self.assertTrue(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra=CODIGO_ALERTA_MEDICAO_SEM_NOTA,
                entidade_id=medicao.pk,
                status="ABERTO",
            ).exists()
        )

    def test_regra_medicao_sem_nota_respeita_dias_parametrizados(self):
        ParametroAlertaEmpresa.obter_ou_criar(self.empresa)
        ParametroAlertaEmpresa.objects.filter(empresa=self.empresa).update(
            medicao_sem_nota_dias=10
        )
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato com gatilho customizado",
            fornecedor="Fornecedor Medicao",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("18000.00"),
            data_assinatura=timezone.localdate() - timedelta(days=30),
            status="EM_EXECUCAO",
        )
        Medicao.objects.create(
            contrato=contrato,
            obra=self.obra,
            centro_custo=self.analitico,
            numero_da_medicao="MED-2026-1010",
            data_medicao=timezone.localdate() - timedelta(days=8),
            descricao="Medicao ainda dentro da tolerancia parametrizada",
            status="APROVADA",
        )

        alertas = sincronizar_alertas_medicao_sem_nota(self.obra)

        self.assertEqual(alertas, [])

    def test_regra_nota_sem_rateio_gera_alerta(self):
        nota = NotaFiscal.objects.create(
            obra=self.obra,
            tipo="MATERIAL",
            numero="NF-123",
            serie="1",
            data_emissao=timezone.localdate() - timedelta(days=3),
            fornecedor="Fornecedor Rateio",
            cnpj="12.345.678/0001-90",
            descricao="Nota sem rateio completo",
            valor_total=Decimal("1000.00"),
        )
        NotaFiscalCentroCusto.objects.create(
            nota_fiscal=nota,
            centro_custo=self.analitico,
            valor=Decimal("400.00"),
        )

        alertas = sincronizar_alertas_nota_sem_rateio(self.obra)

        self.assertEqual(len(alertas), 1)
        self.assertTrue(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra=CODIGO_ALERTA_NOTA_SEM_RATEIO,
                entidade_id=nota.pk,
                status="ABERTO",
            ).exists()
        )

    def test_regra_risco_vencido_gera_alerta(self):
        risco = Risco.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            categoria="PRAZO",
            titulo="Atraso na fachada",
            descricao="Risco sem tratamento concluido.",
            probabilidade=4,
            impacto=4,
            responsavel=self.user,
            data_meta_tratamento=timezone.localdate() - timedelta(days=12),
            status="EM_TRATAMENTO",
            criado_por=self.user,
        )

        alertas = sincronizar_alertas_risco_vencido(self.obra)

        self.assertEqual(len(alertas), 1)
        self.assertTrue(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra=CODIGO_ALERTA_RISCO_VENCIDO,
                entidade_id=risco.pk,
                status="ABERTO",
            ).exists()
        )

    def test_regra_nc_sem_evolucao_gera_alerta(self):
        nc = QualidadeWorkflowService.abrir(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            descricao="Nao conformidade antiga",
            responsavel=self.user,
            criado_por=self.user,
        )
        NaoConformidade.objects.filter(pk=nc.pk).update(
            data_abertura=timezone.localdate() - timedelta(days=20),
            criado_em=timezone.now() - timedelta(days=20),
        )
        nc.historico.update(timestamp=timezone.now() - timedelta(days=20))

        alertas = sincronizar_alertas_nc_sem_evolucao(self.obra)

        self.assertEqual(len(alertas), 1)
        self.assertTrue(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra=CODIGO_ALERTA_NC_SEM_EVOLUCAO,
                entidade_id=nc.pk,
                status="ABERTO",
            ).exists()
        )

    def test_sincronizacao_operacional_consolida_varias_regras(self):
        Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato base sem medicao",
            fornecedor="Fornecedor Consolidado",
            cnpj="12.345.678/0001-90",
            responsavel="Equipe",
            telefone="11999999999",
            valor_contratado=Decimal("20000.00"),
            data_assinatura=timezone.localdate() - timedelta(days=25),
            status="EM_EXECUCAO",
        )
        nota = NotaFiscal.objects.create(
            obra=self.obra,
            tipo="MATERIAL",
            numero="NF-456",
            serie="1",
            data_emissao=timezone.localdate() - timedelta(days=2),
            fornecedor="Fornecedor Consolidado",
            cnpj="12.345.678/0001-90",
            descricao="Nota consolidada",
            valor_total=Decimal("500.00"),
        )
        NotaFiscalCentroCusto.objects.create(
            nota_fiscal=nota,
            centro_custo=self.analitico,
            valor=Decimal("100.00"),
        )

        alertas = sincronizar_alertas_operacionais_obra(self.obra)

        self.assertGreaterEqual(len(alertas), 2)

    def test_regra_acumulo_riscos_respeita_parametro_da_empresa(self):
        ParametroAlertaEmpresa.obter_ou_criar(self.empresa)
        ParametroAlertaEmpresa.objects.filter(empresa=self.empresa).update(
            acumulo_riscos_quantidade_minima=10,
            acumulo_riscos_quantidade_critica=12,
        )
        for indice in range(9):
            Risco.objects.create(
                empresa=self.empresa,
                obra=self.obra,
                plano_contas=self.analitico,
                categoria="PRAZO",
                titulo=f"Risco {indice}",
                descricao="Risco operacional aberto",
                probabilidade=3,
                impacto=3,
                responsavel=self.user,
                status="EM_ANALISE",
                criado_por=self.user,
            )

        alertas = sincronizar_alertas_operacionais_obra(self.obra)

        self.assertFalse(
            any(alerta.codigo_regra == "RISK-ACC-001" for alerta in alertas)
        )

    def test_regra_cronograma_sem_avanco_e_desvio_prazo_gera_alertas(self):
        hoje = timezone.localdate()
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline Desvio",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base=hoje,
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-900",
            atividade="Atividade atrasada",
            data_inicio_prevista=hoje - timedelta(days=10),
            data_fim_prevista=hoje + timedelta(days=10),
            percentual_concluido=0,
            valor_planejado=Decimal("10000.00"),
        )
        nota = NotaFiscal.objects.create(
            obra=self.obra,
            numero="NF-PLAN-900",
            tipo="SERVICO",
            data_emissao=hoje,
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Custo sem avanço físico",
            valor_total=Decimal("1500.00"),
            status="LANCADA",
        )
        NotaFiscalCentroCusto.objects.create(
            nota_fiscal=nota,
            centro_custo=self.analitico,
            valor=Decimal("1500.00"),
        )

        sincronizar_alertas_operacionais_obra(self.obra)

        self.assertTrue(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra="PLAN-PROG-001",
                entidade_id=item.pk,
                status="ABERTO",
            ).exists()
        )
        self.assertTrue(
            AlertaOperacional.objects.filter(
                obra=self.obra,
                codigo_regra="COST-PROG-002",
                entidade_id=item.pk,
                status="ABERTO",
            ).exists()
        )

    def test_importacao_cronograma_vincula_automaticamente_pelo_codigo_eap(self):
        arquivo = BytesIO()
        df = pd.DataFrame(
            [
                {
                    "CODIGO": "ATV-100",
                    "ATIVIDADE": "Fundacao operacional",
                    "DURACAO_DIAS": "10",
                    "DATA_INICIO": "20/04/2026",
                    "DATA_FIM": "30/04/2026",
                    "CODIGO_EAP": self.analitico.codigo,
                    "VALOR": "1000,00",
                }
            ]
        )
        df.to_excel(arquivo, index=False)
        arquivo.seek(0)
        arquivo_upload = SimpleUploadedFile(
            "cronograma_auto.xlsx",
            arquivo.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        plano = CronogramaService.importar_xlsx(
            arquivo=arquivo_upload,
            obra=self.obra,
            responsavel=self.user,
            titulo="Cronograma com vinculo automatico",
            criar_baseline=False,
        )

        item = PlanoFisicoItem.objects.get(plano=plano, codigo_atividade="ATV-100")
        self.assertEqual(item.plano_contas, self.analitico)

    def test_importacao_cronograma_mantem_codigo_eap_invalido_com_erro_claro(self):
        arquivo = BytesIO()
        df = pd.DataFrame(
            [
                {
                    "CODIGO": "1.1",
                    "ATIVIDADE": "Escavacao",
                    "DURACAO_DIAS": "4",
                    "DATA_INICIO": "20/04/2026",
                    "DATA_FIM": "24/04/2026",
                    "CODIGO_EAP": "EAP-INEXISTENTE",
                }
            ]
        )
        df.to_excel(arquivo, index=False)
        arquivo.seek(0)
        arquivo_upload = SimpleUploadedFile(
            "cronograma_invalido.xlsx",
            arquivo.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        plano = CronogramaService.importar_xlsx(
            arquivo=arquivo_upload,
            obra=self.obra,
            responsavel=self.user,
            titulo="Cronograma com EAP invalida",
            criar_baseline=False,
        )

        item = PlanoFisicoItem.objects.get(plano=plano, codigo_atividade="1.1")
        self.assertEqual(item.codigo_eap_importado, "EAP-INEXISTENTE")
        self.assertIsNone(item.plano_contas)
        self.assertIn("nao localizado", item.erro_vinculo_eap.lower())

    def test_importacao_cronograma_reconhece_colunas_previstas_e_data_com_hora(self):
        arquivo = BytesIO()
        df = pd.DataFrame(
            [
                {
                    "CODIGO": "ATV-DATA-01",
                    "ATIVIDADE": "Montagem de formas",
                    "DURACAO_DIAS": "3",
                    "INICIO PREVISTO": "20/04/2026 08:00:00",
                    "TERMINO PREVISTO": "22/04/2026 18:00:00",
                }
            ]
        )
        df.to_excel(arquivo, index=False)
        arquivo.seek(0)
        arquivo_upload = SimpleUploadedFile(
            "cronograma_datas.xlsx",
            arquivo.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        plano = CronogramaService.importar_xlsx(
            arquivo=arquivo_upload,
            obra=self.obra,
            responsavel=self.user,
            titulo="Cronograma com datas previstas",
            criar_baseline=False,
        )

        item = PlanoFisicoItem.objects.get(plano=plano, codigo_atividade="ATV-DATA-01")
        self.assertEqual(item.data_inicio_prevista, date(2026, 4, 20))
        self.assertEqual(item.data_fim_prevista, date(2026, 4, 22))

    def test_cronograma_detail_exibe_todos_os_itens_sem_corte(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Completo",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        for indice in range(205):
            PlanoFisicoItem.objects.create(
                plano=plano,
                codigo_atividade=f"ATV-{indice:03d}",
                atividade=f"Atividade {indice:03d}",
                duracao=1,
                data_inicio_prevista=date(2026, 4, 1),
                data_fim_prevista=date(2026, 4, 1),
                sort_order=indice,
            )

        response = self.client.get(reverse("plano_fisico_detail", args=[plano.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ATV-204")

    def test_cronograma_consolida_datas_e_percentuais_do_pai_pelos_filhos(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Hierarquico",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        pai = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="1",
            atividade="Estrutura",
            duracao=0,
            data_inicio_prevista=date(2026, 4, 10),
            data_fim_prevista=date(2026, 4, 10),
            percentual_concluido=0,
            sort_order=1,
            level=0,
        )
        filho_1 = PlanoFisicoItem.objects.create(
            plano=plano,
            parent=pai,
            codigo_atividade="1.1",
            atividade="Fundacao",
            duracao=10,
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 10),
            data_inicio_real=date(2026, 4, 2),
            data_fim_real=date(2026, 4, 11),
            percentual_concluido=50,
            sort_order=2,
            level=1,
        )
        filho_2 = PlanoFisicoItem.objects.create(
            plano=plano,
            parent=pai,
            codigo_atividade="1.2",
            atividade="Blocos",
            duracao=20,
            data_inicio_prevista=date(2026, 4, 5),
            data_fim_prevista=date(2026, 4, 25),
            data_inicio_real=date(2026, 4, 6),
            percentual_concluido=100,
            sort_order=3,
            level=1,
        )

        response = self.client.get(reverse("plano_fisico_detail", args=[plano.pk]))

        self.assertEqual(response.status_code, 200)
        itens = list(response.context["itens"])
        item_pai = next(item for item in itens if item.pk == pai.pk)
        self.assertEqual(item_pai.inicio_previsto_exibicao, filho_1.data_inicio_prevista)
        self.assertEqual(item_pai.fim_previsto_exibicao, filho_2.data_fim_prevista)
        self.assertEqual(item_pai.inicio_real_exibicao, filho_1.data_inicio_real)
        self.assertEqual(item_pai.fim_real_exibicao, filho_1.data_fim_real)
        self.assertEqual(item_pai.duracao_exibicao, 25)
        self.assertAlmostEqual(item_pai.percentual_realizado_exibicao, 83.9, places=1)

    def test_importacao_cronograma_usa_valor_planejado_da_eap_quando_houver_vinculo(self):
        arquivo = BytesIO()
        df = pd.DataFrame(
            [
                {
                    "CODIGO": "ATV-EAP-VALOR",
                    "ATIVIDADE": "Atividade com valor da EAP",
                    "DURACAO_DIAS": "5",
                    "DATA_INICIO": "20/04/2026",
                    "DATA_FIM": "25/04/2026",
                    "CODIGO_EAP": self.analitico.codigo,
                    "VALOR": "10,00",
                }
            ]
        )
        df.to_excel(arquivo, index=False)
        arquivo.seek(0)
        arquivo_upload = SimpleUploadedFile(
            "cronograma_valor_eap.xlsx",
            arquivo.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        plano = CronogramaService.importar_xlsx(
            arquivo=arquivo_upload,
            obra=self.obra,
            responsavel=self.user,
            titulo="Cronograma com valor da EAP",
            criar_baseline=False,
        )

        item = PlanoFisicoItem.objects.get(plano=plano, codigo_atividade="ATV-EAP-VALOR")
        self.assertEqual(item.plano_contas, self.analitico)
        self.assertEqual(item.valor_planejado, self.analitico.valor_total_consolidado)

    def test_mapa_correspondencia_lista_exibe_grupos_por_eap_e_por_atividade(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Mapeado",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item_a = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-A",
            atividade="Atividade A",
            duracao=5,
            data_inicio_prevista=date(2026, 4, 20),
            data_fim_prevista=date(2026, 4, 24),
            sort_order=1,
        )
        item_b = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-B",
            atividade="Atividade B",
            duracao=5,
            data_inicio_prevista=date(2026, 4, 25),
            data_fim_prevista=date(2026, 4, 29),
            sort_order=2,
        )
        MapaCorrespondencia.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_fisico_item=item_a,
            plano_contas=self.analitico,
            created_by=self.user,
        )
        MapaCorrespondencia.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_fisico_item=item_b,
            plano_contas=self.analitico,
            created_by=self.user,
        )

        response = self.client.get(reverse("mapa_correspondencia_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EAP -> Cronograma")
        self.assertContains(response, self.analitico.codigo)
        self.assertContains(response, "ATV-A - Atividade A")
        self.assertContains(response, "ATV-B - Atividade B")

    def test_sugerir_mapeamento_ajax_retorna_sugestao_pelo_codigo_importado(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Sugestao",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-SUG",
            atividade="Atividade com sugestao",
            codigo_eap_importado=self.analitico.codigo,
            duracao=3,
            data_inicio_prevista=date(2026, 4, 20),
            data_fim_prevista=date(2026, 4, 22),
        )

        response = self.client.get(reverse("mapa_correspondencia_sugerir"), {"item_id": item.pk})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertIn(self.analitico.pk, payload["sugestoes_ids"])

    def test_vincular_mapeamento_ajax_aceita_n_eap_para_uma_atividade_e_soma_valores(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Multi EAP",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-MULTI",
            atividade="Atividade multi EAP",
            duracao=4,
            data_inicio_prevista=date(2026, 4, 20),
            data_fim_prevista=date(2026, 4, 23),
        )

        response = self.client.post(
            reverse("mapa_correspondencia_vincular"),
            {
                "item_id": item.pk,
                "plano_contas_ids[]": [str(self.analitico.pk), str(self.analitico_2.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        item.refresh_from_db()
        self.assertIsNone(item.plano_contas)
        self.assertEqual(
            item.valor_planejado,
            self.analitico.valor_total_consolidado + self.analitico_2.valor_total_consolidado,
        )
        self.assertEqual(
            MapaCorrespondencia.objects.filter(plano_fisico_item=item, status="ATIVO").count(),
            2,
        )

    def test_vincular_mapeamento_ajax_bloqueia_cenario_n_para_n(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma N N",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item_1 = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-NN-1",
            atividade="Atividade 1",
            duracao=4,
            data_inicio_prevista=date(2026, 4, 20),
            data_fim_prevista=date(2026, 4, 23),
        )
        item_2 = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-NN-2",
            atividade="Atividade 2",
            duracao=4,
            data_inicio_prevista=date(2026, 4, 24),
            data_fim_prevista=date(2026, 4, 27),
        )
        MapaCorrespondencia.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_fisico_item=item_1,
            plano_contas=self.analitico,
            created_by=self.user,
        )
        MapaCorrespondencia.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_fisico_item=item_2,
            plano_contas=self.analitico_2,
            created_by=self.user,
        )

        response = self.client.post(
            reverse("mapa_correspondencia_vincular"),
            {
                "item_id": item_2.pk,
                "plano_contas_ids[]": [str(self.analitico.pk), str(self.analitico_2.pk)],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("N EAP -> N atividades", response.json()["error"])

    def test_importacao_cronograma_zera_valor_planejado_salvo_em_tarefa_pai(self):
        arquivo = BytesIO()
        df = pd.DataFrame(
            [
                {
                    "CODIGO": "1",
                    "ATIVIDADE": "Pacote pai",
                    "DURACAO_DIAS": "10",
                    "DATA_INICIO": "01/04/2026",
                    "DATA_FIM": "10/04/2026",
                    "CODIGO_EAP": self.analitico.codigo,
                },
                {
                    "CODIGO": "1.1",
                    "ATIVIDADE": "Pacote filho",
                    "DURACAO_DIAS": "10",
                    "DATA_INICIO": "01/04/2026",
                    "DATA_FIM": "10/04/2026",
                    "CODIGO_EAP": self.analitico.codigo,
                },
            ]
        )
        df.to_excel(arquivo, index=False)
        arquivo.seek(0)
        arquivo_upload = SimpleUploadedFile(
            "cronograma_pai_filho.xlsx",
            arquivo.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        plano = CronogramaService.importar_xlsx(
            arquivo=arquivo_upload,
            obra=self.obra,
            responsavel=self.user,
            titulo="Cronograma pai e filho",
            criar_baseline=False,
        )

        pai = PlanoFisicoItem.objects.get(plano=plano, codigo_atividade="1")
        filho = PlanoFisicoItem.objects.get(plano=plano, codigo_atividade="1.1")
        self.assertEqual(pai.valor_planejado, Decimal("0.00"))
        self.assertEqual(filho.valor_planejado, self.analitico.valor_total_consolidado)

    def test_cronograma_detail_calcula_duracao_pelas_datas_mesmo_com_duracao_importada_diferente(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Duracao Calculada",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-DUR",
            atividade="Atividade com duracao recalculada",
            duracao=99,
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 5),
            sort_order=1,
        )

        response = self.client.get(reverse("plano_fisico_detail", args=[plano.pk]))

        self.assertEqual(response.status_code, 200)
        item_ctx = next(reg for reg in response.context["itens"] if reg.pk == item.pk)
        self.assertEqual(item_ctx.duracao_exibicao, 5)

    def test_curva_s_planejada_nao_quebra_em_meses_com_menos_dias(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Curva S",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-CURVA",
            atividade="Atividade de longa duracao",
            duracao=61,
            data_inicio_prevista=date(2026, 3, 31),
            data_fim_prevista=date(2026, 5, 31),
            valor_planejado=Decimal("300.00"),
            sort_order=1,
        )

        curva = CronogramaService.gerar_curva_s_planejada(plano.pk)

        self.assertEqual([p["mes"] for p in curva], ["2026-03", "2026-04", "2026-05"])

    def test_curva_s_realizada_usa_valor_agregado_do_cronograma(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Curva S Realizada",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-REAL",
            atividade="Atividade Realizada",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 30),
            data_inicio_real=date(2026, 4, 10),
            percentual_concluido=50,
            valor_planejado=Decimal("1000.00"),
            sort_order=1,
        )
        item.save()

        curva = CronogramaService.gerar_curva_s_realizada(plano.pk, date(2026, 4, 30))

        self.assertEqual(len(curva), 1)
        self.assertEqual(curva[0]["mes"], "2026-04")
        self.assertEqual(Decimal(str(curva[0]["acumulado"])).quantize(Decimal("0.01")), Decimal("500.00"))

    def test_recalculo_valor_planejado_divide_eap_por_duracao_quando_uma_eap_tem_varias_atividades(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Rateio por Duracao",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        atividade_a = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-A",
            atividade="Atividade A",
            duracao=10,
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 10),
        )
        atividade_b = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-B",
            atividade="Atividade B",
            duracao=20,
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 20),
        )

        MapeamentoService.recalcular_valores_planejados(plano)
        atividade_a.refresh_from_db()
        atividade_b.refresh_from_db()

        valor_total = self.analitico.valor_total_consolidado
        valor_atividade_a = (valor_total * Decimal("10") / Decimal("30")).quantize(Decimal("0.01"))
        self.assertEqual(atividade_a.valor_planejado, valor_atividade_a)
        self.assertEqual(atividade_b.valor_planejado, valor_total - valor_atividade_a)

    def test_validacao_de_vinculo_bloqueia_item_pai(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Pai",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        pai = PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="PAI",
            atividade="Pacote principal",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 10),
        )
        PlanoFisicoItem.objects.create(
            plano=plano,
            parent=pai,
            codigo_atividade="FILHO",
            atividade="Atividade filha",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 5),
        )

        with self.assertRaisesMessage(Exception, "atividades folha"):
            MapeamentoService.validar_novo_vinculo(pai, self.analitico)


    def test_emitir_ordem_compra_cria_ordem_e_compromisso_integrado(self):
        solicitacao = SolicitacaoCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            titulo="Compra de aço",
            descricao="Compra estruturada",
            solicitante=self.user,
            data_solicitacao="2026-03-10",
            status="COTANDO",
        )
        item_solicitacao = SolicitacaoCompraItem.objects.create(
            solicitacao=solicitacao,
            plano_contas=self.analitico,
            descricao_tecnica="Aco CA-50 10mm",
            unidade="kg",
            quantidade=Decimal("10.00"),
            valor_estimado_unitario=Decimal("8.00"),
        )
        cotacao = Cotacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=solicitacao,
            fornecedor=self.fornecedor,
            status="APROVADA",
            data_cotacao="2026-03-12",
            criado_por=self.user,
        )
        CotacaoItem.objects.create(
            cotacao=cotacao,
            item_solicitacao=item_solicitacao,
            valor_unitario=Decimal("9.00"),
        )
        fornecedor_2 = Fornecedor.objects.create(
            empresa=self.empresa,
            razao_social="Fornecedor Comparativo LTDA",
            nome_fantasia="Fornecedor Comparativo",
            cnpj="44.444.444/0001-44",
            telefone="1144444444",
        )
        cotacao_2 = Cotacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=solicitacao,
            fornecedor=fornecedor_2,
            status="EM_ANALISE",
            data_cotacao="2026-03-13",
            criado_por=self.user,
        )
        CotacaoItem.objects.create(
            cotacao=cotacao_2,
            item_solicitacao=item_solicitacao,
            valor_unitario=Decimal("10.50"),
        )

        ordem = AquisicoesService.emitir_ordem_compra(cotacao, self.user, "OC integrada", "CONTRATO")

        self.assertIsInstance(ordem, OrdemCompra)
        self.assertEqual(ordem.itens.count(), 1)
        self.assertIsNotNone(ordem.compromisso_relacionado)
        self.assertEqual(ordem.compromisso_relacionado.itens.count(), 1)
        self.assertEqual(ordem.compromisso_relacionado.valor_contratado, Decimal("90.00"))
        self.assertEqual(ordem.compromisso_relacionado.tipo, "CONTRATO")
        self.assertEqual(ordem.compromisso_relacionado.itens.first().descricao_tecnica, "Aco CA-50 10mm")
        self.assertEqual(ordem.compromisso_relacionado.itens.first().unidade, "kg")

    def test_emitir_ordem_compra_exige_duas_cotacoes_de_fornecedores_distintos(self):
        solicitacao = SolicitacaoCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            titulo="Compra sem comparativo",
            descricao="Fluxo sem dois fornecedores",
            solicitante=self.user,
            data_solicitacao="2026-03-10",
            status="COTANDO",
        )
        item_solicitacao = SolicitacaoCompraItem.objects.create(
            solicitacao=solicitacao,
            plano_contas=self.analitico,
            descricao_tecnica="Aco CA-50 10mm",
            unidade="kg",
            quantidade=Decimal("10.00"),
            valor_estimado_unitario=Decimal("8.00"),
        )
        cotacao = Cotacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=solicitacao,
            fornecedor=self.fornecedor,
            status="APROVADA",
            data_cotacao="2026-03-12",
            criado_por=self.user,
        )
        CotacaoItem.objects.create(
            cotacao=cotacao,
            item_solicitacao=item_solicitacao,
            valor_unitario=Decimal("9.00"),
        )

        with self.assertRaises(ValueError):
            AquisicoesService.emitir_ordem_compra(cotacao, self.user, "Sem comparativo")

    def test_integracao_e_eva_consolidam_obra(self):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato integrado",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            data_assinatura="2026-03-01",
        )
        CompromissoItem.objects.create(
            compromisso=contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("10.00"),
            valor_unitario=Decimal("10.00"),
        )
        medicao = Medicao.objects.create(
            contrato=contrato,
            descricao="Medicao integrada",
            data_medicao="2026-03-20",
        )
        MedicaoItem.objects.create(
            medicao=medicao,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=Decimal("5.00"),
            valor_unitario=Decimal("10.00"),
        )
        nota = NotaFiscal.objects.create(
            numero="NF-EVA",
            tipo="SERVICO",
            data_emissao="2026-03-21",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota integrada",
            valor_total=Decimal("40.00"),
            medicao=medicao,
        )
        NotaFiscalCentroCusto.objects.create(
            nota_fiscal=nota,
            centro_custo=self.analitico,
            valor=Decimal("40.00"),
        )
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline obra",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base="2026-03-01",
        )
        PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="A1",
            atividade="Fundação",
            duracao=10,
            data_inicio_prevista=date(2026, 3, 1),
            data_fim_prevista=date(2026, 3, 31),
            percentual_concluido=50,
            valor_planejado=Decimal("100.00"),
            valor_realizado=Decimal("40.00"),
        )

        consolidado = IntegracaoService.consolidar_obra(self.obra, date(2026, 3, 21))
        eva = EVAService.calcular(self.obra, date(2026, 3, 21))
        indicadores = IndicadoresService.resumo_obra(self.obra, date(2026, 3, 21))

        self.assertEqual(consolidado["orcado"], Decimal("2000.00"))
        self.assertEqual(consolidado["comprometido"], Decimal("100.00"))
        self.assertEqual(consolidado["medido"], Decimal("50.00"))
        self.assertEqual(consolidado["executado"], Decimal("50.00"))
        self.assertEqual(consolidado["custo_real"], Decimal("40.00"))
        self.assertGreater(consolidado["planejado"], Decimal("0.00"))
        self.assertEqual(consolidado["planejado_total"], Decimal("100.00"))
        self.assertEqual(eva["EV"], Decimal("50.00"))
        self.assertEqual(eva["AC"], Decimal("40.00"))
        self.assertEqual(indicadores["executado"], Decimal("50.00"))
        self.assertIn("score_operacional", indicadores)
        self.assertEqual(len(indicadores["score_operacional"]["componentes"]), 4)

    def test_eva_operacional_usa_fisico_do_cronograma_e_nao_valor_medido(self):
        contrato = Compromisso.objects.create(
            obra=self.obra,
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato para EVA operacional",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("200.00"),
            data_assinatura="2026-03-01",
        )
        Medicao.objects.create(
            obra=self.obra,
            contrato=contrato,
            descricao="Medicao desalinhada do fisico",
            valor_medido=Decimal("80.00"),
            data_medicao="2026-03-15",
        )
        nota = NotaFiscal.objects.create(
            obra=self.obra,
            numero="NF-EVA-OP",
            tipo="SERVICO",
            data_emissao="2026-03-16",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            descricao="Nota EVA operacional",
            valor_total=Decimal("30.00"),
        )
        NotaFiscalCentroCusto.objects.create(
            nota_fiscal=nota,
            centro_custo=self.analitico,
            valor=Decimal("30.00"),
        )
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline EVA operacional",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base="2026-03-01",
        )
        PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="B1",
            atividade="Estrutura",
            duracao=10,
            data_inicio_prevista=date(2026, 3, 1),
            data_fim_prevista=date(2026, 3, 31),
            percentual_concluido=20,
            valor_planejado=Decimal("100.00"),
            valor_realizado=Decimal("80.00"),
        )

        consolidado = IntegracaoService.consolidar_obra(self.obra, date(2026, 3, 21))
        eva = EVAService.calcular(self.obra, date(2026, 3, 21))

        self.assertEqual(consolidado["medido"], Decimal("80.00"))
        self.assertEqual(consolidado["executado"], Decimal("20.00"))
        self.assertEqual(consolidado["custo_real"], Decimal("30.00"))
        self.assertEqual(eva["EV"], Decimal("20.00"))
        self.assertEqual(eva["AC"], Decimal("30.00"))

    def test_score_operacional_reduz_com_alertas_e_riscos(self):
        risco = Risco.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            titulo="Risco critico",
            categoria="PRAZO",
            descricao="Risco relevante para score",
            probabilidade=5,
            impacto=4,
            responsavel=self.user,
            criado_por=self.user,
            status="EM_TRATAMENTO",
        )
        QualidadeWorkflowService.abrir(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            descricao="Nao conformidade para score",
            responsavel=self.user,
            criado_por=self.user,
        )
        AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="SCORE-TEST-001",
            titulo="Alerta critico de score",
            descricao="Teste do score operacional",
            severidade="CRITICA",
            referencia=risco.codigo,
            status="ABERTO",
        )

        score = IndicadoresService.score_obra(self.obra, date(2026, 3, 21))

        self.assertLess(score["pontuacao"], Decimal("100.00"))
        self.assertEqual(len(score["componentes"]), 4)
        self.assertGreaterEqual(score["total_riscos_ativos"], 1)
        self.assertGreaterEqual(score["total_ncs_abertas"], 1)

    def test_score_so_penaliza_alerta_fora_do_sla(self):
        ParametroAlertaEmpresa.obter_ou_criar(self.empresa)
        ParametroAlertaEmpresa.objects.filter(empresa=self.empresa).update(
            alerta_sem_workflow_dias=7,
            alerta_prazo_solucao_dias=14,
        )
        alerta = AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="PLAN-SUP-001",
            titulo="Alerta em SLA",
            descricao="Ainda dentro do prazo",
            severidade="ALTA",
            referencia="REF-SLA-1",
            status="ABERTO",
        )
        score_em_sla = IndicadoresService.score_obra(self.obra, timezone.localdate())
        self.assertEqual(score_em_sla["total_alertas_pendentes_score"], 0)

        AlertaOperacional.objects.filter(pk=alerta.pk).update(
            criado_em=timezone.now() - timedelta(days=20),
            ultima_acao_em=timezone.now() - timedelta(days=10),
        )
        score_fora_sla = IndicadoresService.score_obra(self.obra, timezone.localdate())
        self.assertGreaterEqual(score_fora_sla["total_alertas_pendentes_score"], 1)

    def test_score_nao_penaliza_alerta_critico_recente_dentro_do_sla(self):
        ParametroAlertaEmpresa.obter_ou_criar(self.empresa)
        ParametroAlertaEmpresa.objects.filter(empresa=self.empresa).update(
            alerta_sem_workflow_dias=7,
            alerta_prazo_solucao_dias=14,
        )
        score_base = IndicadoresService.score_obra(self.obra, timezone.localdate())

        AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="RISK-DUE-001",
            titulo="Alerta crítico recente",
            descricao="Ainda dentro do prazo operacional",
            severidade="CRITICA",
            referencia="REF-SLA-CRIT-1",
            status="ABERTO",
        )

        score_recente = IndicadoresService.score_obra(self.obra, timezone.localdate())
        self.assertEqual(score_recente["total_alertas_pendentes_score"], 0)
        componente_base = next(item for item in score_base["componentes"] if item["nome"] == "Riscos e qualidade")
        componente_recente = next(item for item in score_recente["componentes"] if item["nome"] == "Riscos e qualidade")
        self.assertEqual(componente_recente["pontuacao"], componente_base["pontuacao"])

    def test_fornecedor_calcula_media_de_avaliacao(self):
        FornecedorAvaliacao.objects.create(
            fornecedor=self.fornecedor,
            obra=self.obra,
            nota=4,
            comentario="Bom atendimento",
            avaliado_por=self.user,
        )
        FornecedorAvaliacao.objects.create(
            fornecedor=self.fornecedor,
            obra=self.obra,
            nota=5,
            comentario="Entrega no prazo",
            avaliado_por=self.user,
        )

        self.assertEqual(self.fornecedor.media_avaliacao, Decimal("4.50"))

    def test_home_dashboard_exibe_blocos_novos(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aquisicoes Estruturadas")
        self.assertContains(response, "Riscos e Qualidade")
        self.assertContains(response, "Integração Físico-Financeira")
        self.assertContains(response, "Score Operacional da Obra")
        self.assertContains(response, "Prioridades Executivas")
        self.assertContains(response, "Correlação Operacional")
        self.assertContains(response, "Execucoes automaticas recentes")

    def test_central_alertas_operacionais_carrega(self):
        AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-001",
            titulo="Alerta de teste",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-1",
        )

        response = self.client.get(reverse("alerta_operacional_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central de Alertas Operacionais")
        self.assertContains(response, "TEST-001")
        self.assertContains(response, "Catalogo das Regras")

    @override_settings(CONSTRUTASK_ASYNC_ALERT_SYNC_ENABLED=True)
    def test_central_alertas_operacionais_carrega_com_fila_indisponivel(self):
        AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-QUEUE-001",
            titulo="Alerta com fila indisponivel",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-QUEUE-1",
        )

        with patch(
            "Construtask.tasks.task_sincronizar_alertas_obra.delay",
            side_effect=RuntimeError("broker indisponivel"),
        ):
            response = self.client.get(reverse("alerta_operacional_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TEST-QUEUE-001")

    def test_central_alertas_operacionais_filtra_por_prazo_vencido(self):
        AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-PRAZO-001",
            titulo="Alerta com prazo vencido",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-PRAZO-1",
            status="EM_TRATAMENTO",
            responsavel=self.user,
            prazo_solucao_em=timezone.localdate() - timedelta(days=2),
        )
        AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-PRAZO-002",
            titulo="Alerta em dia",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-PRAZO-2",
            status="EM_TRATAMENTO",
            responsavel=self.user,
            prazo_solucao_em=timezone.localdate() + timedelta(days=3),
        )

        response = self.client.get(reverse("alerta_operacional_list"), {"atraso": "PRAZO"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alerta com prazo vencido")
        self.assertNotContains(response, "Alerta em dia")

    def test_central_alertas_operacionais_filtra_por_sla_estourado(self):
        alerta = AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-SLA-001",
            titulo="Alerta com SLA estourado",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-SLA-1",
            status="ABERTO",
        )
        AlertaOperacional.objects.filter(pk=alerta.pk).update(
            criado_em=timezone.now() - timedelta(days=20),
            ultima_acao_em=timezone.now() - timedelta(days=10),
        )

        response = self.client.get(reverse("alerta_operacional_list"), {"atraso": "SLA"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alerta com SLA estourado")

    def test_painel_executivo_alertas_carrega(self):
        response = self.client.get(reverse("alerta_operacional_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Painel Executivo de Alertas")
        self.assertContains(response, "Prioridades Executivas")
        self.assertContains(response, "Correlacoes Operacionais")

    def test_painel_executivo_alertas_exporta_excel(self):
        response = self.client.get(reverse("alerta_operacional_dashboard_export"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_painel_executivo_alertas_exporta_pdf(self):
        response = self.client.get(reverse("alerta_operacional_dashboard_pdf"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_health_check_publico_retorna_json(self):
        self.client.logout()

        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertTrue(bool(response["X-Request-ID"]))
        self.assertJSONEqual(
            response.content,
            {
                "status": "ok",
                "timestamp": response.json()["timestamp"],
                "checks": {"database": "ok"},
            },
        )

    def test_readiness_check_publico_retorna_json(self):
        self.client.logout()

        response = self.client.get("/ready/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["kind"], "readiness")
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("operacao_saas", response.json())
        self.assertIn("checks", response.json()["operacao_saas"])

    def test_observabilidade_registra_metrica_de_requisicao(self):
        superuser = get_user_model().objects.create_superuser(
            username="Construtask",
            email="construtask@empresa.com",
            password="senha12345",
        )
        self.client.force_login(superuser)
        total_antes = MetricaRequisicao.objects.count()

        response = self.client.get(reverse("observabilidade_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertGreater(MetricaRequisicao.objects.count(), total_antes)
        metrica = MetricaRequisicao.objects.order_by("-id").first()
        self.assertEqual(metrica.path, reverse("observabilidade_dashboard"))
        self.assertEqual(metrica.status_code, 200)
        self.assertTrue(bool(metrica.request_id))

    def test_admin_empresa_nao_acessa_observabilidade(self):
        response = self.client.get(reverse("observabilidade_dashboard"))

        self.assertEqual(response.status_code, 403)

    def test_observabilidade_captura_erro_de_aplicacao(self):
        superuser = get_user_model().objects.create_superuser(
            username="Construtask",
            email="construtask@empresa.com",
            password="senha12345",
        )
        self.client.force_login(superuser)
        self.client.raise_request_exception = False

        response = self.client.get(reverse("observabilidade_teste_erro"))

        self.assertEqual(response.status_code, 500)
        erro = RastroErroAplicacao.objects.order_by("-id").first()
        self.assertIsNotNone(erro)
        self.assertEqual(erro.path, reverse("observabilidade_teste_erro"))
        self.assertEqual(erro.classe_erro, "RuntimeError")
        self.assertIn("Falha de observabilidade", erro.mensagem)

    def test_painel_observabilidade_exibe_blocos_principais(self):
        MetricaRequisicao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            usuario=self.user,
            request_id="req-metrica-1",
            metodo="GET",
            path="/teste-metrica/",
            status_code=200,
            duracao_ms=Decimal("125.50"),
        )
        RastroErroAplicacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            usuario=self.user,
            request_id="req-erro-1",
            metodo="GET",
            path="/teste-erro/",
            status_code=500,
            classe_erro="RuntimeError",
            mensagem="Erro sintetico",
            stacktrace="stacktrace",
        )
        superuser = get_user_model().objects.create_superuser(
            username="Construtask",
            email="construtask@empresa.com",
            password="senha12345",
        )
        self.client.force_login(superuser)

        response = self.client.get(reverse("observabilidade_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Observabilidade")
        self.assertContains(response, "Metricas Recentes")
        self.assertContains(response, "Erros Recentes")
        self.assertContains(response, "Latencia por Endpoint")
        self.assertContains(response, "Retencao: metricas")
        self.assertContains(response, "Erro sintetico")

    def test_operacao_tecnica_dashboard_exibe_alertas_e_checklists(self):
        MetricaRequisicao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            usuario=self.user,
            request_id="req-lento-1",
            metodo="GET",
            path="/cronogramas/",
            status_code=500,
            duracao_ms=Decimal("2500.00"),
        )
        RastroErroAplicacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            usuario=self.user,
            request_id="req-erro-op-1",
            metodo="GET",
            path="/cronogramas/",
            status_code=500,
            classe_erro="RuntimeError",
            mensagem="Erro tecnico aberto",
            stacktrace="trace",
            resolvido=False,
        )
        JobAssincrono.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitado_por=self.user,
            tipo="SINCRONIZAR_ALERTAS_OBRA",
            descricao="Job falho de teste",
            status="FALHOU",
            erro="Falha sintetica",
        )
        superuser = get_user_model().objects.create_superuser(
            username="Construtask",
            email="construtask@empresa.com",
            password="senha12345",
        )
        self.client.force_login(superuser)

        response = self.client.get(reverse("operacao_tecnica_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operacao Tecnica")
        self.assertContains(response, "Alertas Operacionais")
        self.assertContains(response, "Checklist de Deploy")
        self.assertContains(response, "Checklist de Rollback")
        self.assertContains(response, "Rotina de Acompanhamento")
        self.assertContains(response, "Jobs assincronos com falha")

    def test_admin_empresa_nao_acessa_operacao_tecnica(self):
        response = self.client.get(reverse("operacao_tecnica_dashboard"))

        self.assertEqual(response.status_code, 403)

    def test_comando_emitir_resumo_operacao_tecnica_retorna_json(self):
        JobAssincrono.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitado_por=self.user,
            tipo="GERAR_RELATORIO_FINANCEIRO",
            descricao="Job pendente",
            status="PENDENTE",
        )

        stdout = StringIO()
        call_command("emitir_resumo_operacao_tecnica", "--usuario", self.user.username, "--json", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["status"], "ok")
        self.assertIn("alertas", payload)
        self.assertIn("resumo_metricas", payload)
        self.assertIn("resumo_jobs", payload)

    @override_settings(CONSTRUTASK_METRICAS_RETENTION_DAYS=30, CONSTRUTASK_ERROS_APLICACAO_RETENTION_DAYS=60)
    def test_comando_aplicar_retencao_observabilidade_remove_registros_antigos(self):
        metrica_antiga = MetricaRequisicao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            usuario=self.user,
            request_id="req-antiga",
            metodo="GET",
            path="/antiga/",
            status_code=200,
            duracao_ms=Decimal("150.00"),
        )
        erro_antigo = RastroErroAplicacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            usuario=self.user,
            request_id="erro-antigo",
            metodo="GET",
            path="/erro-antigo/",
            status_code=500,
            classe_erro="RuntimeError",
            mensagem="Erro antigo",
            stacktrace="trace",
        )
        MetricaRequisicao.objects.filter(pk=metrica_antiga.pk).update(criado_em=timezone.now() - timedelta(days=45))
        RastroErroAplicacao.objects.filter(pk=erro_antigo.pk).update(criado_em=timezone.now() - timedelta(days=90))

        stdout = StringIO()
        call_command("aplicar_retencao_observabilidade", "--json", stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["metricas"]["removidas"], 1)
        self.assertEqual(payload["erros"]["removidos"], 1)
        self.assertFalse(MetricaRequisicao.objects.filter(pk=metrica_antiga.pk).exists())
        self.assertFalse(RastroErroAplicacao.objects.filter(pk=erro_antigo.pk).exists())

    def test_comando_diagnosticar_latencia_operacional_agrega_endpoints(self):
        MetricaRequisicao.objects.bulk_create(
            [
                MetricaRequisicao(
                    empresa=self.empresa,
                    obra=self.obra,
                    usuario=self.user,
                    request_id="req-lat-1",
                    metodo="GET",
                    path="/financeiro/",
                    status_code=200,
                    duracao_ms=Decimal("210.00"),
                ),
                MetricaRequisicao(
                    empresa=self.empresa,
                    obra=self.obra,
                    usuario=self.user,
                    request_id="req-lat-2",
                    metodo="GET",
                    path="/financeiro/",
                    status_code=500,
                    duracao_ms=Decimal("510.00"),
                ),
            ]
        )

        stdout = StringIO()
        call_command("diagnosticar_latencia_operacional", "--json", stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertGreaterEqual(payload["resumo_metricas"]["total"], 2)
        self.assertEqual(payload["endpoints_lentos"][0]["path"], "/financeiro/")
        self.assertEqual(payload["endpoints_lentos"][0]["erros_500"], 1)

    def test_comando_validar_base_saas_retorna_resumo(self):
        stdout = StringIO()

        call_command("validar_base_saas", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Base SaaS:", output)
        self.assertIn("database:", output)

    @override_settings(
        DEBUG=False,
        CONSTRUTASK_ENVIRONMENT="production",
        CONSTRUTASK_BACKUP_ENABLED=False,
        CONSTRUTASK_BACKUP_PROVIDER="",
        CONSTRUTASK_MEDIA_PERSISTENT=False,
        SECURE_SSL_REDIRECT=False,
        CSRF_TRUSTED_ORIGINS=[],
        ALLOWED_HOSTS=[],
    )
    def test_comando_validar_base_saas_json_reflete_pendencias(self):
        stdout = StringIO()

        call_command("validar_base_saas", "--json", stdout=stdout)

        payload = stdout.getvalue()
        self.assertIn('"status": "error"', payload)
        self.assertIn('"backup"', payload)

    def test_registro_backup_e_teste_recuperacao_aparecem_na_base_saas(self):
        call_command(
            "registrar_backup_saas",
            "--provedor",
            "s3",
            "--artefato",
            "backup-2026-04-17.dump",
            "--checksum",
            "abc123",
            "--tamanho-bytes",
            "2048",
            "--usuario",
            self.user.username,
        )
        backup = OperacaoBackupSaaS.objects.get(tipo="BACKUP")

        call_command(
            "registrar_teste_recuperacao_saas",
            "--backup-id",
            str(backup.pk),
            "--status",
            "SUCESSO",
            "--usuario",
            self.user.username,
        )

        user_model = get_user_model()
        admin_sistema = user_model.objects.create_superuser(
            username="Construtask",
            email="sistema@construtask.com",
            password="senhaforte123",
        )
        self.client.force_login(admin_sistema)

        response = self.client.get(reverse("sistema_admin"), {"empresa": self.empresa.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ultimo Backup")
        self.assertContains(response, "Teste de Recuperacao")
        self.assertContains(response, "s3")

    def test_sincronizacao_alertas_por_job_cria_registro_pendente(self):
        response = self.client.post(reverse("alerta_operacional_sincronizar_job"), follow=True)

        self.assertEqual(response.status_code, 200)
        job = JobAssincrono.objects.get(tipo="SINCRONIZAR_ALERTAS_OBRA")
        self.assertEqual(job.status, "PENDENTE")
        self.assertEqual(job.obra, self.obra)

    def test_importacao_plano_contas_por_job_processa_arquivo(self):
        arquivo = BytesIO()
        pd.DataFrame(
            [
                {"ITEM": "1", "DESCRICAO": "Estrutura", "UN": "vb", "QTD": 1, "VALOR UNIT": 1000},
                {"ITEM": "1.1", "DESCRICAO": "Fundacao", "UN": "vb", "QTD": 1, "VALOR UNIT": 400},
                {"ITEM": "1.1.1", "DESCRICAO": "Bloco", "UN": "vb", "QTD": 1, "VALOR UNIT": 250},
                {"ITEM": "1.1.1.1", "DESCRICAO": "Concreto", "UN": "m3", "QTD": 2, "VALOR UNIT": 50},
                {"ITEM": "1.1.1.1.1", "DESCRICAO": "Aplicacao", "UN": "m3", "QTD": 2, "VALOR UNIT": 50},
                {"ITEM": "1.1.1.1.1.1", "DESCRICAO": "Equipe", "UN": "h", "QTD": 10, "VALOR UNIT": 10},
            ]
        ).to_excel(arquivo, index=False)
        arquivo.seek(0)
        upload = SimpleUploadedFile(
            "plano_job.xlsx",
            arquivo.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        response = self.client.post(reverse("plano_contas_importar"), {"arquivo": upload}, follow=True)

        self.assertEqual(response.status_code, 200)
        job = JobAssincrono.objects.get(tipo="IMPORTAR_PLANO_CONTAS")
        self.assertEqual(job.status, "PENDENTE")

        call_command("processar_jobs_assincronos", limite=5)
        job.refresh_from_db()

        self.assertEqual(job.status, "CONCLUIDO")
        self.assertTrue(PlanoContas.objects.filter(obra=self.obra, codigo="1.1.1.1.1.1").exists())

    def test_relatorio_financeiro_por_job_gera_arquivo_resultado(self):
        response = self.client.post(
            reverse("projecao_financeira_job"),
            {"meses": 6},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        job = JobAssincrono.objects.get(tipo="GERAR_RELATORIO_FINANCEIRO")
        self.assertEqual(job.status, "PENDENTE")
        self.assertEqual(job.parametros["relatorio"], "PROJECAO_FINANCEIRA")

        call_command("processar_jobs_assincronos", limite=5)
        job.refresh_from_db()

        self.assertEqual(job.status, "CONCLUIDO")
        self.assertTrue(bool(job.arquivo_resultado))

    def test_jobs_assincronos_lista_registros_do_contexto(self):
        JobAssincrono.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitado_por=self.user,
            tipo="SINCRONIZAR_ALERTAS_OBRA",
            status="CONCLUIDO",
            descricao="Sincronizacao concluida",
            resultado={"alertas_abertos": 2},
        )

        response = self.client.get(reverse("jobs_assincronos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jobs Assincronos")
        self.assertContains(response, "Sincronizacao concluida")

    def test_detalhe_alerta_operacional_exibe_historico(self):
        alerta = AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-DET-001",
            titulo="Alerta detalhado",
            descricao="Descricao operacional",
            severidade="CRITICA",
            referencia="REF-DET",
        )
        AlertaOperacionalHistorico.objects.create(
            alerta=alerta,
            usuario=self.user,
            acao="CRIACAO",
            status_novo="ABERTO",
            observacao="Criado automaticamente",
        )

        response = self.client.get(reverse("alerta_operacional_detail", args=[alerta.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Histórico do Alerta")
        self.assertContains(response, "Criado automaticamente")

    def test_sincronizacao_operacional_registra_execucao_automatica(self):
        hoje = timezone.localdate()
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline automatizada",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base=hoje,
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-AUTO-001",
            atividade="Estrutura sem compra",
            duracao=5,
            data_inicio_prevista=hoje + timedelta(days=10),
            data_fim_prevista=hoje + timedelta(days=15),
            percentual_concluido=0,
        )

        sincronizar_alertas_operacionais_obra(self.obra)

        self.assertTrue(
            ExecucaoRegraOperacional.objects.filter(
                obra=self.obra,
                codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
                referencia=f"{item.pk}:{self.analitico.pk}",
                resultado="CRIADO",
            ).exists()
        )

    def test_regra_operacional_exibe_catalogo_e_execucao_no_detalhe(self):
        hoje = timezone.localdate()
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline detalhe",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base=hoje,
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-AUTO-DET",
            atividade="Frente detalhada",
            duracao=5,
            data_inicio_prevista=hoje + timedelta(days=8),
            data_fim_prevista=hoje + timedelta(days=13),
            percentual_concluido=0,
        )
        sincronizar_alertas_planejamento_suprimentos(self.obra)
        alerta = AlertaOperacional.objects.get(
            obra=self.obra,
            codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
            referencia=f"{item.pk}:{self.analitico.pk}",
        )

        response = self.client.get(reverse("alerta_operacional_detail", args=[alerta.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Regra Operacional")
        self.assertContains(response, "Execucao Automatica")
        self.assertContains(response, CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS)

    def test_catalogo_alertas_empresa_retorna_parametros_da_empresa(self):
        ParametroAlertaEmpresa.objects.update_or_create(
            empresa=self.empresa,
            defaults={"planejamento_suprimentos_janela_dias": 45},
        )

        catalogo = catalogo_alertas_empresa(self.empresa)
        regra = next(item for item in catalogo if item["codigo"] == CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS)

        self.assertEqual(regra["valor_atual"], "45 dia(s)")

    def test_workflow_alerta_operacional_atualiza_status_e_historico(self):
        alerta = AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-WF-001",
            titulo="Alerta workflow",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-WF",
        )

        response = self.client.post(
            reverse("alerta_operacional_workflow", args=[alerta.pk]),
            {
                "acao": "encerrar",
                "observacao": "Tratado e comprovado.",
                "next": reverse("alerta_operacional_detail", args=[alerta.pk]),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        alerta.refresh_from_db()
        self.assertEqual(alerta.status, "ENCERRADO")
        self.assertEqual(alerta.ultima_acao_por, self.user)
        self.assertTrue(
            AlertaOperacionalHistorico.objects.filter(
                alerta=alerta,
                acao="ENCERRAMENTO",
                observacao="Tratado e comprovado.",
            ).exists()
        )

    def test_assumir_alerta_exige_prazo_para_solucao(self):
        alerta = AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-WF-ASSUME-001",
            titulo="Alerta para assumir",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-ASSUME",
        )

        response = self.client.post(
            reverse("alerta_operacional_workflow", args=[alerta.pk]),
            {
                "acao": "assumir",
                "observacao": "Vou tratar.",
                "next": reverse("alerta_operacional_detail", args=[alerta.pk]),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        alerta.refresh_from_db()
        self.assertEqual(alerta.status, "ABERTO")
        self.assertIsNone(alerta.prazo_solucao_em)
        self.assertContains(response, "Informe o prazo para solucao ao assumir o alerta.")

    def test_assumir_alerta_grava_prazo_para_solucao(self):
        alerta = AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-WF-ASSUME-002",
            titulo="Alerta para assumir com prazo",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-ASSUME-OK",
        )
        prazo = timezone.localdate() + timedelta(days=5)

        response = self.client.post(
            reverse("alerta_operacional_workflow", args=[alerta.pk]),
            {
                "acao": "assumir",
                "observacao": "Tratamento iniciado.",
                "prazo_solucao_em": prazo.isoformat(),
                "next": reverse("alerta_operacional_detail", args=[alerta.pk]),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        alerta.refresh_from_db()
        self.assertEqual(alerta.status, "EM_TRATAMENTO")
        self.assertEqual(alerta.responsavel, self.user)
        self.assertEqual(alerta.prazo_solucao_em, prazo)

    def test_alerta_justificado_permanece_justificado_apos_nova_sincronizacao(self):
        hoje = timezone.localdate()
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Baseline justificativa",
            responsavel_importacao=self.user,
            status="BASELINE",
            is_baseline=True,
            data_base=hoje,
        )
        item = PlanoFisicoItem.objects.create(
            plano=plano,
            plano_contas=self.analitico,
            codigo_atividade="ATV-JUST-001",
            atividade="Frente com justificativa ativa",
            duracao=5,
            data_inicio_prevista=hoje + timedelta(days=7),
            data_fim_prevista=hoje + timedelta(days=12),
            percentual_concluido=0,
        )
        sincronizar_alertas_planejamento_suprimentos(self.obra)
        alerta = AlertaOperacional.objects.get(
            obra=self.obra,
            codigo_regra=CODIGO_ALERTA_PLANEJAMENTO_SUPRIMENTOS,
            referencia=f"{item.pk}:{self.analitico.pk}",
        )
        response_justificativa = self.client.post(
            reverse("alerta_operacional_workflow", args=[alerta.pk]),
            {
                "acao": "justificar",
                "observacao": "Risco aceito enquanto a contratacao complementar eh concluida.",
                "next": reverse("alerta_operacional_detail", args=[alerta.pk]),
            },
            follow=True,
        )
        self.assertEqual(response_justificativa.status_code, 200)

        sincronizar_alertas_planejamento_suprimentos(self.obra)

        alerta.refresh_from_db()
        self.assertEqual(alerta.status, "JUSTIFICADO")
        self.assertEqual(
            alerta.observacao_status,
            "Risco aceito enquanto a contratacao complementar eh concluida.",
        )

    def test_tecnico_nao_pode_encerrar_alerta_operacional(self):
        alerta = AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="TEST-WF-TEC-001",
            titulo="Alerta bloqueado por perfil",
            descricao="Descricao operacional",
            severidade="ALTA",
            referencia="REF-WF-TEC",
        )
        tecnico = self._criar_usuario_operacional("tecnico_alerta", "TECNICO_OBRAS")
        self.client.force_login(tecnico)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("alerta_operacional_workflow", args=[alerta.pk]),
            {
                "acao": "encerrar",
                "observacao": "Tentativa indevida.",
                "next": reverse("alerta_operacional_detail", args=[alerta.pk]),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        alerta.refresh_from_db()
        self.assertEqual(alerta.status, "ABERTO")
        self.assertContains(response, "nao pode encerrar alertas operacionais")

    def test_lista_de_nao_conformidades_carrega(self):
        QualidadeWorkflowService.abrir(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            descricao="Falha de concretagem",
            responsavel=self.user,
            criado_por=self.user,
        )
        response = self.client.get(reverse("nao_conformidade_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Falha de concretagem")

    def test_cadastro_de_fornecedor_via_view(self):
        response = self.client.post(
            reverse("fornecedor_create"),
            {
                "razao_social": "Fornecedor Web LTDA",
                "nome_fantasia": "Fornecedor Web",
                "cnpj": "33.333.333/0001-33",
                "contato": "Ana",
                "telefone": "1144444444",
                "email": "contato@fornecedorweb.com",
                "ativo": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Fornecedor.objects.filter(cnpj="33.333.333/0001-33").exists())

    def test_fluxo_web_de_solicitacao_cotacao_e_ordem(self):
        solicitacao = SolicitacaoCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            titulo="Compra via tela",
            descricao="Solicitacao para fluxo web",
            solicitante=self.user,
            data_solicitacao="2026-03-18",
            status="COTANDO",
        )
        item = SolicitacaoCompraItem.objects.create(
            solicitacao=solicitacao,
            plano_contas=self.analitico,
            unidade="m3",
            quantidade=Decimal("8.00"),
            valor_estimado_unitario=Decimal("12.00"),
        )
        cotacao = Cotacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=solicitacao,
            fornecedor=self.fornecedor,
            status="APROVADA",
            data_cotacao="2026-03-19",
            criado_por=self.user,
        )
        CotacaoItem.objects.create(
            cotacao=cotacao,
            item_solicitacao=item,
            valor_unitario=Decimal("11.50"),
        )
        fornecedor_2 = Fornecedor.objects.create(
            empresa=self.empresa,
            razao_social="Fornecedor Web Comparativo LTDA",
            nome_fantasia="Fornecedor Web Comparativo",
            cnpj="66.666.666/0001-66",
            telefone="1166666666",
        )
        cotacao_2 = Cotacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=solicitacao,
            fornecedor=fornecedor_2,
            status="EM_ANALISE",
            data_cotacao="2026-03-20",
            criado_por=self.user,
        )
        CotacaoItem.objects.create(
            cotacao=cotacao_2,
            item_solicitacao=item,
            valor_unitario=Decimal("12.20"),
        )

        response_lista = self.client.get(reverse("solicitacao_compra_list"))
        self.assertEqual(response_lista.status_code, 200)
        self.assertContains(response_lista, solicitacao.numero)

        response_detail = self.client.get(reverse("cotacao_detail", args=[cotacao.pk]))
        self.assertEqual(response_detail.status_code, 200)
        self.assertContains(response_detail, cotacao.numero)

        response_emitir = self.client.post(
            reverse("cotacao_detail", args=[cotacao.pk]),
            {"acao": "emitir_oc", "descricao": "Contrato gerado pela view", "tipo_resultado": "CONTRATO"},
            follow=True,
        )
        self.assertEqual(response_emitir.status_code, 200)
        ordem = OrdemCompra.objects.get(cotacao_aprovada=cotacao)
        self.assertEqual(ordem.compromisso_relacionado.tipo, "CONTRATO")
        self.assertEqual(ordem.status, "RASCUNHO")
        self.assertContains(response_emitir, ordem.numero)

    def test_tecnico_envia_ordem_compra_para_aprovacao_mas_nao_aprova(self):
        tecnico = self._criar_usuario_operacional("tecnico_oc", "TECNICO_OBRAS")
        ordem = OrdemCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=SolicitacaoCompra.objects.create(
                empresa=self.empresa,
                obra=self.obra,
                plano_contas=self.analitico,
                titulo="Solicitacao OC",
                descricao="Solicitacao para workflow",
                solicitante=self.user,
                data_solicitacao="2026-03-10",
                status="COTANDO",
            ),
            cotacao_aprovada=Cotacao.objects.create(
                empresa=self.empresa,
                obra=self.obra,
                solicitacao=SolicitacaoCompra.objects.filter(titulo="Solicitacao OC").first(),
                fornecedor=Fornecedor.objects.create(
                    empresa=self.empresa,
                    razao_social="Fornecedor Workflow LTDA",
                    nome_fantasia="Fornecedor Workflow",
                    cnpj="88.888.888/0001-88",
                ),
                status="APROVADA",
                data_cotacao="2026-03-12",
                criado_por=self.user,
            ),
            fornecedor=Fornecedor.objects.get(cnpj="88.888.888/0001-88"),
            descricao="Ordem para envio",
            emitido_por=self.user,
            valor_total=Decimal("1000.00"),
            data_emissao="2026-03-12",
            status="RASCUNHO",
        )
        self.client.force_login(tecnico)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response_envio = self.client.post(
            reverse("ordem_compra_detail", args=[ordem.pk]),
            {"acao": "enviar_para_aprovacao"},
            follow=True,
        )

        self.assertEqual(response_envio.status_code, 200)
        ordem.refresh_from_db()
        self.assertEqual(ordem.status, "EM_APROVACAO")
        self.assertEqual(ordem.enviado_para_aprovacao_por, tecnico)

        response_aprovacao = self.client.post(
            reverse("ordem_compra_detail", args=[ordem.pk]),
            {"acao": "aprovar"},
            follow=True,
        )

        self.assertEqual(response_aprovacao.status_code, 200)
        ordem.refresh_from_db()
        self.assertEqual(ordem.status, "EM_APROVACAO")
        self.assertIsNone(ordem.aprovado_por)

    def test_engenheiro_aprova_ordem_compra_ate_cinquenta_mil(self):
        engenheiro = self._criar_usuario_operacional("engenheiro_oc", "ENGENHEIRO_OBRAS")
        ordem = OrdemCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=SolicitacaoCompra.objects.create(
                empresa=self.empresa,
                obra=self.obra,
                plano_contas=self.analitico,
                titulo="Solicitacao OC Eng",
                descricao="Solicitacao para aprovacao",
                solicitante=self.user,
                data_solicitacao="2026-03-10",
                status="COTANDO",
            ),
            cotacao_aprovada=Cotacao.objects.create(
                empresa=self.empresa,
                obra=self.obra,
                solicitacao=SolicitacaoCompra.objects.filter(titulo="Solicitacao OC Eng").first(),
                fornecedor=Fornecedor.objects.create(
                    empresa=self.empresa,
                    razao_social="Fornecedor Eng LTDA",
                    nome_fantasia="Fornecedor Eng",
                    cnpj="77.777.777/0001-77",
                ),
                status="APROVADA",
                data_cotacao="2026-03-12",
                criado_por=self.user,
            ),
            fornecedor=Fornecedor.objects.get(cnpj="77.777.777/0001-77"),
            descricao="Ordem dentro da alcada",
            emitido_por=self.user,
            valor_total=Decimal("50000.00"),
            data_emissao="2026-03-12",
            status="EM_APROVACAO",
        )
        self.client.force_login(engenheiro)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("ordem_compra_detail", args=[ordem.pk]),
            {"acao": "aprovar", "parecer_aprovacao": "Ordem aprovada."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        ordem.refresh_from_db()
        self.assertEqual(ordem.status, "APROVADA")
        self.assertEqual(ordem.aprovado_por, engenheiro)

    def test_coordenador_retorna_ordem_compra_para_ajuste_com_parecer(self):
        coordenador = self._criar_usuario_operacional("coord_oc", "COORDENADOR_OBRAS")
        ordem = OrdemCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=SolicitacaoCompra.objects.create(
                empresa=self.empresa,
                obra=self.obra,
                plano_contas=self.analitico,
                titulo="Solicitacao OC Coord",
                descricao="Solicitacao para ajuste",
                solicitante=self.user,
                data_solicitacao="2026-03-10",
                status="COTANDO",
            ),
            cotacao_aprovada=Cotacao.objects.create(
                empresa=self.empresa,
                obra=self.obra,
                solicitacao=SolicitacaoCompra.objects.filter(titulo="Solicitacao OC Coord").first(),
                fornecedor=Fornecedor.objects.create(
                    empresa=self.empresa,
                    razao_social="Fornecedor Coord LTDA",
                    nome_fantasia="Fornecedor Coord",
                    cnpj="66.666.666/0001-66",
                ),
                status="APROVADA",
                data_cotacao="2026-03-12",
                criado_por=self.user,
            ),
            fornecedor=Fornecedor.objects.get(cnpj="66.666.666/0001-66"),
            descricao="Ordem para ajuste",
            emitido_por=self.user,
            valor_total=Decimal("90000.00"),
            data_emissao="2026-03-12",
            status="EM_APROVACAO",
        )
        self.client.force_login(coordenador)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("ordem_compra_detail", args=[ordem.pk]),
            {"acao": "retornar_para_ajuste", "parecer_aprovacao": "Ajustar condicoes comerciais."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        ordem.refresh_from_db()
        self.assertEqual(ordem.status, "RASCUNHO")
        self.assertEqual(ordem.parecer_aprovacao, "Ajustar condicoes comerciais.")

    def test_fluxo_web_cria_solicitacao_com_varios_centros_de_custo(self):
        centro_2 = self.analitico_2

        response = self.client.post(
            reverse("solicitacao_compra_create"),
            {
                "titulo": "Solicitacao multi item",
                "descricao": "Compra com varios centros",
                "status": "RASCUNHO",
                "data_solicitacao": "2026-03-21",
                "observacoes": "Observacoes gerais",
                "itens-TOTAL_FORMS": "2",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "0",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-plano_contas": str(self.analitico.pk),
                "itens-0-descricao_tecnica": "Tubo galvanizado 3 polegadas",
                "itens-0-unidade": "m",
                "itens-0-quantidade": "12.00",
                "itens-1-plano_contas": str(centro_2.pk),
                "itens-1-descricao_tecnica": "Curva galvanizada 90 graus",
                "itens-1-unidade": "un",
                "itens-1-quantidade": "8.00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        solicitacao = SolicitacaoCompra.objects.get(titulo="Solicitação multi item")
        self.assertEqual(solicitacao.itens.count(), 2)
        self.assertEqual(solicitacao.obra, self.obra)
        self.assertEqual(solicitacao.plano_contas, self.analitico)
        self.assertContains(response, "Tubo galvanizado 3 polegadas")
        self.assertContains(response, "Curva galvanizada 90 graus")

    def test_superuser_cria_solicitacao_pelo_contexto_da_obra(self):
        superuser = get_user_model().objects.create_superuser(
            username="root_global",
            email="root@construtask.com",
            password="senhaforte123",
        )
        self.client.force_login(superuser)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        response = self.client.post(
            reverse("solicitacao_compra_create"),
            {
                "titulo": "Solicitacao superuser",
                "descricao": "Criada pelo contexto da obra",
                "status": "RASCUNHO",
                "data_solicitacao": "2026-03-21",
                "observacoes": "",
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "0",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-plano_contas": str(self.analitico.pk),
                "itens-0-descricao_tecnica": "Item global por obra",
                "itens-0-unidade": "m3",
                "itens-0-quantidade": "3.00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        solicitacao = SolicitacaoCompra.objects.get(titulo="Solicitação superuser")
        self.assertEqual(solicitacao.obra, self.obra)
        self.assertEqual(solicitacao.empresa, self.empresa)

    def test_fluxo_web_cotacao_comparativa_exige_justificativa_e_salva_multiplos_fornecedores(self):
        solicitacao = SolicitacaoCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            titulo="Solicitacao com anexos",
            descricao="Compra com propostas",
            solicitante=self.user,
            data_solicitacao="2026-03-22",
            status="COTANDO",
        )
        item = SolicitacaoCompraItem.objects.create(
            solicitacao=solicitacao,
            plano_contas=self.analitico,
            descricao_tecnica="Concreto usinado fck 30",
            unidade="m3",
            quantidade=Decimal("5.00"),
        )
        fornecedor_2 = Fornecedor.objects.create(
            empresa=self.empresa,
            razao_social="Fornecedor Comparativo de Concreto LTDA",
            nome_fantasia="Fornecedor Comparativo",
            cnpj="77.777.777/0001-77",
            telefone="1177777777",
        )
        arquivo = SimpleUploadedFile("proposta-fornecedor.pdf", b"arquivo-teste", content_type="application/pdf")

        response_invalido = self.client.post(
            reverse("cotacao_create"),
            {
                "solicitacao": str(solicitacao.pk),
                "data_cotacao": "2026-03-23",
                "validade_ate": "2026-03-30",
                "observacoes": "Teste",
                "justificativa_escolha": "",
                "fornecedores-TOTAL_FORMS": "4",
                "fornecedores-INITIAL_FORMS": "0",
                "fornecedores-MIN_NUM_FORMS": "0",
                "fornecedores-MAX_NUM_FORMS": "1000",
                "fornecedores-0-fornecedor": str(self.fornecedor.pk),
                "fornecedores-0-escolhido": "on",
                "fornecedores-0-item_%s_valor_unitario" % item.pk: "120.00",
                "fornecedores-0-item_%s_prazo_entrega_dias" % item.pk: "7",
                "fornecedores-1-fornecedor": str(fornecedor_2.pk),
                "fornecedores-1-item_%s_valor_unitario" % item.pk: "118.00",
                "fornecedores-1-item_%s_prazo_entrega_dias" % item.pk: "5",
                "fornecedores-2-fornecedor": "",
                "fornecedores-3-fornecedor": "",
            },
            follow=True,
        )
        self.assertEqual(response_invalido.status_code, 200)
        self.assertContains(response_invalido, "justificativa")

        response = self.client.post(
            reverse("cotacao_create"),
            {
                "solicitacao": str(solicitacao.pk),
                "data_cotacao": "2026-03-23",
                "validade_ate": "2026-03-30",
                "observacoes": "Teste",
                "justificativa_escolha": "Fornecedor escolhido por menor preco e melhor prazo.",
                "fornecedores-TOTAL_FORMS": "4",
                "fornecedores-INITIAL_FORMS": "0",
                "fornecedores-MIN_NUM_FORMS": "0",
                "fornecedores-MAX_NUM_FORMS": "1000",
                "fornecedores-0-fornecedor": str(self.fornecedor.pk),
                "fornecedores-0-escolhido": "on",
                "fornecedores-0-anexo_descricao": "Proposta comercial vencedora",
                "fornecedores-0-anexo_arquivo": arquivo,
                "fornecedores-0-item_%s_valor_unitario" % item.pk: "120.00",
                "fornecedores-0-item_%s_prazo_entrega_dias" % item.pk: "7",
                "fornecedores-1-fornecedor": str(fornecedor_2.pk),
                "fornecedores-1-anexo_descricao": "Proposta comparativa",
                "fornecedores-1-item_%s_valor_unitario" % item.pk: "118.00",
                "fornecedores-1-item_%s_prazo_entrega_dias" % item.pk: "5",
                "fornecedores-2-fornecedor": "",
                "fornecedores-3-fornecedor": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        cotacao = Cotacao.objects.get(solicitacao=solicitacao, fornecedor=self.fornecedor)
        self.assertEqual(cotacao.itens.count(), 1)
        self.assertEqual(cotacao.anexos.count(), 1)
        self.assertEqual(Cotacao.objects.filter(solicitacao=solicitacao).count(), 2)
        self.assertContains(response, "Concreto usinado fck 30")
        self.assertContains(response, "Proposta comercial vencedora")

    def test_rotas_pdf_de_aquisicoes_retorna_pdf(self):
        solicitacao = SolicitacaoCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            titulo="Solicitacao PDF",
            descricao="Teste de PDF",
            solicitante=self.user,
            data_solicitacao="2026-03-18",
            status="COTANDO",
        )
        item = SolicitacaoCompraItem.objects.create(
            solicitacao=solicitacao,
            plano_contas=self.analitico,
            unidade="m3",
            quantidade=Decimal("8.00"),
            valor_estimado_unitario=Decimal("12.00"),
        )
        cotacao = Cotacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=solicitacao,
            fornecedor=self.fornecedor,
            status="APROVADA",
            data_cotacao="2026-03-19",
            criado_por=self.user,
        )
        CotacaoItem.objects.create(
            cotacao=cotacao,
            item_solicitacao=item,
            valor_unitario=Decimal("11.50"),
        )

        response_solicitacao = self.client.get(reverse("solicitacao_compra_pdf", args=[solicitacao.pk]))
        response_cotacao = self.client.get(reverse("cotacao_pdf", args=[cotacao.pk]))

        self.assertEqual(response_solicitacao.status_code, 200)
        self.assertEqual(response_solicitacao["Content-Type"], "application/pdf")
        self.assertEqual(response_cotacao.status_code, 200)
        self.assertEqual(response_cotacao["Content-Type"], "application/pdf")
        self.assertIn(b"/Subtype /Image", response_solicitacao.content)
        self.assertIn(b"/Subtype /Image", response_cotacao.content)


class AditivosContratoTests(BaseFinanceTestCase):
    def criar_contrato_com_item(self, *, valor_base=Decimal("1000.00"), data_prevista_inicio=None, data_prevista_fim=None):
        contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            centro_custo=self.analitico,
            descricao="Contrato para testes de aditivos",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=valor_base,
            data_assinatura="2026-03-01",
            data_prevista_inicio=data_prevista_inicio,
            data_prevista_fim=data_prevista_fim,
        )
        quantidade = Decimal("10.00")
        valor_unitario = (valor_base / quantidade).quantize(Decimal("0.01"))
        CompromissoItem.objects.create(
            compromisso=contrato,
            centro_custo=self.analitico,
            unidade="m3",
            quantidade=quantidade,
            valor_unitario=valor_unitario,
        )
        contrato.refresh_from_db()
        return contrato
    def test_aditivo_valor_recalcula_valor_contratado(self):
        contrato = self.criar_contrato_com_item(valor_base=Decimal("1000.00"))
        self.assertEqual(contrato.valor_contratado, Decimal("1000.00"))

        aditivo = AditivoContrato.objects.create(
            contrato=contrato,
            tipo="VALOR",
            descricao="Aditivo de valor",
        )
        AditivoContratoItem.objects.create(
            aditivo=aditivo,
            centro_custo=self.analitico,
            valor=Decimal("300.00"),
        )
        contrato.refresh_from_db()
        self.assertEqual(contrato.valor_contratado, Decimal("1300.00"))

    def test_aditivo_valor_excede_75_porcento_bloqueia_formset(self):
        contrato = self.criar_contrato_com_item(valor_base=Decimal("1000.00"))

        aditivo_instance = AditivoContrato(contrato=contrato, tipo="VALOR")
        limite = Decimal("1000.00") * Decimal("0.75")
        proposto = (limite + Decimal("1.00")).quantize(Decimal("0.01"))

        formset = AditivoContratoItemFormSet(
            data={
                "itens-TOTAL_FORMS": "1",
                "itens-INITIAL_FORMS": "0",
                "itens-MIN_NUM_FORMS": "0",
                "itens-MAX_NUM_FORMS": "1000",
                "itens-0-centro_custo": str(self.analitico.pk),
                "itens-0-valor": f"{proposto:.2f}",
            },
            instance=aditivo_instance,
            prefix="itens",
            centros_queryset=PlanoContas.objects.filter(pk=self.analitico.pk),
        )

        self.assertFalse(formset.is_valid())
        self.assertFalse(
            formset.forms[0].errors,
            f"Erros de campo inesperados: {formset.forms[0].errors}",
        )
        self.assertIn("75%", str(formset.non_form_errors()))

    def test_cronograma_orcado_distribui_uniforme_entre_datas_da_obra(self):
        obra = Obra.objects.create(
            codigo="10",
            nome="Obra 10",
            cliente="Cliente X",
            responsavel="Resp",
            status="EM_ANDAMENTO",
            data_inicio="2026-01-15",
            data_fim="2026-03-20",
            descricao="Obra para cronograma",
        )
        raiz = PlanoContas.objects.create(obra=obra, codigo="10.00", descricao="Raiz do cronograma")
        folha = PlanoContas.objects.create(
            obra=obra,
            parent=raiz,
            codigo="10.01",
            descricao="Folha do cronograma",
            unidade="m3",
            quantidade=Decimal("100.00"),
            valor_unitario=Decimal("10.00"),
        )

        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test.client import RequestFactory as DjangoRequestFactory

        rf = DjangoRequestFactory()
        request = rf.get(reverse("home"))
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session["obra_contexto_id"] = obra.pk
        request.session.save()

        view = HomeView()
        view.request = request
        context = view.get_context_data()
        cronograma = context.get("cronograma_orcado_meses") or []
        total_orcado = context["indicadores"]["valor_orcado"]
        self.assertEqual(len(cronograma), 3)

        total_mensal = sum((m["valor_mes"] for m in cronograma), start=Decimal("0.00"))
        self.assertEqual(total_mensal, total_orcado)
        self.assertEqual(cronograma[-1]["acumulado"], total_orcado)

    def test_resumo_obra_sem_curva_s_nao_chama_cronograma_service(self):
        cache.clear()
        with patch("Construtask.services_indicadores.CronogramaService.gerar_curva_s_planejada") as planejada:
            with patch("Construtask.services_indicadores.CronogramaService.gerar_curva_s_realizada") as realizada:
                resumo = IndicadoresService.resumo_obra(self.obra, include_curva_s=False)

        self.assertIn("score_operacional", resumo)
        self.assertNotIn("curva_s", resumo)
        planejada.assert_not_called()
        realizada.assert_not_called()

    def test_cronograma_detail_reaproveita_cache_de_curvas(self):
        user = getattr(self, "user", None)
        if user is None:
            user = get_user_model().objects.create_user(username="cache_cronograma", password="senhaforte123")
            UsuarioEmpresa.objects.create(usuario=user, empresa=self.empresa, is_admin_empresa=True)
            self.client.force_login(user)
            session = self.client.session
            session["obra_contexto_id"] = self.obra.pk
            session.save()
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma cacheado",
            responsavel_importacao=user,
            status="ATIVO",
        )
        PlanoFisicoItem.objects.create(
            plano=plano,
            codigo_atividade="ATV-CACHE",
            atividade="Atividade Cache",
            data_inicio_prevista=date(2026, 4, 1),
            data_fim_prevista=date(2026, 4, 30),
            valor_planejado=Decimal("1000.00"),
            sort_order=1,
        )
        cache.clear()

        with patch("Construtask.views_planejamento.CronogramaService.gerar_curva_s_planejada", return_value=[]) as planejada:
            with patch("Construtask.views_planejamento.CronogramaService.gerar_curva_s_realizada", return_value=[]) as realizada:
                response_1 = self.client.get(reverse("plano_fisico_detail", args=[plano.pk]))
                response_2 = self.client.get(reverse("plano_fisico_detail", args=[plano.pk]))

        self.assertEqual(response_1.status_code, 200)
        self.assertEqual(response_2.status_code, 200)
        self.assertEqual(planejada.call_count, 1)
        self.assertEqual(realizada.call_count, 1)

    def test_home_limita_sincronizacao_de_alertas_por_janela_curta(self):
        user = getattr(self, "user", None)
        if user is None:
            user = get_user_model().objects.create_user(username="cache_home", password="senhaforte123")
            UsuarioEmpresa.objects.create(usuario=user, empresa=self.empresa, is_admin_empresa=True)
            self.client.force_login(user)
            session = self.client.session
            session["obra_contexto_id"] = self.obra.pk
            session.save()
        cache.clear()
        with patch("Construtask.views.sincronizar_alertas_operacionais_obra") as sincronizar:
            response_1 = self.client.get(reverse("home"))
            response_2 = self.client.get(reverse("home"))

        self.assertEqual(response_1.status_code, 200)
        self.assertEqual(response_2.status_code, 200)
        self.assertEqual(sincronizar.call_count, 1)


class LgpdRotinasTests(BaseFinanceTestCase):
    def test_anonimiza_fornecedor_inativo(self):
        fornecedor = Fornecedor.objects.create(
            empresa=self.empresa,
            razao_social="Fornecedor Inativo LTDA",
            nome_fantasia="Fornecedor Inativo",
            cnpj="88.888.888/0001-88",
            contato="Carlos",
            telefone="1144444444",
            email="carlos@fornecedor.com",
            ativo=False,
        )

        alterado = anonimizar_fornecedor_inativo(fornecedor)
        fornecedor.refresh_from_db()

        self.assertTrue(alterado)
        self.assertEqual(fornecedor.nome_fantasia, "")
        self.assertEqual(fornecedor.contato, "Contato anonimizado")
        self.assertEqual(fornecedor.telefone, "")
        self.assertEqual(fornecedor.email, "")

    def test_anonimiza_usuario_inativo(self):
        user_model = get_user_model()
        usuario = user_model.objects.create_user(
            username="inativo_lgpd",
            password="senha12345",
            email="inativo@empresa.com",
            first_name="Usuario",
            last_name="Inativo",
            is_active=False,
        )
        UsuarioEmpresa.objects.create(usuario=usuario, empresa=self.empresa)

        alterado = anonimizar_usuario_inativo(usuario)
        usuario.refresh_from_db()

        self.assertTrue(alterado)
        self.assertEqual(usuario.email, f"anonimizado+{usuario.pk}@construtask.local")
        self.assertEqual(usuario.first_name, "")
        self.assertEqual(usuario.last_name, "")

    def test_comando_lgpd_em_modo_simulacao(self):
        out = StringIO()
        call_command("executar_rotinas_lgpd", stdout=out)
        self.assertIn("Modo simulacao", out.getvalue())


class FasesFechamentoGovernancaTests(BaseFinanceTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="gestor_fechamento",
            password="senha12345",
            email="gestor@empresa.com",
        )
        self.usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=self.user,
            empresa=self.empresa,
            is_admin_empresa=True,
            papel_aprovacao="GERENTE_OBRAS",
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

    def _criar_usuario_operacional(self, username, papel_aprovacao):
        user = get_user_model().objects.create_user(username=username, password="senha12345")
        usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=user,
            empresa=self.empresa,
            papel_aprovacao=papel_aprovacao,
        )
        usuario_empresa.obras_permitidas.add(self.obra)
        return user, usuario_empresa

    def test_workflow_solicitacao_compra_controla_aprovacao(self):
        solicitacao = SolicitacaoCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            titulo="Solicitação controlada",
            descricao="Compra sob aprovação",
            solicitante=self.user,
            data_solicitacao="2026-04-01",
        )
        SolicitacaoCompraItem.objects.create(
            solicitacao=solicitacao,
            plano_contas=self.analitico,
            descricao_tecnica="Concreto usinado",
            quantidade=Decimal("3.00"),
            valor_estimado_unitario=Decimal("10.00"),
        )

        response_envio = self.client.post(
            reverse("solicitacao_compra_detail", args=[solicitacao.pk]),
            {"acao": "enviar_para_aprovacao", "parecer_aprovacao": "Enviar para suprimentos."},
            follow=True,
        )
        self.assertEqual(response_envio.status_code, 200)
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, "EM_APROVACAO")
        self.assertEqual(solicitacao.enviado_para_aprovacao_por, self.user)

        response_aprovacao = self.client.post(
            reverse("solicitacao_compra_detail", args=[solicitacao.pk]),
            {"acao": "aprovar", "parecer_aprovacao": "Aprovada para cotação."},
            follow=True,
        )
        self.assertEqual(response_aprovacao.status_code, 200)
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, "APROVADA")
        self.assertEqual(solicitacao.aprovado_por, self.user)
        self.assertContains(response_aprovacao, "Linha do Tempo")

    def test_workflow_cotacao_exige_aprovacao_antes_de_emitir_ordem(self):
        solicitacao = SolicitacaoCompra.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.analitico,
            titulo="Solicitação cotação",
            descricao="Compra comparativa",
            solicitante=self.user,
            data_solicitacao="2026-04-02",
            status="COTANDO",
        )
        item = SolicitacaoCompraItem.objects.create(
            solicitacao=solicitacao,
            plano_contas=self.analitico,
            descricao_tecnica="Aço CA50",
            unidade="kg",
            quantidade=Decimal("8.00"),
            valor_estimado_unitario=Decimal("20.00"),
        )
        fornecedor_2 = Fornecedor.objects.create(
            empresa=self.empresa,
            razao_social="Fornecedor B",
            nome_fantasia="Fornecedor B",
            cnpj="22.222.222/0001-22",
        )
        Cotacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=solicitacao,
            fornecedor=fornecedor_2,
            status="REJEITADA",
            data_cotacao="2026-04-03",
            criado_por=self.user,
        )
        cotacao = Cotacao.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            solicitacao=solicitacao,
            fornecedor=Fornecedor.objects.create(
                empresa=self.empresa,
                razao_social="Fornecedor A",
                nome_fantasia="Fornecedor A",
                cnpj="21.212.212/0001-21",
            ),
            status="EM_ANALISE",
            data_cotacao="2026-04-03",
            criado_por=self.user,
            justificativa_escolha="Melhor prazo e preço.",
        )
        CotacaoItem.objects.create(
            cotacao=cotacao,
            item_solicitacao=item,
            valor_unitario=Decimal("18.00"),
        )

        self.client.post(
            reverse("cotacao_detail", args=[cotacao.pk]),
            {"acao": "enviar_para_aprovacao", "parecer_aprovacao": "Submetida para aprovação."},
            follow=True,
        )
        cotacao.refresh_from_db()
        self.assertEqual(cotacao.status, "EM_APROVACAO")

        self.client.post(
            reverse("cotacao_detail", args=[cotacao.pk]),
            {"acao": "aprovar", "parecer_aprovacao": "Cotação aprovada."},
            follow=True,
        )
        cotacao.refresh_from_db()
        self.assertEqual(cotacao.status, "APROVADA")

        response_ordem = self.client.post(
            reverse("cotacao_detail", args=[cotacao.pk]),
            {"acao": "emitir_oc", "descricao": "Pedido aprovado", "tipo_resultado": "PEDIDO_COMPRA"},
            follow=True,
        )
        self.assertEqual(response_ordem.status_code, 200)
        self.assertTrue(OrdemCompra.objects.filter(cotacao_aprovada=cotacao).exists())

    def test_admin_empresa_atualiza_permissoes_por_modulo(self):
        _, usuario_empresa = self._criar_usuario_operacional("tecnico_permissoes", "TECNICO_OBRAS")
        response = self.client.post(
            reverse("empresa_admin"),
            {
                "acao": "atualizar_permissoes_usuario",
                "usuario_empresa_id": usuario_empresa.pk,
                "perm_compras_view": "on",
                "perm_compras_create": "on",
                "perm_compras_approve": "on",
                "perm_lgpd_view": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            PermissaoModuloAcao.objects.filter(
                usuario_empresa=usuario_empresa,
                modulo="compras",
                acao="approve",
                permitido=True,
            ).exists()
        )


class LgpdGovernancaAvancadaTests(BaseFinanceTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="admin_lgpd",
            password="senha12345",
            email="adminlgpd@empresa.com",
        )
        UsuarioEmpresa.objects.create(
            usuario=self.user,
            empresa=self.empresa,
            is_admin_empresa=True,
            papel_aprovacao="GERENTE_OBRAS",
        )
        self.client.force_login(self.user)
        self.fornecedor = Fornecedor.objects.create(
            empresa=self.empresa,
            razao_social="Fornecedor Privado",
            nome_fantasia="Fornecedor Privado",
            cnpj="33.333.333/0001-33",
            contato="Marcos",
            email="marcos@fornecedor.com",
            telefone="11999998888",
        )

    def test_busca_por_titular_e_formulario_de_consentimento_na_governanca(self):
        response = self.client.get(reverse("lgpd_governanca"), {"titular": "marcos"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Busca por Titular")
        self.assertContains(response, "Fornecedor Privado")
        self.assertContains(response, "Consentimentos")

    def test_fluxos_lgpd_registram_tratamentos_e_consentimentos(self):
        response_consentimento = self.client.post(
            reverse("lgpd_governanca"),
            {
                "acao": "registrar_consentimento",
                "categoria_titular": "FORNECEDOR",
                "email_referencia": "marcos@fornecedor.com",
                "finalidade": "Contato comercial autorizado",
                "texto_aceito": "Aceite para contato comercial e envio de documentos.",
            },
            follow=True,
        )
        self.assertEqual(response_consentimento.status_code, 200)
        consentimento = ConsentimentoLGPD.objects.get(email_referencia="marcos@fornecedor.com")
        self.assertTrue(consentimento.ativo)
        self.assertTrue(
            RegistroTratamentoDadoPessoal.objects.filter(
                entidade="ConsentimentoLGPD",
                acao="CONSENTIMENTO",
            ).exists()
        )

        excluir_logicamente_fornecedor(self.fornecedor, usuario=self.user, justificativa="Pedido do titular")
        self.fornecedor.refresh_from_db()
        self.assertFalse(self.fornecedor.ativo)
        self.assertIsNotNone(self.fornecedor.exclusao_logica_em)

        anonimizar_fornecedor_inativo(self.fornecedor)
        self.fornecedor.refresh_from_db()
        self.assertIsNotNone(self.fornecedor.anonimizado_em)

        descartar_fornecedor_anonimizado(self.fornecedor, usuario=self.user, justificativa="Fim da retenção")
        self.fornecedor.refresh_from_db()
        self.assertIsNotNone(self.fornecedor.descartado_em)
        self.assertTrue(
            RegistroTratamentoDadoPessoal.objects.filter(
                entidade="Fornecedor",
                acao="DESCARTE",
                objeto_id=self.fornecedor.pk,
            ).exists()
        )

class TextNormalizationTests(TestCase):
    def test_corrigir_mojibake_recupera_coluna_corrompida(self):
        original = "MEDIÃ‡ÃƒO"
        corrigido = corrigir_mojibake(original)
        self.assertIsInstance(corrigido, str)
        self.assertLessEqual(corrigido.count("Ã"), original.count("Ã"))
        self.assertTrue(corrigido)

    def test_normalizar_texto_cadastral_corrige_termos_comuns(self):
        texto = "medicao de blocos ceramicos para revisao do orcamento"
        self.assertEqual(
            normalizar_texto_cadastral(texto),
            "medição de blocos cerâmicos para revisão do orçamento",
        )

    def test_comando_normaliza_textos_cadastrais_aplica_em_registro_existente(self):
        empresa = Empresa.objects.create(
            nome="Empresa Teste",
            nome_fantasia="Empresa Teste",
            cnpj="11.111.111/1111-11",
        )
        obra = Obra.objects.create(
            empresa=empresa,
            codigo="OBR-NT",
            nome="obra de ampliacao",
            cliente="cliente nao informado",
            responsavel="tecnico de obras",
            status="EM_ANDAMENTO",
            data_inicio="2026-01-01",
            data_fim="2026-12-31",
            descricao="obra com medicao e orcamento",
        )
        baseline = OrcamentoBaseline.objects.create(
            obra=obra,
            descricao="baseline de orcamento revisao 01",
            status="RASCUNHO",
        )

        out = StringIO()
        call_command("normalizar_textos_cadastrais", "--apply", stdout=out)
        baseline.refresh_from_db()
        obra.refresh_from_db()

        self.assertEqual(baseline.descricao, "baseline de orçamento revisão 01")
        self.assertEqual(obra.nome, "obra de ampliação")
        self.assertIn("Construtask.OrcamentoBaseline", out.getvalue())


class NumeracaoDocumentalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="numeracao", password="senha12345")
        self.empresa = Empresa.objects.create(
            nome="Empresa Numeracao",
            nome_fantasia="Empresa Numeracao",
            cnpj="55.555.555/0001-55",
        )
        self.obra = Obra.objects.create(
            empresa=self.empresa,
            codigo="OBR-NUM",
            nome="Obra Numeracao",
            status="EM_ANDAMENTO",
        )
        self.plano = PlanoContas.objects.create(
            obra=self.obra,
            codigo="01.01.01.01.01",
            descricao="Servico Numerado",
            unidade="un",
            quantidade=Decimal("1"),
            valor_unitario=Decimal("1"),
        )

    def test_nao_conformidade_recebe_numero_anual(self):
        nc = NaoConformidade.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.plano,
            descricao="Falha documental",
            responsavel=self.user,
            criado_por=self.user,
        )
        self.assertEqual(nc.numero, f"NC-{date.today().year}-0001")

    def test_risco_recebe_codigo_anual(self):
        risco = Risco.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            plano_contas=self.plano,
            categoria="PRAZO",
            titulo="Atraso de fornecedor",
            descricao="Possibilidade de atraso",
            probabilidade=3,
            impacto=4,
            responsavel=self.user,
            criado_por=self.user,
        )
        self.assertEqual(risco.codigo, f"RIS-{date.today().year}-0001")

    def test_plano_fisico_recebe_numero_anual(self):
        plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma Mestre",
            responsavel_importacao=self.user,
        )
        self.assertEqual(plano.numero, f"CRN-{date.today().year}-0001")


class ComunicacoesModuleTests(BaseFinanceTestCase):
    def setUp(self):
        super().setUp()
        self.data_reuniao_base = date(2026, 4, 20)
        self.user = get_user_model().objects.create_user(username="gestor_comunicacao", password="senha12345")
        UsuarioEmpresa.objects.create(
            usuario=self.user,
            empresa=self.empresa,
            is_admin_empresa=True,
            papel_aprovacao="GERENTE_OBRAS",
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()

        AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="ALERTA-COM-001",
            titulo="Alerta de comunicacao",
            descricao="Desvio relevante da obra",
            referencia="ALERTA-001",
            severidade="ALTA",
            status="ABERTO",
            data_referencia=self.data_reuniao_base + timedelta(days=1),
        )
        Risco.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            categoria="PRAZO",
            titulo="Risco de atraso",
            descricao="Risco operacional importante",
            probabilidade=4,
            impacto=4,
            responsavel=self.user,
            criado_por=self.user,
            status="EM_TRATAMENTO",
            data_meta_tratamento=self.data_reuniao_base + timedelta(days=2),
        )
        NaoConformidade.objects.create(
            empresa=self.empresa,
            obra=self.obra,
            descricao="Nao conformidade em campo",
            responsavel=self.user,
            criado_por=self.user,
            status="EM_TRATAMENTO",
            data_abertura=self.data_reuniao_base + timedelta(days=3),
        )
        self.contrato = Compromisso.objects.create(
            tipo="CONTRATO",
            obra=self.obra,
            centro_custo=self.analitico,
            descricao="Contrato para pauta",
            fornecedor="Fornecedor A",
            cnpj="12.345.678/0001-90",
            responsavel="Maria",
            telefone="11999999999",
            valor_contratado=Decimal("1000.00"),
            data_assinatura="2026-03-01",
            data_prevista_inicio=self.data_reuniao_base + timedelta(days=4),
            data_prevista_fim=self.data_reuniao_base + timedelta(days=6),
            status="EM_EXECUCAO",
        )
        Medicao.objects.create(
            contrato=self.contrato,
            obra=self.obra,
            descricao="Medicao pendente",
            valor_medido=Decimal("100.00"),
            data_medicao=self.data_reuniao_base + timedelta(days=5),
            data_prevista_inicio=self.data_reuniao_base + timedelta(days=5),
            data_prevista_fim=self.data_reuniao_base + timedelta(days=6),
            status="EM_APROVACAO",
        )
        self.plano = PlanoFisico.objects.create(
            obra=self.obra,
            titulo="Cronograma para pauta",
            responsavel_importacao=self.user,
            status="ATIVO",
        )
        PlanoFisicoItem.objects.create(
            plano=self.plano,
            codigo_atividade="ATV-001",
            atividade="Atividade atrasada",
            duracao=10,
            data_inicio_prevista=self.data_reuniao_base + timedelta(days=1),
            data_fim_prevista=self.data_reuniao_base + timedelta(days=6),
            percentual_concluido=30,
            dias_desvio=5,
            sort_order=1,
        )

    def _montar_payload_pauta(self, reuniao, *, titulo_manual=""):
        payload = {
            "acao": "validar_pauta",
            "itens-TOTAL_FORMS": str(reuniao.itens_pauta.count()),
            "itens-INITIAL_FORMS": str(reuniao.itens_pauta.count()),
            "itens-MIN_NUM_FORMS": "0",
            "itens-MAX_NUM_FORMS": "1000",
            "manual-categoria": "OUTRO",
            "manual-titulo": titulo_manual,
            "manual-descricao": "Item manual adicionado" if titulo_manual else "",
            "manual-resposta_o_que": "Acompanhar item manual" if titulo_manual else "",
            "manual-resposta_quem": "Engenharia" if titulo_manual else "",
            "manual-resposta_quando": "2026-04-30" if titulo_manual else "",
        }
        for indice, item in enumerate(reuniao.itens_pauta.order_by("ordem", "id")):
            payload[f"itens-{indice}-id"] = str(item.pk)
            payload[f"itens-{indice}-ativo"] = "on"
            payload[f"itens-{indice}-ordem"] = str(item.ordem)
            payload[f"itens-{indice}-titulo"] = item.titulo
            payload[f"itens-{indice}-descricao"] = item.descricao
            payload[f"itens-{indice}-resposta_o_que"] = "Executar tratativa"
            payload[f"itens-{indice}-resposta_quem"] = "Equipe da obra"
            payload[f"itens-{indice}-resposta_quando"] = "2026-04-25"
        return payload

    def test_empresa_admin_salva_parametros_comunicacao(self):
        response = self.client.post(
            reverse("empresa_admin"),
            {
                "acao": "salvar_parametros_comunicacao",
                "empresa_id": str(self.empresa.pk),
                "frequencia_curto_prazo_dias": "7",
                "frequencia_medio_prazo_dias": "35",
                "frequencia_longo_prazo_dias": "100",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        parametros = ParametroComunicacaoEmpresa.objects.get(empresa=self.empresa)
        self.assertEqual(parametros.frequencia_medio_prazo_dias, 35)
        self.assertContains(response, "Frequencias das reunioes da empresa atualizadas com sucesso.")

    def test_empresa_admin_exibe_textos_atualizados_de_frequencia_comunicacao(self):
        response = self.client.get(reverse("empresa_admin"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Frequencia padrao das reunioes de curto prazo da empresa.")
        self.assertContains(response, "Frequencia padrao das reunioes de médio prazo da empresa.")
        self.assertContains(response, "Frequencia padrao das reunioes de longo prazo da empresa.")

    def test_criacao_de_reuniao_gera_pauta_automatica(self):
        response = self.client.post(
            reverse("reuniao_comunicacao_create"),
            {
                "tipo_reuniao": "CURTO_PRAZO",
                "titulo": "Reuniao operacional da semana",
                "data_prevista": "2026-04-20",
                "data_realizada": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        reuniao = ReuniaoComunicacao.objects.latest("id")
        categorias = set(reuniao.itens_pauta.values_list("categoria", flat=True))
        self.assertIn("ALERTA", categorias)
        self.assertIn("RISCO", categorias)
        self.assertIn("NAO_CONFORMIDADE", categorias)
        self.assertIn("CRONOGRAMA", categorias)
        self.assertIn("CONTRATO", categorias)
        self.assertIn("MEDICAO", categorias)
        self.assertEqual(reuniao.status, "RASCUNHO")

    def test_validacao_da_pauta_permite_item_manual(self):
        self.client.post(
            reverse("reuniao_comunicacao_create"),
            {
                "tipo_reuniao": "CURTO_PRAZO",
                "titulo": "Reuniao curta",
                "data_prevista": "2026-04-20",
                "data_realizada": "",
            },
        )
        reuniao = ReuniaoComunicacao.objects.latest("id")
        payload = self._montar_payload_pauta(reuniao, titulo_manual="Pendencia do cliente")
        response = self.client.post(reverse("reuniao_comunicacao_detail", args=[reuniao.pk]), payload, follow=True)

        self.assertEqual(response.status_code, 200)
        reuniao.refresh_from_db()
        self.assertIsNotNone(reuniao.pauta_validada_em)
        self.assertEqual(reuniao.pauta_validada_por, self.user)
        self.assertEqual(reuniao.status, "PAUTA_VALIDADA")
        self.assertTrue(reuniao.itens_pauta.filter(origem_tipo="MANUAL", titulo="Pendencia do cliente").exists())
        self.assertTrue(HistoricoReuniaoComunicacao.objects.filter(reuniao=reuniao, acao="PAUTA_VALIDADA").exists())

    def test_validacao_remove_itens_desmarcados_da_exibicao(self):
        self.client.post(
            reverse("reuniao_comunicacao_create"),
            {
                "tipo_reuniao": "CURTO_PRAZO",
                "titulo": "Reuniao curta",
                "data_prevista": "2026-04-20",
                "data_realizada": "",
            },
        )
        reuniao = ReuniaoComunicacao.objects.latest("id")
        item_removido = reuniao.itens_pauta.order_by("ordem", "id").first()
        payload = self._montar_payload_pauta(reuniao)
        indice_item = list(reuniao.itens_pauta.order_by("ordem", "id")).index(item_removido)
        payload.pop(f"itens-{indice_item}-ativo")

        response = self.client.post(reverse("reuniao_comunicacao_detail", args=[reuniao.pk]), payload, follow=True)

        self.assertEqual(response.status_code, 200)
        item_removido.refresh_from_db()
        reuniao.refresh_from_db()
        self.assertFalse(item_removido.ativo)
        self.assertEqual(reuniao.status, "PAUTA_VALIDADA")
        self.assertNotContains(response, item_removido.titulo)

    def test_pauta_automatica_respeita_janela_do_tipo_reuniao(self):
        AlertaOperacional.objects.create(
            obra=self.obra,
            codigo_regra="ALERTA-COM-002",
            titulo="Alerta fora da janela",
            descricao="Nao deve entrar na pauta curta",
            referencia="ALERTA-002",
            severidade="MEDIA",
            status="ABERTO",
            data_referencia=self.data_reuniao_base + timedelta(days=15),
        )
        PlanoFisicoItem.objects.create(
            plano=self.plano,
            codigo_atividade="ATV-999",
            atividade="Atividade fora da janela",
            duracao=3,
            data_inicio_prevista=self.data_reuniao_base + timedelta(days=20),
            data_fim_prevista=self.data_reuniao_base + timedelta(days=22),
            percentual_concluido=0,
            dias_desvio=0,
            sort_order=2,
        )

        self.client.post(
            reverse("reuniao_comunicacao_create"),
            {
                "tipo_reuniao": "CURTO_PRAZO",
                "titulo": "Reuniao curta com janela",
                "data_prevista": "2026-04-20",
                "data_realizada": "",
            },
        )
        reuniao = ReuniaoComunicacao.objects.latest("id")

        titulos = set(reuniao.itens_pauta.values_list("titulo", flat=True))
        self.assertNotIn("[MEDIA] Alerta fora da janela", titulos)
        self.assertNotIn("ATV-999 - Atividade fora da janela", titulos)

    def test_detalhe_da_reuniao_exibe_pauta_em_secoes(self):
        self.client.post(
            reverse("reuniao_comunicacao_create"),
            {
                "tipo_reuniao": "CURTO_PRAZO",
                "titulo": "Reuniao por secao",
                "data_prevista": "2026-04-20",
                "data_realizada": "",
            },
        )
        reuniao = ReuniaoComunicacao.objects.latest("id")

        response = self.client.get(reverse("reuniao_comunicacao_detail", args=[reuniao.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cronograma")
        self.assertContains(response, "Alertas")
        self.assertContains(response, "Riscos")
        self.assertContains(response, "Nao Conformidades")

    def test_pauta_validada_bloqueia_estrutura_mas_permite_respostas(self):
        self.client.post(
            reverse("reuniao_comunicacao_create"),
            {
                "tipo_reuniao": "CURTO_PRAZO",
                "titulo": "Reuniao bloqueada",
                "data_prevista": "2026-04-20",
                "data_realizada": "",
            },
        )
        reuniao = ReuniaoComunicacao.objects.latest("id")
        self.client.post(reverse("reuniao_comunicacao_detail", args=[reuniao.pk]), self._montar_payload_pauta(reuniao))
        reuniao.refresh_from_db()

        item = reuniao.itens_pauta.filter(ativo=True).order_by("ordem", "id").first()
        payload = self._montar_payload_pauta(reuniao)
        indice_item = list(reuniao.itens_pauta.filter(ativo=True).order_by("ordem", "id")).index(item)
        payload["acao"] = "salvar_pauta"
        payload[f"itens-{indice_item}-titulo"] = "Titulo alterado indevidamente"
        payload[f"itens-{indice_item}-descricao"] = "Descricao alterada indevidamente"
        payload[f"itens-{indice_item}-resposta_o_que"] = "Novo plano de acao"
        payload["manual-titulo"] = "Novo item manual bloqueado"

        response = self.client.post(reverse("reuniao_comunicacao_detail", args=[reuniao.pk]), payload, follow=True)

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        reuniao.refresh_from_db()
        self.assertEqual(reuniao.status, "PAUTA_VALIDADA")
        self.assertNotEqual(item.titulo, "Titulo alterado indevidamente")
        self.assertNotEqual(item.descricao, "Descricao alterada indevidamente")
        self.assertEqual(item.resposta_o_que, "Novo plano de ação")
        self.assertFalse(reuniao.itens_pauta.filter(origem_tipo="MANUAL", titulo="Novo item manual bloqueado").exists())
        self.assertContains(response, "Estrutura da pauta bloqueada")

    def test_exportacoes_de_pauta_e_ata(self):
        self.client.post(
            reverse("reuniao_comunicacao_create"),
            {
                "tipo_reuniao": "MEDIO_PRAZO",
                "titulo": "Reuniao exportavel",
                "data_prevista": "2026-04-20",
                "data_realizada": "",
            },
        )
        reuniao = ReuniaoComunicacao.objects.latest("id")
        self.client.post(reverse("reuniao_comunicacao_detail", args=[reuniao.pk]), self._montar_payload_pauta(reuniao))

        response_pauta_excel = self.client.get(reverse("reuniao_comunicacao_pauta_excel", args=[reuniao.pk]))
        response_pauta_pdf = self.client.get(reverse("reuniao_comunicacao_pauta_pdf", args=[reuniao.pk]))
        response_ata_excel = self.client.get(reverse("reuniao_comunicacao_ata_excel", args=[reuniao.pk]))
        response_ata_pdf = self.client.get(reverse("reuniao_comunicacao_ata_pdf", args=[reuniao.pk]))

        self.assertEqual(response_pauta_excel.status_code, 200)
        self.assertEqual(
            response_pauta_excel["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(response_pauta_pdf.status_code, 200)
        self.assertEqual(response_pauta_pdf["Content-Type"], "application/pdf")
        self.assertEqual(response_ata_excel.status_code, 200)
        self.assertEqual(
            response_ata_excel["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(response_ata_pdf.status_code, 200)
        self.assertEqual(response_ata_pdf["Content-Type"], "application/pdf")

    def test_envio_e_aprovacao_da_ata(self):
        self.client.post(
            reverse("reuniao_comunicacao_create"),
            {
                "tipo_reuniao": "MEDIO_PRAZO",
                "titulo": "Reuniao mensal",
                "data_prevista": "2026-04-20",
                "data_realizada": "",
            },
        )
        reuniao = ReuniaoComunicacao.objects.latest("id")
        self.client.post(reverse("reuniao_comunicacao_detail", args=[reuniao.pk]), self._montar_payload_pauta(reuniao))
        response_envio = self.client.post(
            reverse("reuniao_comunicacao_detail", args=[reuniao.pk]),
            {"acao": "enviar_para_aprovacao", "parecer_aprovacao": "Pronta para validar."},
            follow=True,
        )

        self.assertEqual(response_envio.status_code, 200)
        reuniao.refresh_from_db()
        self.assertEqual(reuniao.status, "EM_APROVACAO")
        self.assertIn("Itens deliberados", reuniao.ata_texto)
        self.assertIn("Executar tratativa", reuniao.ata_texto)

        engenheiro = get_user_model().objects.create_user(username="engenheiro_com", password="senha12345")
        usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=engenheiro,
            empresa=self.empresa,
            is_admin_empresa=False,
            papel_aprovacao="ENGENHEIRO_OBRAS",
        )
        usuario_empresa.obras_permitidas.add(self.obra)
        self.client.force_login(engenheiro)
        session = self.client.session
        session["obra_contexto_id"] = self.obra.pk
        session.save()
        response_aprovacao = self.client.post(
            reverse("reuniao_comunicacao_detail", args=[reuniao.pk]),
            {"acao": "aprovar", "parecer_aprovacao": "Ata aprovada."},
            follow=True,
        )

        self.assertEqual(response_aprovacao.status_code, 200)
        reuniao.refresh_from_db()
        self.assertEqual(reuniao.status, "APROVADA")
        self.assertEqual(reuniao.aprovado_por, engenheiro)
        self.assertTrue(AuditEvent.objects.filter(objeto_id=reuniao.pk, acao="APPROVE").exists())


class HardeningProducaoTests(BaseFinanceTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.admin_empresa = get_user_model().objects.create_user(
            username="gestor_hardening",
            password="senha12345",
            email="gestor@empresa.com",
        )
        UsuarioEmpresa.objects.create(
            usuario=self.admin_empresa,
            empresa=self.empresa,
            is_admin_empresa=True,
            papel_aprovacao="GERENTE_OBRAS",
        )
        self.client.force_login(self.admin_empresa)

    def test_criacao_de_usuario_operacional_nao_concede_staff(self):
        response = self.client.post(
            reverse("empresa_admin"),
            {
                "acao": "criar_usuario",
                "username": "operacional_sem_staff",
                "email": "operacional@empresa.com",
                "password": "senha12345",
                "empresa_id": str(self.empresa.pk),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        usuario = get_user_model().objects.get(username="operacional_sem_staff")
        self.assertFalse(usuario.is_staff)
        self.assertTrue(usuario.is_active)

    @override_settings(CONSTRUTASK_ADMIN_SUPERUSER_USERNAME="Construtask")
    def test_admin_do_django_restringe_superuser_para_usuario_construtask(self):
        superuser_nao_autorizado = get_user_model().objects.create_superuser(
            username="outro_superuser",
            email="outro@empresa.com",
            password="senha12345",
        )
        request = RequestFactory().get("/admin/")
        request.user = superuser_nao_autorizado
        self.assertFalse(admin.site.has_permission(request))

        superuser_autorizado = get_user_model().objects.create_superuser(
            username="Construtask",
            email="construtask@empresa.com",
            password="senha12345",
        )
        request_autorizado = RequestFactory().get("/admin/")
        request_autorizado.user = superuser_autorizado
        self.assertTrue(admin.site.has_permission(request_autorizado))

    @override_settings(CONSTRUTASK_LOGIN_MAX_ATTEMPTS=2, CONSTRUTASK_LOGIN_LOCKOUT_MINUTES=15)
    def test_login_bloqueia_apos_tentativas_invalidas_repetidas(self):
        self.client.logout()
        usuario = get_user_model().objects.create_user(username="bloqueado", password="senha12345")

        primeira = self.client.post(reverse("login"), {"username": usuario.username, "password": "senha_errada"})
        segunda = self.client.post(reverse("login"), {"username": usuario.username, "password": "senha_errada"})
        terceira = self.client.post(reverse("login"), {"username": usuario.username, "password": "senha12345"})

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(terceira.status_code, 200)
        self.assertContains(terceira, "Muitas tentativas de login")

    @override_settings(
        CONSTRUTASK_LOGIN_MAX_ATTEMPTS=5,
        CONSTRUTASK_LOGIN_LOCKOUT_MINUTES=15,
        CONSTRUTASK_LOGIN_IP_MAX_ATTEMPTS=2,
        CONSTRUTASK_LOGIN_IP_LOCKOUT_MINUTES=15,
    )
    def test_login_bloqueia_por_ip_mesmo_com_usuarios_diferentes(self):
        self.client.logout()
        usuario_a = get_user_model().objects.create_user(username="ip_lock_a", password="senha12345")
        usuario_b = get_user_model().objects.create_user(username="ip_lock_b", password="senha12345")
        usuario_c = get_user_model().objects.create_user(username="ip_lock_c", password="senha12345")

        primeira = self.client.post(
            reverse("login"),
            {"username": usuario_a.username, "password": "senha_errada"},
            REMOTE_ADDR="10.10.10.10",
        )
        segunda = self.client.post(
            reverse("login"),
            {"username": usuario_b.username, "password": "senha_errada"},
            REMOTE_ADDR="10.10.10.10",
        )
        terceira = self.client.post(
            reverse("login"),
            {"username": usuario_c.username, "password": "senha12345"},
            REMOTE_ADDR="10.10.10.10",
        )

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(terceira.status_code, 200)
        self.assertContains(terceira, "Muitas tentativas de login")

    @override_settings(
        DEBUG=False,
        CONSTRUTASK_ENVIRONMENT="production",
        MEDIA_STORAGE_BACKEND="Construtask.storage_backends.PersistentMediaStorage",
        CONSTRUTASK_MEDIA_PERSISTENT=True,
        CONSTRUTASK_FILESYSTEM_MEDIA_ALLOWED_IN_PRODUCTION=False,
        CONSTRUTASK_BACKUP_ENABLED=True,
        CONSTRUTASK_BACKUP_PROVIDER="s3",
        CONSTRUTASK_BACKUP_RETENTION_DAYS=30,
        CONSTRUTASK_BACKUP_INTERVAL_HOURS=24,
        CSRF_TRUSTED_ORIGINS=["https://construtask.example.com"],
        ALLOWED_HOSTS=["construtask.example.com"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        SECURE_SSL_REDIRECT=True,
        CACHES={
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": "redis://cache.example.com:6379/1",
                "TIMEOUT": 300,
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    "IGNORE_EXCEPTIONS": False,
                    "LOG_IGNORED_EXCEPTIONS": False,
                },
                "KEY_PREFIX": "construtask",
            },
            "critical": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": "redis://cache.example.com:6379/2",
                "TIMEOUT": 300,
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    "IGNORE_EXCEPTIONS": False,
                    "LOG_IGNORED_EXCEPTIONS": False,
                },
                "KEY_PREFIX": "construtask-critical",
            },
        },
    )
    def test_validar_base_saas_reconhece_storage_persistente_explicito_em_producao(self):
        backup = OperacaoBackupSaaS.objects.create(
            tipo="BACKUP",
            status="SUCESSO",
            ambiente="production",
            provedor="s3",
            identificador_artefato="backup-prod.dump",
        )
        OperacaoBackupSaaS.objects.create(
            tipo="TESTE_RESTAURACAO",
            status="SUCESSO",
            ambiente="production",
            provedor="s3",
            backup_referencia=backup,
        )

        stdout = StringIO()
        with patch.dict(os.environ, {"REDIS_URL": "redis://cache.example.com:6379/0"}):
            with patch("django_redis.get_redis_connection") as get_redis_connection:
                get_redis_connection.return_value.ping.return_value = True
                call_command("validar_base_saas", "--json", stdout=stdout)
        payload = stdout.getvalue()
        self.assertIn('"backend_path": "Construtask.storage_backends.PersistentMediaStorage"', payload)
        self.assertIn('"status": "ok"', payload)

    @override_settings(
        DEBUG=False,
        CONSTRUTASK_ENVIRONMENT="production",
        MEDIA_STORAGE_BACKEND="django.core.files.storage.FileSystemStorage",
        CONSTRUTASK_MEDIA_PERSISTENT=True,
        CONSTRUTASK_FILESYSTEM_MEDIA_ALLOWED_IN_PRODUCTION=False,
        CONSTRUTASK_BACKUP_ENABLED=False,
        ALLOWED_HOSTS=["construtask.example.com"],
        CSRF_TRUSTED_ORIGINS=["https://construtask.example.com"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        SECURE_SSL_REDIRECT=True,
    )
    def test_validar_prontidao_producao_aponta_pendencias_criticas(self):
        stdout = StringIO()
        call_command("validar_prontidao_producao", "--json", stdout=stdout)
        payload = stdout.getvalue()
        self.assertIn('"status": "error"', payload)
        self.assertIn('construtask.E007', payload)
        self.assertIn('construtask.E008', payload)
        self.assertIn('construtask.E009', payload)
        self.assertIn('construtask.E012', payload)
        self.assertIn('"readiness"', payload)

    @override_settings(
        DEBUG=False,
        CONSTRUTASK_ENVIRONMENT="production",
        CONSTRUTASK_ADMIN_URL="painel-seguro/",
        MEDIA_STORAGE_BACKEND="Construtask.storage_backends.PersistentMediaStorage",
        CONSTRUTASK_MEDIA_PERSISTENT=True,
        CONSTRUTASK_FILESYSTEM_MEDIA_ALLOWED_IN_PRODUCTION=False,
        CONSTRUTASK_BACKUP_ENABLED=True,
        CONSTRUTASK_BACKUP_PROVIDER="s3",
        CONSTRUTASK_BACKUP_RETENTION_DAYS=30,
        CONSTRUTASK_BACKUP_INTERVAL_HOURS=24,
        ALLOWED_HOSTS=["construtask.example.com"],
        CSRF_TRUSTED_ORIGINS=["https://construtask.example.com"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        SECURE_SSL_REDIRECT=True,
        CACHES={
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": "redis://cache.example.com:6379/1",
                "TIMEOUT": 300,
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    "IGNORE_EXCEPTIONS": False,
                    "LOG_IGNORED_EXCEPTIONS": False,
                },
                "KEY_PREFIX": "construtask",
            },
            "critical": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": "redis://cache.example.com:6379/2",
                "TIMEOUT": 300,
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    "IGNORE_EXCEPTIONS": False,
                    "LOG_IGNORED_EXCEPTIONS": False,
                },
                "KEY_PREFIX": "construtask-critical",
            },
        },
        CELERY_BEAT_SCHEDULE={
            "construtask-backup-postgres-r2": {
                "task": "Construtask.tasks.task_executar_backup_postgres",
                "schedule": timedelta(hours=24),
            }
        },
    )
    def test_validar_prontidao_producao_aceita_admin_customizado_e_agendamento_backup(self):
        backup = OperacaoBackupSaaS.objects.create(
            tipo="BACKUP",
            status="SUCESSO",
            ambiente="production",
            provedor="s3",
            identificador_artefato="backup-prod.dump",
        )
        OperacaoBackupSaaS.objects.create(
            tipo="TESTE_RESTAURACAO",
            status="SUCESSO",
            ambiente="production",
            provedor="s3",
            backup_referencia=backup,
        )
        stdout = StringIO()
        with patch.dict(os.environ, {"REDIS_URL": "redis://cache.example.com:6379/0"}):
            with patch("django_redis.get_redis_connection") as get_redis_connection:
                get_redis_connection.return_value.ping.return_value = True
                call_command("validar_prontidao_producao", "--json", stdout=stdout)
            payload = stdout.getvalue()

        self.assertNotIn("construtask.E010", payload)
        self.assertNotIn("construtask.E011", payload)
        self.assertNotIn("construtask.E012", payload)

    @override_settings(
        DEBUG=False,
        CONSTRUTASK_ENVIRONMENT="production",
        CONSTRUTASK_BACKUP_ENABLED=True,
        CONSTRUTASK_BACKUP_PROVIDER="s3",
        CONSTRUTASK_BACKUP_RETENTION_DAYS=30,
        CONSTRUTASK_BACKUP_INTERVAL_HOURS=24,
        CONSTRUTASK_RECOVERY_TEST_INTERVAL_DAYS=30,
    )
    def test_diagnostico_saas_sinaliza_teste_de_recuperacao_antigo(self):
        from .application.saas import diagnostico_base_saas

        backup = OperacaoBackupSaaS.objects.create(
            tipo="BACKUP",
            status="SUCESSO",
            ambiente="production",
            provedor="s3",
            executado_em=timezone.now(),
        )
        OperacaoBackupSaaS.objects.create(
            tipo="TESTE_RESTAURACAO",
            status="SUCESSO",
            ambiente="production",
            provedor="s3",
            backup_referencia=backup,
            executado_em=timezone.now() - timedelta(days=45),
        )

        diagnostico = diagnostico_base_saas()
        backup_check = diagnostico["checks"]["backup"]

        self.assertEqual(backup_check["status"], "error")
        self.assertFalse(backup_check["teste_recuperacao_recente"])
        self.assertIn("30 dias", backup_check["detalhe"])

    def test_task_sincronizar_alertas_registra_falha_quando_soft_time_limit_explode(self):
        from .tasks import task_sincronizar_alertas_obra

        with patch(
            "Construtask.services_alertas.sincronizar_alertas_operacionais_obra",
            side_effect=SoftTimeLimitExceeded("tempo excedido"),
        ):
            with self.assertRaises(SoftTimeLimitExceeded):
                task_sincronizar_alertas_obra.run(self.obra.pk)

        job = JobAssincrono.objects.filter(tipo="SINCRONIZAR_ALERTAS_OBRA", status="FALHOU").latest("criado_em")
        self.assertEqual(job.empresa, self.empresa)
        self.assertEqual(job.obra, self.obra)
        self.assertEqual(job.parametros["motivo"], "soft_time_limit_exceeded")

    @override_settings(
        CONSTRUTASK_BACKUP_ENABLED=True,
        CONSTRUTASK_BACKUP_PROVIDER="s3",
        CONSTRUTASK_ENVIRONMENT="production",
    )
    def test_task_backup_registra_falha_quando_soft_time_limit_explode(self):
        from .tasks import task_executar_backup_postgres

        with patch("django.core.management.call_command", side_effect=SoftTimeLimitExceeded("tempo excedido")):
            with self.assertRaises(SoftTimeLimitExceeded):
                task_executar_backup_postgres.run()

        operacao = OperacaoBackupSaaS.objects.filter(tipo="BACKUP", status="FALHOU").latest("executado_em")
        self.assertEqual(operacao.ambiente, "production")
        self.assertEqual(operacao.detalhes["motivo"], "soft_time_limit_exceeded")
