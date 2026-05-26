from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Construtask", "0057_alter_alertaoperacionalhistorico_options_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="documento",
            constraint=models.UniqueConstraint(
                fields=("empresa", "obra", "codigo_documento"),
                name="uq_documento_codigo_por_obra",
            ),
        ),
    ]
