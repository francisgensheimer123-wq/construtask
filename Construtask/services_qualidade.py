from django.core.exceptions import ValidationError
from django.utils import timezone

from .models_qualidade import NaoConformidade, NaoConformidadeHistorico


class QualidadeWorkflowService:
    TRANSICOES_PERMITIDAS = {
        "ABERTA": {"EM_TRATAMENTO", "CANCELADA"},
        "EM_TRATAMENTO": {"EM_VERIFICACAO", "CANCELADA"},
        "EM_VERIFICACAO": {"ENCERRADA", "CANCELADA"},
        "ENCERRADA": set(),
        "CANCELADA": set(),
    }

    @classmethod
    def _validar_transicao(cls, nc, novo_status):
        if novo_status not in cls.TRANSICOES_PERMITIDAS.get(nc.status, set()):
            raise ValidationError(
                f"Transicao invalida de {nc.get_status_display()} para "
                f"{dict(NaoConformidade.STATUS_CHOICES).get(novo_status, novo_status)}."
            )

    @staticmethod
    def _snapshot(nc):
        return {
            "status": nc.status,
            "descricao": nc.descricao,
            "causa": nc.causa,
            "acao_corretiva": nc.acao_corretiva,
            "evidencia_tratamento": nc.evidencia_tratamento,
            "evidencia_tratamento_anexo": getattr(nc.evidencia_tratamento_anexo, "name", ""),
            "evidencia_encerramento": nc.evidencia_encerramento,
            "evidencia_encerramento_anexo": getattr(nc.evidencia_encerramento_anexo, "name", ""),
            "eficacia_observacao": nc.eficacia_observacao,
        }

    @classmethod
    def abrir(cls, *, empresa, obra, descricao, responsavel, criado_por, causa="", acao_corretiva="", plano_contas=None):
        nc = NaoConformidade.objects.create(
            empresa=empresa,
            obra=obra,
            plano_contas=plano_contas,
            descricao=descricao,
            causa=causa,
            acao_corretiva=acao_corretiva,
            responsavel=responsavel,
            criado_por=criado_por,
        )
        NaoConformidadeHistorico.objects.create(
            nao_conformidade=nc,
            usuario=criado_por,
            acao="ABERTURA",
            dados_novos=cls._snapshot(nc),
        )
        return nc

    @classmethod
    def iniciar_tratamento(cls, nc, usuario, observacao=""):
        cls._validar_transicao(nc, "EM_TRATAMENTO")
        antes = cls._snapshot(nc)
        nc.status = "EM_TRATAMENTO"
        nc.save()
        NaoConformidadeHistorico.objects.create(
            nao_conformidade=nc,
            usuario=usuario,
            acao="TRATAMENTO",
            observacao=observacao,
            dados_anteriores=antes,
            dados_novos=cls._snapshot(nc),
        )
        return nc

    @classmethod
    def enviar_para_verificacao(cls, nc, usuario, observacao=""):
        cls._validar_transicao(nc, "EM_VERIFICACAO")
        antes = cls._snapshot(nc)
        nc.eficacia_verificada_por = usuario
        nc.eficacia_verificada_em = timezone.now()
        nc.status = "EM_VERIFICACAO"
        nc.save()
        NaoConformidadeHistorico.objects.create(
            nao_conformidade=nc,
            usuario=usuario,
            acao="VERIFICACAO",
            observacao=observacao,
            dados_anteriores=antes,
            dados_novos=cls._snapshot(nc),
        )
        return nc

    @classmethod
    def encerrar(cls, nc, usuario, observacao=""):
        cls._validar_transicao(nc, "ENCERRADA")
        antes = cls._snapshot(nc)
        nc.eficacia_verificada_por = usuario
        nc.eficacia_verificada_em = timezone.now()
        nc.status = "ENCERRADA"
        nc.save()
        NaoConformidadeHistorico.objects.create(
            nao_conformidade=nc,
            usuario=usuario,
            acao="ENCERRAMENTO",
            observacao=observacao,
            dados_anteriores=antes,
            dados_novos=cls._snapshot(nc),
        )
        return nc

    @classmethod
    def cancelar(cls, nc, usuario, observacao=""):
        cls._validar_transicao(nc, "CANCELADA")
        antes = cls._snapshot(nc)
        nc.status = "CANCELADA"
        nc.save()
        NaoConformidadeHistorico.objects.create(
            nao_conformidade=nc,
            usuario=usuario,
            acao="CANCELAMENTO",
            observacao=observacao,
            dados_anteriores=antes,
            dados_novos=cls._snapshot(nc),
        )
        return nc
