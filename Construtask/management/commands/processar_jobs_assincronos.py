from django.core.management.base import BaseCommand

from ...services_jobs import processar_jobs_pendentes


class Command(BaseCommand):
    help = "Processa jobs assincronos pendentes do Construtask."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=10)

    def handle(self, *args, **options):
        jobs = processar_jobs_pendentes(limite=options["limite"])
        self.stdout.write(self.style.SUCCESS(f"{len(jobs)} job(s) processado(s)."))
