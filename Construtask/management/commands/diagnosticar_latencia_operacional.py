import json

from django.core.management.base import BaseCommand

from ...application.observabilidade import diagnostico_latencia_operacional
from ...models import Obra


class Command(BaseCommand):
    help = "Consolida os endpoints mais lentos do contexto atual para diagnostico operacional."

    def add_arguments(self, parser):
        parser.add_argument("--obra-id", type=int, default=None, help="Filtra o diagnostico para uma obra especifica.")
        parser.add_argument("--limite", type=int, default=10, help="Quantidade maxima de endpoints no resumo.")
        parser.add_argument("--json", action="store_true", help="Retorna o resultado em JSON.")

    def handle(self, *args, **options):
        obra = None
        obra_id = options["obra_id"]
        if obra_id:
            obra = Obra.objects.filter(pk=obra_id).first()
            if obra is None:
                self.stderr.write(self.style.ERROR("Obra informada nao encontrada."))
                return

        resultado = diagnostico_latencia_operacional(obra=obra, limite=options["limite"])
        if options["json"]:
            self.stdout.write(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
            return

        resumo = resultado["resumo_metricas"]
        self.stdout.write(
            f"Latencia operacional: media={resumo['media_ms']:.2f}ms | requests={resumo['total']} | lentas={resumo['lentas']}"
        )
        for endpoint in resultado["endpoints_lentos"]:
            self.stdout.write(
                f"- {endpoint['metodo']} {endpoint['path']} | media={endpoint['media_ms'] or 0:.2f}ms | "
                f"pico={endpoint['pico_ms'] or 0:.2f}ms | total={endpoint['total']}"
            )
