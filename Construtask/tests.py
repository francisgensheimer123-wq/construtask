from datetime import date
from decimal import Decimal
from io import BytesIO

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .domain import (
    calcular_saldo_disponivel_compromisso,
    gerar_numero_documento,
    hidratar_medicao_do_contrato,
    validar_itens_compromisso_orcamento,
    validar_itens_medicao_contrato,
)
from .models import (
    AditivoContrato,
    AditivoContratoItem,
    Compromisso,
    CompromissoItem,
    Cotacao,
    CotacaoItem,
    Empresa,
    Fornecedor,
    FornecedorAvaliacao,
    Obra,
    OrdemCompra,
    Medicao,
    MedicaoItem,
    NaoConformidade,
    NotaFiscal,
    NotaFiscalCentroCusto,
    PlanoContas,
    SolicitacaoCompra,
    SolicitacaoCompraItem,
    UsuarioEmpresa,
)
from .models_planejamento import PlanoFisico, PlanoFisicoItem
from .forms import AditivoContratoItemFormSet
from .views import ContratoDetailView, HomeView
from .services import importar_plano_contas_excel, obter_dados_contrato, validar_rateio_nota
from .services_aquisicoes import AquisicoesService
from .services_eva import EVAService
from .services_indicadores import IndicadoresService
from .services_integracao import IntegracaoService
from .services_qualidade import QualidadeWorkflowService
from .templatetags.formatters import money_br, trunc2


class PlanoContasTests(TestCase):
    def test_gera_codigo_hierarquico_com_parent(self):
        raiz = PlanoContas.objects.create(descricao="Raiz")
        filho = PlanoContas.objects.create(descricao="Filho", parent=raiz)

        self.assertEqual(raiz.codigo, "01")
        self.assertEqual(filho.codigo, "01.01")


class BaseFinanceTestCase(TestCase):
    def setUp(self):
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
        self.assertEqual(numero, "CTR-0002")

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

    def test_home_view_responde(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Painel Operacional")

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

    def test_plano_contas_view_responde(self):
        response = self.client.get(reverse("plano_contas_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plano de Contas")
        self.assertContains(response, "Quantidade")
        self.assertContains(response, "Valor Unitario")

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
        medicao = Medicao.objects.get(descricao="Medicao via app")
        self.assertEqual(medicao.valor_medido, Decimal("200.00"))
        self.assertEqual(medicao.itens.count(), 1)

    def test_edicao_medicao_nao_cria_item_extra_vazio(self):
        # Garante que a linha extra do formset (extra=1) nao gere erro
        # nem crie um segundo item quando o centro de custo fica vazio.
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
        self.assertEqual(medicao.descricao, "Medicao para update - alterada")

    def test_exportacao_de_medicoes_retorna_excel(self):
        response = self.client.get(reverse("medicao_export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_medicao_herda_unidade_e_valor_unitario_do_contrato(self):
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
        item = MedicaoItem.objects.get(medicao__descricao="Medicao travada ao contrato")
        self.assertEqual(item.unidade, "m3")
        self.assertEqual(item.valor_unitario, Decimal("20.00"))
        self.assertEqual(item.valor_total, Decimal("60.00"))

    def test_cria_nota_fiscal_pela_interface(self):
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
        self.assertEqual(consolidado["executado"], Decimal("40.00"))
        self.assertGreater(consolidado["planejado"], Decimal("0.00"))
        self.assertEqual(eva["EV"], Decimal("50.00"))
        self.assertEqual(eva["AC"], Decimal("40.00"))
        self.assertEqual(indicadores["executado"], Decimal("40.00"))

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
        self.assertContains(response, "Integracao Fisico-Financeira")

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
        self.assertContains(response_emitir, ordem.compromisso_relacionado.numero)

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
        solicitacao = SolicitacaoCompra.objects.get(titulo="Solicitacao multi item")
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
        solicitacao = SolicitacaoCompra.objects.get(titulo="Solicitacao superuser")
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

