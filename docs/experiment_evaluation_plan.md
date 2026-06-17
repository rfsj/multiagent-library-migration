# Evaluation Plan: Current Agent Evaluation Workflow

This project evaluates a multi-agent workflow for controlled Python library
migration. The primary evaluation unit is a benchmark task under
`benchmark/<task_id>/`.

The current evaluation stack separates four questions:

1. **Planner quality**: did the Diagnosis/Planner Agent produce a valid plan?
2. **Migration quality**: given a frozen valid plan, did the Migration Agent
   produce a correct migration?
3. **Validation quality**: given an existing migrated run, did the Validation
   Agent approve/reject correctly?
4. **End-to-end quality**: did the full workflow succeed from planner through
   final validation?

This separation matters because a failed full run can be caused by the planner,
migration, repair, validation, or their interaction. The isolated evaluations
avoid assigning blame to the wrong agent.

## Main End-to-End Success Definition

A full workflow run succeeds when:

```text
report.status == "success"
AND tests_before == "passed"
AND tests_after == "passed"
AND logs/final_validation.json.status == "approved"
AND final validation reports no production source-library imports/usages
AND final validation reports no out-of-scope changes
```

This follows execution-based evaluation used in coding-agent benchmarks:
correctness is determined by project checks and deterministic validation
evidence, not by judging generated text alone.

## Current Configurations

Registered configs are defined in `scripts/run_evaluation_matrix.py` and reused
by planner and migration matrix runners.

| config | current meaning |
| --- | --- |
| `v3_base` | `DIAGNOSIS_AGENT_IMPL=v3`, `PLANNER_USE_SYMBOL_ANALYSIS=0` |
| `v3_symbol_analysis` | `DIAGNOSIS_AGENT_IMPL=v3`, `PLANNER_USE_SYMBOL_ANALYSIS=1`, `MIGRATION_USE_SCOPE=1` |
| `legacy` | `DIAGNOSIS_AGENT_IMPL=legacy` |

`DIAGNOSIS_USE_AST` controls whether the Diagnosis/Planner path uses Python
AST parsing for project scanning, top-level symbol validation, and deterministic
least-scope splitting. It defaults to enabled. Setting `DIAGNOSIS_USE_AST=0`
disables source-library usage discovery in the diagnosis scanner and skips
AST-based symbol validation/splitting; structural file enumeration for
dependencies and tests still runs because it is part of the benchmark/audit
contract. The choice is recorded in planner reports and environment snapshots.

Important caveat: the current implementation does not expose independent
switches for every conceptual feature. `PLANNER_USE_SYMBOL_ANALYSIS=1` enables
the practical bundle of symbol analysis, least-scope planning support, and
dataframe-flow grouping. Deterministic guardrails and human-review signal
consolidation are always active for v3. Therefore, the current matrix can
compare `v3_base`, `v3_symbol_analysis`, and `legacy`; it cannot honestly claim
separate ablations for guardrails, least-scope, or human-review signal.

## Planner Evaluation

Planner evaluation runs only the planner/diagnosis agent. It does not migrate
files and does not run project tests.

The main metric is `valid_plan`, analogous to valid-plan-rate in planning
benchmarks. A valid plan is not required to match one canonical sequence.
Instead, it must satisfy the task oracle:

- required affected files are covered;
- required symbols are covered when symbol coverage is specified;
- planned edits stay inside allowed scope;
- required dependency update is present when expected;
- ordering constraints hold when specified;
- tests are not planned as migration targets;
- duplicate or overlapping step scopes are avoided.

The validator uses `metadata.json["validity_oracle"]` first, then the older
`expected_planner` field if present, then a weaker project-audit fallback.

### Single Planner Run

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/eval_planner_only.py task_020_full_analytics_pipeline
```

Output:

```text
experiments/runs/<task>_<timestamp>_planner/
  planner_only_report.json
  logs/diagnosis_plan.json
  logs/project_audit_eval.json
  logs/plan_validation.json
  logs/planner_symbol_analysis.json
  logs/planner_guardrails.json
