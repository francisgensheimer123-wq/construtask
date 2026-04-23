# Operacao SaaS em Producao

Este runbook consolida os controles minimos exigidos para operar o Construtask como SaaS em producao com seguranca e previsibilidade operacional.

## Boot e deploy

- O processo `web` nao executa migracoes nem cria superusuarios automaticamente.
- O processo `release` executa `python manage.py check --deploy` antes das migracoes.
- O processo `web` sobe apenas a aplicacao HTTP via Gunicorn.
- O processo `worker` executa Celery worker.
- O processo `worker2` executa Celery beat.
- No Railway, o `Build Command` deve permanecer apenas com `collectstatic`; migracoes dependem da rede interna do runtime e nao devem rodar no build.
- O bootstrap de runtime do `web` executa `check --deploy` e `migrate --noinput` quando o ambiente produtivo estiver ativo.

## Variaveis obrigatorias em producao

- `CONSTRUTASK_ENVIRONMENT=production`
- `DJANGO_SECRET_KEY` ou `SECRET_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `CONSTRUTASK_ADMIN_URL`
- `CONSTRUTASK_BACKUP_ENABLED`
- `CONSTRUTASK_BACKUP_PROVIDER`
- `CONSTRUTASK_BACKUP_RETENTION_DAYS`
- `CONSTRUTASK_BACKUP_INTERVAL_HOURS`

## Controles de seguranca

- O lockout de login depende do cache critico compartilhado em Redis.
- O cache critico nunca deve usar `LocMemCache` em producao.
- O admin deve operar em URL customizada, nunca em `/admin/`.
- O ambiente deve usar `SECURE_SSL_REDIRECT`, cookies `Secure` e HSTS ativo.

## Confiabilidade operacional

- `DATABASES["default"]["CONN_HEALTH_CHECKS"]` deve permanecer habilitado.
- Falhas definitivas de sincronizacao de alertas devem gerar `JobAssincrono` com status `FALHOU`.
- Falhas definitivas de backup devem gerar `OperacaoBackupSaaS` com status `FALHOU`.
- O scheduler de backup depende do Celery beat em execucao.

## Backup e recuperacao

- O backup automatico deve estar habilitado com provedor configurado.
- Deve existir ao menos um backup recente dentro da janela operacional esperada.
- Deve existir teste de recuperacao bem-sucedido dentro da janela definida por `CONSTRUTASK_RECOVERY_TEST_INTERVAL_DAYS`.
- Falhas de backup ou restauracao devem ser acompanhadas por alerta operacional.

## Checklist de go-live

1. Executar `python manage.py check --deploy`.
2. Executar `python manage.py validar_prontidao_producao --json`.
3. Confirmar `web`, `worker` e `worker2` saudaveis.
4. Confirmar conectividade com PostgreSQL e Redis.
5. Confirmar storage persistente e rotina de backup.
6. Registrar um teste de recuperacao antes da entrada de clientes.

## Checklist operacional recorrente

- Revisar falhas em `JobAssincrono` e `OperacaoBackupSaaS`.
- Confirmar execucoes do Celery beat.
- Confirmar backup dentro da janela e ultimo teste de recuperacao valido.
- Revisar logs de seguranca e eventos Sentry.
