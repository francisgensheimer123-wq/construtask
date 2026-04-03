"""
Migração de dados para criar UsuarioEmpresa para superusers existentes.
"""
from django.db import migrations


def criar_usuario_empresa_superusers(apps, schema_editor):
    """Cria UsuarioEmpresa para todos os superusers existentes."""
    UsuarioEmpresa = apps.get_model('Construtask', 'UsuarioEmpresa')
    Empresa = apps.get_model('Construtask', 'Empresa')
    User = apps.get_model('auth', 'User')
    
    # Buscar empresa padrão
    empresa_padrao = Empresa.objects.filter(cnpj='00.000.000/0000-00').first()
    if not empresa_padrao:
        print("Empresa padrão não encontrada, pulando migração de UsuarioEmpresa")
        return
    
    # Para cada superuser, criar UsuarioEmpresa
    for user in User.objects.filter(is_superuser=True):
        _, created = UsuarioEmpresa.objects.get_or_create(
            usuario=user,
            empresa=empresa_padrao,
            defaults={
                'is_admin_empresa': True,
            }
        )
        if created:
            print(f"UsuarioEmpresa criado para superuser: {user.username}")


def reverter_usuario_empresa_superusers(apps, schema_editor):
    """Remove UsuarioEmpresa de superusers."""
    UsuarioEmpresa = apps.get_model('Construtask', 'UsuarioEmpresa')
    UsuarioEmpresa.objects.filter(is_admin_empresa=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('Construtask', '0017_usuarioempresa'),
    ]
    
    operations = [
        migrations.RunPython(criar_usuario_empresa_superusers, reverter_usuario_empresa_superusers),
    ]
