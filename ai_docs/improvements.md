# Melhorias e Otimizações Sugeridas

## Alta Prioridade

### 1. Expandir o AST Transformer

O transformer atual cobre `df["col"] = rhs`, `.reset_index()` e `.sort_values()`. Padrões que o LLM também erra e ainda faltam:

| Padrão pandas | Polars equivalente | Dificuldade |
|---|---|---|
| `.fillna(v)` em expressão de coluna | `.fill_null(v)` | Baixa |
| `.copy()` em DataFrame | Remover (Polars é imutável) | Baixa |
| `df[df["col"] > 0]` (boolean indexing) | `df.filter(pl.col("col") > 0)` | Média |
| `~series.isin([...])` | `.is_in([...]).not_()` | Baixa |
| `.rename(columns={...})` | `.rename({...})` | Baixa |

**Arquivo**: `src/tools/ast_transformer.py`

---

### 2. Corrigir llm_model no report.json

`environment.llm_model` sempre mostra `"rule-based-mvp"` — hardcoded em `src/evaluation/report_generator.py:26`. A variável `LLM_MODEL_NAME` já existe no `.env` mas não é lida.

**Fix**: Ler `os.environ.get("LLM_MODEL_NAME", "unknown")` em `environment_versions()`.

**Arquivo**: `src/evaluation/report_generator.py`

---

### 3. Rodar e Validar task_002, task_004 e task_005

- `task_002_complex_pandas_ops`: Trava com `gemini-2.5-pro` (timeout silencioso) e falha com `flash-lite` sem AST. Potencialmente resolve com `gemini-3.1-flash-lite` + `MIGRATION_AST_FALLBACK=1`.
- `task_004_pyjanitor` e `task_005_ydata_profiling`: Importadas mas nunca executadas. São o teste mais importante de generalização do framework para projetos reais.

---

## Média Prioridade

### 4. Script de Comparação de Runs

Não há forma fácil de comparar resultados entre runs com diferentes modelos/configurações. Um script `scripts/compare_runs.py` que lê múltiplos `report.json` e gera tabela resumida seria valioso para o objetivo de pesquisa.

**Exemplo de output esperado**:
```
task                          | modelo              | AST | status  | retries | accepted
task_001_read_csv_filter      | gemini-3.1-flash-lite | ✓  | success | 0       | 6/6
task_001_read_csv_filter      | gemini-2.5-flash-lite | ✗  | failed  | 7       | 1/6
task_003_multi_file_pandas_ops| gemini-3.1-flash-lite | ✓  | success | 1       | 2/2
```

---

### 5. Implementar false_positives / false_negatives nas Métricas

Os campos existem em `report.json` mas sempre retornam 0 (`src/evaluation/metrics.py`). Para pesquisa:
- **false_positive**: Framework aceitou uma migração incorreta (código passou testes mas tem erros semânticos)
- **false_negative**: Framework rejeitou uma migração correta (código estava certo mas testes falharam por outro motivo)

---

### 6. Timeout Explícito no LLM

`gemini-2.5-pro` trava silenciosamente em arquivos grandes sem erro útil — o processo fica pendurado indefinidamente. Adicionar `timeout=` na chamada LangChain e tratar `TimeoutError` com abort limpo e mensagem descritiva.

**Arquivo**: `src/llm.py` ou na criação do LLM em cada agente

---

### 7. Expandir o Pattern Scanner

Adicionar detecção de padrões ainda não cobertos em `src/tools/pattern_scanner.py`:

| Padrão | ID sugerido | Guidance |
|---|---|---|
| `.copy()` | `copy_call` | `.copy() → remover; Polars DataFrames são imutáveis` |
| `df[mask]` boolean indexing | `boolean_indexing` | `df[mask] → df.filter(mask_as_polars_expr)` |
| `.rename(columns={...})` | `rename_columns` | `.rename(columns={old: new}) → .rename({old: new})` |
| `.value_counts()` | `value_counts` | `.value_counts() → .group_by(col).len().sort("len", descending=True)` |
| `.str.contains(...)` | `str_contains` | `.str.contains(pat) → .str.contains(pat) (mesmo nome, mas retorna Expr em Polars)` |

---

## Otimizações Experimentais

### 8. Registrar Modelo Usado por Step nos Logs

Além de corrigir o `report.json`, incluir o modelo usado em cada `step_NNN_migration.json` para rastreabilidade em experimentos com múltiplos modelos rodando em paralelo.

---

### 9. Modo Diagnose-Only

Um comando `python3 scripts/diagnose_only.py <task_id>` que roda apenas o `DiagnosisAgent` e imprime o plano sem executar a migração. Útil para:
- Validar planejamento antes de pagar pelos tokens de migração
- Debugar por que um projeto está sendo mal planejado
- Testar mudanças no prompt do DiagnosisAgent

---

### 10. Múltiplas Runs por Task para Análise Estatística

`run_all.py` executa cada task uma única vez. LLMs são não-determinísticos — a mesma task pode ter resultado diferente em cada run. Para pesquisa confiável:
- Rodar 3–5 vezes por task/modelo
- Calcular `pass@k`, taxa de sucesso, distribuição de retries
- Agregar em `experiments/summary_<timestamp>.json`

---

## Referências Cruzadas

- Padrões não cobertos pelo AST transformer → ver [gotchas.md](gotchas.md) seções G5–G9
- Modelo recomendado por tipo de task → ver [stack.md](stack.md#guia-de-seleção-de-modelo)
- Regras de generalização (o que NÃO fazer) → ver [business-rules.md](business-rules.md#r3--nenhuma-regra-específica-por-projeto)
