# Planner JSON API (DiagnosisAgent)

Output produced by the **DiagnosisAgent**, consumed by the **MigrationAgent**
to execute changes and by the **ValidationAgent** to verify the result.

---

## Root structure

```json
{
  "agent": "diagnosis_agent",
  "source_library": "pandas",
  "target_library": "polars",
  "read_only": true,
  "dependency_files": [ "requirements.txt" ],
  "affected_files": [ "src/orders/processing.py" ],
  "related_tests": [ "tests/test_processing.py" ],
  "complexity": { "src/orders/processing.py": "low" },
  "migration_steps": [ ... ]
}
```

| Field | Type | Description |
|---|---|---|
| `agent` | string | Agent identifier (`"diagnosis_agent"`). |
| `source_library` | string | Library being migrated from (`"pandas"`). |
| `target_library` | string | Library being migrated to (`"polars"`). |
| `read_only` | boolean | Always `true` — the DiagnosisAgent never modifies code. |
| `dependency_files` | array&lt;string&gt; | Dependency files found (e.g. `requirements.txt`). |
| `affected_files` | array&lt;string&gt; | Source files that import or call the source library. |
| `related_tests` | array&lt;string&gt; | Test files associated with the affected files. |
| `complexity` | object | Map of `path → "low" \| "medium" \| "high"` per file. |
| `migration_steps` | array | Ordered list of steps for the MigrationAgent to execute. |

---

## migration_steps[]

Each entry represents one atomic migration step:

```json
{
  "step_id": "step_001",
  "file": "src/orders/processing.py",
  "description": "Migrate supported pandas read/filter/select/sort usage to Polars.",
  "allowed_files": [ "src/orders/processing.py" ],
  "allowed_symbols": [ "get_paid_orders" ],
  "status": "planned"
}
```

| Field | Type | Description |
|---|---|---|
| `step_id` | string | Unique step identifier (`"step_001"`, `"step_002"`, …). |
| `file` | string | File path relative to the repository root. |
| `description` | string | Human-readable summary of the step's intent. |
| `allowed_files` | array&lt;string&gt; | Files the MigrationAgent is allowed to modify in this step. |
| `allowed_symbols` | array&lt;string&gt; | Optional function/class names inside `file` that this step may migrate. When present, validation checks source-library usage for those symbols instead of the whole file. |
| `status` | string | Initial state: always `"planned"`. Updated by the MigrationAgent to `"completed"` or `"no_change"`. |

For larger files, the planner may split one file into multiple symbol-level
steps. This allows the workflow to measure partial migration success: one
function can fail and be marked for manual review while later functions are
still attempted.

---

## Complete example

```json
{
  "agent": "diagnosis_agent",
  "source_library": "pandas",
  "target_library": "polars",
  "read_only": true,
  "dependency_files": [ "requirements.txt" ],
  "affected_files": [ "src/orders/processing.py" ],
  "related_tests": [ "tests/test_processing.py" ],
  "complexity": {
    "src/orders/processing.py": "low"
  },
  "migration_steps": [
    {
      "step_id": "step_001",
      "file": "src/orders/processing.py",
      "description": "Migrate supported pandas read/filter/select/sort usage to Polars.",
      "allowed_files": [ "src/orders/processing.py" ],
      "allowed_symbols": [ "get_paid_orders" ],
      "status": "planned"
    }
  ]
}
```
