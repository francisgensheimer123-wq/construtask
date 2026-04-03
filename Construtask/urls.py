from django.contrib.auth import views as auth_views
from django.urls import path

from .views_documento import (
    DocumentoListView,
    DocumentoCreateView,
    DocumentoDetailView,
    DocumentoUpdateView,
    documento_delete_view,
    documento_download_view,
)
from .views_risco import (
    RiscoListView,
    RiscoCreateView,
    RiscoDetailView,
    RiscoUpdateView,
    risco_delete_view,
    risco_dashboard_view,
)
from .views_planejamento import (
    PlanoFisicoListView,
    PlanoFisicoCreateView,
    PlanoFisicoDetailView,
    PlanoFisicoUpdateView,
    PlanoFisicoItemUpdateView,
    PlanoFisicoDashboardView,
    MapaCorrespondenciaListView,
    vincular_mapeamento_ajax,
    sugerir_mapeamento_ajax,
    gerar_curva_s_ajax,
    plano_fisico_delete_view,
    criar_baseline_view,
)
from .views_qualidade import (
    NaoConformidadeCreateView,
    NaoConformidadeDetailView,
    NaoConformidadeListView,
    NaoConformidadeUpdateView,
)
from .views_aquisicoes import (
    CotacaoCreateView,
    CotacaoDetailView,
    CotacaoListView,
    FornecedorCreateView,
    FornecedorListView,
    OrdemCompraDetailView,
    OrdemCompraListView,
    SolicitacaoCompraCreateView,
    SolicitacaoCompraDetailView,
    SolicitacaoCompraListView,
    cotacao_pdf_view,
    solicitacao_compra_pdf_view,
)
from .views_usuarios import UsuarioEmpresaCreateView, UsuarioEmpresaListView
from .views import (
    CompromissoCreateView,
    ContratoDetailView,
    AditivoContratoCreateView,
    compromisso_delete_view,
    compromisso_export_view,
    compromisso_pdf_view,
    CompromissoListView,
    CompromissoUpdateView,
    FechamentoMensalView,
    CurvaABCView,
    HomeView,
    MedicaoCreateView,
    medicao_delete_view,
    MedicaoDetailView,
    medicao_export_view,
    MedicaoListView,
    MedicaoUpdateView,
    NotaFiscalCreateView,
    nota_fiscal_delete_view,
    nota_fiscal_export_view,
    NotaFiscalListView,
    NotaFiscalUpdateView,
    ObraCreateView,
    ObraListView,
    ObraUpdateView,
    PlanoContasConsultaView,
    PlanoContasUpdateView,
    contrato_dados_view,
    medicao_dados_view,
    plano_contas_delete_view,
    plano_contas_export_view,
    plano_contas_notas_view,
    plano_contas_importar_view,
    selecionar_obra_contexto_view,
    ProjecaoFinanceiraView,
)


urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("obras/", ObraListView.as_view(), name="obra_list"),
    path("obras/nova/", ObraCreateView.as_view(), name="obra_create"),
    path("obras/<int:pk>/editar/", ObraUpdateView.as_view(), name="obra_update"),
    path("contexto-obra/", selecionar_obra_contexto_view, name="selecionar_obra_contexto"),
    path("fechamento-mensal/", FechamentoMensalView.as_view(), name="fechamento_mensal"),
    path("projecao-financeira/", ProjecaoFinanceiraView.as_view(), name="projecao_financeira"),
    path("curva-abc/", CurvaABCView.as_view(), name="curva_abc"),
    path("plano-de-contas/", PlanoContasConsultaView.as_view(), name="plano_contas_list"),
    path("plano-de-contas/exportar/", plano_contas_export_view, name="plano_contas_export"),
    path("plano-de-contas/<int:pk>/editar/", PlanoContasUpdateView.as_view(), name="plano_contas_update"),
    path("plano-de-contas/excluir/", plano_contas_delete_view, name="plano_contas_delete"),
    path("plano-de-contas/<int:pk>/notas/", plano_contas_notas_view, name="plano_contas_notas"),
    path("plano-de-contas/importar/", plano_contas_importar_view, name="plano_contas_importar"),
    path("compras-contratacoes/", CompromissoListView.as_view(), name="compromisso_list"),
    path("compras-contratacoes/exportar/", compromisso_export_view, name="compromisso_export"),
    path("compras-contratacoes/nova/", CompromissoCreateView.as_view(), name="compromisso_create"),
    path("compras-contratacoes/<int:pk>/", ContratoDetailView.as_view(), name="contrato_detail"),
    path("compras-contratacoes/<int:pk>/pdf/", compromisso_pdf_view, name="compromisso_pdf"),
    path("compras-contratacoes/<int:pk>/aditivos/nova/", AditivoContratoCreateView.as_view(), name="aditivo_contrato_create"),
    path("compras-contratacoes/<int:pk>/editar/", CompromissoUpdateView.as_view(), name="compromisso_update"),
    path("compras-contratacoes/excluir/", compromisso_delete_view, name="compromisso_delete"),
    path("medicoes/", MedicaoListView.as_view(), name="medicao_list"),
    path("medicoes/exportar/", medicao_export_view, name="medicao_export"),
    path("medicoes/nova/", MedicaoCreateView.as_view(), name="medicao_create"),
    path("medicoes/<int:pk>/", MedicaoDetailView.as_view(), name="medicao_detail"),
    path("medicoes/<int:pk>/editar/", MedicaoUpdateView.as_view(), name="medicao_update"),
    path("medicoes/excluir/", medicao_delete_view, name="medicao_delete"),
    path("contratos/<int:pk>/dados/", contrato_dados_view, name="contrato_dados"),
    path("medicoes/<int:pk>/dados/", medicao_dados_view, name="medicao_dados"),
    path("notas-fiscais/", NotaFiscalListView.as_view(), name="nota_fiscal_list"),
    path("notas-fiscais/exportar/", nota_fiscal_export_view, name="nota_fiscal_export"),
    path("notas-fiscais/nova/", NotaFiscalCreateView.as_view(), name="nota_fiscal_create"),
    path("notas-fiscais/<int:pk>/editar/", NotaFiscalUpdateView.as_view(), name="nota_fiscal_update"),
    path("notas-fiscais/excluir/", nota_fiscal_delete_view, name="nota_fiscal_delete"),
    
    # Auth URLs
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    
    # UsuarioEmpresa URLs
    path("empresa-admin/", UsuarioEmpresaListView.as_view(), name="empresa_admin"),
    
    # ISO 7.5 - Controle Documental
    path("documentos/", DocumentoListView.as_view(), name="documento_list"),
    path("documentos/novo/", DocumentoCreateView.as_view(), name="documento_create"),
    path("documentos/<int:pk>/", DocumentoDetailView.as_view(), name="documento_detail"),
    path("documentos/<int:pk>/editar/", DocumentoUpdateView.as_view(), name="documento_update"),
    path("documentos/<int:pk>/excluir/", documento_delete_view, name="documento_delete"),
    path("documentos/<int:pk>/download/", documento_download_view, name="documento_download"),
    path("documentos/<int:pk>/download/<int:revisao_pk>/", documento_download_view, name="documento_download_revisao"),

    # ISO 10 - Qualidade Operacional
    path("nao-conformidades/", NaoConformidadeListView.as_view(), name="nao_conformidade_list"),
    path("nao-conformidades/nova/", NaoConformidadeCreateView.as_view(), name="nao_conformidade_create"),
    path("nao-conformidades/<int:pk>/", NaoConformidadeDetailView.as_view(), name="nao_conformidade_detail"),
    path("nao-conformidades/<int:pk>/editar/", NaoConformidadeUpdateView.as_view(), name="nao_conformidade_update"),

    # PMBOK 12 - Aquisições Estruturadas
    path("fornecedores/", FornecedorListView.as_view(), name="fornecedor_list"),
    path("fornecedores/novo/", FornecedorCreateView.as_view(), name="fornecedor_create"),
    path("solicitacoes-compra/", SolicitacaoCompraListView.as_view(), name="solicitacao_compra_list"),
    path("solicitacoes-compra/nova/", SolicitacaoCompraCreateView.as_view(), name="solicitacao_compra_create"),
    path("solicitacoes-compra/<int:pk>/", SolicitacaoCompraDetailView.as_view(), name="solicitacao_compra_detail"),
    path("solicitacoes-compra/<int:pk>/pdf/", solicitacao_compra_pdf_view, name="solicitacao_compra_pdf"),
    path("cotacoes/", CotacaoListView.as_view(), name="cotacao_list"),
    path("cotacoes/nova/", CotacaoCreateView.as_view(), name="cotacao_create"),
    path("cotacoes/<int:pk>/", CotacaoDetailView.as_view(), name="cotacao_detail"),
    path("cotacoes/<int:pk>/pdf/", cotacao_pdf_view, name="cotacao_pdf"),
    path("ordens-compra/", OrdemCompraListView.as_view(), name="ordem_compra_list"),
    path("ordens-compra/<int:pk>/", OrdemCompraDetailView.as_view(), name="ordem_compra_detail"),
    
    # ISO 6.1 - Gestão de Riscos
    path("riscos/", RiscoListView.as_view(), name="risco_list"),
    path("riscos/novo/", RiscoCreateView.as_view(), name="risco_create"),
    path("riscos/<int:pk>/", RiscoDetailView.as_view(), name="risco_detail"),
    path("riscos/<int:pk>/editar/", RiscoUpdateView.as_view(), name="risco_update"),
    path("riscos/<int:pk>/excluir/", risco_delete_view, name="risco_delete"),
    path("riscos/dashboard/", risco_dashboard_view, name="risco_dashboard"),
    
    # PMBOK 6 / ISO 6.1 - Planejamento Físico
    path("cronogramas/", PlanoFisicoListView.as_view(), name="plano_fisico_list"),
    path("cronogramas/importar/", PlanoFisicoCreateView.as_view(), name="plano_fisico_importar"),
    path("cronogramas/<int:pk>/", PlanoFisicoDetailView.as_view(), name="plano_fisico_detail"),
    path("cronogramas/<int:pk>/editar/", PlanoFisicoUpdateView.as_view(), name="plano_fisico_update"),
    path("cronogramas/<int:pk>/itens/<int:item_pk>/editar/", PlanoFisicoItemUpdateView.as_view(), name="plano_fisico_item_update"),
    path("cronogramas/<int:pk>/baseline/", criar_baseline_view, name="plano_fisico_criar_baseline"),
    path("cronogramas/excluir/", plano_fisico_delete_view, name="plano_fisico_delete"),
    path("cronogramas/dashboard/", PlanoFisicoDashboardView.as_view(), name="plano_fisico_dashboard"),
    
    # Mapeamento Cronograma ↔ EAP
    path("mapeamentos/", MapaCorrespondenciaListView.as_view(), name="mapa_correspondencia_list"),
    path("mapeamentos/vincular/", vincular_mapeamento_ajax, name="mapa_correspondencia_vincular"),
    path("mapeamentos/sugerir/", sugerir_mapeamento_ajax, name="mapa_correspondencia_sugerir"),
    
    # APIs
    path("api/curva-s/", gerar_curva_s_ajax, name="api_curva_s"),
]
