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

### G14 — Anti-Join com `indicator=True` Não Tem Equivalente Direto no Polars

**Sintoma**: Step falha com `ColumnNotFoundError: unable to find column "customer_id_right"` após múltiplos retries, ou o resultado inclui `None` onde deveriam aparecer apenas IDs válidos.

**Causa**: O padrão pandas de anti-join:
```python
merged = left.merge(right, on="key", how="left", indicator=True)
result = merged[merged["_merge"] == "left_only"]
```
não tem equivalente direto em Polars. O LLM frequentemente tenta usar a coluna `key_right` como proxy para detectar não-matches, mas em Polars um left join sobre colunas de mesmo nome **não gera** `key_right` — a chave do lado direito é descartada e só fica a do lado esquerdo.

**Solução correta em Polars**:
```python
# Opção 1: join anti-explícito (mais idiomático)
result = left.join(right.select("key").unique(), on="key", how="anti")

# Opção 2: is_in + filter
matched_keys = right["key"].unique()
result = left.filter(~pl.col("key").is_in(matched_keys))
```

**Onde aparece**: Qualquer função que usa `merge(..., how="left", indicator=True)` seguida de filtro em `_merge == "left_only"` — padrão comum em `customers_without_invoices`, deduplicação por chave externa, etc.

**Task que expôs o bug**: `task_016_merge_multi_type` — `customers_without_invoices` e `all_billing_pairs` (outer join com chave nula descrita abaixo).

---

### G15 — Outer Join no Polars Perde a Chave do Lado Direito em Não-Matches

**Sintoma**: Após outer join, registros que só existem na tabela da direita aparecem com `customer_id = None` em vez do valor real.

**Causa**: Em pandas `merge(..., how="outer")`, a coluna de join é preenchida com o valor de qualquer lado que tenha match. Em Polars `join(..., how="full")`, a coluna de join fica com o valor do lado **esquerdo** — quando o left não tem match, ela é `null`.

**Exemplo**:
```python
# pandas: C6 aparece com customer_id="C6"
customers.merge(invoices, on="customer_id", how="outer")

# polars (ERRADO): C6 aparece com customer_id=null
customers.join(invoices, on="customer_id", how="full")

# polars (CORRETO): coalescer manualmente
result = customers.join(invoices, on="customer_id", how="full", coalesce=True)
```

**Nota**: `coalesce=True` (padrão no Polars ≥ 0.19) resolve em casos simples. Em joins com renaming (`suffix`), verificar se a coluna chave está sendo selecionada do lado correto.

---

### G16 — `DataFrame.loc` Não Existe em Polars

**Sintoma**: `AttributeError: 'DataFrame' object has no attribute 'loc'` após migração parcial.

**Causa**: Polars não tem indexação label-based via `.loc[label]` nem positional via `.iloc[pos]`. O LLM frequentemente gera código que remove o `import pandas as pd` mas deixa chamadas a `.loc[]` intactas, ou tenta usar `.loc` no objeto polars.

**Padrões comuns e equivalentes**:
```python
# pandas: filtro por condição de coluna
df.loc[df["status"] == "active"]
# polars
df.filter(pl.col("status") == "active")

# pandas: seleção de colunas
df.loc[:, ["a", "b"]]
# polars
df.select(["a", "b"])

# pandas: atualizar valor por posição
df.loc[idx, "col"] = value
# polars: DataFrames são imutáveis — usar with_columns + when/then/otherwise

# pandas: filtrar por índice de label
df.loc["2024-01-01":"2024-03-31"]
# polars: sem índice — usar filter com coluna de data explícita
df.filter((pl.col("date") >= "2024-01-01") & (pl.col("date") <= "2024-03-31"))
```

**Onde aparece**: Qualquer código que usa `.loc` para filtrar, selecionar ou atualizar — muito comum em projetos com DataFrames usados como estruturas de lookup (ex: `fitter.py` usa `df.loc[nome_distribuicao]` para acessar resultados por índice de string).

**Task que expôs**: `task_028_fitter` — `df_errors` é um DataFrame indexado por nome de distribuição; o código usa `.loc["gamma"]` para acessar erros por chave.

---

### G17 — `DataFrame.sort()` no Polars Não Aceita `key=` nem `categories=`

**Sintoma**: `TypeError: sort() got an unexpected keyword argument 'categories'` ou `TypeError: sort() got an unexpected keyword argument 'key'`.

**Causa**: Em pandas, `DataFrame.sort_values()` aceita `key=` (função aplicada antes do sort) e séries categóricas mantêm ordem de categoria ao ordenar. Em Polars, `DataFrame.sort()` não tem parâmetro `key=` nem lógica de categoria automática.

