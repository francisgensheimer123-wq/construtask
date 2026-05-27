from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Construtask", "0058_documento_codigo_unico_por_obra"),
    ]

    operations = [
        migrations.AlterField(
            model_name="planofisicoitem",
            name="percentual_concluido",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Percentual realizado informado para a atividade (0-100)",
                max_digits=5,
            ),
        ),
    ]
