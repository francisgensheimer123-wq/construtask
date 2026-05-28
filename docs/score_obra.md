# Cálculo do Score da Obra

O score da obra é calculado pelo `IndicadoresService.score_obra`.

Ele soma 4 componentes, cada um valendo até **25 pontos**:

1. **Prazo**
2. **Custo**
3. **Governança**
4. **Riscos e qualidade**

Pontuação máxima total: **100 pontos**.

## 1. Base do Cálculo

Para calcular o score, o sistema usa:

- EVA da obra, principalmente:
  - `SPI`: eficiência de prazo
  - `CPI`: eficiência de custo
- Alertas operacionais ativos
- Alertas fora do SLA
- Riscos ativos
- Não conformidades abertas

Alertas considerados ativos:

```text
ABERTO
EM_TRATAMENTO
JUSTIFICADO
```

Riscos considerados ativos:

```text
Todos, exceto FECHADO e CANCELADO
```

Não conformidades consideradas abertas:

```text
Todas, exceto ENCERRADA e CANCELADA
```

## 2. Como um Alerta Entra no Score

Nem todo alerta ativo penaliza o score.

O alerta só entra como "pendente para score" quando:

```text
dias sem movimentação >= alerta_sem_workflow_dias
```

ou

```text
dias em aberto > alerta_prazo_solucao_dias
```

Esses prazos vêm dos parâmetros da empresa em `ParametroAlertaEmpresa`.

Ou seja: alerta novo ou tratado recentemente pode estar ativo, mas ainda não penalizar o score.

## 3. Componente Prazo

Máximo: **25 pontos**

Começa com:

```text
25 pontos
```

Penalidade por SPI:

Se `SPI < 1`, calcula:

```text
(1 - SPI) * 20
```

Mas limitado a no máximo:

```text
15 pontos
```

Exemplo:

```text
SPI = 0,80
penalidade = (1 - 0,80) * 20 = 4 pontos
```

Além disso, penaliza alertas pendentes de prazo.

Códigos considerados de prazo:

```text
PLAN-PROG-001
PLAN-PROG-002
PLAN-PROG-003
```

Cada alerta pendente desses códigos tira:

```text
2 pontos
```

Limitado a:

```text
10 pontos
```

Fórmula:

```text
Prazo = 25 - penalidade_SPI - penalidade_alertas_prazo
```

Nunca fica abaixo de zero.

## 4. Componente Custo

Máximo: **25 pontos**

Começa com:

```text
25 pontos
```

Penalidade por CPI:

Se `CPI < 1`, calcula:

```text
(1 - CPI) * 20
```

Limitado a:

```text
15 pontos
```

Exemplo:

```text
CPI = 0,75
penalidade = (1 - 0,75) * 20 = 5 pontos
```

Também penaliza alertas pendentes de custo.

Códigos considerados de custo:

```text
COST-PROG-001
COST-PROG-002
COST-BUD-001
```

Cada alerta pendente desses códigos tira:

```text
2 pontos
```

Limitado a:

```text
10 pontos
```

Fórmula:

```text
Custo = 25 - penalidade_CPI - penalidade_alertas_custo
```

Nunca fica abaixo de zero.

## 5. Componente Governança

Máximo: **25 pontos**

Esse componente olha alertas pendentes ligados a lastro operacional, medições, notas fiscais e suprimentos.

Códigos considerados:

```text
CONT-MED-001
MED-NF-001
NF-RAT-001
PLAN-SUP-001
```

Cada alerta pendente desses códigos tira:

```text
3 pontos
```

Limitado a:

```text
25 pontos
```

Fórmula:

```text
Governança = 25 - min(total_alertas_governanca * 3, 25)
```

## 6. Componente Riscos e Qualidade

Máximo: **25 pontos**

Esse componente considera:

- Riscos críticos
- Riscos altos
- Não conformidades abertas
- Alertas críticos pendentes
- Alertas pendentes de risco/qualidade

Classificação dos riscos:

```text
Risco crítico: nível > 15
Risco alto: nível entre 10 e 15
```

Penalidade por riscos, NCs e alertas críticos:

```text
riscos_críticos * 4
+ riscos_altos * 2
+ quantidade_de_NCs_abertas
+ alertas_críticos_pendentes
```

Limitado a:

```text
25 pontos
```

Depois soma outra penalidade por alertas específicos de risco/qualidade.

Códigos considerados:

```text
RISK-DUE-001
RISK-ACC-001
NC-EVO-001
```

Cada alerta pendente desses códigos tira:

```text
2 pontos
```

Limitado a:

```text
10 pontos
```

Fórmula:

```text
Riscos e qualidade =
25
- penalidade_riscos
- penalidade_alertas_risco_qualidade
```

Nunca fica abaixo de zero.

## 7. Soma Final

O score final é:

```text
Score =
Prazo
+ Custo
+ Governança
+ Riscos e qualidade
```

Depois o sistema limita o resultado entre:

```text
0 e 100
```

E arredonda para duas casas decimais.

## 8. Faixas do Score

A faixa final é:

```text
>= 85     Excelente
>= 70     Saudável
>= 50     Atenção
< 50      Crítico
```

## 9. Faixas de Cada Componente

Cada componente também recebe um nível próprio, proporcional ao máximo dele.

Como cada componente vale 25 pontos:

```text
>= 85% do componente     excelente
>= 70%                   saudável
>= 50%                   atenção
< 50%                    crítico
```

Na prática:

```text
21,25 a 25,00     excelente
17,50 a 21,24     saudável
12,50 a 17,49     atenção
0,00 a 12,49      crítico
```

## Resumo

O score começa em **100**, dividido em quatro blocos de **25 pontos**, e perde pontos conforme atraso de prazo, ineficiência de custo, alertas fora do SLA, riscos ativos e não conformidades abertas.
