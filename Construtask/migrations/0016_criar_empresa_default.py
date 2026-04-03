"""
Migração de dados para criar empresa padrão e fazer backfill.
"""
from django.db import migrations


def criar_empresa_default(apps, schema_editor):
    """Cria empresa padrão e faz backfill nas obras existentes."""
    Empresa = apps.get_model('Construtask', 'Empresa')
    Obra = apps.get_model('Construtask', 'Obra')
    
    # Verificar se já existe empresa padrão
    empresa, created = Empresa.objects.get_or_create(
        cnpj='00.000.000/0000-00',
        defaults={
            'nome': 'Empresa Padrão',
            'nome_fantasia': 'Empresa Padrão',
            'ativo': True,
        }
    )
    
    if created:
        print(f"Empresa padrão criada: {empresa.nome}")
    else:
        print(f"Empresa padrão já existe: {empresa.nome}")
    
    # Backfill: associar todas as obras existentes à empresa padrão
    obras_sem_empresa = Obra.objects.filter(empresa__isnull=True)
    count = obras_sem_empresa.update(empresa=empresa)
    print(f"Obras associadas à empresa padrão: {count}")


def reverter_empresa_default(apps, schema_editor):
    """Remove empresa das obras (não deleta a empresa)."""
    Obra = apps.get_model('Construtask', 'Obra')
    Obra.objects.update(empresa=None)


class Migration(migrations.Migration):
    dependencies = [
        ('Construtask', '0015_empresa_obra_empresa_userprofile_auditevent'),
    ]
    
    operations = [
        migrations.RunPython(criar_empresa_default, reverter_empresa_default),
    ]
