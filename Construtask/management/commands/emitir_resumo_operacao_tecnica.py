import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory

from Construtask.application.operacao import contexto_operacao_request


class Command(BaseCommand):
    help = "Emite um resumo tecnico consolidado da operacao para acompanhamento."

    def add_arguments(self, parser):
        parser.add_argument("--usuario", help="Username para contextualizar o resumo.")
        parser.add_argument("--json", action="store_true", help="Retorna o resumo em JSON.")

    def handle(self, *args, **options):
        username = options.get("usuario")
        if not username:
            raise CommandError("Informe --usuario para montar o contexto tecnico da operacao.")

        user = get_user_model().objects.filter(username=username).first()
        if not user:
            raise CommandError(f"Usuario '{username}' nao encontrado.")

        request = RequestFactory().get("/operacao-tecnica/")
        request.user = user
        request.session = {}
        contexto = contexto_operacao_request(request)
        payload = {
            "status": "ok",
            "alertas": contexto["alertas_operacionais_tecnicos"],
            "resumo_metricas": contexto["resumo_metricas"],
            "resumo_erros": contexto["resumo_erros"],
            "resumo_jobs": contexto["resumo_jobs"],
            "saas": contexto["diagnostico_saas"]["status"],
        }
        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
            return

        self.stdout.write(self.style.SUCCESS("Resumo tecnico da operacao"))
        self.stdout.write(f"Status SaaS: {payload['saas']}")
        self.stdout.write(
            f"Jobs pendentes: {payload['resumo_jobs']['pendentes']} | "
            f"falharam: {payload['resumo_jobs']['falharam']}"
        )
        self.stdout.write(
            f"Requests: {payload['resumo_metricas']['total']} | "
            f"erros 500: {payload['resumo_metricas']['erros_500']}"
        )
        self.stdout.write(f"Alertas tecnicos: {len(payload['alertas'])}")
