# Padrões de Design

## Padrão Arquitetural: Pipeline Multiagente com Grafo de Estado

O sistema é um pipeline de agentes LLM orquestrado por um grafo acíclico-dirigido com ciclos de retry, implementado em LangGraph.

Cada agente tem responsabilidade única e não pode invadir o escopo de outro:

```
DiagnosisAgent  →  read-only, plano JSON
MigrationAgent  →  escreve apenas em allowed_files
ValidationAgent →  read-only + subprocess (pip, pytest)
RepairAgent     →  read-only, produz plano JSON
```

## Padrão de Grafo LangGraph (StateGraph)

```python
graph = StateGraph(GraphState)
graph.add_node("diagnose", ...)
graph.add_node("select_next_step", ...)
graph.add_node("snapshot_before_step", ...)
graph.add_node("migrate_step", ...)
graph.add_node("validate_step", ...)

# Edges condicionais criam o loop de retry/replan
graph.add_conditional_edges("validate_step", route_after_validation, {
    "select_next_step": ...,   # accepted → próximo step
    "snapshot_before_step": ..., # rejected_implementation → retry
    "diagnose": ...,             # rejected_plan → replan
    "__end__": ...,              # stop
})
```

**GraphState** é imutável: cada node retorna um dict de updates que o LangGraph aplica como patch no estado existente.

## Padrão de Structured Output

Todos os outputs LLM usam `llm.with_structured_output(PydanticModel)`:

```python
class MigrationResult(BaseModel):
    migrated_code: str
    changes_summary: str

llm = get_llm().with_structured_output(MigrationResult)
result: MigrationResult = chain.invoke(payload)
```

Isso garante que respostas malformadas são rejeitadas e retryadas antes de chegar ao código de aplicação.

## Padrão de Retry Estruturado

Cada step tem 3 camadas de retry com feedback acumulativo:

```
Attempt 1: LLM migration + AST fallback
     ↓ fails
Attempt 2: retry com RepairPlan (failure_category + instructions + acceptance_criteria + must_not_do)
     ↓ fails  
Attempt 3: retry com RepairPlan atualizado
     ↓ fails
→ step marcado como failed, arquivo recebe comment MANUAL REVIEW, continua próximo step
```

O `RepairAgent` categoriza a falha antes do retry, tornando o feedback cirúrgico:
- `polars_api_error` → lista APIs incorretas e corretas
- `dependent_expression_order` → instrui sobre split de `with_columns`
- `producer_consumer_type_mismatch` → orienta sobre ordem de migração

## Padrão de Snapshot para Auditoria

Antes de cada step de migração, o framework faz cópia completa do projeto:

```python
shutil.copytree(project_dir, snapshots_dir / f"before_{step_id}")
```

Isso permite:
- Restaurar estado ao falhar (rollback determinístico)
- Comparação diff antes/depois para `out_of_scope` detection
- Reprodução de qualquer step individualmente

## Padrão de Scope Enforcement

O `DiagnosisAgent` declara explicitamente quais arquivos cada step pode tocar:

```json
{
  "step_id": "step_001",
  "allowed_files": ["src/orders/processing.py", "requirements.txt"],
  "allowed_symbols": ["load_orders", "paid_orders"]
}
```

O `ValidationAgent` verifica: se qualquer arquivo fora dessa lista foi modificado → `out_of_scope_changes > 0` → step rejeitado.

## Padrão de Symbol-Level Migration

Para arquivos com múltiplas funções, o diagnóstico divide em steps por símbolo:

```python
# Arquivo com 3 funções independentes → 3 steps
step_001: allowed_symbols=["load_orders"]
step_002: allowed_symbols=["load_customers"]
step_003: allowed_symbols=["paid_orders"]
```

O `MigrationAgent._apply_allowed_symbol_scope()` faz AST merge:
1. LLM migra o arquivo inteiro
2. Extrai apenas os símbolos permitidos do código migrado
3. Substitui no arquivo original, deixando o resto intacto

## Padrão de Atomic Grouped Steps

Para arquivos com dependência cross-file (produtor → consumidor), o diagnóstico os agrupa em um único step atômico:

```json
{
  "step_id": "step_001",
  "file": "src/analytics/loaders.py",
  "files": ["src/analytics/loaders.py", "src/analytics/quality.py", "src/analytics/summaries.py"],
  "allowed_files": ["src/analytics/loaders.py", "src/analytics/quality.py", "src/analytics/summaries.py"]
}
```

**Razão**: Se loaders.py é migrado para Polars mas quality.py ainda usa pandas API, o test suite falha. Migrando tudo junto, os testes passam atomicamente.

## Padrão de Fallback Determinístico (AST Transformer)

O `ast_transformer.py` é um fallback para padrões que LLMs menores consistentemente erram:

```
LLM output
    ↓
Pattern re-scan (pattern_scanner.py)
    ↓ patterns still found?
AST Transformer (ast_transformer.py)  ← MIGRATION_AST_FALLBACK=1
    ↓
Implementation Review (ImplementationReviewAgent)
    ↓
Validation
```

O transformer opera em 3 passes independentes, cada um re-parseando o output do anterior:
1. Column assignments (`df["col"] = rhs` → `df.with_columns(...)`)
2. reset_index removal (`.reset_index(drop=True)` → delete)
3. sort_values rename (`.sort_values(...)` → `.sort(descending=...)`)

## Organização de Código

```
src/
├── agents/    # Cada agente é uma classe com método principal público
├── graph/     # Nodes (funções puras) + State (TypedDict) + workflow (composição)
├── tools/     # Funções puras sem estado, usadas pelos agentes
└── evaluation/ # Funções de cálculo de métricas e formatação de report
```

**Regra**: Agents dependem de Tools. Tools não dependem de Agents. Graph depende de Agents.

## Convenções de Nomenclatura

- Agentes: `<Name>Agent` (classe), `<name>_agent` (instância)
- Prompts: `/prompts/<agent_name>_v<version>.md`
- Logs: `<step_id>_<type>.json` (ex: `step_001_migration.json`)
- Tasks: `task_<NNN>_<description>/`
- Runs: `<task_id>_<YYYYMMDDTHHMMSSz>/`

## Padrões de Teste do Framework

```
tests/
├── test_benchmark_structure.py   # Testes do DiagnosisAgent (sanitização, agrupamento)
├── test_pattern_scanner.py       # Testes das detecções do scanner
├── test_ast_transformer.py       # Testes de cada pass do transformer
├── test_migration_agent.py       # Testes do symbol scoping, requirements migration
└── test_workflow_migration_graph.py  # Testes end-to-end com mocks de LLM
```

Testes do framework nunca tocam projetos reais: usam `tmp_path` (pytest fixture) para criar projetos sintéticos.
