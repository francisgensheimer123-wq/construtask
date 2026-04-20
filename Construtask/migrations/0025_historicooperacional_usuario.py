from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Construtask", "0024_compromissoitem_descricao_tecnica_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="historicooperacional",
            name="usuario",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="historicos_operacionais",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
