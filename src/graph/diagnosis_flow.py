from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from src import llm_proxy
from src.graph.state import GraphState


class DiagnosisRunner(Protocol):
    def run(
        self,
        project_dir: Path,
        logs_dir: Path,
        source_library: str,
        target_library: str,
        replan_feedback: dict[str, Any] | None = None,
        replan_attempt: int = 0,
    ) -> dict[str, Any]: ...


def build_diagnosis_node(diagnosis_agent: DiagnosisRunner, logs_dir: Path):
    def diagnose(graph_state: GraphState) -> dict[str, Any]:
        replan_count = graph_state["replan_count"]
        llm_proxy.set_label(
            "diagnose" if not replan_count else f"replan_{replan_count}"
        )
        diagnosis = diagnosis_agent.run(
            graph_state["project_dir"],
            logs_dir,
            source_library=graph_state["source_library"],
            target_library=graph_state["target_library"],
            replan_feedback=graph_state["replan_feedback"],
            replan_attempt=graph_state["replan_count"],
        )
        return {"diagnosis": diagnosis, "next_action": None}

    return diagnose
