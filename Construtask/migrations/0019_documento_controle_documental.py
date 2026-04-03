# Generated migration for ISO 7.5 Document Control
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('Construtask', '0018_criar_usuario_empresa_admin'),
    ]

    operations = [
        migrations.CreateModel(
            name='Documento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('processo', models.CharField(blank=True, help_text='Processo/Atividade ISO relacionado', max_length=100)),
                ('tipo_documento', models.CharField(choices=[('PROCEDIMENTO', 'Procedimento'), ('INSTRUCAO', 'Instrução de Trabalho'), ('REGISTRO', 'Registro de Qualidade'), ('MANUAL', 'Manual'), ('POLITICA', 'Política'), ('ROTEIRO', 'Roteiro/Checklist'), ('FORMULARIO', 'Formulário'), ('OUTRO', 'Outro')], max_length=20)),
                ('codigo_documento', models.CharField(help_text='Código único do documento', max_length=30)),
                ('titulo', models.CharField(max_length=255)),
                ('status', models.CharField(choices=[('RASCUNHO', 'Rascunho'), ('EM_REVISAO', 'Em Revisão'), ('APROVADO', 'Aprovado'), ('OBSOLETO', 'Obsoleto')], default='RASCUNHO', max_length=20)),
                ('versao_atual', models.PositiveIntegerField(default=1)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='documentos', to='Construtask.empresa')),
                ('obra', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='documentos', to='Construtask.obra')),
                ('plano_contas', models.ForeignKey(blank=True, help_text='Vincular à EAP nível 5', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='documentos', to='Construtask.planocontas')),
                ('criado_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='documentos_criados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Documento Controlado',
                'verbose_name_plural': 'Documentos Controlados',
                'ordering': ['-criado_em'],
            },
        ),
        migrations.AddIndex(
            model_name='documento',
            index=models.Index(fields=['empresa', 'status'], name='documento_empresa_status_idx'),
        ),
        migrations.AddIndex(
            model_name='documento',
            index=models.Index(fields=['codigo_documento'], name='documento_codigo_idx'),
        ),
        migrations.CreateModel(
            name='DocumentoRevisao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('versao', models.PositiveIntegerField()),
                ('arquivo', models.FileField(help_text='Arquivo do documento (PDF, DOCX)', upload_to='documentos/%Y/%m')),
                ('checksum', models.CharField(blank=True, help_text='Hash SHA-256 do arquivo para integridade', max_length=64)),
                ('status', models.CharField(choices=[('ELABORACAO', 'Em Elaboração'), ('REVISAO', 'Em Revisão'), ('APROVADO', 'Aprovado')], default='ELABORACAO', max_length=20)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('data_revisao', models.DateTimeField(blank=True, null=True)),
                ('data_aprovacao', models.DateTimeField(blank=True, null=True)),
                ('parecer', models.TextField(blank=True, help_text='Parecer sobre a revisão')),
                ('arquivo_aprovado', models.FileField(blank=True, help_text='Cópia imutável do arquivo aprovado', upload_to='documentos/aprovados/%Y/%m')),
                ('documento', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='revisoes', to='Construtask.documento')),
                ('criado_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='revisoes_criadas', to=settings.AUTH_USER_MODEL)),
                ('revisor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='revisoes_revisadas', to=settings.AUTH_USER_MODEL)),
                ('aprobador', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='documento_revisoes_aprovadas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Revisão de Documento',
                'verbose_name_plural': 'Revisões de Documentos',
                'ordering': ['-versao'],
            },
        ),
        migrations.AddIndex(
            model_name='documentorevisao',
            index=models.Index(fields=['documento', 'status'], name='revisao_doc_status_idx'),
        ),
        migrations.AddConstraint(
            model_name='documentorevisao',
            constraint=models.UniqueConstraint(fields=('documento', 'versao'), name='uq_documento_versao'),
        ),
    ]
