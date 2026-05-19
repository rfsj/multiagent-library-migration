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