**Solução**:
```python
# pandas: ordenar com função de chave
df.sort_values("col", key=lambda s: s.str.lower())
# polars
df.sort(pl.col("col").str.to_lowercase())

# pandas: ordenar preservando ordem categórica
df["tier"] = pd.Categorical(df["tier"], categories=["low","mid","high"], ordered=True)
df.sort_values("tier")
# polars: ordenar com enum ou mapeamento numérico explícito
order_map = {"low": 0, "mid": 1, "high": 2}
df.with_columns(pl.col("tier").replace(order_map).alias("_sort_key")).sort("_sort_key").drop("_sort_key")
```

**Task que expôs**: `task_028_fitter` — `summary()` ordena resultados de fitting por erro usando `key=` implícito nas categorias pandas.

---

### G18 — Projetos Multi-Arquivo Grandes Esgotam Retries Sem Migração Completa

**Sintoma**: `status: failed`, `tests_after: passed`, mas `unmigrated_uses > 0`. Os testes passam mas o framework rejeita porque pandas ainda está presente em algum arquivo.

**Causa**: O DiagnosisAgent planeja um step por arquivo (ou grupo de arquivos acoplados). Se um projeto tem muitos arquivos com muitas referências pandas (ex: ta com 7 arquivos e 428 referências), o agente pode migrar os primeiros steps com sucesso mas esgotar os 3 retries em um step mais complexo — deixando outros arquivos sem migrar. O resultado final: testes passam (os arquivos não-migrados ainda são pandas válido), mas `old_imports_remaining > 0`.

**Exemplo**: `task_027_ta` — 7 arquivos de indicadores técnicos, 428 referências pandas. O agente migrou parcialmente `trend.py` mas não conseguiu completar nos 3 retries, e os demais arquivos ficaram intactos.

**Mitigações**:
- Aumentar `MAX_RETRIES` para projetos grandes
- Usar `gemini-2.5-pro` em vez de `flash-lite` para arquivos com muitas interdependências
- Dividir projetos grandes em tasks menores (um módulo por task)
- Considerar que projetos com > 5 arquivos migráveis têm risco elevado de migração incompleta com modelos menores

---

## Comportamentos Não-Óbvios do Framework

### G10 — Testes Cross-File Quebram ao Migrar Produtores Individualmente

**Sintoma**: Step 001 (loaders.py) migrado para Polars → Step 002 (summaries.py) testa e falha com `AttributeError: 'DataFrame' object has no attribute 'groupby'` em summaries.py que ainda não foi migrado.

**Causa**: O pytest roda **todos** os testes, incluindo testes de summaries.py. Quando loaders.py retorna Polars DataFrames, summaries.py (ainda pandas) quebra ao receber esses DataFrames.

**Solução**: O framework agrupa arquivos acoplados em um **step atômico** via `grouped_before_consumers` strategy. Todos os arquivos do grupo migram juntos, e os testes só rodam no final quando todos estão em Polars.

**Por que não fica óbvio**: `unmigrated_uses=0` para loaders.py indica migração bem-sucedida, mas os testes falham por causa de summaries.py. Isso pode parecer um bug no framework mas é comportamento esperado do pytest.

---


### G12 — benchmark/ É Ignorado pelo Git

**Sintoma**: Clones do repositório não têm as tasks de benchmark. Depois de clonar, `run_task.py` falha com "task directory not found".

**Causa**: `.gitignore` inclui `benchmark/` para evitar versionar clones externos e dados de experimento local.

**Solução**: Criar tasks manualmente (`scripts/create_benchmark_task.py`) ou importar via `scripts/import_github_project.py`. As 26 tasks (task_001 a task_026) existem apenas localmente e precisam ser recriadas em cada clone.

---

### G13 — allowed_symbols=[]: Escopo É o Arquivo Inteiro

**Sintoma**: Com `allowed_symbols=[]`, o MigrationAgent migra o arquivo inteiro, não apenas parte.

**Causa**: `allowed_symbols=[]` é interpretado como "sem restrição de símbolo" = "arquivo inteiro é permitido". Isso é diferente de `allowed_symbols=["fn1"]` que limita a exatamente esse símbolo.

**Contexto**: Steps gerados para arquivos de arquivos acoplados (`files=[...]`) sempre usam `allowed_symbols=[]` porque precisam migrar os arquivos por inteiro para manter coerência de tipo.

---

## Guia de Configuração Estratégica

### G19 — Matriz de Decisão: Modelo × Complexidade de Task

Não existe um modelo ideal para todos os casos. Use esta matriz para escolher:

| Complexidade da task | Modelo recomendado | AST fallback | Risco |
|---|---|---|---|
| 1 arquivo, <100 linhas, padrões simples (filter, sort, groupby) | `gemini-3.1-flash-lite` | opcional | baixo |
| 1 arquivo, 100–300 linhas, padrões variados | `gemini-3.1-flash-lite` | **obrigatório** | médio |
| 2–3 arquivos acoplados, qualquer tamanho | `gemini-3.1-flash-lite` | **obrigatório** | médio |
| 1 arquivo, >300 linhas, padrões complexos (`transform`, `apply`, `loc`) | `gemini-2.5-flash` | **obrigatório** | alto |
| Projeto real com 5+ arquivos e >200 refs pandas | `gemini-2.5-pro` | obrigatório | muito alto — pro pode dar timeout |

