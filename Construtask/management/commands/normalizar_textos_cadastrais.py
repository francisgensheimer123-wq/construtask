from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

from Construtask.text_normalization import TEXT_NORMALIZATION_TARGETS, normalizar_texto_cadastral


class Command(BaseCommand):
    help = "Normaliza textos cadastrais já gravados no banco."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persiste as alterações no banco.")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        total_fields = 0
        total_objects = 0

        with transaction.atomic():
            for label, fields in TEXT_NORMALIZATION_TARGETS.items():
                model = apps.get_model(label)
                changed_objects = 0
                changed_fields = 0
                for obj in model.objects.all().iterator():
                    updated_fields = []
                    for field_name in fields:
                        current = getattr(obj, field_name, None)
                        if not isinstance(current, str) or current == "":
                            continue
                        normalized = normalizar_texto_cadastral(current)
                        if normalized != current:
                            setattr(obj, field_name, normalized)
                            updated_fields.append(field_name)
                    if updated_fields:
                        changed_objects += 1
                        changed_fields += len(updated_fields)
                        if apply_changes:
                            obj.save(update_fields=updated_fields)
                if changed_objects:
                    self.stdout.write(f"{label}: {changed_objects} registros, {changed_fields} campos ajustados")
                    total_objects += changed_objects
                    total_fields += changed_fields
            if not apply_changes:
                transaction.set_rollback(True)

        action = "Aplicado" if apply_changes else "Simulado"
        self.stdout.write(self.style.SUCCESS(f"{action}: {total_objects} registros e {total_fields} campos normalizados"))
