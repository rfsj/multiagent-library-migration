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

**Descrição**: Analisa o projeto em modo read-only e gera um plano ordenado de steps, com análise de dependência cross-file (DataFrame flow).

**Capacidades**:
- Detecta arquivos afetados via análise AST de imports e chamadas de API
- Analisa fluxo producer→consumer de DataFrames entre arquivos
- Divide arquivos em steps por símbolo quando independentes (auditabilidade granular)
- Agrupa arquivos acoplados em steps atômicos (corretude de testes)
- Detecta se `requirements.txt` precisa ser atualizado com target library

**Arquivo de output**: `logs/diagnosis_plan.json`

---

### 3. MigrationAgent — Execução de Steps

**Descrição**: Recebe um step planejado e produz código migrado usando LLM + validações determinísticas.

**Pipeline interno**:
1. LLM migration com prompt contextual (padrão API mapping + pattern analysis)
2. Symbol scoping (preserva código fora do escopo do step)
3. Syntax validation Python 3.9
4. Re-scan pós-migração (detecta padrões pandas não convertidos)
5. AST fallback determinístico (se `MIGRATION_AST_FALLBACK=1`)
6. Implementation review (se DataFrame flow analysis presente)

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

### 7. Pattern Scanner — Detecção Estática de Padrões Confusos

**Descrição**: Analisa código-fonte via AST e detecta padrões pandas que o LLM tende a não converter corretamente.

**Padrões detectados**: `.sort_values()`, `.groupby()`, `.merge()`, `.reset_index()`, `.drop_duplicates()`, `.apply(lambda)`, `.pivot_table()`, `.fillna()`, `.isna()`, `.notna()`, `.astype()`, `.isin()`, `df["col"] = expr`, colunas dependentes sequenciais

**Output**: Lista de `PatternHit(line, pattern_id, guidance)` com instrução de conversão específica por padrão

**Uso**: Alimenta o prompt do MigrationAgent (seção MANDATORY transformations) e o re-scan pós-migração

---

### 8. AST Transformer — Fallback Determinístico

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

### 9. Benchmark Runner

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

### 10. Importação de Projetos Externos

**Descrição**: Importa um projeto GitHub para o formato de benchmark auditável.

```bash
python3 scripts/import_github_project.py <task_id> <repo_url>
```

Faz clone temporário, remove `.git`, copia para `benchmark/<task_id>/input_project/` e cria `metadata.json`.

---

### 11. Preparação de Baseline

**Descrição**: Corrige problemas básicos de baseline em projetos importados (sem migrar a biblioteca).

```bash
# Ver propostas sem aplicar
python3 scripts/prepare_benchmark_project.py <task_id>

# Aplicar correções
python3 scripts/prepare_benchmark_project.py <task_id> --apply
```

Exemplos de correções: ajuste de `pytest.ini` de `test` para `tests`, criação de módulo `load_config` quando importado mas ausente.

## Funcionalidades em Desenvolvimento / Não Testadas

- **Tasks 4 e 5**: `task_004_pyjanitor` e `task_005_ydata_profiling` foram importadas mas ainda não executadas. Projetos reais com mais complexidade.
- **Expansão para outras bibliotecas**: Arquitetura suporta qualquer par source→target library, mas nenhum além de pandas→polars foi validado.
- **false_positives / false_negatives**: Campos nas métricas existem mas retornam sempre 0 (não implementados).

## Funcionalidades Intencionalmente Fora do Escopo

- **Migração de testes**: O framework nunca modifica arquivos de teste; isso é uma restrição de design, não uma limitação.
- **Refactoring cosmético**: O MigrationAgent só faz o mínimo necessário para migrar; linting e formatação são excluídos.
- **Resolução automática de breaking changes de API**: Operações sem equivalente direto (ex: `pd.eval()`, `pd.melt()`) são marcadas para revisão manual.
