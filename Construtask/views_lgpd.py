from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import TemplateView

from .models import ConsentimentoLGPD, RegistroAcessoDadoPessoal, RegistroTratamentoDadoPessoal
from .models_aquisicoes import Fornecedor
from .permissions import get_empresa_operacional, is_admin_empresa
from .services_lgpd import (
    anonimizar_fornecedor_inativo,
    buscar_titular,
    descartar_fornecedor_anonimizado,
    excluir_logicamente_fornecedor,
    obter_inventario_dados_pessoais,
    obter_inventario_modelos_dados_pessoais,
    obter_politica_retencao_padrao,
    obter_politica_descarte_anonimizacao,
    obter_resumo_rotinas_lgpd,
    registrar_consentimento,
    revogar_consentimento,
)
from .views import _datahora_local, _pdf_relatorio_probatorio_response


class LgpdGovernancaView(LoginRequiredMixin, TemplateView):
    template_name = "app/lgpd_governanca.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser and not is_admin_empresa(request.user):
            messages.error(request, "Voce nao tem permissao para acessar a governanca LGPD.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        empresa = get_empresa_operacional(self.request)
        logs = RegistroAcessoDadoPessoal.objects.select_related("usuario", "empresa")
        tratamentos = RegistroTratamentoDadoPessoal.objects.select_related("usuario", "empresa")
        consentimentos = ConsentimentoLGPD.objects.select_related("usuario", "empresa")
        if empresa:
            logs = logs.filter(empresa=empresa)
            tratamentos = tratamentos.filter(empresa=empresa)
            consentimentos = consentimentos.filter(empresa=empresa)
        termo_busca = (self.request.GET.get("titular") or "").strip()
        context.update(
            {
                "inventario_dados_pessoais": obter_inventario_dados_pessoais(),
                "inventario_modelos_lgpd": obter_inventario_modelos_dados_pessoais(),
                "politica_retencao_padrao": obter_politica_retencao_padrao(),
                "politica_descarte_anonimizacao": obter_politica_descarte_anonimizacao(),
                "resumo_rotinas_lgpd": obter_resumo_rotinas_lgpd(),
                "logs_dados_pessoais": logs[:20],
                "tratamentos_lgpd": tratamentos[:20],
                "consentimentos_lgpd": consentimentos[:20],
                "empresa_lgpd": empresa,
                "titular_busca": termo_busca,
                "titulares_localizados": buscar_titular(empresa, termo_busca) if termo_busca else [],
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        empresa = get_empresa_operacional(request)
        acao = request.POST.get("acao")
        if acao == "registrar_consentimento":
            categoria = request.POST.get("categoria_titular") or "USUARIO"
            finalidade = (request.POST.get("finalidade") or "Comunicacao institucional e operacao autorizada").strip()
            texto = (request.POST.get("texto_aceito") or "Consentimento LGPD registrado no portal Construtask.").strip()
            email = (request.POST.get("email_referencia") or "").strip()
            registrar_consentimento(
                request,
                categoria_titular=categoria,
                finalidade=finalidade,
                texto_aceito=texto,
                email_referencia=email,
            )
            messages.success(request, "Consentimento LGPD registrado com sucesso.")
            return redirect("lgpd_governanca")

        if acao == "revogar_consentimento":
            consentimento = ConsentimentoLGPD.objects.filter(pk=request.POST.get("consentimento_id")).first()
            if consentimento and (request.user.is_superuser or consentimento.empresa_id == getattr(empresa, "pk", None)):
                revogar_consentimento(consentimento, usuario=request.user)
                messages.success(request, "Consentimento revogado com sucesso.")
            else:
                messages.error(request, "Consentimento nao encontrado.")
            return redirect("lgpd_governanca")

        if acao in {"excluir_fornecedor", "anonimizar_fornecedor", "descartar_fornecedor"}:
            fornecedor = Fornecedor.objects.filter(pk=request.POST.get("fornecedor_id")).first()
            if not fornecedor or (empresa and fornecedor.empresa_id != empresa.pk and not request.user.is_superuser):
                messages.error(request, "Fornecedor nao encontrado para tratamento LGPD.")
                return redirect("lgpd_governanca")
            if acao == "excluir_fornecedor":
                excluir_logicamente_fornecedor(
                    fornecedor,
                    usuario=request.user,
                    justificativa=(request.POST.get("justificativa") or "").strip(),
                )
                messages.success(request, "Fornecedor marcado com exclusao logica.")
            elif acao == "anonimizar_fornecedor":
                anonimizar_fornecedor_inativo(fornecedor)
                messages.success(request, "Fornecedor anonimizado.")
            else:
                descartar_fornecedor_anonimizado(
                    fornecedor,
                    usuario=request.user,
                    justificativa=(request.POST.get("justificativa") or "").strip(),
                )
                messages.success(request, "Fornecedor marcado como descartado.")
            return redirect("lgpd_governanca")

        return redirect("lgpd_governanca")


class PoliticaPrivacidadeView(TemplateView):
    template_name = "app/politica_privacidade.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "inventario_dados_pessoais": obter_inventario_dados_pessoais(),
                "politica_retencao_padrao": obter_politica_retencao_padrao(),
                "politica_descarte_anonimizacao": obter_politica_descarte_anonimizacao(),
            }
        )
        return context


class TermosUsoView(TemplateView):
    template_name = "app/termos_uso.html"


def lgpd_governanca_pdf_view(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if not request.user.is_superuser and not is_admin_empresa(request.user):
        messages.error(request, "Voce nao tem permissao para exportar a governanca LGPD.")
        return redirect("home")

    empresa = get_empresa_operacional(request)
    resumo = {
        "Empresa": empresa.nome if empresa else "Nao informada",
        "Emitido em": _datahora_local(timezone.now()).strftime("%d/%m/%Y %H:%M"),
        "Categorias Mapeadas": len(obter_inventario_dados_pessoais()),
        "Registros de Acesso Recentes": RegistroAcessoDadoPessoal.objects.filter(empresa=empresa).count() if empresa else RegistroAcessoDadoPessoal.objects.count(),
        "Tratamentos Registrados": RegistroTratamentoDadoPessoal.objects.filter(empresa=empresa).count() if empresa else RegistroTratamentoDadoPessoal.objects.count(),
    }
    historico = [
        {
            "Data": _datahora_local(log.criado_em).strftime("%d/%m/%Y %H:%M") if log.criado_em else "Nao informado",
            "Acao": log.get_acao_display(),
            "Usuario": str(log.usuario) if log.usuario else "Nao informado",
            "Descricao": f"{log.entidade} | {log.identificador or 'Nao informado'} | {log.finalidade}",
        }
        for log in RegistroAcessoDadoPessoal.objects.select_related("usuario", "empresa").filter(empresa=empresa)[:20]
    ]
    extras = [
        {
            "Categoria": item["categoria_titular"],
            "Entidade": item["entidade"],
            "Base Legal": item["base_legal"],
            "Retencao": item["retencao"],
        }
        for item in obter_inventario_dados_pessoais()
    ]
    return _pdf_relatorio_probatorio_response(
        "governanca_lgpd.pdf",
        "Governanca LGPD",
        resumo,
        historico,
        extras,
        extras_titulo="Inventario de Tratamento",
        extras_colunas=[("Categoria", 90), ("Entidade", 150), ("Base Legal", 120), ("Retencao", 135)],
        incluir_historico=True,
    )
