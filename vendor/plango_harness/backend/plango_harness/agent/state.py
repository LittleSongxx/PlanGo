from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from .contracts import (
    ActionProposal,
    ActionResult,
    AdvocateReport,
    AgentArtifact,
    CritiqueReport,
    Evidence,
    MemoryProposal,
    PlaceCandidate,
    PlanCandidate,
    PlanDraft,
    RunPhase,
    TripSpec,
    VerifierResult,
)


def _later_deadline(previous, value):
    """Browser pause extensions can arrive with resumed checkpoint writes."""
    if previous is None:
        return value
    if value is None:
        return previous
    return max(previous, value)


def _latest_reference(previous, value):
    """Resumed checkpoint writes may repeat an accepted message's immutable timestamp."""
    if previous is None:
        return value
    if value is None:
        return previous
    return max((previous, value), key=datetime.fromisoformat)


def _cumulative_count(previous, value):
    return max(int(previous or 0), int(value or 0))


def _budget_checkpoint(previous, value):
    """Fan-out/replay repeats one grant; never add duplicated allowance."""
    if not previous or not previous.get("id"):
        return value or {}
    if not value or not value.get("id"):
        return previous
    if previous["id"] != value["id"]:
        # Old checkpoints have no grant_seq; their persisted start time is
        # the compatibility ordering until the next explicit user grant.
        key = "grant_seq" if previous.get("grant_seq") is not None and value.get("grant_seq") is not None else "started_at"
        return value if float(value.get(key, 0)) >= float(previous.get(key, 0)) else previous
    def revision(row):
        return (float(row.get("resumed_at") or row.get("started_at") or 0), float(row.get("deadline_at") or 0))
    return value if revision(value) >= revision(previous) else previous


class PlanGoState(TypedDict, total=False):
    run_id: str
    browser_wait: dict[str, Any] | None
    location_origin: dict[str, Any]
    browser_vision_turn: int | None
    browser_vision_reason: str | None
    browser_steps: int
    processed_image_hash: str | None
    browser_image_context: str
    browser_image_turn_id: int | None
    browser_skill_context: str
    browser_next: dict[str, Any]
    browser_observation: dict[str, Any]
    browser_action: dict[str, Any] | None
    browser_before_action: dict[str, Any]
    browser_receipt_pending: bool
    browser_artifacts: list[dict[str, Any]]
    browser_task_context: dict[str, Any]
    thread_id: str
    user_id: str
    input_text: str
    pending_message: str | None
    requirement_reference_at: Annotated[str | None, _latest_reference]
    requirement_patch: list[dict[str, Any]]
    requirement_refresh: dict[str, bool]
    previous_plan: PlanCandidate | None
    selected_poi: dict[str, Any] | None
    messages: Annotated[list[AnyMessage], add_messages]

    phase: RunPhase
    outcome: str | None
    reason: str
    turn_count: int
    turn_id: int
    plan_version: int
    tool_call_count: Annotated[int, _cumulative_count]
    turn_budget: Annotated[dict[str, Any], _budget_checkpoint]
    budget_pause: dict[str, Any] | None
    repair_round: int
    repair_applied: bool
    started_at: float | None
    deadline_at: Annotated[float | None, _later_deadline]
    timeout_stage: str | None
    model_token_count: int
    model_call_count: int
    model_fallback_count: int
    model_total_latency_ms: float
    model_last_error: str | None
    model_last_usage: dict[str, int]
    model_calls: list[dict[str, Any]]
    # These two lists are audit-oriented fan-out accumulators. They are not a
    # second source of truth; the durable projection/event tables remain the
    # query surface.
    delegated_roles: Annotated[list[str], operator.add]
    advocate_role: str | None
    clarification: dict[str, Any] | None
    interrupt_id: str | None
    consumed_command_id: str | None

    trip_spec: TripSpec | None
    structured_requirement_edit: dict[str, Any] | None
    previous_spec: TripSpec | None
    memory_context: list[dict[str, Any]]
    # Evidence is the active turn's working set. Historical evidence remains
    # available through run_event/artifacts; keeping this list replaceable
    # prevents a replan from inheriting stale observations.
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    place_candidates: list[PlaceCandidate]
    artifacts: Annotated[list[AgentArtifact], operator.add]
    candidate_plans: list[PlanCandidate]
    plan_draft: PlanDraft | None
    draft_errors: list[dict[str, Any]]
    advocate_reports: Annotated[list[AdvocateReport], operator.add]
    critique: CritiqueReport | None
    verifier: VerifierResult | None
    selected_plan: PlanCandidate | None
    action_proposal: ActionProposal | None
    action_results: list[ActionResult]
    memory_delta: list[MemoryProposal]
    approval_decision: str | None
    execution_goal: dict[str, Any] | None
    preparation_restart: dict[str, Any] | None
    execution_outcome: dict[str, Any] | None
    execution_started: bool
    reflection_done: bool

    # The deterministic coordinator selects a bounded workflow transition.
    next_action: str | None
    next_arguments: dict[str, Any]
    last_observation: dict[str, Any] | None
    trace: Annotated[list[dict[str, Any]], operator.add]


