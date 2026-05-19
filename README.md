# multiagent-library-migration

MVP de pesquisa para migração controlada de bibliotecas em projetos de software
usando um fluxo multiagente baseado em LLMs.

O caso inicial migra um projeto Python simples de `pandas` para `polars`.
Nesta primeira versão, os agentes são implementados de forma determinística
(rule-based) para tornar o benchmark reproduzível. A arquitetura preserva os
papéis de diagnóstico, migração e validação para evolução posterior com
LangGraph e chamadas reais a LLMs.

## Objetivos do MVP

- Criar uma estrutura inicial replicável.
- Executar uma tarefa de benchmark `pandas -> polars`.
- Rodar testes antes e depois da migração.
- Gerar logs, diff e relatório JSON.
- Registrar versões relevantes do ambiente.

## Execução rápida

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 scripts/run_task.py task_001_read_csv_filter
```

Também é possível usar:

```bash
make run-task
```

Os resultados são gravados em `experiments/runs/`.

## Estrutura

```text
src/agents/       agentes de diagnóstico, migração e validação
src/tools/        scanner, executor de testes, diff e comparação
src/evaluation/   métricas e geração de relatório
src/graph/        estado e workflow simples do MVP
benchmark/        tarefas de benchmark
scripts/          scripts de execução
docs/             protocolo, replicação e métricas
prompts/          prompts versionados dos agentes
```

## Tarefa inicial

`benchmark/task_001_read_csv_filter` contém um projeto Python com:

- `pd.read_csv`;
- filtro por coluna;
- seleção de colunas;
- ordenação;
- testes com `pytest`.

O relatório final contém campos como `tests_before`, `tests_after`,
`old_imports_remaining`, `unmigrated_uses`, `out_of_scope_changes` e `status`.
