"""
Serviços de Importação de Cronograma
Atende: ISO 6.1 (Planejamento) + PMBOK 6 (Cronograma)
Suporta: XLSX (Excel) e MPP (Microsoft Project)
"""

from collections import defaultdict
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
from .numeric_utils import coerce_decimal, coerce_int
from .text_normalization import corrigir_mojibake


class CronogramaService:
    """
    Serviço para importação e controle de cronogramas.
    """
    
    # Mapeamento de colunas esperadas no XLSX
    COLUNAS_OBRIGATORIAS = ["CODIGO", "ATIVIDADE", "DURACAO_DIAS", "DATA_INICIO", "DATA_FIM"]
    COLUNAS_OPCIONAIS = ["PREDECESSORA", "SUCESSORA", "MARCO", "CODIGO_EAP", "VALOR", "WBS", "NIVEL"]

    @classmethod
    def _distribuir_valor_por_mes(cls, inicio, fim, valor_total):
        valores_por_mes = defaultdict(Decimal)
        if not inicio or not fim or not valor_total:
            return valores_por_mes

        if fim < inicio:
            inicio, fim = fim, inicio

        total_dias = (fim - inicio).days + 1
        if total_dias <= 0:
            return valores_por_mes

        cursor = inicio
        valor_total = Decimal(str(valor_total))
        acumulado = Decimal("0.00")

        while cursor <= fim:
            proximo_mes = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
            fim_mes = min(fim, proximo_mes - timedelta(days=1))
            dias_mes = (fim_mes - cursor).days + 1
            chave = f"{cursor.year}-{cursor.month:02d}"
            if fim_mes == fim:
                valor_mes = valor_total - acumulado
            else:
                valor_mes = (valor_total * Decimal(dias_mes) / Decimal(total_dias)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
                acumulado += valor_mes
            valores_por_mes[chave] += valor_mes
            cursor = fim_mes + timedelta(days=1)

        return valores_por_mes

    @classmethod
    def _normalizar_codigo_eap(cls, valor):
        codigo = cls._normalizar_string(valor)
        if not codigo:
            return ""
        return codigo.strip().upper()

    @classmethod
    def _resolver_plano_contas_por_codigo_eap(cls, obra, codigo_eap):
        if not obra or not codigo_eap:
            return None

        from .models import PlanoContas

        codigo_normalizado = cls._normalizar_codigo_eap(codigo_eap)
        queryset = PlanoContas.objects.filter(obra=obra, filhos__isnull=True)

        exato = queryset.filter(codigo__iexact=codigo_normalizado).first()
        if exato:
            return exato

        return None

    @classmethod
    def _resolver_hierarquia_item(cls, row, codigo, idx, pilha_por_nivel):
        wbs_code = cls._normalizar_string(row.get("WBS")) or codigo
        level = cls._parse_level(row.get("NIVEL"))
        if level is None:
            level = cls._inferir_level_por_codigo(wbs_code)
        level = max(level or 0, 0)

        parent = pilha_por_nivel.get(level - 1) if level > 0 else None

        for chave in [chave for chave in list(pilha_por_nivel.keys()) if chave >= level]:
            pilha_por_nivel.pop(chave, None)

        return {
            "level": level,
            "wbs_code": wbs_code,
            "parent": parent,
            "sort_order": idx,
        }

    @classmethod
    def analisar_xlsx(cls, arquivo, obra=None):
        try:
            df = pd.read_excel(arquivo, dtype=str)
        except Exception as e:
            raise ValidationError(f"Erro ao ler arquivo Excel: {str(e)}")

        df = cls._normalizar_colunas(df)
        cls._validar_colunas(df)

        total_linhas = len(df.index)
        atividades_validas = 0
        sem_datas = 0
        com_codigo_eap = 0
        eap_reconhecida = 0
        preview = []

        for idx, row in df.iterrows():
            codigo = cls._normalizar_string(row.get("CODIGO"))
            atividade = cls._normalizar_string(row.get("ATIVIDADE"))
            if not codigo or not atividade:
                continue
            atividades_validas += 1
            data_inicio = cls._parse_data(row.get("DATA_INICIO"))
            data_fim = cls._parse_data(row.get("DATA_FIM"))
            if not data_inicio or not data_fim:
                sem_datas += 1
            codigo_eap = cls._normalizar_string(row.get("CODIGO_EAP"))
            reconhecida = False
            if codigo_eap:
                com_codigo_eap += 1
                if obra:
                    from .models import PlanoContas

                    reconhecida = cls._resolver_plano_contas_por_codigo_eap(obra, codigo_eap) is not None
                    if reconhecida:
                        eap_reconhecida += 1
            if len(preview) < 8:
                preview.append(
                    {
                        "linha": idx + 2,
                        "codigo": codigo,
                        "atividade": atividade,
                        "data_inicio": data_inicio.strftime("%d/%m/%Y") if data_inicio else "Nao identificada",
                        "data_fim": data_fim.strftime("%d/%m/%Y") if data_fim else "Nao identificada",
                        "codigo_eap": codigo_eap or "-",
                        "eap_reconhecida": reconhecida,
                    }
                )

        try:
            arquivo.seek(0)
        except Exception:
            pass

        return {
            "colunas_presentes": list(df.columns),
            "total_linhas": total_linhas,
            "atividades_validas": atividades_validas,
            "sem_datas": sem_datas,
            "com_codigo_eap": com_codigo_eap,
            "eap_reconhecida": eap_reconhecida,
            "preview": preview,
        }
    
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
        analise = cls.analisar_xlsx(arquivo, obra=obra)
        df = pd.read_excel(arquivo, dtype=str)
        df = cls._normalizar_colunas(df)
        
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
        
        plano._resumo_importacao = {
            "total_linhas": analise["total_linhas"],
            "atividades_validas": analise["atividades_validas"],
            "sem_datas": analise["sem_datas"],
            "com_codigo_eap": analise["com_codigo_eap"],
            "eap_reconhecida": analise["eap_reconhecida"],
            "itens_criados": len(itens_criados),
        }
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
            'CODIGO EAP': 'CODIGO_EAP',
            'CENTRO DE CUSTO': 'CODIGO_EAP',
            'CC': 'CODIGO_EAP',
            'WBS': 'WBS',
            'NIVEL': 'NIVEL',
            'NÍVEL': 'NIVEL',
            'VALOR': 'VALOR',
            'VALOR PLANEJADO': 'VALOR',
            'IMPORTÂNCIA': 'VALOR',
        }
        
        colunas_normalizadas = {}
        for col in df.columns:
            col_upper = corrigir_mojibake(str(col).upper().strip())
            col_upper = (
                col_upper
                .replace("Ã‰", "É")
                .replace("Ã", "Í")
                .replace("Ã“", "Ó")
                .replace("Ã‚", "Â")
                .replace("Ã‡", "Ç")
            )
            if col_upper in {"DATA INICIO PREVISTA", "DATA_INICIO_PREVISTA", "INICIO PREVISTO", "INICIO PLANEJADO", "DATA DE INICIO"}:
                col_upper = "DATA_INICIO"
            elif col_upper in {"DATA FIM PREVISTA", "DATA_FIM_PREVISTA", "FIM PREVISTO", "FIM PLANEJADO", "DATA DE FIM", "TERMINO PREVISTO", "TERMINO PLANEJADO", "TÉRMINO PREVISTO"}:
                col_upper = "DATA_FIM"
            elif col_upper in {"CÓDIGO EAP"}:
                col_upper = "CODIGO EAP"
            elif col_upper in {"NÍVEL"}:
                col_upper = "NIVEL"
            elif col_upper in {"IMPORTÂNCIA"}:
                col_upper = "IMPORTÃ‚NCIA"
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
        pilha_por_nivel = {}
        
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
            erro_vinculo_eap = ""
            if codigo_eap and obra:
                plano_contas = cls._resolver_plano_contas_por_codigo_eap(obra, codigo_eap)
                if plano_contas is None:
                    erro_vinculo_eap = f"Codigo da EAP '{codigo_eap}' nao localizado na EAP da obra."

            hierarquia = cls._resolver_hierarquia_item(row, codigo, idx, pilha_por_nivel)
            
            valor_planejado_item = valor or 0

            # Criar item
            item = PlanoFisicoItem.objects.create(
                plano=plano,
                parent=hierarquia["parent"],
                plano_contas=plano_contas,
                codigo_eap_importado=codigo_eap or "",
                erro_vinculo_eap=erro_vinculo_eap,
                codigo_atividade=codigo,
                atividade=atividade,
                predecessor=predecessor,
                successor=successor,
                duracao=duracao or 0,
                data_inicio_prevista=data_inicio,
                data_fim_prevista=data_fim,
                is_marco=is_marco,
                valor_planejado=valor_planejado_item,
                level=hierarquia["level"],
                wbs_code=hierarquia["wbs_code"],
                sort_order=hierarquia["sort_order"]
            )

            pilha_por_nivel[hierarquia["level"]] = item

            if plano_contas and obra and obra.empresa_id:
                MapaCorrespondencia.objects.update_or_create(
                    empresa=obra.empresa,
                    obra=obra,
                    plano_fisico_item=item,
                    plano_contas=plano_contas,
                    status="ATIVO",
                    defaults={
                        "percentual_rateio": 100,
                        "created_by": plano.responsavel_importacao,
                    },
                )

            itens_criados.append(item)
            codigos_processados.add(codigo)

        MapeamentoService.recalcular_valores_planejados(plano)

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
    def _parse_data(cls, valor):
        """Converte valor para data com tolerancia a formatos do Excel."""
        if valor is None:
            return None

        if isinstance(valor, date):
            return valor
        if hasattr(valor, "to_pydatetime"):
            try:
                return valor.to_pydatetime().date()
            except Exception:
                pass

        valor_str = cls._normalizar_string(valor)
        if not valor_str:
            return None

        formatos = [
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
            "%d/%m/%y",
            "%m/%d/%Y",
            "%Y/%m/%d",
        ]
        for fmt in formatos:
            try:
                return datetime.strptime(valor_str, fmt).date()
            except ValueError:
                continue

        if valor_str.replace(".", "", 1).isdigit():
            try:
                numero = float(valor_str)
                if numero > 20000:
                    return (datetime(1899, 12, 30) + timedelta(days=numero)).date()
            except ValueError:
                pass

        try:
            data_convertida = pd.to_datetime(valor_str, dayfirst=True, errors="coerce")
            if pd.notna(data_convertida):
                return data_convertida.date()
        except Exception:
            pass

        return None

    @classmethod
    def _parse_level(cls, valor):
        if valor is None:
            return None

        valor_str = cls._normalizar_string(valor)
        if not valor_str:
            return None

        try:
            numero = int(float(valor_str.replace(",", ".")))
        except ValueError:
            return None

        if numero <= 0:
            return 0
        return numero - 1

    @classmethod
    def _inferir_level_por_codigo(cls, codigo):
        codigo_str = cls._normalizar_string(codigo)
        if not codigo_str:
            return 0

        for separador in [".", "/"]:
            if separador in codigo_str:
                partes = [parte for parte in codigo_str.split(separador) if parte.strip()]
                if len(partes) > 1:
                    return len(partes) - 1
        return 0
    
    @classmethod
    def _parse_int(cls, valor):
        """Converte valor para inteiro."""
        return coerce_int(valor, default=None)
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
        return coerce_decimal(valor, default=None, allow_none=True, quantize="0.01")
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
            itens_originais = list(plano_original.itens.all().order_by("sort_order", "pk"))
            mapa_novos_itens = {}
            for item in itens_originais:
                novo_item = PlanoFisicoItem.objects.create(
                    plano=novo_plano,
                    parent=mapa_novos_itens.get(item.parent_id),
                    plano_contas=item.plano_contas,
                    codigo_eap_importado=item.codigo_eap_importado,
                    erro_vinculo_eap=item.erro_vinculo_eap,
                    codigo_atividade=item.codigo_atividade,
                    atividade=item.atividade,
                    predecessor=item.predecessor,
                    successor=item.successor,
                    duracao=item.duracao,
                    data_inicio_prevista=item.data_inicio_prevista,
                    data_fim_prevista=item.data_fim_prevista,
                    data_inicio_real=item.data_inicio_real,
                    data_fim_real=item.data_fim_real,
                    percentual_concluido=item.percentual_concluido,
                    is_marco=item.is_marco,
                    valor_planejado=item.valor_planejado,
                    valor_realizado=item.valor_realizado,
                    level=item.level,
                    wbs_code=item.wbs_code,
                    sort_order=item.sort_order
                )
                mapa_novos_itens[item.id] = novo_item
            mapeamentos_originais = list(
                MapaCorrespondencia.objects.filter(
                    plano_fisico_item__plano=plano_original,
                    status="ATIVO",
                    plano_contas__isnull=False,
                ).select_related("plano_contas")
            )
            for mapeamento in mapeamentos_originais:
                novo_item = mapa_novos_itens.get(mapeamento.plano_fisico_item_id)
                if not novo_item:
                    continue
                MapaCorrespondencia.objects.create(
                    empresa=plano_original.obra.empresa,
                    obra=plano_original.obra,
                    plano_fisico_item=novo_item,
                    plano_contas=mapeamento.plano_contas,
                    percentual_rateio=100,
                    status="ATIVO",
                    created_by=responsavel,
                )
            MapeamentoService.recalcular_valores_planejados(novo_plano)
            return novo_plano
    
    @classmethod
    def atualizar_percentuais(cls, plano_id):
        """
        Atualiza os percentuais de execução das atividades.
        Pode ser integrado com medições.
        """
        from .models import NotaFiscalCentroCusto
        
        plano = PlanoFisico.objects.get(pk=plano_id)
        
        analise = MapeamentoService.analisar_vinculos(plano)
        itens = analise["itens"]
        totais_realizados_eap = {}

        for eap_id, itens_vinculados in analise["eap_to_items"].items():
            eap = analise["eap_obj"].get(eap_id)
            if not eap:
                continue
            centros_ids = list(eap.get_descendants(include_self=True).values_list('id', flat=True))
            totais_realizados_eap[eap_id] = (
                NotaFiscalCentroCusto.objects.filter(
                    nota_fiscal__obra=plano.obra,
                    nota_fiscal__status__in=['CONFERIDA', 'PAGA'],
                    centro_custo_id__in=centros_ids
                ).aggregate(total=Sum('valor'))['total'] or Decimal("0.00")
            )

        for item in itens:
            realizado = Decimal("0.00")
            for eap in analise["item_to_eaps"].get(item.pk, []):
                valor_planejado_contrib = analise["contribuicoes"].get((item.pk, eap.pk), Decimal("0.00"))
                valor_total_eap = eap.valor_total_consolidado or Decimal("0.00")
                if valor_total_eap > 0:
                    realizado += totais_realizados_eap.get(eap.pk, Decimal("0.00")) * (valor_planejado_contrib / valor_total_eap)
            item.valor_realizado = realizado.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if realizado else Decimal("0.00")
            if item.valor_planejado > 0:
                item.percentual_concluido = min(100, round((item.valor_realizado / item.valor_planejado) * 100, 1))
            item.save()
        
        return plano
    
    @classmethod
    def gerar_curva_s_planejada(cls, plano_id):
        """
        Gera dados para curva S planejada.
        """
        plano = PlanoFisico.objects.get(pk=plano_id)
        valores_por_mes = defaultdict(Decimal)
        itens = plano.itens.filter(filhos__isnull=True).order_by("data_inicio_prevista", "id")

        for item in itens:
            distribuicao = cls._distribuir_valor_por_mes(
                item.data_inicio_prevista,
                item.data_fim_prevista,
                item.valor_planejado or Decimal("0.00"),
            )
            for chave, valor_mes in distribuicao.items():
                valores_por_mes[chave] += valor_mes

        resultado = []
        acumulado = Decimal("0.00")
        for chave in sorted(valores_por_mes.keys()):
            acumulado += valores_por_mes[chave]
            resultado.append({
                "mes": chave,
                "valor_mes": float(valores_por_mes[chave]),
                "acumulado": float(acumulado),
            })

        return resultado
    
    @classmethod
    def gerar_curva_s_realizada(cls, plano_id, data_corte=None):
        """
        Gera dados para curva S realizada (baseado em medições).
        """
        if data_corte is None:
            data_corte = date.today()

        plano = PlanoFisico.objects.get(pk=plano_id)
        valores_por_mes = defaultdict(Decimal)
        itens = plano.itens.filter(filhos__isnull=True).order_by("data_inicio_real", "id")

        for item in itens:
            valor_realizado = item.valor_realizado or Decimal("0.00")
            if not valor_realizado or valor_realizado <= 0 or not item.data_inicio_real:
                continue

            if item.percentual_concluido >= 100 and item.data_fim_real:
                fim_real = item.data_fim_real
            else:
                fim_real = data_corte

            if fim_real > data_corte:
                fim_real = data_corte
            if fim_real < item.data_inicio_real:
                fim_real = item.data_inicio_real

            distribuicao = cls._distribuir_valor_por_mes(
                item.data_inicio_real,
                fim_real,
                valor_realizado,
            )
            for chave, valor_mes in distribuicao.items():
                valores_por_mes[chave] += valor_mes

        resultado = []
        acumulado = Decimal("0.00")
        for chave in sorted(valores_por_mes.keys()):
            acumulado += valores_por_mes[chave]
            resultado.append({
                "mes": chave,
                "valor_mes": float(valores_por_mes[chave]),
                "acumulado": float(acumulado),
            })

        return resultado


