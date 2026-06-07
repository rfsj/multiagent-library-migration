# Gotchas e Conhecimento Tácito

## Limitações de Modelos LLM

### G1 — gemini-2.5-flash-lite Não Converte df["col"] = expr

**Sintoma**: Step falha com `TypeError: DataFrame object does not support Series assignment by index` após 3 retries.

**Causa**: O modelo converte o import (`pd` → `pl`) e `pd.read_csv()` → `pl.read_csv()`, mas consistentemente deixa atribuições de coluna pandas (`orders["col"] = expr`) sem converter para `with_columns`. Isso acontece mesmo com feedback explícito nas retries.

**Solução**: Ativar `MIGRATION_AST_FALLBACK=1` no `.env`. O AST transformer converte deterministicamente `df["col"] = rhs` → `df = df.with_columns(rhs.alias("col"))` para todos os casos onde o RHS não tem mais métodos pandas.

**Contexto**: `gemini-2.5-pro` resolve sem AST fallback. `gemini-3.1-flash-lite` + AST fallback tem resultado equivalente ao pro com muito menos custo/latência.

---

### G2 — gemini-2.5-pro Dá Timeout 504 em Arquivos Grandes

**Sintoma**: Task trava indefinidamente na chamada de diagnosis sem retornar 504 explícito — apenas para de responder.

**Causa**: `gemini-2.5-pro` é mais lento e projetos com arquivos grandes (>150 linhas) excedem o timeout da API silenciosamente.

**Solução**: Usar `gemini-3.1-flash-lite` + `MIGRATION_AST_FALLBACK=1` para tasks com arquivos maiores. Para task_002 (processing.py, ~250 linhas), o pro consistentemente trava.

---

### G3 — unmigrated_uses=0 Não Garante Migração Correta

**Sintoma**: Validation aceita um step mas o código migrado ainda tem APIs pandas que causam falha em runtime.

**Causa**: `_ast_count_source_usage` conta apenas referências `pd.xxx` (atributos do alias da biblioteca). Chamadas de métodos pandas em variáveis locais (`.sort_values()`, `.groupby()`) não são contadas porque não têm o prefixo `pd.`.

**Implicação**: Um step com `unmigrated_uses=0` pode ter `.sort_values()` ou `df["col"] = expr` e ainda passar essa checagem. O `pytest` é a verdadeira barreira de segurança.

**Solução**: O `pattern_scanner.py` detecta esses padrões por nome de método e os inclui no prompt do LLM. O AST fallback resolve deterministicamente os que o LLM erra.

---

### G4 — Modelo Gera Python 3.10+ Syntax em Projeto 3.9

**Sintoma**: Validation rejeita com `Syntax error: X | Y union syntax causes TypeError at runtime on Python 3.9`.

**Causa**: LLMs tendem a usar `str | None` (PEP 604) que é válido sintaticamente no 3.9 mas falha em runtime quando annotations são avaliadas.

**Solução**: `MigrationAgent._validate_python39_syntax()` detecta esse padrão via AST e força retry com feedback: "adicione `from __future__ import annotations` ou use `Optional[str]`."

---

## Armadilhas pandas→polars

### G5 — Reset Index Deve Ser Deletado, Não Convertido

**Sintoma**: Código com `.reset_index(drop=True)` após migração causa `AttributeError: 'DataFrame' object has no attribute 'reset_index'`.

**Causa**: Polars DataFrames não têm índice de linha. `.reset_index(drop=True)` não tem equivalente — deve ser simplesmente removido.

**Solução**: Pattern scanner detecta e o AST transformer remove. O prompt do MigrationAgent inclui: `"Delete lines that don't apply in {target_library} (e.g., .reset_index(drop=True) for polars)."`.

---

### G6 — Colunas Dependentes Não Podem Ir no Mesmo with_columns

**Sintoma**: `ColumnNotFoundError: "gross_revenue" not found` ao tentar usar `pl.col("gross_revenue")` no mesmo `with_columns` onde ela é criada.

**Causa**: Em Polars, todas as expressões dentro de um único `with_columns(...)` são avaliadas **simultaneamente** contra o DataFrame original. Uma coluna criada dentro do mesmo call não existe ainda quando outras expressões são avaliadas.

**Exemplo**:
```python
# ERRADO
df = df.with_columns([
    (pl.col("qty") * pl.col("price")).alias("gross"),
    (pl.col("gross") * 0.9).alias("net"),  # "gross" não existe ainda!
])

# CORRETO
df = df.with_columns((pl.col("qty") * pl.col("price")).alias("gross"))
df = df.with_columns((pl.col("gross") * 0.9).alias("net"))
```

