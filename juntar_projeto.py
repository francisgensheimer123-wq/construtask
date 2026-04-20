"""
Script para unir todo o código Python do projeto Construtask em um único arquivo.
Executar: python juntar_projeto.py
"""

import os
from pathlib import Path

# Diretório do projeto
BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "construtask_completo.py"

# Arquivos Python na ordem de dependência
ARQUIVOS = [
    # Configuração
    ("setup/__init__.py", "setup/__init__.py"),
    ("setup/asgi.py", "setup/asgi.py"),
    ("setup/wsgi.py", "setup/wsgi.py"),
    ("setup/settings.py", "setup/settings.py"),
    ("setup/urls.py", "setup/urls.py"),
    
    # Módulos do app principal em ordem
    ("Construtask/__init__.py", "Construtask/__init__.py"),
    ("Construtask/apps.py", "Construtask/apps.py"),
    ("Construtask/domain.py", "Construtask/domain.py"),
    ("Construtask/text_normalization.py", "Construtask/text_normalization.py"),
    ("Construtask/models.py", "Construtask/models.py"),
    ("Construtask/models_aquisicoes.py", "Construtask/models_aquisicoes.py"),
    ("Construtask/models_qualidade.py", "Construtask/models_qualidade.py"),
    ("Construtask/models_risco.py", "Construtask/models_risco.py"),
    ("Construtask/models_planejamento.py", "Construtask/models_planejamento.py"),
    ("Construtask/permissions.py", "Construtask/permissions.py"),
    ("Construtask/mixins_tenant.py", "Construtask/mixins_tenant.py"),
    ("Construtask/auth_mixins.py", "Construtask/auth_mixins.py"),
    ("Construtask/context_processors.py", "Construtask/context_processors.py"),
    ("Construtask/auth_middleware.py", "Construtask/auth_middleware.py"),
    ("Construtask/audit.py", "Construtask/audit.py"),
    ("Construtask/audit_com_diff.py", "Construtask/audit_com_diff.py"),
    ("Construtask/signals.py", "Construtask/signals.py"),
    ("Construtask/services.py", "Construtask/services.py"),
    ("Construtask/services_aprovacao.py", "Construtask/services_aprovacao.py"),
    ("Construtask/services_aquisicoes.py", "Construtask/services_aquisicoes.py"),
    ("Construtask/services_qualidade.py", "Construtask/services_qualidade.py"),
    ("Construtask/services_eva.py", "Construtask/services_eva.py"),
    ("Construtask/services_indicadores.py", "Construtask/services_indicadores.py"),
    ("Construtask/services_integracao.py", "Construtask/services_integracao.py"),
    ("Construtask/services_tenant.py", "Construtask/services_tenant.py"),
    ("Construtask/importacao_cronograma.py", "Construtask/importacao_cronograma.py"),
    ("Construtask/correcoes_modelos.py", "Construtask/correcoes_modelos.py"),
    ("Construtask/forms.py", "Construtask/forms.py"),
    ("Construtask/views.py", "Construtask/views.py"),
    ("Construtask/views_documento.py", "Construtask/views_documento.py"),
    ("Construtask/views_qualidade.py", "Construtask/views_qualidade.py"),
    ("Construtask/views_risco.py", "Construtask/views_risco.py"),
    ("Construtask/views_planejamento.py", "Construtask/views_planejamento.py"),
    ("Construtask/views_aquisicoes.py", "Construtask/views_aquisicoes.py"),
    ("Construtask/views_usuarios.py", "Construtask/views_usuarios.py"),
    ("Construtask/admin.py", "Construtask/admin.py"),
    ("Construtask/urls.py", "Construtask/urls.py"),
    ("Construtask/tests.py", "Construtask/tests.py"),
]

def separar_modulo(nome_arquivo):
    """Cria um separador visual entre módulos"""
    nome = nome_arquivo.replace("\\", "/").replace("/", ".").replace(".py", "")
    linhas = [
        "",
        "=" * 80,
        f"# MÓDULO: {nome}",
        "=" * 80,
        "",
    ]
    return "\n".join(linhas)

def main():
    print("🔄 Iniciando junção dos arquivos Python...")
    print(f"📁 Diretório base: {BASE_DIR}")
    print(f"📄 Arquivo de saída: {OUTPUT_FILE}")
    print()
    
    total_linhas = 0
    arquivos_incluidos = 0
    arquivos_nao_encontrados = []
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as output:
        # Cabeçalho do arquivo
        output.write('''# =============================================================================
# CONSTRUTASK 2.0 - PROJETO COMPLETO CONSOLIDADO
# =============================================================================
#
# ATENÇÃO: Este arquivo é uma consolidação de todos os módulos Python do projeto.
# Para desenvolvimento, utilize os arquivos originais separados.
#
# Gerado em: ''' + str(__import__('datetime').datetime.now()) + '''
# =============================================================================

import os
import sys
from pathlib import Path
from datetime import datetime

''')
        
        for alias, arquivo in ARQUIVOS:
            caminho = BASE_DIR / arquivo
            
            if caminho.exists():
                with open(caminho, "r", encoding="utf-8") as f:
                    conteudo = f.read()
                    linhas = conteudo.split("\n")
                    total_linhas += len(linhas)
                    arquivos_incluidos += 1
                    
                    output.write(separar_modulo(arquivo))
                    output.write(conteudo)
                    output.write("\n")
                    print(f"  ✅ {arquivo} ({len(linhas)} linhas)")
            else:
                arquivos_nao_encontrados.append(arquivo)
                print(f"  ⚠️  {arquivo} - NÃO ENCONTRADO")
    
    print()
    print("=" * 80)
    print(f"📊 RESUMO:")
    print(f"   • Arquivos incluídos: {arquivos_incluidos}")
    print(f"   • Total de linhas: {total_linhas:,}")
    print(f"   • Arquivos não encontrados: {len(arquivos_nao_encontrados)}")
    if arquivos_nao_encontrados:
        print(f"   • Lista: {', '.join(arquivos_nao_encontrados)}")
    print(f"   • Arquivo gerado: {OUTPUT_FILE}")
    print("=" * 80)
    print()
    print("✅ Projeto consolidado com sucesso!")

if __name__ == "__main__":
    main()
