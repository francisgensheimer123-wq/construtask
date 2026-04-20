from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ...models import OperacaoBackupSaaS

User = get_user_model()


class Command(BaseCommand):
    help = "Registra uma execucao de backup SaaS com evidencias de operacao."

    def add_arguments(self, parser):
        parser.add_argument("--provedor", default=getattr(settings, "CONSTRUTASK_BACKUP_PROVIDER", ""))
        parser.add_argument("--status", default="SUCESSO", choices=["SUCESSO", "FALHOU", "PARCIAL"])
        parser.add_argument("--artefato", default="")
        parser.add_argument("--checksum", default="")
        parser.add_argument("--tamanho-bytes", type=int, default=0)
        parser.add_argument("--observacao", default="")
        parser.add_argument("--usuario", default="")
        parser.add_argument("--executado-em", default="")

    def handle(self, *args, **options):
        usuario = None
        if options["usuario"]:
            try:
                usuario = User.objects.get(username=options["usuario"])
            except User.DoesNotExist as exc:
                raise CommandError("Usuario informado para auditoria do backup nao encontrado.") from exc

        executado_em = timezone.now()
        if options["executado_em"]:
            try:
                executado_em = timezone.datetime.fromisoformat(options["executado_em"])
                if timezone.is_naive(executado_em):
                    executado_em = timezone.make_aware(executado_em, timezone.get_current_timezone())
            except ValueError as exc:
                raise CommandError("Use --executado-em em ISO-8601 valido.") from exc

        operacao = OperacaoBackupSaaS.objects.create(
            tipo="BACKUP",
            status=options["status"],
            ambiente=getattr(settings, "CONSTRUTASK_ENVIRONMENT", "development"),
            provedor=options["provedor"],
            identificador_artefato=options["artefato"],
            checksum=options["checksum"],
            tamanho_bytes=options["tamanho_bytes"],
            observacao=options["observacao"],
            solicitado_por=usuario,
            executado_em=executado_em,
            detalhes={
                "retencao_dias": getattr(settings, "CONSTRUTASK_BACKUP_RETENTION_DAYS", 0),
                "intervalo_horas": getattr(settings, "CONSTRUTASK_BACKUP_INTERVAL_HOURS", 24),
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Backup SaaS registrado com id {operacao.pk}."))