```

Important fields:

```text
valid_plan
plan_validity_score
file_coverage_rate
affected_file_coverage_rate
scope_precision_rate
symbol_coverage_rate
expected_step_coverage_rate
dependency_plan_valid
step_order_valid
human_review_match
granularity_valid
plan_violations
migration_step_count
llm_calls.total
```

### Planner Matrix

Dry-run first:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_planner_matrix.py \
  --tasks task_001_read_csv_filter,task_002_complex_pandas_ops,task_003_multi_file_pandas_ops,task_004_join_window_pipeline,task_005_time_series_resampling,task_006_fillna_type_cast,task_007_string_operations,task_008_groupby_isna_isin,task_009_apply_map,task_010_value_counts_nlargest,task_011_rename_drop_dropna,task_012_datetime_extraction,task_013_melt_wide_to_long,task_014_cut_where_assign,task_015_cumulative_rolling,task_016_merge_multi_type,task_017_string_datetime_two_files,task_018_pivot_rank_groupby,task_019_three_file_pipeline,task_020_full_analytics_pipeline,task_021_groupby_transform,task_022_apply_axis1,task_023_period_expanding,task_024_concat_where,task_025_transform_apply_pipeline,task_026_hurst \
  --configs v3_symbol_analysis \
  --attempts 3 \
  --k 1,3 \
  --dry-run
```

Run the matrix:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_planner_matrix.py \
  --tasks task_001_read_csv_filter,task_002_complex_pandas_ops,task_003_multi_file_pandas_ops,task_004_join_window_pipeline,task_005_time_series_resampling,task_006_fillna_type_cast,task_007_string_operations,task_008_groupby_isna_isin,task_009_apply_map,task_010_value_counts_nlargest,task_011_rename_drop_dropna,task_012_datetime_extraction,task_013_melt_wide_to_long,task_014_cut_where_assign,task_015_cumulative_rolling,task_016_merge_multi_type,task_017_string_datetime_two_files,task_018_pivot_rank_groupby,task_019_three_file_pipeline,task_020_full_analytics_pipeline,task_021_groupby_transform,task_022_apply_axis1,task_023_period_expanding,task_024_concat_where,task_025_transform_apply_pipeline,task_026_hurst \
  --configs v3_symbol_analysis \
  --attempts 3 \
  --k 1,3
```

Shorter equivalent for all benchmark tasks:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_planner_matrix.py \
  --configs v3_symbol_analysis \
  --attempts 3 \
  --k 1,3
```

Output:

```text
experiments/evaluations/planner_matrix_<timestamp>/
  planner_run_level.csv
  planner_pass_at_k.csv
  planner_ablation.csv
  planner_matrix_report.json
```

Generate HTML:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/generate_html_report.py \
  experiments/evaluations/planner_matrix_<timestamp>
```

Recompute planner metrics for an existing planner matrix without rerunning
planner calls:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/recompute_planner_matrix.py \
  experiments/evaluations/planner_matrix_<timestamp> \
  --k 1,3

.venv/bin/python scripts/generate_html_report.py \
  experiments/evaluations/planner_matrix_<timestamp>
```

## Migration Evaluation

Migration evaluation runs the Migration Agent using an existing frozen
`logs/diagnosis_plan.json`. This isolates migration quality from planner
variance.

Current `eval_migration_only.py` now runs final deterministic validation after
migration and writes `migration_metrics`. It is no longer only a raw
"changed files" runner.

Migration success requires:

```text
tests_after passed
AND final_validation.status == "approved"
AND no out-of-scope changes
AND required changed files were changed
AND no production source-library imports/usages remain
AND target-library usage appears in required migrated files
```

The migration oracle is derived from `metadata.json["migration_oracle"]` if
present. If absent, it falls back to `validity_oracle` / `expected_planner`.

### Single Migration Run From A Planner Output

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/eval_migration_only.py \
  task_020_full_analytics_pipeline \
  --diagnosis-plan experiments/runs/<planner_run>/logs/diagnosis_plan.json
