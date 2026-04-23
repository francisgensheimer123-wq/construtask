from datetime import timedelta

from django.db.models import Max, Q
from django.utils import timezone

from ..models import AlertaOperacional, Compromisso, Medicao, ParametroComunicacaoEmpresa
from ..models_comunicacoes import HistoricoReuniaoComunicacao, ItemPautaReuniao, ReuniaoComunicacao
from ..models_planejamento import PlanoFisico, PlanoFisicoItem
from ..models_qualidade import NaoConformidade
from ..models_risco import Risco


SECOES_PAUTA = (
    ("CRONOGRAMA", "Cronograma"),
    ("ALERTA", "Alertas"),
    ("RISCO", "Riscos"),
    ("NAO_CONFORMIDADE", "Nao Conformidades"),
    ("CONTRATO", "Contratos"),
    ("MEDICAO", "Medicoes"),
    ("OUTRO", "Outros Itens"),
)


def _rotulo_penultimo_ultimo(item):
    parent = getattr(item, "parent", None)
    titulo = getattr(item, "atividade", None) or getattr(item, "titulo", None) or str(item)
    if parent and getattr(parent, "atividade", None):
        return f"{parent.atividade} / {titulo}"
    return titulo


def periodicidade_reuniao_empresa(empresa, tipo_reuniao):
    parametros = ParametroComunicacaoEmpresa.obter_ou_criar(empresa)
    if tipo_reuniao == "CURTO_PRAZO":
        return parametros.frequencia_curto_prazo_dias
    if tipo_reuniao == "MEDIO_PRAZO":
        return parametros.frequencia_medio_prazo_dias
    return parametros.frequencia_longo_prazo_dias


def titulo_padrao_reuniao(obra, tipo_reuniao):
    titulos = {
        "CURTO_PRAZO": "Reuniao de Curto Prazo",
        "MEDIO_PRAZO": "Reuniao de Medio Prazo",
        "LONGO_PRAZO": "Reuniao de Longo Prazo",
    }
    return f"{titulos.get(tipo_reuniao, 'Reuniao de Comunicacao')} - {obra.codigo}"


