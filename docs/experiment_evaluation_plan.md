# Evaluation Plan: Execution-Based Evaluation and Pass@K

This project evaluates a multi-agent workflow for controlled Python library
migration. The primary evaluation unit is a benchmark task under
`benchmark/<task_id>/`.

The recommended academic framing is:

1. **Execution-Based Evaluation**: a run succeeds only when the migrated project
   passes tests and final validation approves the migration.
2. **Pass@K / pass^k**: repeated independent runs measure whether the system can
   find a correct migration within a run budget and how reliable it is across
   repeated attempts.

## Main Success Definition

A run is successful when:

```text
report.status == "success"
AND tests_before == "passed"
AND tests_after == "passed"
AND logs/final_validation.json.status == "approved"
AND final validation reports no production source-library imports/usages
AND final validation reports no out-of-scope changes
```

This follows the same principle used in coding-agent benchmarks such as
SWE-bench: correctness is determined by executing the project checks rather than
only judging generated text.

## Metrics

### Execution-Based Metrics

- `success_rate`: fraction of runs where `report.status == "success"`.
- `final_validation_pass_rate`: fraction of runs where
  `logs/final_validation.json.status == "approved"`.
- `tests_after_pass_rate`: fraction of runs where post-migration tests pass.
- `scope_violation_rate`: fraction of runs with `out_of_scope_changes > 0`.
- `unmigrated_usage_rate`: fraction of runs with `unmigrated_uses > 0`.
- `step_approval_rate`: accepted validation verdicts divided by attempted
  validation verdicts.
- `cost_to_success`: rank and LLM calls until the first successful run.

### Pass@K

For a task with repeated attempts:

```text
pass@K = at least one of the first K runs succeeded
```

Use this when the system is allowed a budget of multiple candidate runs.

Recommended values:

```text
pass@1, pass@3, pass@5
```

### pass^k

For reliability:

```text
pass^k = all of the first k runs succeeded
```

This is stricter than pass@K. It measures consistency, not search ability.

Recommended values:

```text
pass^1, pass^3, pass^5
```

## Experimental Comparisons

Run the same task set under controlled configurations:

```text
V3 base
V3 + symbol analysis
V3 + least-scope
V3 + guardrails
V3 + human-review signal
V3 + dataframe flow grouping
```

Use environment flags to isolate conditions:

```bash
DIAGNOSIS_AGENT_IMPL=v3
PLANNER_USE_SYMBOL_ANALYSIS=0 or 1
MIGRATION_MODE=research
```

Keep the model fixed when comparing design patterns. Change only one condition
at a time.

## Commands

### 1. Planner Only

Runs only the planner/diagnosis agent, writes a diagnosis plan, and validates
the plan against a deterministic benchmark contract. This is a planner-only
analogue of valid-plan-rate evaluations in planning benchmarks: it does not
execute migration or tests, but it checks whether the produced plan is
structurally valid for the migration task.

```bash
.venv/bin/python scripts/eval_planner_only.py task_020
```

Output files:

```text
experiments/runs/<task>_<timestamp>_planner/
  planner_only_report.json
  logs/diagnosis_plan.json
  logs/project_audit_eval.json
  logs/plan_validation.json
  logs/planner_symbol_analysis.json
  logs/planner_guardrails.json
```

Important output fields:

- `valid_plan`
- `plan_validity_score`
- `file_coverage_rate`
- `symbol_coverage_rate`
- `expected_step_coverage_rate`
- `scope_precision_rate`
- `dependency_plan_valid`
- `step_order_valid`
- `plan_violations`
- `migration_step_count`
- `affected_source_files`
- `human_review_required`
- `planner_warnings`
- `llm_calls.total`

If `benchmark/<task_id>/metadata.json` defines `validity_oracle`, that object
is used as the stronger oracle. The older name `expected_planner` is still
accepted for compatibility. Otherwise the validator falls back to the project
audit (`affected_source_files`, dependency summary, and discovered scope). This
keeps imported real-project benchmarks usable without adding task-specific
hardcodes.

The oracle is a validity contract, not a single canonical step sequence. It
defines properties any valid plan must satisfy while allowing multiple correct
decompositions.

Example `validity_oracle` contract:

```json
{
  "validity_oracle": {
    "required_source_files": ["src/orders/processing.py"],
    "dependency_update_required": true,
    "allowed_dependency_files": ["requirements.txt"],
    "required_symbol_coverage": {
      "src/orders/processing.py": ["get_paid_orders", "get_pending_orders"]
    },
    "coverage_policy": "whole_file_or_all_symbols",
    "allowed_granularity": ["file", "symbol"],
    "forbidden_files": ["tests/*", "test/*"],
    "required_ordering": [
      {
        "before": "dependency_update",
        "after": "source_migration"
      }
    ],
    "expected_step_groups": [
      { "allowed_files": ["requirements.txt"] },
      {
        "allowed_files": ["src/orders/processing.py"],
        "required_symbols": ["get_paid_orders"]
      }
    ],
    "human_review_required": false
  }
}
```

The validator does not require an exact step-id sequence. It checks structural
equivalence: expected files/symbols are covered, planned edits stay in scope,
dependency updates are present when needed, and basic ordering constraints hold.

### 2. Migration Only

Runs only the migration agent using an existing planner output.

