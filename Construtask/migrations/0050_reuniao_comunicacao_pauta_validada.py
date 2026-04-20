from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Construtask", "0049_modulo_comunicacoes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="reuniaocomunicacao",
            name="status",
            field=models.CharField(
                choices=[
                    ("RASCUNHO", "Rascunho"),
                    ("PAUTA_VALIDADA", "Pauta Validada"),
                    ("EM_APROVACAO", "Em Aprovacao"),
                    ("APROVADA", "Aprovada"),
                ],
                default="RASCUNHO",
                max_length=20,
            ),
        ),
    ]
