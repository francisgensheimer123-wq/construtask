"""
Serviços de Importação de Cronograma
Atende: ISO 6.1 (Planejamento) + PMBOK 6 (Cronograma)
Suporta: XLSX (Excel) e MPP (Microsoft Project)
"""

from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from .models_planejamento import (
    MapaCorrespondencia,
    PlanoFisico,
    PlanoFisicoBaseline,
    PlanoFisicoItem,
)


class CronogramaService:
    """
    Serviço para importação e controle de cronogramas.
    """
    
    # Mapeamento de colunas esperadas no XLSX
    COLUNAS_OBRIGATORIAS = ["CODIGO", "ATIVIDADE", "DURACAO_DIAS", "DATA_INICIO", "DATA_FIM"]
    COLUNAS_OPCIONAIS = ["PREDECESSORA", "SUCESSORA", "MARCO", "CODIGO_EAP", "VALOR"]
    
    @classmethod
    def importar_xlsx(cls, arquivo, obra, responsavel, titulo=None, criar_baseline=False):
        """
        Importa cronograma de arquivo Excel (.xlsx).
        
        Args:
            arquivo: Arquivo Excel uploaded
            obra: Instância de Obra
            responsavel: Usuário que está importando
            titulo: Título do cronograma (opcional)
            criar_baseline: Se True, cria como baseline
            
        Returns:
            PlanoFisico: Cronograma criado
            
        Raises:
            ValidationError: Se houver erro de validação
        """
        # Ler arquivo Excel
        try:
            df = pd.read_excel(arquivo, dtype=str)
        except Exception as e:
            raise ValidationError(f"Erro ao ler arquivo Excel: {str(e)}")
        
        # Normalizar nomes das colunas
        df = cls._normalizar_colunas(df)
        
        # Validar colunas obrigatórias
        cls._validar_colunas(df)
        
        # Criar cronograma
        with transaction.atomic():
            # Detectar tipo de arquivo
            tipo_arquivo = "XLSX"
            if arquivo.name.lower().endswith('.xlsx'):
                tipo_arquivo = "XLSX"
            elif arquivo.name.lower().endswith('.xls'):
                tipo_arquivo = "XLSX"
                
            # Criar título se não informado
            if not titulo:
                titulo = arquivo.name.replace('.xlsx', '').replace('.xls', '')
            
            # Criar PlanoFisico
            plano = PlanoFisico.objects.create(
                obra=obra,
                titulo=titulo,
                arquivo_origem=arquivo,
                tipo_arquivo=tipo_arquivo,
                is_baseline=criar_baseline,
                responsavel_importacao=responsavel,
                status="BASELINE" if criar_baseline else "ATIVO",
                data_base=datetime.now().date()
            )
            
            # Processar itens
            itens_criados = cls._processar_itens(df, plano, obra)
            
            # Criar baseline se solicitado
            if criar_baseline:
                cls._criar_baseline(plano, responsavel, "Importação inicial")
        
        return plano
    
    @classmethod
    def _normalizar_colunas(cls, df):
        """
        Normaliza nomes das colunas para maiúsculas e remove acentos.
        """
        # Mapeamento de variações comuns
        mapeamento = {
            'CÓDIGO': 'CODIGO',
            'CODIGO_ATIVIDADE': 'CODIGO',
            'CÓD.': 'CODIGO',
            'ATIVIDADE': 'ATIVIDADE',
            'TAREFA': 'ATIVIDADE',
            'DESCRIÇÃO': 'ATIVIDADE',
            'DURAÇÃO': 'DURACAO_DIAS',
            'DURAÇÃO_DIAS': 'DURACAO_DIAS',
            'DURACAO': 'DURACAO_DIAS',
            'DIAS': 'DURACAO_DIAS',
            'DATA INÍCIO': 'DATA_INICIO',
            'DATA_INICIO': 'DATA_INICIO',
            'INÍCIO': 'DATA_INICIO',
            'DATA FIM': 'DATA_FIM',
            'DATA_FIM': 'DATA_FIM',
            'FIM': 'DATA_FIM',
            'TÉRMINO': 'DATA_FIM',
            'PREDECESSORA': 'PREDECESSORA',
            'PREDECESSOR': 'PREDECESSORA',
            'PRED.': 'PREDECESSORA',
            'SUCESSORA': 'SUCESSORA',
            'SUCESSOR': 'SUCESSORA',
            'SUC.': 'SUCESSORA',
            'MARCO': 'MARCO',
            'MILESTONE': 'MARCO',
            'MARCADOR': 'MARCO',
            'EAP': 'CODIGO_EAP',
            'CENTRO DE CUSTO': 'CODIGO_EAP',
            'CC': 'CODIGO_EAP',
            'VALOR': 'VALOR',
            'VALOR PLANEJADO': 'VALOR',
            'IMPORTÂNCIA': 'VALOR',
        }
        
        colunas_normalizadas = {}
        for col in df.columns:
            col_upper = str(col).upper().strip()
            colunas_normalizadas[col] = mapeamento.get(col_upper, col_upper)
        
        df = df.rename(columns=colunas_normalizadas)
        
        # Preencher valores NaN com string vazia
        df = df.where(pd.notnull(df), None)
        
        return df
    
    @classmethod
    def _validar_colunas(cls, df):
        """
        Valida se todas as colunas obrigatórias existem.
        """
        colunas_presentes = [col.upper() for col in df.columns]
        colunas_faltando = []
        
        for col in cls.COLUNAS_OBRIGATORIAS:
            if col not in colunas_presentes:
                colunas_faltando.append(col)
        
        if colunas_faltando:
            raise ValidationError(
                f"Colunas obrigatórias não encontradas no arquivo: {', '.join(colunas_faltando)}"
            )
    
    @classmethod
    def _processar_itens(cls, df, plano, obra):
        """
        Processa o DataFrame e cria os itens do cronograma.
        """
        itens_criados = []
        codigos_processados = set()
        
        for idx, row in df.iterrows():
            codigo = cls._normalizar_string(row.get('CODIGO'))
            atividade = cls._normalizar_string(row.get('ATIVIDADE'))
            
            if not codigo or not atividade:
                continue
            
            # Evitar duplicatas
            if codigo in codigos_processados:
                continue
            
            # Processar datas
            data_inicio = cls._parse_data(row.get('DATA_INICIO'))
            data_fim = cls._parse_data(row.get('DATA_FIM'))
            
            # Processar duração
            duracao = cls._parse_int(row.get('DURACAO_DIAS'))
            if duracao is None and data_inicio and data_fim:
                duracao = (data_fim - data_inicio).days
            
            # Processar marco
            marco_str = cls._normalizar_string(row.get('MARCO'))
            is_marco = marco_str in ['SIM', 'S', 'YES', 'Y', 'TRUE', '1', 'X']
            
            # Processar predecessor/successor (garantir string vazia, não None)
            predecessor = cls._normalizar_string(row.get('PREDECESSORA')) or ""
            successor = cls._normalizar_string(row.get('SUCESSORA')) or ""
            
            # Processar valor
            valor = cls._parse_decimal(row.get('VALOR'))
            
            # Processar código EAP (vínculo com orçamento)
            plano_contas = None
            codigo_eap = cls._normalizar_string(row.get('CODIGO_EAP'))
            if codigo_eap and obra:
                from .models import PlanoContas
                plano_contas = PlanoContas.objects.filter(
                    obra=obra,
                    codigo__icontains=codigo_eap
                ).first()
            
            # Criar item
            item = PlanoFisicoItem.objects.create(
                plano=plano,
                plano_contas=plano_contas,
                codigo_atividade=codigo,
                atividade=atividade,
                predecessor=predecessor,
                successor=successor,
                duracao=duracao or 0,
                data_inicio_prevista=data_inicio,
                data_fim_prevista=data_fim,
                is_marco=is_marco,
                valor_planejado=valor or 0,
                sort_order=idx
            )
            
            itens_criados.append(item)
            codigos_processados.add(codigo)
        
        return itens_criados
    
    @classmethod
    def _normalizar_string(cls, valor):
        """Normaliza uma string para uso interno."""
        if valor is None:
            return None
        return str(valor).strip()
    
    @classmethod
    def _parse_data(cls, valor):
        """Converte valor para data."""
        if valor is None:
            return None
        
        # Se já é date
        if isinstance(valor, date):
            return valor
        
        # Tentar vários formatos
        formatos = [
            '%Y-%m-%d',
            '%d/%m/%Y',
            '%d/%m/%y',
            '%m/%d/%Y',
            '%Y/%m/%d',
        ]
        
        valor_str = cls._normalizar_string(valor)
        if not valor_str:
            return None
        
        for fmt in formatos:
            try:
                return datetime.strptime(valor_str, fmt).date()
            except ValueError:
                continue
        
        return None
    
    @classmethod
    def _parse_int(cls, valor):
        """Converte valor para inteiro."""
        if valor is None:
            return None
        
        valor_str = cls._normalizar_string(valor)
        if not valor_str:
            return None
        
        # Remover pontos e vírgulas que são separadores de milhar
        valor_str = valor_str.replace('.', '').replace(',', '')
        
        try:
            return int(valor_str)
        except ValueError:
            return None
    
    @classmethod
    def _parse_decimal(cls, valor):
        """Converte valor para Decimal."""
        if valor is None:
            return None
        
        valor_str = cls._normalizar_string(valor)
        if not valor_str:
            return None
        
        # Normalizar para formato brasileiro
        # Substituir ponto de milhar por vazio, vírgula por ponto
        valor_str = valor_str.replace('.', '').replace(',', '.')
        
        try:
            return Decimal(valor_str).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except ValueError:
            return None
    
    @classmethod
    def _criar_baseline(cls, plano, responsavel, observacao=""):
        """Cria uma versão de baseline do cronograma."""
        baseline = PlanoFisicoBaseline.objects.create(
            plano=plano,
            versao=plano.versao,
            arquivo=plano.arquivo_origem,
            observacao=observacao,
            responsavel=responsavel
        )
        return baseline
    
    @classmethod
    def criar_nova_versao(cls, plano_original, responsavel, arquivo_novo=None, observacao=""):
        """
        Cria uma nova versão do cronograma (não-baseline).
        """
        with transaction.atomic():
            # Criar nova versão
            nova_versao = plano_original.versao + 1
            
            novo_plano = PlanoFisico.objects.create(
                obra=plano_original.obra,
                titulo=plano_original.titulo,
                descricao=plano_original.descricao,
                arquivo_origem=arquivo_novo or plano_original.arquivo_origem,
                tipo_arquivo=plano_original.tipo_arquivo,
                versao=nova_versao,
                is_baseline=False,
                baseline_de=plano_original if plano_original.is_baseline else plano_original.baseline_de,
                data_base=plano_original.data_base,
                responsavel_importacao=responsavel,
                status="ATIVO"
            )
            
            # Copiar itens da versão anterior
            itens_originais = plano_original.itens.all()
            for item in itens_originais:
                PlanoFisicoItem.objects.create(
                    plano=novo_plano,
                    plano_contas=item.plano_contas,
                    codigo_atividade=item.codigo_atividade,
                    atividade=item.atividade,
                    predecessor=item.predecessor,
                    successor=item.successor,
                    duracao=item.duracao,
                    data_inicio_prevista=item.data_inicio_prevista,
                    data_fim_prevista=item.data_fim_prevista,
                    is_marco=item.is_marco,
                    valor_planejado=item.valor_planejado,
                    level=item.level,
                    wbs_code=item.wbs_code,
                    sort_order=item.sort_order
                )
            
            return novo_plano
    
    @classmethod
    def atualizar_percentuais(cls, plano_id):
        """
        Atualiza os percentuais de execução das atividades.
        Pode ser integrado com medições.
        """
        from .models import NotaFiscalCentroCusto
        
        plano = PlanoFisico.objects.get(pk=plano_id)
        
        # Atualizar cada item
        itens = plano.itens.all()
        
        for item in itens:
            # Calcular percentual baseado em valor realizado
            if item.plano_contas:
                # Buscar valor realizado no centro de custo
                centros_ids = list(item.plano_contas.get_descendants(include_self=True).values_list('id', flat=True))
                
                realizado = NotaFiscalCentroCusto.objects.filter(
                    nota_fiscal__obra=plano.obra,
                    nota_fiscal__status__in=['CONFERIDA', 'PAGA'],
                    centro_custo_id__in=centros_ids
                ).aggregate(total=Sum('valor'))['total'] or 0
                
                if item.valor_planejado > 0:
                    item.valor_realizado = realizado
                    item.percentual_concluido = min(100, round((realizado / item.valor_planejado) * 100, 1))
            
            item.save()
        
        return plano
    
    @classmethod
    def gerar_curva_s_planejada(cls, plano_id):
        """
        Gera dados para curva S planejada.
        """
        from collections import defaultdict
        
        plano = PlanoFisico.objects.get(pk=plano_id)
        
        # Agrupar por mês
        valores_por_mes = defaultdict(Decimal)
        
        for item in plano.itens.all():
            if item.data_inicio_prevista and item.data_fim_prevista:
                # Distribuir valor uniformemente pelos meses
                meses = (item.data_fim_prevista.year - item.data_inicio_prevista.year) * 12 + \
                        item.data_fim_prevista.month - item.data_inicio_prevista.month + 1
                
                if meses > 0:
                    valor_mes = item.valor_planejado / meses
                    cursor = item.data_inicio_prevista
                    for _ in range(meses):
                        chave = f"{cursor.year}-{cursor.month:02d}"
                        valores_por_mes[chave] += valor_mes
                        cursor = cursor.replace(month=cursor.month + 1) if cursor.month < 12 else cursor.replace(year=cursor.year + 1, month=1)
        
        # Converter para lista acumulada
        resultado = []
        acumulado = Decimal('0.00')
        for chave in sorted(valores_por_mes.keys()):
            acumulado += valores_por_mes[chave]
            resultado.append({
                'mes': chave,
                'valor_mes': float(valores_por_mes[chave]),
                'acumulado': float(acumulado)
            })
        
        return resultado
    
    @classmethod
    def gerar_curva_s_realizada(cls, plano_id, data_corte=None):
        """
        Gera dados para curva S realizada (baseado em medições).
        """
        from collections import defaultdict
        from .models import Medicao
        
        if data_corte is None:
            data_corte = date.today()
        
        plano = PlanoFisico.objects.get(pk=plano_id)
        
        # Buscar medições até a data de corte
        medicoes = Medicao.objects.filter(
            obra=plano.obra,
            data_medicao__lte=data_corte,
            status__in=['CONFERIDA', 'APROVADA', 'FATURADA']
        )
        
        # Agrupar por mês
        valores_por_mes = defaultdict(Decimal)
        
        for med in medicoes:
            chave = f"{med.data_medicao.year}-{med.data_medicao.month:02d}"
            valores_por_mes[chave] += med.valor_medido
        
        # Converter para lista acumulada
        resultado = []
        acumulado = Decimal('0.00')
        for chave in sorted(valores_por_mes.keys()):
            acumulado += valores_por_mes[chave]
            resultado.append({
                'mes': chave,
                'valor_mes': float(valores_por_mes[chave]),
                'acumulado': float(acumulado)
            })
        
        return resultado


