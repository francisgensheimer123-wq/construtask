from .models_qualidade import NaoConformidade, NaoConformidadeHistorico


class QualidadeWorkflowService:
    @staticmethod
    def _snapshot(nc):
        return {
            "status": nc.status,
            "descricao": nc.descricao,
            "causa": nc.causa,
            "acao_corretiva": nc.acao_corretiva,
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
        antes = cls._snapshot(nc)
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
        antes = cls._snapshot(nc)
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
