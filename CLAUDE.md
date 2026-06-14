# CLAUDE.md

Leia `ai_docs/index.md` antes de trabalhar neste projeto. Ele indexa toda a documentação técnica disponível em `ai_docs/`.

## O que é este projeto

Framework de pesquisa experimental para migração controlada de bibliotecas Python usando um pipeline multiagente LLM. Caso de uso validado: `pandas → polars`.

## Documentação

- `ai_docs/index.md` — visão geral e glossário
- `ai_docs/stack.md` — linguagens, LLM providers, ferramentas
- `ai_docs/patterns.md` — arquitetura multiagente, LangGraph, padrões de retry
- `ai_docs/features.md` — todos os agentes e ferramentas documentados
- `ai_docs/business-rules.md` — contratos de escopo, critérios de aceitação
- `ai_docs/gotchas.md` — limitações de modelos, armadilhas pandas→polars
- `ai_docs/integrations.md` — LLM providers, formato de tasks e artefatos
- `ai_docs/improvements.md` — melhorias priorizadas para próximas iterações

## Comandos Principais

```bash
# Rodar uma task de benchmark
python3 scripts/run_task.py <task_id>

# Rodar todas as tasks
python3 scripts/run_all.py

# Rodar N seeds de uma task e agregar pass@k (o modo vem de MIGRATION_MODE)
MIGRATION_MODE=research_cot python3 scripts/run_seeds.py <task_id> --n 5

# Resumir o run mais recente de uma task numa linha de comparação
python3 scripts/summarize_run.py <task_id> <label>

# Importar projeto GitHub como task
python3 scripts/import_github_project.py <task_id> <repo_url>

# Preparar baseline de projeto importado
python3 scripts/prepare_benchmark_project.py <task_id> [--apply]

# Testes do framework
python3 -m pytest tests/ -q
```

## Configuração (.env)

```env
LLM_PROVIDER=google          # ou "anthropic"
LLM_MODEL=gemini-3.1-flash-lite
GOOGLE_API_KEY=...
MIGRATION_MODE=research      # research | research_cot | research_fewshot | research_cot_fewshot | assisted
```

`MIGRATION_MODE` controla a assistência. Presets: `research` (default — LLM puro, nenhuma
camada) e `assisted` (todas as camadas + few-shot + CoT). Há ainda 4 variantes de pesquisa
que ablacionam as duas técnicas *de prompt* (CoT e few-shot) sobre a base pura:

| MIGRATION_MODE         | CoT | few-shot | prompt base | demais camadas |
|------------------------|-----|----------|-------------|----------------|
| `research` (puro)      | ✗   | ✗        | v4          | off            |
| `research_cot`         | ✓   | ✗        | v5          | off            |
| `research_fewshot`     | ✗   | ✓        | v4          | off            |
| `research_cot_fewshot` | ✓   | ✓        | v5          | off            |
| `assisted`             | ✓   | ✓        | v5          | scanner/rescan/AST/syntax/scope on |

Cada camada é ablável por env var independentemente do preset: `MIGRATION_USE_AST`,
`MIGRATION_USE_SCANNER`, `MIGRATION_USE_RESCAN`, `MIGRATION_USE_SYNTAX_REGEN`,
`MIGRATION_USE_SCOPE`, `MIGRATION_USE_FEWSHOT`, `MIGRATION_USE_COT`.

- **CoT** (`use_cot`): força o campo obrigatório `migration_plan` (raciocínio passo a passo)
  via schema `MigrationResultCoT`. Sem isso, o Gemini dropa o campo por ser opcional.
- **few-shot** (`use_few_shot`): injeta pares de exemplo Human/AI genéricos no prompt; os
  exemplos só mostram o `migration_plan` quando o CoT também está on.
- **prompt base**: selecionado por `use_cot` (v5 = com CoT, v4 = sem). `MIGRATION_PROMPT_FILE`
  sobrescreve explicitamente o arquivo (em `prompts/`).

Modelos recomendados: `gemini-3.1-flash-lite` + `assisted` (custo/benefício), `gemini-2.5-pro` (maior qualidade, pode dar timeout em arquivos grandes).

## Restrições Críticas (ver business-rules.md)

- Nunca modificar arquivos de teste (`tests/`)
- Nunca adicionar regras específicas por nome de arquivo, coluna ou projeto
- `allowed_files` de cada step define exatamente o que pode ser tocado
- DiagnosisAgent é read-only — nunca escreve código

## Estrutura de Artefatos

Cada execução gera em `experiments/runs/<task_id>_<timestamp>/`:
- `report.json` — resultado final com métricas
- `diff.patch` — todas as mudanças aplicadas
- `logs/` — plano, migrations, validations, verdicts por step
- `logs/llm_proxy.jsonl` — **todas as chamadas ao LLM** (request + response); inspecione aqui quando um agente produzir algo inesperado
- `snapshots/` — estado do projeto em cada ponto

`benchmark/` e `experiments/` são ignorados pelo git.
