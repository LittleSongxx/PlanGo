from __future__ import annotations

import operator
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


class PlanoraState(TypedDict, total=False):
    run_id: str
    browser_wait: dict[str, Any] | None
    location_origin: dict[str, Any]
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
    messages: Annotated[list[AnyMessage], add_messages]

    phase: RunPhase
    outcome: str | None
    reason: str
    turn_count: int
    turn_id: int
    plan_version: int
    tool_call_count: int
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
    execution_started: bool
    reflection_done: bool

    # The model's next decision is deliberately a small, validated command.
    next_action: str | None
    next_arguments: dict[str, Any]
    last_observation: dict[str, Any] | None
    trace: Annotated[list[dict[str, Any]], operator.add]


def initial_state(
    *, run_id: str, user_id: str, input_text: str, thread_id: str | None = None
) -> PlanoraState:
    return {
        "run_id": run_id,
        "thread_id": thread_id or run_id,
        "user_id": user_id,
        "input_text": input_text,
        "messages": [HumanMessage(content=input_text)],
        "phase": RunPhase.CREATED,
        "outcome": None,
        "reason": "",
        "turn_count": 0,
        "turn_id": 1,
        "plan_version": 0,
        "tool_call_count": 0,
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
        "execution_started": False,
        "reflection_done": False,
        "next_action": None,
        "next_arguments": {},
        "last_observation": None,
        "trace": [],
    }
