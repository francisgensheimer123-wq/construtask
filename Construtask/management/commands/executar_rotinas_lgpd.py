from django.core.management.base import BaseCommand

from Construtask.services_lgpd import executar_rotinas_anonimizacao, obter_resumo_rotinas_lgpd


class Command(BaseCommand):
    help = "Executa rotinas seguras de anonimização LGPD para cadastros inativos."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Aplica efetivamente as rotinas.")

    def handle(self, *args, **options):
        if not options["apply"]:
            resumo = obter_resumo_rotinas_lgpd()
            self.stdout.write(self.style.WARNING("Modo simulacao: nenhuma alteracao foi aplicada."))
            self.stdout.write(f"Fornecedores inativos elegiveis: {resumo['fornecedores_inativos']}")
            self.stdout.write(f"Usuarios inativos elegiveis: {resumo['usuarios_inativos']}")
            self.stdout.write("Use --apply para executar as rotinas.")
            return

        resultado = executar_rotinas_anonimizacao()
        self.stdout.write(self.style.SUCCESS("Rotinas LGPD executadas com sucesso."))
        self.stdout.write(f"Fornecedores anonimizados: {resultado['fornecedores_anonimizados']}")
        self.stdout.write(f"Usuarios anonimizados: {resultado['usuarios_anonimizados']}")
