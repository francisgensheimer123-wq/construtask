# Generated migration for ISO 6.1 - Gestão de Riscos

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('Construtask', '0020_remove_documentorevisao_uq_documento_versao_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Risco',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('processo', models.CharField(blank=True, help_text='Processo/Atividade relacionado', max_length=100)),
                ('categoria', models.CharField(choices=[('TECNICO', 'Técnico'), ('AMBIENTAL', 'Ambiental'), ('SEGURANCA', 'Segurança do Trabalho'), ('FINANCEIRO', 'Financeiro'), ('PRAZO', 'Prazo'), ('QUALIDADE', 'Qualidade'), ('FORNECEDOR', 'Fornecedor'), ('REGULATORIO', 'Regulatório'), ('OUTRO', 'Outro')], max_length=20)),
                ('titulo', models.CharField(max_length=200)),
                ('descricao', models.TextField(verbose_name='Descrição detalhada do risco')),
                ('causa', models.TextField(blank=True, verbose_name='Causa raiz identificada')),
                ('probabilidade', models.PositiveSmallIntegerField(choices=[(1, '1 - Rara'), (2, '2 - Improvável'), (3, '3 - Possível'), (4, '4 - Provável'), (5, '5 - Quase Certa')], help_text='1=Rara a 5=Quase Certa')),
                ('impacto', models.PositiveSmallIntegerField(choices=[(1, '1 - Insignificante'), (2, '2 - Menor'), (3, '3 - Moderado'), (4, '4 - Maior'), (5, '5 - Catastrófico')], help_text='1=Insignificante a 5=Catastrófico')),
                ('nivel', models.PositiveSmallIntegerField(blank=True, editable=False, help_text='Calculado: Probabilidade × Impacto')),
                ('plano_resposta', models.TextField(blank=True, help_text='Estratégia e ações para mitigar o risco', verbose_name='Plano de ação de resposta')),
                ('data_meta_tratamento', models.DateField(blank=True, help_text='Data meta para tratamento do risco', null=True)),
                ('status', models.CharField(choices=[('IDENTIFICADO', 'Identificado'), ('EM_ANALISE', 'Em Análise'), ('EM_TRATAMENTO', 'Em Tratamento'), ('MITIGADO', 'Mitigado'), ('FECHADO', 'Fechado'), ('CANCELADO', 'Cancelado')], default='IDENTIFICADO', max_length=20)),
                ('data_fechamento', models.DateField(blank=True, null=True)),
                ('observacoes', models.TextField(blank=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='riscos', to='Construtask.empresa')),
                ('obra', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='riscos', to='Construtask.obra')),
                ('plano_contas', models.ForeignKey(blank=True, help_text='EAP nível 5 associado ao risco (opcional)', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='riscos', to='Construtask.planocontas')),
                ('responsavel', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='riscos_responsaveis', to=settings.AUTH_USER_MODEL)),
                ('criado_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='riscos_criados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Risco',
                'verbose_name_plural': 'Riscos',
                'ordering': ['-criado_em'],
            },
        ),
        migrations.AddIndex(
            model_name='risco',
            index=models.Index(fields=['empresa', 'obra', 'status'], name='risco_empresa_status_idx'),
        ),
        migrations.AddIndex(
            model_name='risco',
            index=models.Index(fields=['nivel'], name='risco_nivel_idx'),
        ),
        migrations.CreateModel(
            name='RiscoHistorico',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('acao', models.CharField(choices=[('CRIACAO', 'Criação'), ('ALTERACAO', 'Alteração'), ('STATUS', 'Mudança de Status'), ('TRATAMENTO', 'Plano de Tratamento'), ('FECHAMENTO', 'Fechamento'), ('REABERTURA', 'Reabertura')], max_length=20)),
                ('dados_anteriores', models.JSONField(blank=True, null=True)),
                ('dados_novos', models.JSONField(blank=True, null=True)),
                ('observacao', models.TextField(blank=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('risco', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='historico', to='Construtask.risco')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Histórico de Risco',
                'verbose_name_plural': 'Históricos de Riscos',
                'ordering': ['-timestamp'],
            },
        ),
    ]