def construir_itens_automaticos_pauta(obra, *, data_reuniao=None, janela_dias=None):
    inicio_periodo = data_reuniao or timezone.localdate()
    janela = max(int(janela_dias or 0), 0)
    fim_periodo = inicio_periodo + timedelta(days=janela)
    itens = []

    alertas = (
        AlertaOperacional.objects.filter(obra=obra, status__in=["ABERTO", "EM_TRATAMENTO", "JUSTIFICADO"])
        .filter(Q(data_referencia__range=(inicio_periodo, fim_periodo)) | Q(data_referencia__isnull=True, criado_em__date__range=(inicio_periodo, fim_periodo)))
        .select_related("responsavel")
        .order_by("data_referencia", "-criado_em")[:10]
    )
    for alerta in alertas:
        titulo_alerta = alerta.titulo
        if alerta.entidade_tipo == "PlanoFisicoItem" and alerta.entidade_id:
            atividade = (
                PlanoFisicoItem.objects.select_related("parent")
                .filter(pk=alerta.entidade_id)
                .first()
            )
            if atividade:
                titulo_alerta = _rotulo_penultimo_ultimo(atividade)
        itens.append(
            {
                "categoria": "ALERTA",
                "referencia_modelo": "AlertaOperacional",
                "referencia_id": alerta.pk,
                "titulo": f"[{alerta.severidade}] {titulo_alerta}",
                "descricao": alerta.descricao,
                "contexto": {
                    "codigo_regra": alerta.codigo_regra,
                    "status": alerta.status,
                    "responsavel": getattr(alerta.responsavel, "username", ""),
                },
            }
        )

    riscos = (
        Risco.objects.filter(obra=obra)
        .exclude(status__in=["FECHADO", "CANCELADO"])
        .filter(
            Q(data_meta_tratamento__range=(inicio_periodo, fim_periodo))
            | Q(data_meta_tratamento__isnull=True, criado_em__date__range=(inicio_periodo, fim_periodo))
        )
        .select_related("responsavel")
        .order_by("data_meta_tratamento", "-nivel", "-criado_em")[:10]
    )
    for risco in riscos:
        itens.append(
            {
                "categoria": "RISCO",
                "referencia_modelo": "Risco",
                "referencia_id": risco.pk,
                "titulo": f"[Nivel {risco.nivel}] {risco.titulo}",
                "descricao": risco.descricao,
                "contexto": {
                    "status": risco.status,
                    "responsavel": getattr(risco.responsavel, "username", ""),
                    "data_meta_tratamento": risco.data_meta_tratamento.isoformat() if risco.data_meta_tratamento else "",
                },
            }
        )

    ncs = (
        NaoConformidade.objects.filter(obra=obra)
        .exclude(status__in=["ENCERRADA", "CANCELADA"])
        .filter(data_abertura__range=(inicio_periodo, fim_periodo))
        .select_related("responsavel")
        .order_by("data_abertura", "-criado_em")[:10]
    )
    for nc in ncs:
        itens.append(
            {
                "categoria": "NAO_CONFORMIDADE",
                "referencia_modelo": "NaoConformidade",
                "referencia_id": nc.pk,
                "titulo": f"{nc.numero} - {nc.get_status_display()}",
                "descricao": nc.descricao,
                "contexto": {
                    "status": nc.status,
                    "responsavel": getattr(nc.responsavel, "username", ""),
                },
            }
        )

    plano_ativo = PlanoFisico.objects.filter(obra=obra, status__in=["ATIVO", "BASELINE"]).order_by("-updated_at").first()
    if plano_ativo:
        atividades = (
            PlanoFisicoItem.objects.filter(plano=plano_ativo)
            .filter(
                percentual_concluido__lt=100,
            )
            .filter(
                Q(data_inicio_prevista__range=(inicio_periodo, fim_periodo))
                | Q(data_fim_prevista__range=(inicio_periodo, fim_periodo))
            )
            .order_by("data_inicio_prevista", "data_fim_prevista", "-dias_desvio")[:12]
        )
        for atividade in atividades:
            itens.append(
                {
                    "categoria": "CRONOGRAMA",
                    "referencia_modelo": "PlanoFisicoItem",
                    "referencia_id": atividade.pk,
                    "titulo": f"{atividade.codigo_atividade} - {_rotulo_penultimo_ultimo(atividade)}",
                    "descricao": f"Previsto para {atividade.data_fim_prevista.strftime('%d/%m/%Y') if atividade.data_fim_prevista else '-'} | Concluido: {atividade.percentual_concluido}%",
                    "contexto": {
                        "dias_desvio": atividade.dias_desvio,
                        "percentual_concluido": atividade.percentual_concluido,
                    },
                }
            )

    contratos = (
        Compromisso.objects.filter(obra=obra)
        .filter(tipo="CONTRATO")
        .exclude(status__in=["ENCERRADO", "CANCELADO"])
        .filter(
            Q(data_prevista_inicio__range=(inicio_periodo, fim_periodo))
            | Q(data_prevista_fim__range=(inicio_periodo, fim_periodo))
            | Q(data_prevista_inicio__isnull=True, data_prevista_fim__isnull=True, data_assinatura__range=(inicio_periodo, fim_periodo))
        )
        .order_by("data_prevista_inicio", "data_prevista_fim", "-data_assinatura")[:10]
    )
    for contrato in contratos:
        itens.append(
            {
                "categoria": "CONTRATO",
                "referencia_modelo": "Compromisso",
                "referencia_id": contrato.pk,
                "titulo": f"{contrato.numero} - {contrato.fornecedor}",
                "descricao": f"Status: {contrato.get_status_display()} | Valor: {contrato.valor_contratado}",
                "contexto": {
                    "status": contrato.status,
                    "valor_contratado": str(contrato.valor_contratado),
                },
            }
        )

    medicoes = (
        Medicao.objects.filter(obra=obra)
        .exclude(status__in=["APROVADA", "FATURADA"])
        .filter(
            Q(data_medicao__range=(inicio_periodo, fim_periodo))
            | Q(data_prevista_inicio__range=(inicio_periodo, fim_periodo))
            | Q(data_prevista_fim__range=(inicio_periodo, fim_periodo))
        )
        .select_related("contrato")
        .order_by("data_prevista_inicio", "data_prevista_fim", "-data_medicao")[:10]
    )
    for medicao in medicoes:
        itens.append(
            {
                "categoria": "MEDICAO",
                "referencia_modelo": "Medicao",
                "referencia_id": medicao.pk,
                "titulo": f"{medicao.numero_da_medicao} - {medicao.contrato.numero}",
                "descricao": f"Status: {medicao.get_status_display()} | Valor medido: {medicao.valor_medido}",
                "contexto": {
                    "status": medicao.status,
                    "valor_medido": str(medicao.valor_medido),
                },
            }
        )

    return itens