**Detecção**: `pattern_scanner._detect_dependent_assignments()` identifica este padrão via AST e o reporta como `dependent_column_assign` com a instrução de split.

---

### G7 — Null Ordering é Invertido entre pandas e Polars

**Sintoma**: Tests falham com mismatch de linhas no início/fim de resultado ordenado. Nulos aparecem em posição diferente.

**Causa**:
- pandas `sort_values()`: `na_position="last"` por padrão → nulos no **final**
- polars `sort()`: nulos no **início** por padrão

**Solução**: Adicionar `nulls_last=True` ao `sort()` do Polars quando a coluna pode ter valores nulos (especialmente após conversão de datas com `strict=False`).

**Onde importa**: Qualquer coluna criada com `pl.col("x").str.to_date(strict=False)` ou `str.to_datetime(strict=False)` pode ter nulos.

---

### G8 — Pivot Output Tem Ordem de Colunas Não-Determinística

**Sintoma**: Test falha com `AssertionError: DataFrame are different` — colunas têm valores corretos mas em ordem diferente.

**Causa**: `pd.pivot_table()` retorna colunas em ordem lexicográfica determinística. `polars.pivot()` não garante ordem das colunas pivotadas.

**Solução**:
```python
# Após pivot, ordenar explicitamente
index_cols = ["month"]
product_cols = sorted([c for c in matrix.columns if c not in index_cols])
matrix = matrix.select([*index_cols, *product_cols])
```

O prompt do MigrationAgent inclui este padrão explicitamente.

---

### G9 — API de Pivot Mudou no Polars 1.17.x

**Sintoma**: `TypeError: pivot() got an unexpected keyword argument 'columns'`

**Causa**: Em Polars 1.17.x, o parâmetro foi renomeado de `columns=` para `on=`. `fill_value=` também mudou para `.fill_null()` encadeado.

**Solução**:
```python
# Polars 1.17.x
matrix = df.pivot(
    values="net_revenue",
    index="month",
    on="product",          # não "columns="
    aggregate_function="sum",
).fill_null(0.0)           # não fill_value=
```

**Versão testada**: `polars==1.17.1`.

---

## Comportamentos Não-Óbvios do Framework

### G10 — Testes Cross-File Quebram ao Migrar Produtores Individualmente

**Sintoma**: Step 001 (loaders.py) migrado para Polars → Step 002 (summaries.py) testa e falha com `AttributeError: 'DataFrame' object has no attribute 'groupby'` em summaries.py que ainda não foi migrado.

**Causa**: O pytest roda **todos** os testes, incluindo testes de summaries.py. Quando loaders.py retorna Polars DataFrames, summaries.py (ainda pandas) quebra ao receber esses DataFrames.

**Solução**: O framework agrupa arquivos acoplados em um **step atômico** via `grouped_before_consumers` strategy. Todos os arquivos do grupo migram juntos, e os testes só rodam no final quando todos estão em Polars.

**Por que não fica óbvio**: `unmigrated_uses=0` para loaders.py indica migração bem-sucedida, mas os testes falham por causa de summaries.py. Isso pode parecer um bug no framework mas é comportamento esperado do pytest.

---

### G11 — report.json Sempre Mostra llm_model="rule-based-mvp"

**Sintoma**: Campo `environment.llm_model` em `report.json` sempre é `"rule-based-mvp"`, independente do modelo usado.

**Causa**: Hardcoded em `src/evaluation/report_generator.py:26`. O modelo real está no `.env` (`LLM_MODEL_NAME`), mas o campo de report não foi atualizado para lê-lo.

**Workaround**: Verificar o `.env` da run para saber qual modelo foi usado. O `LLM_MODEL_NAME` do `.env` é apenas um metadado registrado pelo usuário, não lido pelo framework.

---

### G12 — benchmark/ É Ignorado pelo Git

**Sintoma**: Clones do repositório não têm as tasks de benchmark. Depois de clonar, `run_task.py` falha com "task directory not found".

**Causa**: `.gitignore` inclui `benchmark/` para evitar versionar clones externos e dados de experimento local.

**Solução**: Criar tasks manualmente (`scripts/create_benchmark_task.py`) ou importar via `scripts/import_github_project.py`. As tasks fornecidas (task_001 a task_005) precisam ser recriadas em cada clone.

---

### G13 — allowed_symbols=[]: Escopo É o Arquivo Inteiro

