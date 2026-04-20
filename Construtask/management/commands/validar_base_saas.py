import json

from django.core.management.base import BaseCommand

from ...application.saas import diagnostico_base_saas


class Command(BaseCommand):
    help = "Valida a base operacional SaaS e imprime o diagnostico atual."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json", help="Retorna a saida em JSON.")

    def handle(self, *args, **options):
        diagnostico = diagnostico_base_saas()
        if options["as_json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "status": diagnostico["status"],
                        "ambiente": diagnostico["ambiente"],
                        "checks": diagnostico["checks"],
                    },
                    ensure_ascii=True,
                    default=str,
                    indent=2,
                )
            )
            return

        self.stdout.write(self.style.NOTICE(f"Base SaaS: {diagnostico['status'].upper()} ({diagnostico['ambiente']})"))
        for codigo, item in diagnostico["checks"].items():
            self.stdout.write(f"- {codigo}: {item['status'].upper()} | {item['detalhe']}")
