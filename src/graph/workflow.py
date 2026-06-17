from __future__ import annotations

import os

from langgraph.graph import END, StateGraph

from src.agents.diagnosis_agent import DiagnosisAgent as LegacyDiagnosisAgent
from src.agents.migration_agent import MigrationAgent
from src.agents.planner_v3_agent import PlannerV3Agent
from src.agents.repair_agent import RepairAgent
from src.agents.validation_agent import ValidationAgent
from src.graph.diagnosis_flow import build_diagnosis_node
from src.graph.migration_flow import (
    build_migration_node,
    build_snapshot_node,
    route_after_selection,
    select_next_step,
)
from src.graph.state import GraphState, WorkflowState, to_graph_state, to_workflow_state
from src.graph.validation_flow import build_validation_node, route_after_validation

DiagnosisAgent = PlannerV3Agent


def run_simple_workflow(state: WorkflowState) -> WorkflowState:
    logs_dir = state.run_dir / "logs"
    snapshots_dir = state.run_dir / "snapshots"
    diagnosis_agent = _build_diagnosis_agent()
    migration_agent = MigrationAgent()
    repair_agent = RepairAgent()
    validation_agent = ValidationAgent()

    graph = StateGraph(GraphState)
    graph.add_node("diagnose", build_diagnosis_node(diagnosis_agent, logs_dir))
    graph.add_node("select_next_step", select_next_step)
    graph.add_node("snapshot_before_step", build_snapshot_node(snapshots_dir))
    graph.add_node("migrate_step", build_migration_node(migration_agent, logs_dir))
    graph.add_node(
        "validate_step", build_validation_node(validation_agent, logs_dir, repair_agent)
    )
    graph.set_entry_point("diagnose")
    graph.add_edge("diagnose", "select_next_step")
    graph.add_conditional_edges(
        "select_next_step",
        route_after_selection,
        {"snapshot_before_step": "snapshot_before_step", "__end__": END},
    )
    graph.add_edge("snapshot_before_step", "migrate_step")
    graph.add_edge("migrate_step", "validate_step")
    graph.add_conditional_edges(
        "validate_step",
        route_after_validation,
        {
            "diagnose": "diagnose",
            "select_next_step": "select_next_step",
            "snapshot_before_step": "snapshot_before_step",
            "__end__": END,
        },
    )

    final_state = graph.compile().invoke(
        to_graph_state(state),
        config={"recursion_limit": 100},
    )
    return to_workflow_state(state, final_state)


def _build_diagnosis_agent():
    impl = os.getenv("DIAGNOSIS_AGENT_IMPL", "v3").strip().lower()
    if impl in {"legacy", "current", "v2"}:
        return LegacyDiagnosisAgent()
    return DiagnosisAgent()