class MapeamentoService:
    """
    Serviço para mapeamento entre cronograma e EAP (orçamento).
    """
    
    @classmethod
    def sugerir_correspondencia(cls, plano_fisico_item, lista_plano_contas):
        """
        Sugere correspondência por similaridade de nomes/códigos.
        
        Usa fuzzy matching simples (contém código ou descrição).
        """
        sugestoes = []
        
        codigo_atividade = plano_fisico_item.codigo_atividade.lower()
        atividade = plano_fisico_item.atividade.lower()
        
        for pc in lista_plano_contas:
            pontuacao = 0
            codigo_eap = pc.codigo.lower()
            descricao = pc.descricao.lower()
            
            # Código contém no código EAP
            if codigo_atividade in codigo_eap or codigo_eap in codigo_atividade:
                pontuacao += 50
            
            # Descrição contém parte do código
            if codigo_atividade in descricao:
                pontuacao += 30
            
            # Atividade contém no código EAP
            if atividade in codigo_eap:
                pontuacao += 20
            
            if pontuacao > 0:
                sugestoes.append({
                    'plano_contas': pc,
                    'pontuacao': pontuacao
                })
        
        # Ordenar por pontuação
        sugestoes.sort(key=lambda x: x['pontuacao'], reverse=True)
        
        return sugestoes[:5]  # Top 5
    
    @classmethod
    def vincular_em_massa(cls, plano_fisico, plano_contas, percentual=100):
        """
        Vincula todas as atividades de um cronograma a um centro de custo.
        """
        empresa = plano_fisico.obra.empresa
        
        itens = plano_fisico.itens.all()
        correspondencias_criadas = []
        
        for item in itens:
            # Verificar se já existe mapeamento
            existente = MapaCorrespondencia.objects.filter(
                plano_fisico_item=item,
                status='ATIVO'
            ).first()
            
            if existente:
                existente.plano_contas = plano_contas
                existente.percentual_rateio = percentual
                existente.save()
            else:
                correspondencia = MapaCorrespondencia.objects.create(
                    empresa=empresa,
                    obra=plano_fisico.obra,
                    plano_fisico_item=item,
                    plano_contas=plano_contas,
                    percentual_rateio=percentual,
                    status='ATIVO'
                )
                correspondencias_criadas.append(correspondencia)
        
        return correspondencias_criadas
    
    @classmethod
    def verificar_divergencias(cls, plano_fisico_id):
        """
        Lista atividades sem mapeamento ou com rateio incompleto.
        """
        plano = PlanoFisico.objects.get(pk=plano_fisico_id)
        
        divergencias = []
        
        for item in plano.itens.all():
            mapeamentos = MapaCorrespondencia.objects.filter(
                plano_fisico_item=item,
                status='ATIVO'
            )
            
            if not mapeamentos.exists():
                divergencias.append({
                    'item': item,
                    'tipo': 'SEM_VINCULO',
                    'mensagem': f"Atividade {item.codigo_atividade} sem vínculo com EAP"
                })
            else:
                total_rateio = sum(m.percentual_rateio for m in mapeamentos)
                if total_rateio < 100:
                    divergencias.append({
                        'item': item,
                        'tipo': 'RATEIO_INCOMPLETO',
                        'mensagem': f"Atividade {item.codigo_atividade} com rateio incompleto ({total_rateio}%)"
                    })
        
        return divergencias
    
    @classmethod
    def consolidar_valores_por_eap(cls, plano_fisico_id):
        """
        Consolida valores do cronograma mapeado para a EAP.
        
        Returns:
            dict: Dicionário com plano_contas_id -> valores consolidados
        """
        plano = PlanoFisico.objects.get(pk=plano_fisico_id)
        
        consolidados = {}
        
        # Buscar todos os mapeamentos ativos
        mapeamentos = MapaCorrespondencia.objects.filter(
            plano_fisico_item__plano=plano,
            status='ATIVO'
        ).select_related('plano_fisico_item', 'plano_contas')
        
        for mp in mapeamentos:
            pc_id = mp.plano_contas_id
            if not pc_id:
                continue
            
            item = mp.plano_fisico_item
            percentual = mp.percentual_rateio / 100
            
            if pc_id not in consolidados:
                consolidados[pc_id] = {
                    'plano_contas': mp.plano_contas,
                    'valor_planejado': 0,
                    'valor_realizado': 0,
                    'percentual_concluido': 0
                }
            
            consolidados[pc_id]['valor_planejado'] += item.valor_planejado * percentual
            consolidados[pc_id]['valor_realizado'] += item.valor_realizado * percentual
        
        # Calcular percentuais
        for pc_id in consolidados:
            dados = consolidados[pc_id]
            if dados['valor_planejado'] > 0:
                dados['percentual_concluido'] = round(
                    (dados['valor_realizado'] / dados['valor_planejado']) * 100, 1
                )
        
        return consolidados
