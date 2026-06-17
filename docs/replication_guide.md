# Guia de Replicação

## Requisitos

- Python 3.10 ou superior.
- Acesso à internet para instalar dependências via `pip`.

## Passos

```bash
git clone <repo>
cd multiagent-library-migration
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 scripts/run_task.py task_001_read_csv_filter
```

O relatório ficará em:

```text
experiments/runs/<task_id>_<timestamp>/report.json
```

Arquivos úteis da execução:

- `logs/tests_before.log`
- `logs/diagnosis_plan.json`
- `logs/step_001_migration.json`
- `logs/step_001_validation.json`
- `logs/final_validation.json`
- `diff.patch`
- `prompts/*.md`

## Docker

```bash
docker build -t multiagent-library-migration .
docker run --rm multiagent-library-migration
```

Para replicar avaliações com Docker, crie um `.env` a partir do exemplo e
configure o provedor/modelo/chave:

```bash
cp .env.example .env
# edite LLM_PROVIDER, LLM_MODEL e a chave correspondente
```

O `compose.yaml` preserva artefatos em:

```text
experiments/runs/
experiments/evaluations/
```

Avaliação isolada do agente diagnóstico/planner:

```bash
TASK_ID=task_020_full_analytics_pipeline \
ATTEMPTS=3 \
K=1,3 \
make docker-planner-eval

LATEST_MATRIX=$(ls -td experiments/evaluations/planner_matrix_* | head -1)
docker compose run --rm migration-runner \
  python scripts/generate_html_report.py "$LATEST_MATRIX"
```

Avaliação isolada do agente de migração, consumindo planos congelados da
matriz do planner:

```bash
PLANNER_MATRIX=$(ls -td experiments/evaluations/planner_matrix_* | head -1)

PLANNER_MATRIX="$PLANNER_MATRIX" \
K=1,3 \
make docker-migration-eval

LATEST_MIGRATION_MATRIX=$(ls -td experiments/evaluations/migration_matrix_* | head -1)
docker compose run --rm migration-runner \
  python scripts/generate_html_report.py "$LATEST_MIGRATION_MATRIX"
```

Avaliação isolada do agente de validação, consumindo runs migradas:

```bash
RUNS=$(ls -td experiments/evaluations/migration_matrix_* | head -1)

RUNS="$RUNS" \
make docker-validation-eval

LATEST_VALIDATION_MATRIX=$(ls -td experiments/evaluations/validation_matrix_* | head -1)
docker compose run --rm migration-runner \
  python scripts/generate_html_report.py "$LATEST_VALIDATION_MATRIX"
```

Avaliação do fluxo completo dos agentes:

```bash
TASK_ID=task_020_full_analytics_pipeline \
ATTEMPTS=3 \
K=1,3 \
make docker-full-eval

LATEST_MATRIX=$(ls -td experiments/evaluations/matrix_* | head -1)
docker compose run --rm migration-runner \
  python scripts/generate_html_report.py "$LATEST_MATRIX"
```

Para rodar ablação sem AST no diagnóstico, defina no `.env`:

```text
DIAGNOSIS_USE_AST=0
```
