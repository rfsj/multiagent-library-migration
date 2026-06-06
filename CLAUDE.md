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
MIGRATION_AST_FALLBACK=1     # recomendado para modelos menores
```

Modelos recomendados: `gemini-3.1-flash-lite` + AST (custo/benefício), `gemini-2.5-pro` (maior qualidade, pode dar timeout em arquivos grandes).

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
- `snapshots/` — estado do projeto em cada ponto

`benchmark/` e `experiments/` são ignorados pelo git.
