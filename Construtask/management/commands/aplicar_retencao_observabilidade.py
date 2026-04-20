import json

from django.core.management.base import BaseCommand

from ...application.observabilidade import aplicar_retencao_observabilidade


class Command(BaseCommand):
    help = "Aplica a politica de retencao das metricas de requisicao e rastros de erro."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Apenas simula a retencao sem excluir registros.")
        parser.add_argument("--json", action="store_true", help="Retorna o resultado em JSON.")

    def handle(self, *args, **options):
        resultado = aplicar_retencao_observabilidade(dry_run=options["dry_run"])
        if options["json"]:
            self.stdout.write(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
            return

        self.stdout.write(f"Retencao observabilidade: {resultado['status']}")
        self.stdout.write(
            f"Metricas removidas: {resultado['metricas']['removidas']} | janela: {resultado['metricas']['retention_days']} dias"
        )
        self.stdout.write(
            f"Erros removidos: {resultado['erros']['removidos']} | janela: {resultado['erros']['retention_days']} dias"
        )
