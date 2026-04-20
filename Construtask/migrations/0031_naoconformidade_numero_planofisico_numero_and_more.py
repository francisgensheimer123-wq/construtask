from collections import defaultdict

from django.db import migrations, models


def _obter_ano(objeto, atributos_data):
    for atributo in atributos_data:
        valor = getattr(objeto, atributo, None)
        if valor:
            return getattr(valor, "year", None) or 0
    return 0


def _chave_ordenacao(objeto, atributos_data):
    valores = []
    for atributo in atributos_data:
        valor = getattr(objeto, atributo, None)
        if hasattr(valor, "isoformat"):
            valores.append(valor.isoformat())
        else:
            valores.append(str(valor or ""))
    valores.append(str(objeto.pk))
    return tuple(valores)


def _renumerar_documentos(apps, schema_editor):
    configuracoes = [
        ("Construtask", "Compromisso", "numero", "CTR-", ("data_assinatura", "criado_em")),
        ("Construtask", "Medicao", "numero_da_medicao", "MED-", ("data_medicao", "criado_em")),
        ("Construtask", "SolicitacaoCompra", "numero", "SC-", ("data_solicitacao", "criado_em")),
        ("Construtask", "Cotacao", "numero", "COT-", ("data_cotacao", "criado_em")),
        ("Construtask", "OrdemCompra", "numero", "OC-", ("data_emissao", "criado_em")),
        ("Construtask", "NaoConformidade", "numero", "NC-", ("data_abertura", "criado_em")),
        ("Construtask", "Risco", "codigo", "RIS-", ("criado_em",)),
        ("Construtask", "PlanoFisico", "numero", "CRN-", ("data_base", "data_importacao", "created_at")),
    ]

    for app_label, model_name, campo, prefixo, atributos_data in configuracoes:
        model = apps.get_model(app_label, model_name)
        objetos = list(model.objects.all().order_by("id"))
        grupos = defaultdict(list)
        for objeto in objetos:
            ano = _obter_ano(objeto, atributos_data)
            grupos[ano].append(objeto)

        for ano, itens in grupos.items():
            contador = 1
            for objeto in sorted(itens, key=lambda item: _chave_ordenacao(item, atributos_data)):
                valor = f"{prefixo}{ano:04d}-{contador:04d}" if ano else f"{prefixo}0000-{contador:04d}"
                setattr(objeto, campo, valor)
                objeto.save(update_fields=[campo])
                contador += 1


class Migration(migrations.Migration):

    dependencies = [
        ("Construtask", "0030_notafiscal_data_vencimento"),
    ]

    operations = [
        migrations.AddField(
            model_name="naoconformidade",
            name="numero",
            field=models.CharField(blank=True, editable=False, max_length=30, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="planofisico",
            name="numero",
            field=models.CharField(blank=True, editable=False, max_length=30, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="risco",
            name="codigo",
            field=models.CharField(blank=True, editable=False, max_length=30, null=True, unique=True),
        ),
        migrations.RunPython(_renumerar_documentos, migrations.RunPython.noop),
    ]
