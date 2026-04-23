#!/usr/bin/env python
"""
Backup do PostgreSQL para Cloudflare R2.
Executar via: python manage.py executar_backup_r2
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime

import boto3
from botocore.config import Config

# ── Configuração via variáveis de ambiente ─────────────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"]
STORAGE_OPTIONS = json.loads(os.environ["DJANGO_MEDIA_STORAGE_OPTIONS_JSON"])

BUCKET_NAME    = STORAGE_OPTIONS["bucket_name"]
ENDPOINT_URL   = STORAGE_OPTIONS["endpoint_url"]
ACCESS_KEY     = STORAGE_OPTIONS["access_key"]
SECRET_KEY     = STORAGE_OPTIONS["secret_key"]
REGION_NAME    = STORAGE_OPTIONS.get("region_name", "auto")

RETENTION_DAYS = int(os.environ.get("CONSTRUTASK_BACKUP_RETENTION_DAYS", "30"))
ENVIRONMENT    = os.environ.get("CONSTRUTASK_ENVIRONMENT", "production")

# Prefixo separado dos arquivos de media da aplicação
BACKUP_PREFIX = "backups/postgres/"

# ── Helpers ────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[backup] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def checksum_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_database_url(url):
    """Extrai componentes do DATABASE_URL para pg_dump."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        "host":     parsed.hostname,
        "port":     str(parsed.port or 5432),
        "user":     parsed.username,
        "password": parsed.password,
        "dbname":   parsed.path.lstrip("/"),
    }


def build_r2_client():
    # R2 exige endpoint sem protocolo no URL — garantir https://
    endpoint = ENDPOINT_URL
    if not endpoint.startswith("http"):
        endpoint = f"https://{endpoint}"

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name=REGION_NAME,
        config=Config(signature_version="s3v4"),
    )


# ── Execução principal ─────────────────────────────────────────────────────
def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"construtask_{ENVIRONMENT}_{timestamp}.dump"
    key       = f"{BACKUP_PREFIX}{filename}"

    db = parse_database_url(DATABASE_URL)
    log(f"Iniciando pg_dump → {filename}")

    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        env = os.environ.copy()
        env["PGPASSWORD"] = db["password"]

        result = subprocess.run(
            [
                "pg_dump",
                "-h", db["host"],
                "-p", db["port"],
                "-U", db["user"],
                "-d", db["dbname"],
                "-F", "c",          # formato custom — comprimido e restaurável com pg_restore
                "-f", tmp_path,
            ],
            env=env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            log(f"ERRO pg_dump: {result.stderr}")
            sys.exit(1)

        size_bytes = os.path.getsize(tmp_path)
        checksum   = checksum_sha256(tmp_path)
        log(f"pg_dump OK — {size_bytes / 1024 / 1024:.2f} MB — sha256: {checksum[:16]}...")

        # ── Upload para R2 ─────────────────────────────────────────────
        log(f"Enviando para R2: {BUCKET_NAME}/{key}")
        s3 = build_r2_client()

        with open(tmp_path, "rb") as f:
            s3.upload_fileobj(
                f,
                BUCKET_NAME,
                key,
                ExtraArgs={
                    "Metadata": {
                        "environment": ENVIRONMENT,
                        "timestamp":   timestamp,
                        "checksum":    checksum,
                    }
                },
            )
        log("Upload concluído.")

        # ── Registrar no banco via management command ──────────────────
        log("Registrando operação no banco...")
        reg = subprocess.run(
            [
                sys.executable, "manage.py", "registrar_backup_saas",
                "--provedor",       "cloudflare-r2",
                "--status",         "SUCESSO",
                "--artefato",       key,
                "--checksum",       checksum,
                "--tamanho-bytes",  str(size_bytes),
                "--observacao",     f"pg_dump custom format — {filename}",
            ],
            capture_output=True,
            text=True,
        )
        print(reg.stdout.strip())
        if reg.returncode != 0:
            log(f"AVISO: registrar_backup_saas falhou: {reg.stderr}")
        else:
            log("Registro no banco OK.")

        # ── Limpeza de backups antigos no R2 ──────────────────────────
        _limpar_backups_antigos(s3, timestamp)

        log("Backup finalizado com sucesso.")
        return 0

    except Exception as exc:
        # Registrar falha no banco para o dashboard não ficar verde indevidamente
        subprocess.run(
            [
                sys.executable, "manage.py", "registrar_backup_saas",
                "--provedor", "cloudflare-r2",
                "--status",   "FALHOU",
                "--observacao", str(exc)[:200],
            ],
            capture_output=True,
        )
        log(f"ERRO: {exc}")
        sys.exit(1)

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _limpar_backups_antigos(s3, timestamp_atual):
    """Remove arquivos com mais de RETENTION_DAYS dias do prefixo de backup."""
    from datetime import timezone as tz
    from datetime import timedelta

    limite = datetime.now(tz=tz.utc) - timedelta(days=RETENTION_DAYS)
    log(f"Verificando backups anteriores a {limite.strftime('%Y-%m-%d')} para remoção...")

    paginator = s3.get_paginator("list_objects_v2")
    removidos = 0
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=BACKUP_PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(timestamp_atual + ".dump"):
                continue  # nunca remove o que acabou de fazer
            if obj["LastModified"] < limite:
                s3.delete_object(Bucket=BUCKET_NAME, Key=obj["Key"])
                log(f"  Removido: {obj['Key']}")
                removidos += 1

    log(f"Limpeza: {removidos} arquivo(s) removido(s).")


if __name__ == "__main__":
    sys.exit(main())
