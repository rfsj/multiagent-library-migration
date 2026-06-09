# Funcionalidades

## Funcionalidades Principais

### 1. Pipeline de Migração Multiagente

**Descrição**: Executa uma migração pandas→polars em múltiplas etapas — diagnóstico, migração, validação — com feedback estruturado entre elas.

**Casos de Uso**: Migrar um projeto Python que usa pandas para polars, preservando comportamento dos testes.

**Entrada**: `benchmark/<task_id>/input_project/` (projeto Python com pandas)

**Saída**: `experiments/runs/<task_id>_<timestamp>/report.json` + `diff.patch` + artefatos de auditoria

**Componentes envolvidos**: `scripts/run_task.py`, `src/graph/workflow.py`, todos os 5 agentes

---

### 2. DiagnosisAgent — Planejamento de Migração

**Descrição**: Analisa o projeto em modo read-only e gera um plano ordenado de steps, usando dois passes LLM distintos.

**Estratégia de dois passes**:
1. **Passe 1 — DataFrameFlowAnalysis**: LLM analisa apenas o fluxo produtor→consumidor de DataFrames entre arquivos. Produz `DataFrameFlowAnalysis` com symbols, groups e planning_strategy.
2. **Passe 2 — DiagnosisPlan**: LLM recebe o resultado do passe 1 como contexto e gera o plano completo de migração com todos os steps.

A separação evita que o LLM tente analisar dependências e planejar steps ao mesmo tempo — o passe 1 é mais focado e produz resultado mais preciso.

**Capacidades**:
- Detecta arquivos afetados via análise AST de imports e chamadas de API
- Analisa fluxo producer→consumer de DataFrames entre arquivos (passe 1)
- Divide arquivos em steps por símbolo quando independentes, em ordem topológica
- Agrupa arquivos acoplados em steps atômicos (strategy: `grouped_before_consumers`)
- Detecta se `requirements.txt` precisa ser atualizado com target library
- Fallback de parsing: usa `StrOutputParser` + JSON manual em vez de `with_structured_output` para o plano (mais robusto a modelos que retornam texto em vez de function call)
- Deduplicação e sanitização de steps planejados com avisos auditáveis

**Arquivos de output**: `logs/diagnosis_plan.json`, `logs/dataframe_flow_analysis.json`, `logs/project_audit.json`

---

### 3. MigrationAgent — Execução de Steps

**Descrição**: Recebe um step planejado e produz código migrado usando LLM + múltiplas camadas de validação determinística.

**Pipeline interno (por arquivo)**:
1. LLM migration com prompt contextual (pattern analysis + retry feedback se retry)
2. Symbol scoping (AST merge — substitui apenas os símbolos permitidos no arquivo original)
3. Syntax validation Python 3.9 (detecta PEP 604 `X | Y` sem `__future__` annotations)
4. Re-scan pós-migração (detecta padrões pandas remanescentes → retry automático uma vez)
5. AST fallback determinístico (se `MIGRATION_AST_FALLBACK=1`)
6. Implementation review (se `dataframe_flow_analysis` presente no step)
7. Limpeza automática de `import pandas as pd` não utilizado após symbol scoping

**Grouped steps (dois-fases atômicas)**:
- Fase 1: migra todos os arquivos do grupo em memória (sem escrever em disco)
- Fase 2: só escreve se TODOS os arquivos produziram output LLM válido (evita estado híbrido parcial)
- Feedback de retry é escopado por arquivo (`_scoped_retry_feedback`) para evitar que erros de um arquivo contaminem a migração de outro

**Suporte a hash-locked requirements**: Quando `requirements.txt` usa `--hash=sha256:`, o agente resolve os hashes da versão mais recente via PyPI API e insere as entradas corretas.

**Arquivo de output**: `logs/step_NNN_migration.json`

---

### 4. ValidationAgent — Verificação por Step e Final

**Descrição**: Verifica deterministicamente se um step de migração é válido e emite um verdict estruturado.

**Verificações por step**:
- `diff`: arquivos modificados fora de `allowed_files` → `out_of_scope_changes`
- `pytest`: suite completa deve passar
- `AST scan`: conta imports e chamadas da biblioteca origem no arquivo migrado

**Validação final** (após todos os steps):
- `pytest`: suite completa deve passar
- Scan de todo o projeto: `old_imports_remaining == 0` e `unmigrated_uses == 0`
- `diff` geral: sem mudanças fora do escopo planejado

**Verdict possíveis**: `accepted` | `rejected_implementation` | `rejected_plan`

---

### 5. RepairAgent — Geração de Plano de Reparo

**Descrição**: Transforma evidência de validação em um plano estruturado que o MigrationAgent usa no retry.

**Categorias de falha detectadas**:

| Categoria | Exemplo de falha |
|---|---|
| `polars_api_error` | `.groupby()` → precisa `.group_by()` |
| `producer_consumer_type_mismatch` | summaries.py chama `.group_by()` em pandas DataFrame |
| `dependent_expression_order` | `ColumnNotFoundError` por criar e referenciar coluna no mesmo `with_columns` |
| `unsupported_operation` | `df["col"] = value` em Polars DataFrame |
| `semantic_equivalence_error` | Saída com linhas em ordem diferente |
| `unknown` | Insuficiente evidência para categorizar |

**Output inclui**: `instructions_for_migration_agent`, `acceptance_criteria`, `must_not_do`

---

### 6. ImplementationReviewAgent — Revisão Pré-Validação

**Descrição**: Revisa o código migrado ANTES de ir para validação, focando em erros típicos de migração DataFrame.

