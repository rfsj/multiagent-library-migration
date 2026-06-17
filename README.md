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

Ou usar OpenAI com o extra correspondente:

```bash
pip install -e ".[openai]"
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o-mini
export OPENAI_API_KEY=<sua-chave>
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

Os resultados são gravados em `experiments/runs/`. Para avaliações replicáveis
com HTML, configure `.env` a partir de `.env.example` e rode:

```bash
TASK_ID=task_020_full_analytics_pipeline ATTEMPTS=3 K=1,3 make docker-full-eval
LATEST_MATRIX=$(ls -td experiments/evaluations/matrix_* | head -1)
docker compose run --rm migration-runner \
  python scripts/generate_html_report.py "$LATEST_MATRIX"
```

O relatório HTML ficará em `experiments/evaluations/<matrix>/report.html`.
Avaliações isoladas também podem ser replicadas via Docker:

```bash
# 1. Diagnóstico/planner
TASK_ID=task_020_full_analytics_pipeline ATTEMPTS=3 K=1,3 make docker-planner-eval

# 2. Migração, consumindo planos congelados do planner
PLANNER_MATRIX=$(ls -td experiments/evaluations/planner_matrix_* | head -1)
PLANNER_MATRIX=$PLANNER_MATRIX K=1,3 make docker-migration-eval

# 3. Validação, consumindo runs migradas
RUNS=$(ls -td experiments/evaluations/migration_matrix_* | head -1)
RUNS=$RUNS make docker-validation-eval
```

## Importar Projeto Real

Para testar um projeto pandas externo, use o importador em vez de fazer apenas
`git clone` dentro de `benchmark/`. O runner espera sempre a estrutura
`benchmark/<task_id>/metadata.json` e `benchmark/<task_id>/input_project/`.

```bash
python3 scripts/import_github_project.py \
  task_meu_projeto \
  https://github.com/usuario/repositorio
```

Opcionalmente:

```bash
python3 scripts/import_github_project.py \
  task_meu_projeto \
  https://github.com/usuario/repositorio \
  --branch main \
  --source-library pandas \
  --target-library polars \
  --overwrite
```

O comando faz clone temporario, remove metadados `.git`, cria `metadata.json` e
copia o projeto para `input_project/`. Depois rode:

```bash
python3 scripts/run_task.py task_meu_projeto
```

Se o baseline do projeto importado falhar antes da migracao, nao corrija isso
dentro dos agentes de migration. Use a etapa separada de preparacao:

```bash
python3 scripts/prepare_benchmark_project.py task_meu_projeto
```

Por padrao ela roda em modo `dry_run`, registra
`benchmark/<task_id>/preparation/preparation_report.json` e lista correcoes
seguras. Para aplicar:

```bash
python3 scripts/prepare_benchmark_project.py task_meu_projeto --apply
```

Essa ferramenta e pre-migracao: ela pode corrigir problemas basicos de baseline,
como `pytest.ini` apontando para `test` quando a pasta real e `tests`, ou um
loader YAML de configuracao ausente quando os testes esperam explicitamente
`<pacote>.config.load_config`. Ela nao deve fazer migracao de biblioteca.

Tambem ha um atalho via `make`:

```bash
make import-github TASK_ID=task_meu_projeto REPO_URL=https://github.com/usuario/repositorio
```

E para aplicar preparacao de benchmark:

```bash
make prepare-benchmark TASK_ID=task_meu_projeto
```

`benchmark/` e ignorado pelo git para evitar versionar projetos externos e
benchmarks gerados localmente.

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
