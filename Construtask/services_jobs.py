import csv
from io import StringIO

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .application.financeiro import registrar_fechamento_mensal
from .models import JobAssincrono, Obra, PlanoContas
from .queries.financeiro import construir_dados_fechamento_mensal, construir_dados_projecao_financeira
from .services import importar_plano_contas_excel
from .services_alertas import resumo_alertas_operacionais, sincronizar_alertas_operacionais_obra
from .importacao_cronograma import CronogramaService


JOB_HANDLERS = {}


def registrar_job_handler(tipo):
    def decorator(func):
        JOB_HANDLERS[tipo] = func
        return func

    return decorator


def listar_jobs_recentes(*, empresa=None, obra=None, limite=10):
    queryset = (
        JobAssincrono.objects.select_related("obra", "solicitado_por")
        .only(
            "id",
            "obra__codigo",
            "solicitado_por__username",
            "tipo",
            "status",
            "descricao",
            "criado_em",
            "concluido_em",
            "erro",
        )
        .order_by("-criado_em")
    )
    if empresa:
        queryset = queryset.filter(empresa=empresa)
    if obra:
        queryset = queryset.filter(obra=obra)
    return list(queryset[:limite])


def enfileirar_job(
    *,
    tipo,
    descricao,
    solicitado_por=None,
    empresa=None,
    obra=None,
    parametros=None,
    arquivo_entrada=None,
):
    job = JobAssincrono.objects.create(
        empresa=empresa or getattr(obra, "empresa", None),
        obra=obra,
        solicitado_por=solicitado_por,
        tipo=tipo,
        descricao=descricao,
        parametros=parametros or {},
        arquivo_entrada=arquivo_entrada,
    )
    return job


def executar_job(job):
    handler = JOB_HANDLERS.get(job.tipo)
    if not handler:
        raise ValueError(f"Handler nao registrado para o job {job.tipo}.")

    with transaction.atomic():
        job.status = "EM_EXECUCAO"
        job.iniciado_em = timezone.now()
        job.tentativas += 1
        job.erro = ""
        job.save(update_fields=["status", "iniciado_em", "tentativas", "erro", "atualizado_em"])

    try:
        resultado = handler(job)
    except Exception as exc:
        job.status = "FALHOU"
        job.concluido_em = timezone.now()
        job.erro = str(exc)
        job.save(update_fields=["status", "concluido_em", "erro", "atualizado_em"])
        raise

    job.status = "CONCLUIDO"
    job.concluido_em = timezone.now()
    job.resultado = resultado or {}
    job.save(update_fields=["status", "concluido_em", "resultado", "arquivo_resultado", "atualizado_em"])
    return job


import logging as _logging

_job_worker_logger = _logging.getLogger("construtask.jobs.worker")


def processar_jobs_pendentes(*, limite=10):
    from django.conf import settings
    from django.utils import timezone as _tz

    # SQLite (dev) não suporta select_for_update com skip_locked
    usar_lock = settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"

    if usar_lock:
        from django.db import transaction
        with transaction.atomic():
            jobs = list(
                JobAssincrono.objects
                .select_for_update(skip_locked=True)
                .filter(status="PENDENTE")
                .order_by("criado_em")[:limite]
            )
            pks = [j.pk for j in jobs]
            if pks:
                JobAssincrono.objects.filter(pk__in=pks).update(
                    status="EM_EXECUCAO",
                    iniciado_em=_tz.now(),
                    tentativas=models.F("tentativas") + 1,
                )
    else:
        jobs = list(JobAssincrono.objects.filter(status="PENDENTE").order_by("criado_em")[:limite])

    processados = []
    for job in jobs:
        job.refresh_from_db()
        try:
            _executar_job_seguro(job)
        except Exception:
            pass
        processados.append(job)
    return processados


def _executar_job_seguro(job):
    from django.utils import timezone as _tz
    handler = JOB_HANDLERS.get(job.tipo)
    if not handler:
        job.status = "FALHOU"
        job.concluido_em = _tz.now()
        job.erro = f"Handler nao registrado para tipo '{job.tipo}'."
        job.save(update_fields=["status", "concluido_em", "erro", "atualizado_em"])
        return
    try:
        resultado = handler(job)
        job.status = "CONCLUIDO"
        job.concluido_em = _tz.now()
        job.resultado = resultado or {}
        job.save(update_fields=["status", "concluido_em", "resultado", "arquivo_resultado", "atualizado_em"])
    except Exception as exc:
        _job_worker_logger.exception(f"[Job {job.pk}] Falha ao executar job '{job.tipo}'.")
        job.status = "FALHOU"
        job.concluido_em = _tz.now()
        job.erro = str(exc)
        job.save(update_fields=["status", "concluido_em", "erro", "atualizado_em"])