**Cheques obrigatórios**:
- Símbolos públicos removidos acidentalmente
- Coluna criada e referenciada no mesmo `with_columns` (ColumnNotFoundError)
- Atribuição de coluna com sintaxe pandas em Polars DataFrame
- Pandas imports restantes no escopo migrado
- Sort descending list não inverte ascending corretamente
- `nulls_last` faltando para colunas com nulos
- `nunique` migrado como `count` em vez de `n_unique`
- `drop_duplicates` sem `maintain_order=True`

Só é invocado quando `dataframe_flow_analysis` está presente no step (projetos com DataFrames).

---

### 7. Ferramentas Determinísticas de Suporte

**test_runner.py — Wrapper de pytest**:
- `run_pytest(project_dir, log_file)` — executa `pytest -q` via subprocess, grava stdout em `log_file`, retorna `{"status": "passed"|"failed", "passed": bool, "returncode": int}`

**diff_analyzer.py — Comparação de diretórios**:
- `changed_files(before, after)` — lista arquivos modificados entre dois snapshots
- `unified_diff(before, after)` — executa `diff -ruN` e retorna texto do patch
- `analyze_diff(before, after, allowed_files)` — retorna `out_of_scope_changes` e `out_of_scope_files`

**output_comparator.py — Normalização de records**:
- `normalize_records(value)` — converte Polars DataFrame (`.to_dicts()`) ou pandas DataFrame (`.to_dict(orient="records")`) para `list[dict]`, permitindo comparações agnósticas à biblioteca

**patch_applier.py — Escrita atômica**:
- `write_text_if_changed(path, content)` — só escreve se o conteúdo mudou; retorna `True` se escreveu. Evita toques desnecessários em disco nos snapshots.

---

### 8. Pattern Scanner — Detecção Estática de Padrões Confusos

**Descrição**: Analisa código-fonte via AST e detecta padrões pandas que o LLM tende a não converter corretamente.

**Padrões detectados**: `.sort_values()`, `.groupby()`, `.merge()`, `.reset_index()`, `.drop_duplicates()`, `.apply(lambda)`, `.pivot_table()`, `.fillna()`, `.isna()`, `.notna()`, `.astype()`, `.isin()`, `df["col"] = expr`, colunas dependentes sequenciais

**Output**: Lista de `PatternHit(line, pattern_id, guidance)` com instrução de conversão específica por padrão

**Uso**: Alimenta o prompt do MigrationAgent (seção MANDATORY transformations) e o re-scan pós-migração

---

### 9. AST Transformer — Fallback Determinístico

**Descrição**: Aplica transformações mecânicas pandas→polars que não dependem de julgamento do LLM. Ativo quando `MIGRATION_AST_FALLBACK=1`.

**Transformações**:

| Padrão pandas | Polars equivalente |
|---|---|
| `df["col"] = rhs` | `df = df.with_columns((polars_rhs).alias("col"))` |
| `.reset_index(drop=True)` | Remove a chamada |
| `.sort_values(by, ascending=[T,F])` | `.sort(by, descending=[F,T])` |

**Limitações**: RHS com métodos pandas ainda não convertidos (ex: `.fillna()`) é pulado com log em `skipped`. Preserva formatação e comentários — aplica substituições linha a linha, sem `ast.unparse` no arquivo inteiro.

**Toggle**: `MIGRATION_AST_FALLBACK=1` no `.env` ou como variável de ambiente

---

### 10. Benchmark Runner

**Descrição**: Executa uma ou todas as tasks de benchmark e gera relatórios comparáveis.

```bash
# Uma task
python3 scripts/run_task.py task_001_read_csv_filter

# Todas as tasks
python3 scripts/run_all.py

# Com skip de instalação de dependências
python3 scripts/run_task.py task_001_read_csv_filter --skip-install
```

---

### 11. Importação de Projetos Externos

**Descrição**: Importa um projeto GitHub para o formato de benchmark auditável.

```bash
python3 scripts/import_github_project.py <task_id> <repo_url>
```

Faz clone temporário, remove `.git`, copia para `benchmark/<task_id>/input_project/` e cria `metadata.json`.

---

### 12. Criação de Tasks de Benchmark

**Descrição**: Cria a estrutura de diretórios e arquivos para uma nova task de benchmark do zero.

```bash
python3 scripts/create_benchmark_task.py <task_id>
```

Gera `benchmark/<task_id>/input_project/` com estrutura básica e `metadata.json` template.

---

### 13. Preparação de Baseline

**Descrição**: Corrige problemas básicos de baseline em projetos importados (sem migrar a biblioteca).

```bash
# Ver propostas sem aplicar
python3 scripts/prepare_benchmark_project.py <task_id>

# Aplicar correções
python3 scripts/prepare_benchmark_project.py <task_id> --apply
```

Exemplos de correções: ajuste de `pytest.ini` de `test` para `tests`, criação de módulo `load_config` quando importado mas ausente.

## Funcionalidades em Desenvolvimento / Não Testadas

- **Tasks sintéticas 019–026**: Criadas mas ainda não validadas com execução completa. Cobrem padrões mais complexos: pipelines multi-arquivo, `groupby().transform()`, `apply(axis=1)`, `period`, `expanding`, `concat`, `where`.
- **Expansão para outras bibliotecas**: Arquitetura suporta qualquer par source→target library, mas nenhum além de pandas→polars foi validado.
- **false_positives / false_negatives**: Campos nas métricas existem mas retornam sempre 0 (não implementados).

## Funcionalidades Intencionalmente Fora do Escopo

- **Migração de testes**: O framework nunca modifica arquivos de teste; isso é uma restrição de design, não uma limitação.
- **Refactoring cosmético**: O MigrationAgent só faz o mínimo necessário para migrar; linting e formatação são excluídos.
- **Resolução automática de breaking changes de API**: Operações sem equivalente direto (ex: `pd.eval()`, `pd.melt()`) são marcadas para revisão manual.