```

Output:

```text
experiments/runs/<task>_<timestamp>_migration/
  migration_only_report.json
  diff.patch
  logs/diagnosis_plan.json
  logs/*_migration.json
  logs/final_validation.json
  logs/final_pytest.log
```

Important fields:

```text
status
executed_step_count
changed_files
tests_after.status
final_validation.status
migration_metrics.migration_success
migration_metrics.behavior_preserved
migration_metrics.scope_compliance
migration_metrics.source_usage_removed
migration_metrics.target_usage_added
migration_metrics.out_of_scope_changes
migration_metrics.old_imports_remaining
migration_metrics.unmigrated_uses
migration_metrics.diff_line_count
migration_metrics.violations
```

### Migration Matrix From A Planner Matrix

This is the recommended migration-agent evaluation. It consumes the planner
matrix run directories and runs migration for each selected diagnosis plan.
Use `--only-valid-plans` to avoid testing migration against invalid plans.

Dry-run:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_migration_matrix.py \
  --planner-matrix experiments/evaluations/planner_matrix_<timestamp> \
  --only-valid-plans \
  --k 1,3 \
  --dry-run
```

Run:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_migration_matrix.py \
  --planner-matrix experiments/evaluations/planner_matrix_<timestamp> \
  --only-valid-plans \
  --k 1,3
```

Output:

```text
experiments/evaluations/migration_matrix_<timestamp>/
  migration_run_level.csv
  migration_pass_at_k.csv
  migration_ablation.csv
  migration_matrix_report.json
```

Generate HTML:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/generate_html_report.py \
  experiments/evaluations/migration_matrix_<timestamp>
```

## Validation Evaluation

Validation evaluation runs the Validation Agent against an existing run
directory containing:

```text
project/
snapshots/before_migration/
logs/diagnosis_plan.json
```

The evaluator compares the observed validator decision with
`metadata.json["validation_oracle"]` when present. If no validation oracle is
defined, the run is still summarized, but `validation_decision_correct` is
unknown because there is no independent label.

Useful validation labels:

```json
{
  "validation_oracle": {
    "expected_verdict": "rejected",
    "expected_rejection_reasons": ["pytest_failed", "out_of_scope_change"],
    "must_detect": ["tests/test_example.py"]
  }
}
```

For successful migrations:

```json
{
  "validation_oracle": {
    "expected_verdict": "approved",
    "expected_rejection_reasons": [],
    "must_detect": []
  }
}
```

### Single Validation Run

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/eval_validation_only.py \
  experiments/runs/<migration_or_full_run> \
  --task-id task_020_full_analytics_pipeline
```

Output:

```text
experiments/runs/<migration_or_full_run>/
  validation_only_report.json
  logs/final_validation.json
  logs/final_pytest.log
  logs/validation_only_pytest.log
```

Important fields:

```text
status
tests.status
final_validation.status
validation_metrics.oracle_available
validation_metrics.expected_verdict
validation_metrics.observed_verdict
validation_metrics.validation_decision_correct
validation_metrics.false_accept
validation_metrics.false_reject
validation_metrics.rejection_reason_match
validation_metrics.observed_rejection_reasons
```

### Validation Matrix

From a migration matrix:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_validation_matrix.py \
  --runs experiments/evaluations/migration_matrix_<timestamp>
```

From a full evaluation matrix:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_validation_matrix.py \
  --runs experiments/evaluations/matrix_<timestamp>
```

From explicit run directories:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_validation_matrix.py \
  --runs experiments/runs/<run_a>,experiments/runs/<run_b>,experiments/runs/<run_c>
```

Output:

```text
experiments/evaluations/validation_matrix_<timestamp>/
  validation_run_level.csv
  validation_summary.csv
  validation_matrix_report.json
```

Generate HTML:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/generate_html_report.py \
  experiments/evaluations/validation_matrix_<timestamp>
```

## Full Workflow Evaluation

Full workflow evaluation runs planner, migration, validation, repair/retry
logic, and final reporting end to end. Use it to measure the system as a whole,
not to isolate one agent.

### Single Task With Pass@K

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/eval_full.py \
  task_020_full_analytics_pipeline \
  --attempts 5 \
  --k 1,3,5
```

Output:

```text
experiments/evaluations/<task>_<timestamp>_full_eval.json
experiments/runs/<task>_<timestamp>/report.json
```

Important fields:

```text
success_count
success_rate
pass_at_k.pass@1
pass_at_k.pass@3
pass_at_k.pass@5
pass_caret_k.pass^1
pass_caret_k.pass^3
pass_caret_k.pass^5
cost_to_success.first_success_rank
cost_to_success.llm_calls_to_first_success
attempts[].run_dir
attempts[].out_of_scope_changes
attempts[].unmigrated_uses
attempts[].llm_calls
```

### Full Evaluation Matrix

Dry-run:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_evaluation_matrix.py \
  --tasks task_001_read_csv_filter,task_020_full_analytics_pipeline \
  --configs v3_base,v3_symbol_analysis \
  --attempts 3 \
  --k 1,3 \
  --dry-run
```

Run:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_evaluation_matrix.py \
  --tasks task_001_read_csv_filter,task_020_full_analytics_pipeline \
  --configs v3_base,v3_symbol_analysis \
  --attempts 3 \
  --k 1,3
```

List configs:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/run_evaluation_matrix.py --list-configs
```

Output:

```text
experiments/evaluations/matrix_<timestamp>/
  run_level.csv
  pass_at_k.csv
  ablation.csv
  matrix_report.json
```

Generate HTML:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/generate_html_report.py \
  experiments/evaluations/matrix_<timestamp>
```

## HTML Reports

`scripts/generate_html_report.py` currently supports:

```text
experiments/evaluations/matrix_<timestamp>/
experiments/evaluations/planner_matrix_<timestamp>/
experiments/evaluations/migration_matrix_<timestamp>/
experiments/evaluations/validation_matrix_<timestamp>/
experiments/evaluations/<task>_<timestamp>_full_eval.json
experiments/runs/<task>_<timestamp>_planner/planner_only_report.json
```

General command:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/generate_html_report.py <evaluation_dir_or_json>
```

Explicit output path:

```bash
cd /Users/sbsn-test/Documents/Projects/multiagent-library-migration

.venv/bin/python scripts/generate_html_report.py \
  experiments/evaluations/planner_matrix_<timestamp> \
  --output experiments/evaluations/planner_matrix_<timestamp>/report.html
```

The generated HTML is self-contained and includes print-specific CSS for PDF
export.

## Result Tables

### Planner Matrix Tables

```text
planner_run_level.csv:
task_id | config | attempt | valid_plan | plan_validity_score |
file_coverage_rate | scope_precision_rate | symbol_coverage_rate |
expected_step_coverage_rate | dependency_plan_valid | step_order_valid |
human_review_match | granularity_valid | plan_violations | llm_calls | run_dir
```

```text
planner_pass_at_k.csv:
task_id | config | attempts | valid_plan_rate | planner_pass@1 |
planner_pass@3 | planner_pass@5 | planner_pass^1 | planner_pass^3 |
planner_pass^5 | first_success_rank | llm_calls_to_success
```

```text
planner_ablation.csv:
config | tasks | attempts | valid_plan_rate | planner_pass@3 |
avg_plan_validity_score | file_coverage_rate | symbol_coverage_rate |
scope_violation_rate | dependency_plan_valid_rate | step_order_valid_rate |
human_review_match_rate | granularity_valid_rate | avg_llm_calls
```

### Migration Matrix Tables

```text
migration_run_level.csv:
task_id | config | attempt | migration_success | behavior_preserved |
final_validation_approved | source_usage_removed | target_usage_added |
scope_compliance | out_of_scope_changes | old_imports_remaining |
unmigrated_uses | missing_required_changed_files |
missing_target_usage_files | diff_line_count | violations | llm_calls | run_dir
```

```text
migration_pass_at_k.csv:
task_id | config | attempts | migration_success_rate | migration_pass@1 |
migration_pass@3 | migration_pass@5 | migration_pass^1 | migration_pass^3 |
migration_pass^5 | first_success_rank | llm_calls_to_success
```

```text
migration_ablation.csv:
config | tasks | attempts | migration_success_rate | migration_pass@3 |
behavior_preservation_rate | scope_compliance_rate |
source_usage_removed_rate | target_usage_added_rate | avg_diff_line_count |
avg_llm_calls | avg_duration_seconds
```

### Validation Matrix Tables

```text
validation_run_level.csv:
task_id | oracle_available | expected_verdict | observed_verdict |
validation_decision_correct | false_accept | false_reject |
rejection_reason_match | expected_rejection_reasons |
observed_rejection_reasons | tests_passed | final_validation_status |
out_of_scope_changes | old_imports_remaining | unmigrated_uses
```

```text
validation_summary.csv:
runs | labeled_runs | validation_accuracy | false_accept_rate |
false_reject_rate | rejection_reason_match_rate | approval_rate |
rejection_rate | avg_llm_calls | avg_duration_seconds
```

### Full Workflow Matrix Tables

```text
run_level.csv:
task_id | config | attempt | success | tests_after | final_validation |
out_of_scope_changes | unmigrated_uses | retries | replans | llm_calls | run_dir
```

```text
pass_at_k.csv:
task_id | config | attempts | success_rate | pass@1 | pass@3 | pass@5 |
pass^1 | pass^3 | pass^5 | first_success_rank | llm_calls_to_success
```

```text
ablation.csv:
config | tasks | success_rate | pass@3 | pass@5 | avg_llm_calls |
avg_retries | scope_violation_rate | unmigrated_usage_rate
```

## Interpretation

- High planner `valid_plan_rate` means the planner usually produces a valid
  migration contract.
- High migration success with valid frozen plans means the Migration Agent can
  execute good plans.
- Low migration success with high planner validity points to implementation
  capability, repair quality, dependency handling, or pandas/polars semantic
  gaps.
- High validation `false_accept_rate` is the most dangerous validation failure:
  the validator approved a known-bad run.
- High validation `false_reject_rate` means the validator is too strict or the
  oracle is incomplete.
- High end-to-end `pass@K` with low `pass^k` means the system can find a
  solution but is unstable across repeated attempts.
- High `pass^k` means the workflow is stable.
- High scope violations indicate planner/migration scope-control weaknesses.
- High unmigrated usage indicates planner coverage or migration capability
  weaknesses.
