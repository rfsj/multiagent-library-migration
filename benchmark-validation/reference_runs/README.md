# Validation Reference Runs

This directory contains versioned reference runs for evaluating the
`ValidationAgent` without running diagnosis or migration agents.

Each case directory has the same minimum layout consumed by
`scripts/eval_validation_only.py`:

```text
task_<id>/<case_id>/
  project/
  snapshots/before_migration/
  logs/diagnosis_plan.json
  validation_oracle.json
  report.json
```

The manifest orders cases by task and case type. Current coverage includes
`task_001_read_csv_filter` through `task_011_hurst`.

Case types:

- `approved`: valid migrated project, only planned files changed.
- `pytest_failed`: in-scope migrated project with a behavioral regression.
- `out_of_scope_change`: valid migrated project plus an unplanned file edit.
- `source_usage_remaining`: tests pass, but production code still imports and
  uses the source library.

Not every task has every case type. Approved cases are included only when an
existing migrated run already had deterministic validation approval. Rejected
cases are copied from existing failed migrated runs when available, or built
from the original unmigrated benchmark project for source-usage detection.

Run the suite with:

```bash
.venv/bin/python scripts/run_validation_matrix.py \
  --runs benchmark-validation/reference_runs

LATEST_VALIDATION_MATRIX=$(ls -td experiments/evaluations/validation_matrix_* | head -1)

.venv/bin/python scripts/generate_html_report.py "$LATEST_VALIDATION_MATRIX"
```

During evaluation, the runner copies these immutable fixtures to:

```text
benchmark-validation/runs/validation_matrix_<timestamp>/
```

The aggregated CSV/JSON/HTML metrics stay under:

```text
experiments/evaluations/validation_matrix_<timestamp>/
```