def initial_state(
    *, run_id: str, user_id: str, input_text: str, thread_id: str | None = None
) -> PlanGoState:
    return {
        "run_id": run_id,
        "browser_vision_turn": None,
        "browser_vision_reason": None,
        "thread_id": thread_id or run_id,
        "user_id": user_id,
        "input_text": input_text,
        "requirement_reference_at": None,
        "requirement_patch": [],
        "requirement_refresh": {},
        "previous_plan": None,
        "selected_poi": None,
        "messages": [HumanMessage(content=input_text, id=f"user:{run_id}:1")],
        "phase": RunPhase.CREATED,
        "outcome": None,
        "reason": "",
        "turn_count": 0,
        "turn_id": 1,
        "plan_version": 0,
        "tool_call_count": 0,
        "turn_budget": {},
        "budget_pause": None,
        "repair_round": 0,
        "repair_applied": False,
        "started_at": None,
        "deadline_at": None,
        "timeout_stage": None,
        "model_token_count": 0,
        "model_call_count": 0,
        "model_fallback_count": 0,
        "model_total_latency_ms": 0.0,
        "model_last_error": None,
        "model_last_usage": {},
        "model_calls": [],
        "delegated_roles": [],
        "advocate_role": None,
        "clarification": None,
        "interrupt_id": None,
        "memory_context": [],
        "previous_spec": None,
        "evidence": [],
        "weather": None,
        "place_candidates": [],
        "artifacts": [],
        "candidate_plans": [],
        "plan_draft": None,
        "draft_errors": [],
        "advocate_reports": [],
        "critique": None,
        "verifier": None,
        "selected_plan": None,
        "action_proposal": None,
        "action_results": [],
        "memory_delta": [],
        "approval_decision": None,
        "execution_goal": None,
        "execution_outcome": None,
        "execution_started": False,
        "reflection_done": False,
        "next_action": None,
        "next_arguments": {},
        "last_observation": None,
        "trace": [],
    }


def planning_reset(state: dict[str, Any]) -> dict[str, Any]:
    """Invalidate decisions while retaining observations until canonical requirements are compared."""
    # Additive report/role lists remain audit history; [] is not an overwrite.
    # The workflow's current-report selector binds reports to the active run/turn.
    return {
        "previous_spec": state.get("trip_spec") or state.get("previous_spec"),
        "previous_plan": state.get("selected_plan") or state.get("previous_plan")
        or next(iter(state.get("candidate_plans") or []), None),
        "trip_spec": None, "plan_draft": None, "draft_errors": [],
        "place_candidates": state.get("place_candidates", []),
        "candidate_plans": [], "selected_plan": None, "verifier": None, "critique": None,
        "action_proposal": None, "action_results": [], "approval_decision": None,
        "interrupt_id": None, "advocate_reports": [], "delegated_roles": [],
        "evidence": state.get("evidence", []), "weather": state.get("weather"),
        "memory_delta": [], "execution_goal": None, "execution_outcome": None,
        "preparation_restart": None,
        "execution_started": False, "reflection_done": False,
        "requirement_patch": [], "requirement_refresh": {},
        "structured_requirement_edit": None,
        "last_observation": None,
    }
