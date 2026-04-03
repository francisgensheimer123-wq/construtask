# -*- coding: utf-8 -*-
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'setup.settings')
django.setup()

from django.contrib.auth import get_user_model
from Construtask.models import Empresa, UsuarioEmpresa, Obra

User = get_user_model()

print("=" * 50)
print("CRIANDO USUARIOS DE TESTE")
print("=" * 50)

# Empresa de teste
empresa_teste, created = Empresa.objects.get_or_create(
    cnpj='12.345.678/0001-90',
    defaults={
        'nome': 'Empresa de Teste LTDA',
        'nome_fantasia': 'Empresa Teste',
        'email': 'teste@empresa.com',
        'telefone': '(11) 99999-9999',
        'ativo': True,
    }
)
print("[OK] Empresa de teste: %s" % empresa_teste.nome)

# Obter obras da empresa padrao
obras_disponiveis = list(Obra.objects.filter(empresa__cnpj='00.000.000/0000-00')[:3])

# Criar usuario Admin da Empresa
admin_empresa, created = User.objects.get_or_create(
    username='admin_empresa',
    defaults={
        'email': 'admin@empresateste.com',
        'is_staff': True,
        'is_active': True,
    }
)
if created:
    admin_empresa.set_password('admin123')
    admin_empresa.save()
    print("[OK] Admin da empresa criado: admin_empresa / admin123")

# Criar vinculo UsuarioEmpresa para o admin
ue_admin, created = UsuarioEmpresa.objects.get_or_create(
    usuario=admin_empresa,
    empresa=empresa_teste,
    defaults={'is_admin_empresa': True}
)
print("[OK] UsuarioEmpresa criado para admin_empresa (is_admin=True)")

# Criar usuario Comum
usuario_comum, created = User.objects.get_or_create(
    username='operador',
    defaults={
        'email': 'operador@empresateste.com',
        'is_staff': True,
        'is_active': True,
    }
)
if created:
    usuario_comum.set_password('operador123')
    usuario_comum.save()
    print("[OK] Usuario comum criado: operador / operador123")

# Criar vinculo UsuarioEmpresa para o usuario comum
ue_comum, created = UsuarioEmpresa.objects.get_or_create(
    usuario=usuario_comum,
    empresa=empresa_teste,
    defaults={'is_admin_empresa': False}
)
if created:
    if obras_disponiveis:
        ue_comum.obras_permitidas.set(obras_disponiveis)
        print("[OK] UsuarioEmpresa criado para operador (com %d obras liberadas)" % len(obras_disponiveis))
    else:
        print("[OK] UsuarioEmpresa criado para operador (sem obras liberadas)")

# Criar outro usuario comum
usuario_novo, created = User.objects.get_or_create(
    username='novo_usuario',
    defaults={
        'email': 'novo@empresateste.com',
        'is_staff': True,
        'is_active': True,
    }
)
if created:
    usuario_novo.set_password('novo123')
    usuario_novo.save()
    print("[OK] Novo usuario criado: novo_usuario / novo123")

# Criar vinculo
ue_novo, created = UsuarioEmpresa.objects.get_or_create(
    usuario=usuario_novo,
    empresa=empresa_teste,
    defaults={'is_admin_empresa': False}
)
print("[OK] UsuarioEmpresa criado para novo_usuario")

print("")
print("=" * 50)
print("USUARIOS DE TESTE CRIADOS:")
print("=" * 50)
print("| Usuario       | Senha       | Tipo                |")
print("|---------------|-------------|---------------------|")
print("| admin_empresa | admin123    | Admin da Empresa    |")
print("| operador      | operador123 | Usuario Comum       |")
print("| novo_usuario  | novo123     | Usuario Sem Obras    |")
print("=" * 50)
