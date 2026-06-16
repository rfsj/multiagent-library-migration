from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]

from src.agents.diagnosis_agent import DiagnosisAgent

TASK_DIR = ROOT / "benchmark" / "task_001_read_csv_filter"
LOGS_DIR = ROOT / "experiments" / "diagnosis"


def main() -> None:
    metadata = json.loads((TASK_DIR / "metadata.json").read_text(encoding="utf-8"))

    agent = DiagnosisAgent()
    plan = agent.run(
        project_dir=TASK_DIR / "input_project",
        logs_dir=LOGS_DIR,
        source_library=metadata["source_library"],
        target_library=metadata["target_library"],
    )

    print(json.dumps(plan, indent=2))
    print(f"\nPlan saved to: {LOGS_DIR / 'diagnosis_plan.json'}")


if __name__ == "__main__":
    main()
