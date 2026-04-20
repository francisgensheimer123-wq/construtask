from django.conf import settings


def construir_alertas_operacionais_tecnicos(*, diagnostico_saas, resumo_jobs, resumo_metricas, resumo_erros, endpoints_lentos):
    alertas = []
    checks = diagnostico_saas.get("checks", {})

    for chave in ("database", "storage", "backup", "security"):
        check = checks.get(chave)
        if not check or check.get("status") == "ok":
            continue
        severidade = "critico" if check.get("status") == "error" else "atencao"
        alertas.append(
            {
                "codigo": f"saas-{chave}",
                "severidade": severidade,
                "titulo": check.get("titulo", chave.title()),
                "detalhe": check.get("detalhe", ""),
                "acao": "Revisar configuracao operacional antes do proximo deploy.",
            }
        )

    if resumo_jobs.get("falharam", 0) > 0:
        alertas.append(
            {
                "codigo": "jobs-falharam",
                "severidade": "critico",
                "titulo": "Jobs assincronos com falha",
                "detalhe": f"{resumo_jobs['falharam']} job(s) falharam no contexto atual.",
                "acao": "Reprocessar os jobs e investigar o erro raiz antes da proxima rotina.",
            }
        )
    if resumo_jobs.get("pendentes", 0) >= 10:
        alertas.append(
            {
                "codigo": "jobs-pendentes",
                "severidade": "atencao",
                "titulo": "Fila de jobs acumulada",
                "detalhe": f"{resumo_jobs['pendentes']} job(s) ainda pendentes.",
                "acao": "Verificar worker, janela de processamento e gargalos de fila.",
            }
        )

    if resumo_metricas.get("erros_500", 0) > 0:
        alertas.append(
            {
                "codigo": "http-500",
                "severidade": "critico",
                "titulo": "Erros 500 recentes",
                "detalhe": f"{resumo_metricas['erros_500']} request(s) com status 500 foram registradas.",
                "acao": "Abrir o painel de observabilidade e atacar a causa dominante.",
            }
        )
    if resumo_erros.get("abertos", 0) > 0:
        alertas.append(
            {
                "codigo": "erros-abertos",
                "severidade": "atencao",
                "titulo": "Rastros de erro ainda abertos",
                "detalhe": f"{resumo_erros['abertos']} erro(s) ainda sem resolucao marcada.",
                "acao": "Revisar os rastros abertos e registrar resolucao ou mitigacao.",
            }
        )

    threshold_ms = int(getattr(settings, "CONSTRUTASK_SLOW_REQUEST_THRESHOLD_MS", 1000) or 1000)
    for endpoint in endpoints_lentos[:3]:
        media_ms = float(endpoint.get("media_ms") or 0)
        if media_ms < threshold_ms:
            continue
        alertas.append(
            {
                "codigo": f"latencia-{endpoint['metodo']}-{endpoint['path']}",
                "severidade": "atencao" if media_ms < threshold_ms * 2 else "critico",
                "titulo": "Endpoint com latencia acima da meta",
                "detalhe": (
                    f"{endpoint['metodo']} {endpoint['path']} com media de {media_ms:.2f} ms "
                    f"e pico de {float(endpoint.get('pico_ms') or 0):.2f} ms."
                ),
                "acao": "Inspecionar consultas, cache e volume de dados do endpoint.",
            }
        )

    ordem = {"critico": 0, "atencao": 1, "info": 2}
    return sorted(alertas, key=lambda item: (ordem.get(item["severidade"], 9), item["titulo"]))