class MapeamentoService:
    """
    Serviço para mapeamento entre cronograma e EAP (orçamento).
    """

    @staticmethod
    def _peso_duracao_item(item):
        if item.data_inicio_prevista and item.data_fim_prevista:
            dias = (item.data_fim_prevista - item.data_inicio_prevista).days + 1
            if dias > 0:
                return Decimal(str(dias))
        return Decimal("1")

    @classmethod
    def _obter_plano(cls, plano_fisico_ou_id):
        if isinstance(plano_fisico_ou_id, PlanoFisico):
            return plano_fisico_ou_id
        return PlanoFisico.objects.get(pk=plano_fisico_ou_id)

    @classmethod
    def _obter_mapeamentos_ativos(cls, plano):
        itens = list(
            plano.itens.filter(filhos__isnull=True).select_related("plano_contas")
        )
        itens_por_id = {item.pk: item for item in itens}
        registros = []
        vistos = set()

        for mapeamento in (
            MapaCorrespondencia.objects.filter(
                plano_fisico_item__plano=plano,
                plano_fisico_item__filhos__isnull=True,
                status="ATIVO",
                plano_contas__isnull=False,
            )
            .select_related("plano_fisico_item", "plano_contas")
            .order_by("plano_contas__codigo", "plano_fisico_item__codigo_atividade", "id")
        ):
            chave = (mapeamento.plano_fisico_item_id, mapeamento.plano_contas_id)
            if chave in vistos:
                continue
            vistos.add(chave)
            registros.append(
                {
                    "item": itens_por_id.get(mapeamento.plano_fisico_item_id) or mapeamento.plano_fisico_item,
                    "plano_contas": mapeamento.plano_contas,
                    "origem": "MAPEAMENTO",
                }
            )

        for item in itens:
            if item.plano_contas_id:
                chave = (item.pk, item.plano_contas_id)
                if chave in vistos:
                    continue
                vistos.add(chave)
                registros.append(
                    {
                        "item": item,
                        "plano_contas": item.plano_contas,
                        "origem": "ITEM",
                    }
                )
        return itens, registros

    @classmethod
    def analisar_vinculos(cls, plano_fisico_ou_id):
        plano = cls._obter_plano(plano_fisico_ou_id)
        itens, registros = cls._obter_mapeamentos_ativos(plano)
        item_to_eaps = defaultdict(list)
        eap_to_items = defaultdict(list)
        eap_obj = {}
        itens_por_id = {item.pk: item for item in itens}

        for registro in registros:
            item = registro["item"]
            eap = registro["plano_contas"]
            if eap.pk not in [obj.pk for obj in item_to_eaps[item.pk]]:
                item_to_eaps[item.pk].append(eap)
            if item.pk not in [obj.pk for obj in eap_to_items[eap.pk]]:
                eap_to_items[eap.pk].append(item)
            eap_obj[eap.pk] = eap

        mensagens = {}
        itens_invalidos = set()

        visitados_itens = set()
        visitados_eaps = set()
        componentes = []

        for item in itens:
            if item.pk in visitados_itens or item.pk not in item_to_eaps:
                continue

            fila_itens = [item.pk]
            componente_itens = set()
            componente_eaps = set()

            while fila_itens:
                item_id = fila_itens.pop()
                if item_id in componente_itens:
                    continue
                componente_itens.add(item_id)
                visitados_itens.add(item_id)

                for eap in item_to_eaps.get(item_id, []):
                    if eap.pk not in componente_eaps:
                        componente_eaps.add(eap.pk)
                    if eap.pk in visitados_eaps:
                        continue
                    visitados_eaps.add(eap.pk)
                    for item_relacionado in eap_to_items.get(eap.pk, []):
                        if item_relacionado.pk not in componente_itens:
                            fila_itens.append(item_relacionado.pk)

            componentes.append((componente_itens, componente_eaps))

        for componente_itens, componente_eaps in componentes:
            if len(componente_itens) > 1 and len(componente_eaps) > 1:
                for item_id in componente_itens:
                    itens_invalidos.add(item_id)
                    mensagens[item_id] = (
                        "Configuracao N EAP -> N atividades nao suportada. "
                        "Quebre o cronograma para manter vinculos 1 EAP -> 1 atividade."
                    )

        valores_item = {item.pk: (item.valor_planejado or Decimal("0.00")) for item in itens}
        contribuicoes = {}
        pesos = {item.pk: cls._peso_duracao_item(item) for item in itens}

        for item in itens:
            if item.pk in item_to_eaps:
                valores_item[item.pk] = Decimal("0.00")

        for eap_id, itens_vinculados in eap_to_items.items():
            if any(item.pk in itens_invalidos for item in itens_vinculados):
                continue
            valor_total_eap = eap_obj[eap_id].valor_total_consolidado or Decimal("0.00")
            if len(itens_vinculados) == 1:
                item = itens_vinculados[0]
                valores_item[item.pk] += valor_total_eap
                contribuicoes[(item.pk, eap_id)] = valor_total_eap
                continue

            soma_pesos = sum((pesos[item.pk] for item in itens_vinculados), Decimal("0.00"))
            if soma_pesos <= 0:
                soma_pesos = Decimal(str(len(itens_vinculados)))
                pesos_locais = {item.pk: Decimal("1") for item in itens_vinculados}
            else:
                pesos_locais = {item.pk: pesos[item.pk] for item in itens_vinculados}

            distribuido = Decimal("0.00")
            for indice, item in enumerate(itens_vinculados, start=1):
                if indice == len(itens_vinculados):
                    valor_item = valor_total_eap - distribuido
                else:
                    valor_item = (
                        (valor_total_eap * pesos_locais[item.pk] / soma_pesos)
                        .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    )
                    distribuido += valor_item
                valores_item[item.pk] += valor_item
                contribuicoes[(item.pk, eap_id)] = valor_item

        codigos_por_item = {
            item.pk: [eap.codigo for eap in item_to_eaps.get(item.pk, [])]
            for item in itens
        }
        return {
            "plano": plano,
            "itens": itens,
            "item_to_eaps": item_to_eaps,
            "eap_to_items": eap_to_items,
            "eap_obj": eap_obj,
            "itens_invalidos": itens_invalidos,
            "mensagens": mensagens,
            "valores_item": valores_item,
            "contribuicoes": contribuicoes,
            "codigos_por_item": codigos_por_item,
        }

    @classmethod
    def recalcular_valores_planejados(cls, plano_fisico_ou_id):
        analise = cls.analisar_vinculos(plano_fisico_ou_id)
        itens = analise["itens"]
        valores_item = analise["valores_item"]
        codigos_por_item = analise["codigos_por_item"]

        itens_invalidos = analise["itens_invalidos"]

        for item in itens:
            item.valor_planejado = (
                Decimal("0.00")
                if item.pk in itens_invalidos
                else valores_item.get(item.pk, item.valor_planejado or Decimal("0.00"))
            )
            if item.pk in itens_invalidos:
                item.erro_vinculo_eap = analise["mensagens"].get(item.pk, "")
                item.plano_contas = None
            elif item.codigo_eap_importado and not codigos_por_item.get(item.pk):
                item.erro_vinculo_eap = f"Codigo da EAP '{item.codigo_eap_importado}' nao localizado na EAP da obra."
                item.plano_contas = None
            else:
                eaps = analise["item_to_eaps"].get(item.pk, [])
                item.erro_vinculo_eap = ""
                item.plano_contas = eaps[0] if len(eaps) == 1 else None
            item.save(update_fields=["valor_planejado", "erro_vinculo_eap", "plano_contas", "updated_at"])

        PlanoFisicoItem.objects.filter(plano=analise["plano"], filhos__isnull=False).update(
            valor_planejado=Decimal("0.00"),
            plano_contas=None,
        )
        return analise

    @classmethod
    def validar_novo_vinculo(cls, item, plano_contas, *, substituindo_vinculos_item=False):
        if not item or not plano_contas:
            return None
        if item.filhos.exists():
            raise ValidationError("O vinculo com a EAP so pode ser definido em atividades folha do cronograma.")
        if plano_contas.filhos.exists():
            raise ValidationError("O vinculo com a EAP deve apontar para item analitico da EAP (nivel 6).")
        eaps_item = set(
            MapaCorrespondencia.objects.filter(
                plano_fisico_item=item,
                status="ATIVO",
                plano_contas__isnull=False,
            ).exclude(plano_contas=plano_contas).values_list("plano_contas_id", flat=True)
        )
        if not substituindo_vinculos_item and item.plano_contas_id and item.plano_contas_id != plano_contas.pk:
            eaps_item.add(item.plano_contas_id)

        itens_eap = set(
            MapaCorrespondencia.objects.filter(
                plano_contas=plano_contas,
                status="ATIVO",
                plano_fisico_item__filhos__isnull=True,
            ).exclude(plano_fisico_item=item).values_list("plano_fisico_item_id", flat=True)
        )
        if eaps_item and itens_eap:
            raise ValidationError(
                "Esse vinculo criaria um cenario N EAP -> N atividades, que nao e suportado. "
                "Quebre o cronograma para manter vinculos 1 EAP -> 1 atividade."
            )
        return None

    @classmethod
    def validar_conjunto_vinculos_item(cls, item, plano_contas_ids):
        if not item:
            return None
        if item.filhos.exists():
            raise ValidationError("O vinculo com a EAP so pode ser definido em atividades folha do cronograma.")

        plano_contas_ids = {int(pk) for pk in plano_contas_ids if pk}
        from .models import PlanoContas
        nao_analiticos = PlanoContas.objects.filter(pk__in=plano_contas_ids).exclude(filhos__isnull=True)
        if nao_analiticos.exists():
            raise ValidationError("O vinculo com a EAP deve apontar apenas para itens analiticos da EAP (nivel 6).")
        plano = item.plano
        _, registros = cls._obter_mapeamentos_ativos(plano)

        item_to_eaps = defaultdict(set)
        eap_to_items = defaultdict(set)

        for registro in registros:
            registro_item = registro["item"]
            eap = registro["plano_contas"]
            if registro_item.pk == item.pk:
                continue
            item_to_eaps[registro_item.pk].add(eap.pk)
            eap_to_items[eap.pk].add(registro_item.pk)

        for plano_contas_id in plano_contas_ids:
            item_to_eaps[item.pk].add(plano_contas_id)
            eap_to_items[plano_contas_id].add(item.pk)

        if not item_to_eaps.get(item.pk):
            return None

        componente_itens = set()
        componente_eaps = set()
        fila_itens = [item.pk]

        while fila_itens:
            item_id = fila_itens.pop()
            if item_id in componente_itens:
                continue
            componente_itens.add(item_id)
            for eap_id in item_to_eaps.get(item_id, set()):
                if eap_id not in componente_eaps:
                    componente_eaps.add(eap_id)
                for item_relacionado in eap_to_items.get(eap_id, set()):
                    if item_relacionado not in componente_itens:
                        fila_itens.append(item_relacionado)

        if len(componente_itens) > 1 and len(componente_eaps) > 1:
            raise ValidationError(
                "Esse vinculo criaria um cenario N EAP -> N atividades, que nao e suportado. "
                "Quebre o cronograma para manter vinculos 1 EAP -> 1 atividade."
            )
        return None
    
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
        itens = plano_fisico.itens.filter(filhos__isnull=True)
        itens_atualizados = []
        
        for item in itens:
            # Verificar se já existe mapeamento
            cls.validar_novo_vinculo(item, plano_contas)
            MapaCorrespondencia.objects.filter(
                plano_fisico_item=item,
                status="ATIVO",
            ).exclude(plano_contas=plano_contas).update(status="INATIVO")
            MapaCorrespondencia.objects.update_or_create(
                empresa=plano_fisico.obra.empresa,
                obra=plano_fisico.obra,
                plano_fisico_item=item,
                plano_contas=plano_contas,
                defaults={
                    "status": "ATIVO",
                    "percentual_rateio": 100,
                    "created_by": plano_fisico.responsavel_importacao,
                },
            )
            item.plano_contas = plano_contas
            item.erro_vinculo_eap = ""
            item.save(update_fields=["plano_contas", "erro_vinculo_eap", "updated_at"])
            itens_atualizados.append(item)

        cls.recalcular_valores_planejados(plano_fisico)
        return itens_atualizados
    
    @classmethod
    def verificar_divergencias(cls, plano_fisico_id, analise=None):
        """
        Lista atividades sem vinculo valido ou com estrutura nao suportada.
        """
        analise = analise or cls.analisar_vinculos(plano_fisico_id)
        divergencias = []

        for item in analise["itens"]:
            codigos = analise["codigos_por_item"].get(item.pk, [])
            if item.pk in analise["itens_invalidos"]:
                divergencias.append({
                    'item': item,
                    'tipo': 'VINCULO_N_N',
                    'mensagem': analise["mensagens"].get(item.pk, "Configuracao N EAP -> N atividades nao suportada."),
                })
            elif not codigos:
                divergencias.append({
                    'item': item,
                    'tipo': 'SEM_VINCULO',
                    'mensagem': f"Atividade {item.codigo_atividade} sem vinculo com EAP",
                })

        return divergencias
    
    @classmethod
    def consolidar_valores_por_eap(cls, plano_fisico_id):
        """
        Consolida valores do cronograma mapeado para a EAP.
        
        Returns:
            dict: Dicionário com plano_contas_id -> valores consolidados
        """
        analise = cls.analisar_vinculos(plano_fisico_id)
        consolidados = {}

        for (item_id, pc_id), valor_planejado in analise["contribuicoes"].items():
            if item_id in analise["itens_invalidos"]:
                continue
            if pc_id not in consolidados:
                plano_contas = next((eap for eap in analise["item_to_eaps"].get(item_id, []) if eap.pk == pc_id), None)
                consolidados[pc_id] = {
                    'plano_contas': plano_contas,
                    'valor_planejado': Decimal("0.00"),
                    'valor_realizado': Decimal("0.00"),
                    'percentual_concluido': 0,
                }
            item = next(item for item in analise["itens"] if item.pk == item_id)
            item_total = analise["valores_item"].get(item_id, Decimal("0.00"))
            participacao = (valor_planejado / item_total) if item_total > 0 else Decimal("0.00")
            consolidados[pc_id]['valor_planejado'] += valor_planejado
            consolidados[pc_id]['valor_realizado'] += (item.valor_realizado or Decimal("0.00")) * participacao

        for pc_id in consolidados:
            dados = consolidados[pc_id]
            if dados['valor_planejado'] > 0:
                dados['percentual_concluido'] = round(
                    (dados['valor_realizado'] / dados['valor_planejado']) * 100, 1
                )

        return consolidados
