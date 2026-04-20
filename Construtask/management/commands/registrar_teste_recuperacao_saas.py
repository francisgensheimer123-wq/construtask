from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ...models import OperacaoBackupSaaS

User = get_user_model()


class Command(BaseCommand):
    help = "Registra um teste de recuperacao SaaS associado a um backup existente."

    def add_arguments(self, parser):
        parser.add_argument("--backup-id", type=int, required=True)
        parser.add_argument("--status", default="SUCESSO", choices=["SUCESSO", "FALHOU", "PARCIAL"])
        parser.add_argument("--observacao", default="")
        parser.add_argument("--usuario", default="")
        parser.add_argument("--executado-em", default="")

    def handle(self, *args, **options):
        try:
            backup = OperacaoBackupSaaS.objects.get(pk=options["backup_id"], tipo="BACKUP")
        except OperacaoBackupSaaS.DoesNotExist as exc:
            raise CommandError("Backup de referencia nao encontrado.") from exc

        usuario = None
        if options["usuario"]:
            try:
                usuario = User.objects.get(username=options["usuario"])
            except User.DoesNotExist as exc:
                raise CommandError("Usuario informado para auditoria do teste nao encontrado.") from exc

        executado_em = timezone.now()
        if options["executado_em"]:
            try:
                executado_em = timezone.datetime.fromisoformat(options["executado_em"])
                if timezone.is_naive(executado_em):
                    executado_em = timezone.make_aware(executado_em, timezone.get_current_timezone())
            except ValueError as exc:
                raise CommandError("Use --executado-em em ISO-8601 valido.") from exc

        operacao = OperacaoBackupSaaS.objects.create(
            tipo="TESTE_RESTAURACAO",
            status=options["status"],
            ambiente=getattr(settings, "CONSTRUTASK_ENVIRONMENT", "development"),
            provedor=backup.provedor,
            identificador_artefato=backup.identificador_artefato,
            checksum=backup.checksum,
            tamanho_bytes=backup.tamanho_bytes,
            observacao=options["observacao"],
            solicitado_por=usuario,
            backup_referencia=backup,
            executado_em=executado_em,
            detalhes={"backup_id": backup.pk},
        )
        self.stdout.write(self.style.SUCCESS(f"Teste de recuperacao registrado com id {operacao.pk}."))