```bash
.venv/bin/python scripts/eval_migration_only.py task_020 \
  --diagnosis-plan experiments/runs/<planner_run>/logs/diagnosis_plan.json
```

Output files:

```text
experiments/runs/<task>_<timestamp>_migration/
  migration_only_report.json
  diff.patch
  logs/diagnosis_plan.json
  logs/*_migration.json
```

Important output fields:

- `executed_step_count`
- `changed_files`
- `migrations[].status`
- `migrations[].changed_files`
- `llm_calls.total`

This does not run validation. It measures migration-agent behavior given a fixed
plan.

### 3. Validation Only

Runs only the validation agent against an existing run directory.

```bash
.venv/bin/python scripts/eval_validation_only.py \
  experiments/runs/<migration_or_full_run>
```

The run directory must contain:

```text
project/
snapshots/before_migration/
logs/diagnosis_plan.json
```

Output files:

```text
experiments/runs/<run>/
  validation_only_report.json
  logs/final_validation.json
  logs/final_pytest.log
  logs/validation_only_pytest.log
```

Important output fields:

- `status`
- `tests.status`
- `final_validation.status`
- `final_validation.old_imports_remaining`
- `final_validation.unmigrated_uses`
- `final_validation.out_of_scope_changes`

### 4. Full Workflow and Pass@K

Runs the full existing workflow multiple times and computes pass@K/pass^k.

```bash
.venv/bin/python scripts/eval_full.py task_020 --attempts 5 --k 1,3,5
```

Output files:

```text
experiments/evaluations/<task>_<timestamp>_full_eval.json
experiments/runs/<task>_<timestamp>/report.json
```

Important output fields:

- `success_count`
- `success_rate`
- `pass_at_k.pass@1`
- `pass_at_k.pass@3`
- `pass_at_k.pass@5`
- `pass_caret_k.pass^1`
- `pass_caret_k.pass^3`
- `pass_caret_k.pass^5`
- `cost_to_success.first_success_rank`
- `cost_to_success.llm_calls_to_first_success`
- `attempts[].run_dir`
- `attempts[].out_of_scope_changes`
- `attempts[].unmigrated_uses`
- `attempts[].llm_calls`

### 5. Full Evaluation Matrix (config x task)

Runs `eval_full.py` across a matrix of planner configs and tasks, then builds
the three result tables below automatically.

```bash
.venv/bin/python scripts/run_evaluation_matrix.py --dry-run
.venv/bin/python scripts/run_evaluation_matrix.py --list-configs
.venv/bin/python scripts/run_evaluation_matrix.py \
  --tasks task_001_read_csv_filter,task_020_full_analytics_pipeline \
  --configs v3_base,v3_symbol_analysis \
  --attempts 3 --k 1,3
```

`--tasks` defaults to every task under `benchmark/`; `--configs` defaults to
all registered configs. Always run `--dry-run` first to see the matrix size
(`configs x tasks x attempts` full-workflow runs, each making several LLM
calls) before committing to a full sweep.

Registered configs (`--list-configs` to print the exact env overrides):

| config               | env overrides |
|-----------------------|----------------|
| `v3_base`             | `PLANNER_USE_SYMBOL_ANALYSIS=0` |
| `v3_symbol_analysis`  | `PLANNER_USE_SYMBOL_ANALYSIS=1` |
| `legacy`              | `DIAGNOSIS_AGENT_IMPL=legacy` |

**Caveat:** `PLANNER_USE_SYMBOL_ANALYSIS` is the only real, independent
ablation flag in `PlannerV3Agent`. "Least-scope planning" and the
deterministic `dataframe_flow_analysis` derivation (cross-file
producer/consumer grouping) only have an effect once symbol analysis is on
(no separate flag for either), and "deterministic guardrails" / the
"human-review-signal" consolidation are unconditional in v3 — there is no
off-switch for them. So the conditions listed under "Experimental
Comparisons" above collapse to the three configs in this table;
guardrails/least-scope/human-review-signal/flow-grouping are always active
for any `v3_*` config rather than independently testable.

Output files:

```text
experiments/evaluations/matrix_<timestamp>/
  run_level.csv
  pass_at_k.csv
  ablation.csv
  matrix_report.json   # raw eval_full.py output per (config, task)
```

## Suggested Result Tables

### Run-Level Table

```text
task_id | config | attempt | success | tests_after | final_validation |
out_of_scope_changes | unmigrated_uses | retries | replans | llm_calls | run_dir
```

### Pass@K Table

```text
task_id | config | attempts | success_rate | pass@1 | pass@3 | pass@5 |
pass^1 | pass^3 | pass^5 | first_success_rank | llm_calls_to_success
```

### Ablation Table

```text
config | tasks | success_rate | pass@3 | pass@5 | avg_llm_calls |
avg_retries | scope_violation_rate | unmigrated_usage_rate
```

## Interpretation

- High `pass@K` with low `pass^k`: the system can find a solution, but is not
  reliable across repeated runs.
- High `pass^k`: the agentic workflow is stable.
- Low `pass@K`: the workflow rarely finds a valid migration under the run
  budget.
- High success but high cost: the workflow works, but needs optimization.
- High scope violations: planner/migration scope control is weak.
- High unmigrated usage: planner coverage or migration capability is weak.
- High human-review rate with low failure rate: human-review detection may be
  too conservative.
