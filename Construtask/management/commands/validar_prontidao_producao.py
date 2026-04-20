import json

from django.core.checks import run_checks
from django.core.management.base import BaseCommand

from ...observability import health_status, readiness_status


class Command(BaseCommand):
    help = "Valida a prontidao operacional da aplicacao para producao."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json", help="Retorna o diagnostico em JSON.")

    def handle(self, *args, **options):
        health = health_status()
        readiness = readiness_status()
        deploy_checks = run_checks(include_deployment_checks=True)
        payload = {
            "health": health,
            "readiness": readiness,
            "deploy_checks": [
                {
                    "id": item.id,
                    "level": item.__class__.__name__,
                    "message": item.msg,
                }
                for item in deploy_checks
            ],
            "status": "ok" if not deploy_checks and readiness["status"] == "ok" and health["status"] == "ok" else "error",
        }

        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=True, default=str, indent=2))
            return

        self.stdout.write(self.style.NOTICE(f"Prontidao de producao: {payload['status'].upper()}"))
        self.stdout.write(f"- health: {health['status'].upper()}")
        self.stdout.write(f"- readiness: {readiness['status'].upper()}")
        if payload["deploy_checks"]:
            self.stdout.write("- checks de deploy:")
            for item in payload["deploy_checks"]:
                self.stdout.write(f"  * {item['id']} [{item['level']}] {item['message']}")
        else:
            self.stdout.write("- checks de deploy: OK")