**Sintoma**: Com `allowed_symbols=[]`, o MigrationAgent migra o arquivo inteiro, não apenas parte.

**Causa**: `allowed_symbols=[]` é interpretado como "sem restrição de símbolo" = "arquivo inteiro é permitido". Isso é diferente de `allowed_symbols=["fn1"]` que limita a exatamente esse símbolo.

**Contexto**: Steps gerados para arquivos de arquivos acoplados (`files=[...]`) sempre usam `allowed_symbols=[]` porque precisam migrar os arquivos por inteiro para manter coerência de tipo.

---

## Dicas de Desenvolvimento

### Ambiente Local

```bash
# Setup mínimo
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Editar .env com sua chave

# Verificar que testes do framework passam
python3 -m pytest tests/ -q

# Rodar task mais simples primeiro
python3 scripts/run_task.py task_001_read_csv_filter
```

### Debugging de uma Run

```bash
# Ver o plano de diagnóstico
cat experiments/runs/task_001_*/logs/diagnosis_plan.json | python3 -m json.tool | head -50

# Ver o que o LLM produziu no último retry
cat experiments/runs/task_001_*/logs/step_001_migration.json

# Ver o veredicto e feedback
cat experiments/runs/task_001_*/logs/step_001_verdict.json

# Ver o diff final
cat experiments/runs/task_001_*/diff.patch
```

#### Inspecionando chamadas ao LLM (`llm_proxy.jsonl`)

Cada run gera `logs/llm_proxy.jsonl` com **todos os requests e responses** trocados com o LLM em ordem cronológica. Se um agente produziu algo errado, quebrou no meio ou gerou saída inesperada, este é o primeiro lugar para olhar — você vê exatamente o prompt que entrou e o payload bruto que voltou (incluindo `function_call` para structured output do Gemini e `tool_calls` para Anthropic).

```bash
# Contar quantas chamadas foram feitas
wc -l experiments/runs/task_001_*/logs/llm_proxy.jsonl

# Ver todos os prompts enviados (campo messages[0][1].content = human message)
cat experiments/runs/task_001_*/logs/llm_proxy.jsonl \
  | python3 -c "
import sys, json
for line in sys.stdin:
    e = json.loads(line)
    if e['event'] == 'request':
        print('=== REQUEST', e['ts'], '===')
        for msg in e['messages'][0]:
            print(f\"[{msg['role']}]\", msg['content'][:300])
        print()
"

# Ver a resposta bruta de uma chamada específica (ex: segunda resposta)
python3 -c "
import json
lines = open('experiments/runs/task_001_*/logs/llm_proxy.jsonl').readlines()
resps = [json.loads(l) for l in lines if json.loads(l)['event'] == 'response']
print(json.dumps(resps[1], indent=2, ensure_ascii=False))
"

# Filtrar só erros de structured output (text vazio e sem function_call)
cat experiments/runs/task_001_*/logs/llm_proxy.jsonl \
  | python3 -c "
import sys, json
for line in sys.stdin:
    e = json.loads(line)
    if e['event'] == 'response':
        gens = e.get('generations', [[]])[0]
        if gens and not gens[0].get('text') and not gens[0].get('function_call') and not gens[0].get('tool_calls'):
            print('EMPTY RESPONSE:', e['ts'], e['run_id'])
"
```

> **Nota**: Respostas de structured output (`with_structured_output`) sempre chegam com `text: ""` — o payload real fica em `function_call.args` (Gemini) ou `tool_calls[0].function.arguments` (Anthropic). Isso é esperado.

### O Que Eu Gostaria de Ter Sabido

- `MIGRATION_AST_FALLBACK=1` é essencial para modelos smaller (flash-lite, flash); sem ele, esses modelos falham consistentemente em column assignments
- `gemini-2.5-pro` funciona bem para tasks simples mas trava silenciosamente (sem 504 explícito) em arquivos maiores — cancele depois de ~3 minutos sem output
- O campo `unmigrated_uses=0` na validation NÃO significa que a migração está 100% correta — apenas que não há mais `pd.xxx` no código; APIs pandas por nome de método (`.sort_values()`, etc.) não são contadas
- Resultados são não-determinísticos: a mesma task com o mesmo modelo pode ter resultados diferentes em runs diferentes; para comparações experimentais, rode múltiplas vezes
- `before_step_NNN/` snapshots são a chave para debugar: comparar snapshot antes vs. projeto atual mostra exatamente o que o LLM produziu em cada step