def criar_reuniao_com_pauta_automatica(obra, tipo_reuniao, usuario, *, data_prevista=None):
    periodicidade = periodicidade_reuniao_empresa(obra.empresa, tipo_reuniao)
    data_base = data_prevista or timezone.localdate()
    reuniao = ReuniaoComunicacao.objects.create(
        empresa=obra.empresa,
        obra=obra,
        tipo_reuniao=tipo_reuniao,
        titulo=titulo_padrao_reuniao(obra, tipo_reuniao),
        periodicidade_dias=periodicidade,
        data_prevista=data_base,
        criado_por=usuario,
    )
    itens = construir_itens_automaticos_pauta(obra, data_reuniao=data_base, janela_dias=periodicidade)
    for ordem, item in enumerate(itens, start=1):
        ItemPautaReuniao.objects.create(
            reuniao=reuniao,
            ordem=ordem,
            origem_tipo="AUTOMATICA",
            categoria=item["categoria"],
            referencia_modelo=item.get("referencia_modelo", ""),
            referencia_id=item.get("referencia_id"),
            titulo=item["titulo"],
            descricao=item.get("descricao", ""),
            contexto=item.get("contexto", {}),
        )
    registrar_historico_reuniao(reuniao, usuario, "CRIACAO", "Reuniao criada com pauta automatica inicial.")
    return reuniao


def compilar_ata_reuniao(reuniao):
    linhas = [
        f"Ata da {reuniao.get_tipo_reuniao_display()}",
        f"Reuniao: {reuniao.numero} - {reuniao.titulo}",
        f"Obra: {reuniao.obra.codigo} - {reuniao.obra.nome}",
        f"Data prevista: {reuniao.data_prevista.strftime('%d/%m/%Y') if reuniao.data_prevista else '-'}",
        "",
        "Itens deliberados:",
    ]
    itens = list(reuniao.itens_pauta.filter(ativo=True).order_by("categoria", "ordem", "id"))
    indice = 1
    for categoria, rotulo in SECOES_PAUTA:
        itens_categoria = [item for item in itens if item.categoria == categoria]
        if not itens_categoria:
            continue
        linhas.extend(["", f"{rotulo}:", ""])
        for item in itens_categoria:
            linhas.extend(
                [
                    f"{indice}. {item.titulo}",
                    f"   Contexto: {item.descricao or '-'}",
                    f"   O que sera feito: {item.resposta_o_que or '-'}",
                    f"   Quem executara: {item.resposta_quem or '-'}",
                    f"   Quando: {item.resposta_quando.strftime('%d/%m/%Y') if item.resposta_quando else '-'}",
                    "",
                ]
            )
            indice += 1
    return "\n".join(linhas).strip()


def atualizar_resumo_pauta(reuniao):
    itens = reuniao.itens_pauta.filter(ativo=True).values_list("titulo", flat=True)
    reuniao.pauta_resumo = "\n".join(f"- {titulo}" for titulo in itens[:20])
    reuniao.save(update_fields=["pauta_resumo", "atualizado_em"])
    return reuniao.pauta_resumo


def registrar_historico_reuniao(reuniao, usuario, acao, observacao=""):
    return HistoricoReuniaoComunicacao.objects.create(
        reuniao=reuniao,
        usuario=usuario,
        acao=acao,
        observacao=observacao,
    )


def proxima_data_sugerida_reuniao(reuniao):
    base = reuniao.data_realizada or reuniao.data_prevista or timezone.localdate()
    return base + timedelta(days=reuniao.periodicidade_dias or 0)


def resumo_reunioes_obra(obra):
    reunioes = ReuniaoComunicacao.objects.filter(obra=obra)
    agregados = reunioes.aggregate(
        ultima_prevista=Max("data_prevista"),
        ultima_realizada=Max("data_realizada"),
    )
    return {
        "total": reunioes.count(),
        "rascunhos": reunioes.filter(status="RASCUNHO").count(),
        "pautas_validadas": reunioes.filter(status="PAUTA_VALIDADA").count(),
        "em_aprovacao": reunioes.filter(status="EM_APROVACAO").count(),
        "aprovadas": reunioes.filter(status="APROVADA").count(),
        "ultima_prevista": agregados["ultima_prevista"],
        "ultima_realizada": agregados["ultima_realizada"],
    }