**Sinais de que o modelo escolhido é insuficiente**:
- 3 retries sempre falhando no mesmo passo → troque para modelo maior
- `unmigrated_uses > 0` depois de 3 retries → AST fallback não está ativado ou o padrão é fora do escopo do AST
- Timeout silencioso no DiagnosisAgent → arquivo muito grande para o modelo (use flash-lite)

**Regra prática validada** (tasks 001–028):
> `gemini-3.1-flash-lite` + `MIGRATION_AST_FALLBACK=1` cobre >90% das tasks sintéticas. Para projetos reais com arquivos grandes ou padrões complexos (`transform`, `apply axis=1`, `.loc`), nem pro nem flash-lite garantem sucesso sem solução determinista adicional.

---

### G20 — O Que o AST Fallback Cobre (e o Que Não Cobre)

O `MIGRATION_AST_FALLBACK=1` aplica transformações mecânicas **após** o LLM, como segunda camada de segurança. É determinístico e não usa tokens.

**O que o AST resolve automaticamente**:
- `df["col"] = expr` → `df = df.with_columns(expr.alias("col"))`
- `.reset_index(drop=True)` → removido
- `.sort_values("x")` → `.sort("x")`
- `.sort_values("x", ascending=False)` → `.sort("x", descending=True)`

**O que o AST NÃO resolve (precisa do LLM)**:

| Padrão pandas | Por que o AST não cobre |
|---|---|
| `df.loc[mask]` / `df.iloc[n]` | Semântica depende do tipo de índice — não é mecânico |
| `groupby().transform(func)` | Requer reescrita estrutural com `over()` |
| `apply(func, axis=1)` | Requer análise da função para gerar `when/then/otherwise` |
| `pd.cut(col, bins, labels)` | Requer `cut()` como método de coluna, não função de módulo |
| `dt.to_period("M")` | Sem equivalente direto — requer `dt.strftime()` |
| `expanding().mean()` | Requer substituição por `cum_mean()` |
| `pd.concat([a, b], axis=1)` | Requer `pl.concat([a, b], how="horizontal")` com análise de alinhamento |
| `Series.where(cond, other)` | Semântica invertida em relação ao `filter` — requer `when/then/otherwise` |
| `merge(..., indicator=True)` | Anti-join precisa de `join(..., how="anti")` |

**Implicação prática**: Ativar AST fallback é sempre seguro e melhora a taxa de sucesso para padrões simples. Para os padrões acima, o AST não ajuda — o sucesso depende inteiramente da qualidade do LLM escolhido e do número de retries.

---

### G21 — Soluções Deterministas vs LLM: Quando Confiar em Cada Uma

O framework tem três camadas de transformação com garantias diferentes:

**Camada 1 — Determinística pura (AST transformer)**
- Confiança: 100% para os padrões cobertos
- Velocidade: instantânea, sem tokens
- Limite: só funciona para transformações mecânicas 1-para-1
- Exemplos cobertos: G5, G1 (column assignment), sort direction

**Camada 2 — Heurística estrutural (pattern_scanner)**
- Confiança: alta para detecção, mas produz apenas *instruções* para o LLM
- Velocidade: rápida, sem tokens
- Limite: detecta padrões mas não os resolve — informa o LLM o que precisa mudar
- Exemplos: dependent_column_assign (G6), método `.loc`, `transform`, `apply`

**Camada 3 — LLM (MigrationAgent + RepairAgent)**
- Confiança: variável (30–95% por step dependendo do modelo e complexidade)
- Velocidade: lenta, consome tokens
- Limite: não-determinístico; o mesmo prompt pode produzir resultados diferentes
- Necessário para: toda lógica que requer entendimento semântico

**Regra de ouro**:
> Se o padrão tem uma tradução 1-para-1 que pode ser expressa como substituição de texto ou AST, use solução determinista. Se o padrão requer entender *o que o código está fazendo* (não apenas *como está escrito*), o LLM é necessário — e o número de retries e a qualidade do modelo importam muito.

**Padrões que poderiam ter solução determinista mas ainda não têm** (candidatos para o AST transformer):
- `.reset_index()` sem `drop=True` (precisa de lógica condicional)
- `.fillna(method="ffill")` → `.forward_fill()`
- `pd.to_datetime(col)` → `pl.col(col).str.to_datetime()`
- `df.rename(columns={"a": "b"})` → `df.rename({"a": "b"})`

Adicionar esses ao AST transformer aumentaria a taxa de sucesso sem custo de tokens.

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
