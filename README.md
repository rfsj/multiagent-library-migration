# multiagent-library-migration

MVP de pesquisa para migração controlada de bibliotecas em projetos de software
usando um fluxo multiagente baseado em LLMs.

O caso inicial migra um projeto Python simples de `pandas` para `polars`.
A arquitetura separa os papéis de diagnóstico, migração e validação para manter
o processo auditável e reduzir alterações fora do escopo. O diagnóstico usa
LangChain com saída estruturada; a execução das etapas planejadas é orquestrada
com LangGraph, preservando snapshots, logs de migração e validações por etapa.

## Objetivos do MVP

- Criar uma estrutura inicial replicável.
- Executar uma tarefa de benchmark `pandas -> polars`.
- Rodar testes antes e depois da migração.
- Gerar logs, diff e relatório JSON.
- Registrar versões relevantes do ambiente.

## Agentes

O contrato operacional dos agentes está em [`AGENTS.md`](AGENTS.md). O fluxo é
dividido em três partes:

- **Diagnosis**: analisa o projeto em modo somente leitura e gera o plano JSON.
- **Migration**: executa uma etapa planejada por vez via LangGraph, respeitando
  `allowed_files`.
- **Validation**: compara snapshots, roda testes e aprova ou rejeita cada etapa.

## Execução rápida

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
export LLM_PROVIDER=google
export LLM_MODEL=<modelo>
python3 scripts/run_task.py task_001_read_csv_filter
```

Também é possível usar `LLM_PROVIDER=anthropic` instalando o extra opcional:

```bash
pip install -e ".[anthropic]"
```

Ou executar a tarefa padrão via `make`:

```bash
make run-task
```

Para executar no Docker preservando os artefatos no diretório local
`experiments/runs/`, use:

```bash
make docker-run
```

Os resultados são gravados em `experiments/runs/`.

## Estrutura

```text
AGENTS.md         contrato operacional dos agentes
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