def _salvar_csv_job(job, nome_arquivo, cabecalhos, linhas):
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=cabecalhos)
    writer.writeheader()
    for linha in linhas:
        writer.writerow(linha)
    job.arquivo_resultado.save(nome_arquivo, ContentFile(buffer.getvalue().encode("utf-8")), save=False)
    return {"arquivo": job.arquivo_resultado.name, "linhas": len(linhas)}


@registrar_job_handler("SINCRONIZAR_ALERTAS_OBRA")
def executar_job_sincronizar_alertas(job):
    obra = job.obra or Obra.objects.get(pk=job.parametros["obra_id"])
    sincronizar_alertas_operacionais_obra(obra)
    resumo = resumo_alertas_operacionais(obra)
    return {
        "obra_id": obra.pk,
        "alertas_abertos": resumo.get("abertos", 0),
        "alertas_criticos": resumo.get("criticos", 0),
    }


@registrar_job_handler("IMPORTAR_PLANO_CONTAS")
def executar_job_importar_plano_contas(job):
    obra = job.obra or Obra.objects.get(pk=job.parametros["obra_id"])
    if not job.arquivo_entrada:
        raise ValueError("Job de importacao sem arquivo de entrada.")
    with job.arquivo_entrada.open("rb") as arquivo:
        importar_plano_contas_excel(arquivo, obra=obra)
    return {
        "obra_id": obra.pk,
        "total_planos": PlanoContas.objects.filter(obra=obra).count(),
    }


@registrar_job_handler("GERAR_RELATORIO_FINANCEIRO")
def executar_job_relatorio_financeiro(job):
    tipo_relatorio = job.parametros.get("relatorio")
    obra = job.obra or Obra.objects.get(pk=job.parametros["obra_id"])
    if tipo_relatorio == "FECHAMENTO_MENSAL":
        ano = int(job.parametros["ano"])
        mes = int(job.parametros["mes"])
        registrar_fechamento_mensal(obra=obra, ano=ano, mes=mes)
        dados = construir_dados_fechamento_mensal(obra=obra, ano=ano, mes=mes)
        linhas = [
            {
                "Centro de Custo": f'{linha["centro"].codigo} - {linha["centro"].descricao}',
                "Comprometido": linha["comprometido"],
                "Medido": linha["medido"],
                "Notas": linha["notas"],
                "Saldo a Medir": linha["saldo_a_medir"],
                "Saldo a Executar": linha["saldo_a_executar"],
            }
            for linha in dados["resumo_centros"]
        ]
        metadata = _salvar_csv_job(
            job,
            f"fechamento_mensal_{obra.pk}_{ano}_{mes:02d}.csv",
            ["Centro de Custo", "Comprometido", "Medido", "Notas", "Saldo a Medir", "Saldo a Executar"],
            linhas,
        )
        metadata.update({"relatorio": tipo_relatorio, "ano": ano, "mes": mes})
        return metadata

    if tipo_relatorio == "PROJECAO_FINANCEIRA":
        meses = int(job.parametros.get("meses") or 12)
        dados = construir_dados_projecao_financeira(obra=obra, meses_qtd=meses)
        linhas = [
            {"Mes": item["label"], "Entradas": item["entrada"], "Saidas": item["saida"], "Saldo": item["saldo"]}
            for item in dados["series"]
        ]
        metadata = _salvar_csv_job(
            job,
            f"projecao_financeira_{obra.pk}_{meses}m.csv",
            ["Mes", "Entradas", "Saidas", "Saldo"],
            linhas,
        )
        metadata.update({"relatorio": tipo_relatorio, "meses": meses})
        return metadata

    raise ValueError("Tipo de relatorio financeiro nao suportado.")


@registrar_job_handler("IMPORTAR_CRONOGRAMA")
def executar_job_importar_cronograma(job):
    obra = job.obra or Obra.objects.get(pk=job.parametros["obra_id"])

    if not job.arquivo_entrada:
        raise ValueError("Job de importacao de cronograma sem arquivo de entrada.")

    titulo = job.parametros.get("titulo") or None
    criar_baseline = job.parametros.get("criar_baseline", False)
    responsavel = job.solicitado_por

    with job.arquivo_entrada.open("rb") as arquivo:
        plano = CronogramaService.importar_xlsx(
            arquivo=arquivo,
            obra=obra,
            responsavel=responsavel,
            titulo=titulo,
            criar_baseline=criar_baseline,
        )

    resumo = getattr(plano, "_resumo_importacao", {})
    return {
        "obra_id": obra.pk,
        "plano_fisico_id": plano.pk,
        "titulo": plano.titulo,
        "total_linhas": resumo.get("total_linhas", 0),
        "atividades_validas": resumo.get("atividades_validas", 0),
        "itens_criados": resumo.get("itens_criados", 0),
        "sem_datas": resumo.get("sem_datas", 0),
        "com_codigo_eap": resumo.get("com_codigo_eap", 0),
        "eap_reconhecida": resumo.get("eap_reconhecida", 0),
    }