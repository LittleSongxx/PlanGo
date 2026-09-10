"""Small, explicit LangGraph subgraphs used at specialist boundaries."""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from plango_harness.agent.contracts import (
    ActionResult,
    AdvocateReport,
    AgentArtifact,
    CritiqueReport,
    Evidence,
    MemoryProposal,
    PlaceCandidate,
    PlanCandidate,
    RunPhase,
    TripSpec,
    VerifierResult,
)
from typing_extensions import TypedDict


class RequirementState(TypedDict, total=False):
    requirement_proposal: dict[str, Any] | None
    consumed_command_id: str | None
    interrupt_id: str | None
    structured_requirement_edit: dict[str, Any] | None
    turn_budget: dict[str, Any]
    browser_task_context: dict[str, Any]
    requirement_reference_at: str | None
    requirement_patch: list[dict[str, Any]]
    requirement_refresh: dict[str, bool]
    previous_plan: PlanCandidate | None
    selected_poi: dict[str, Any] | None
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    candidate_plans: list[PlanCandidate]
    location_origin: dict[str, Any]
    run_id: str
    turn_id: int
    user_id: str
    input_text: str
    memory_context: list[dict[str, Any]]
    previous_spec: TripSpec | None
    place_candidates: list[PlaceCandidate]
    messages: list[AnyMessage]
    tool_call_count: int
    phase: RunPhase
    reason: str
    last_observation: dict[str, Any] | None
    trip_spec: TripSpec | None
    clarification: dict[str, Any] | None
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class RequirementInput(TypedDict, total=False):
    requirement_proposal: dict[str, Any] | None
    structured_requirement_edit: dict[str, Any] | None
    turn_budget: dict[str, Any]
    browser_task_context: dict[str, Any]
    requirement_reference_at: str | None
    requirement_patch: list[dict[str, Any]]
    requirement_refresh: dict[str, bool]
    previous_plan: PlanCandidate | None
    selected_poi: dict[str, Any] | None
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    candidate_plans: list[PlanCandidate]
    location_origin: dict[str, Any]
    run_id: str
    turn_id: int
    user_id: str
    input_text: str
    memory_context: list[dict[str, Any]]
    previous_spec: TripSpec | None
    place_candidates: list[PlaceCandidate]
    clarification: dict[str, Any] | None
    messages: list[AnyMessage]
    tool_call_count: int


class RequirementOutput(TypedDict, total=False):
    input_text: str
    messages: list[AnyMessage]
    consumed_command_id: str | None
    interrupt_id: str | None
    place_candidates: list[PlaceCandidate]
    requirement_patch: list[dict[str, Any]]
    requirement_refresh: dict[str, bool]
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    location_origin: dict[str, Any]
    trip_spec: TripSpec | None
    clarification: dict[str, Any] | None
    tool_call_count: int
    phase: RunPhase
    outcome: str | None
    reason: str
    last_observation: dict[str, Any] | None
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class DiscoveryState(TypedDict, total=False):
    turn_budget: dict[str, Any]
    browser_task_context: dict[str, Any]
    previous_spec: TripSpec | None
    requirement_refresh: dict[str, bool]
    selected_poi: dict[str, Any] | None
    turn_id: int
    run_id: str
    user_id: str
    turn_count: int
    next_arguments: dict[str, Any]
    trip_spec: TripSpec
    place_candidates: list[PlaceCandidate]
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    phase: RunPhase
    tool_call_count: int
    reason: str
    last_observation: dict[str, Any] | None
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class DiscoveryInput(TypedDict, total=False):
    turn_budget: dict[str, Any]
    browser_task_context: dict[str, Any]
    previous_spec: TripSpec | None
    requirement_refresh: dict[str, bool]
    selected_poi: dict[str, Any] | None
    place_candidates: list[PlaceCandidate]
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    turn_id: int
    run_id: str
    user_id: str
    turn_count: int
    next_arguments: dict[str, Any]
    trip_spec: TripSpec
    tool_call_count: int


class DiscoveryOutput(TypedDict, total=False):
    place_candidates: list[PlaceCandidate]
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    phase: RunPhase
    tool_call_count: int
    outcome: str | None
    reason: str
    last_observation: dict[str, Any] | None
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class AdvocateInput(TypedDict, total=False):
    turn_budget: dict[str, Any]
    evidence: list[Evidence]
    run_id: str
    user_id: str
    turn_id: int
    trip_spec: TripSpec
    place_candidates: list[PlaceCandidate]
    advocate_role: str


class AdvocateState(AdvocateInput, total=False):
    advocate_reports: Annotated[list[AdvocateReport], operator.add]
    delegated_roles: Annotated[list[str], operator.add]
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class AdvocateOutput(TypedDict, total=False):
    advocate_reports: Annotated[list[AdvocateReport], operator.add]
    delegated_roles: Annotated[list[str], operator.add]
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class CriticState(TypedDict, total=False):
    turn_budget: dict[str, Any]
    browser_task_context: dict[str, Any]
    previous_spec: TripSpec | None
    turn_id: int
    run_id: str
    user_id: str
    trip_spec: TripSpec
    selected_plan: PlanCandidate
    place_candidates: list[PlaceCandidate]
    verifier: VerifierResult
    candidate_plans: list[PlanCandidate]
    repair_round: int
    plan_version: int
    critique: CritiqueReport | None
    phase: RunPhase
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    tool_call_count: int
    reason: str
    selected_plan_out: PlanCandidate | None
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class CriticInput(TypedDict, total=False):
    turn_budget: dict[str, Any]
    browser_task_context: dict[str, Any]
    previous_spec: TripSpec | None
    turn_id: int
    run_id: str
    user_id: str
    trip_spec: TripSpec
    selected_plan: PlanCandidate
    place_candidates: list[PlaceCandidate]
    verifier: VerifierResult
    candidate_plans: list[PlanCandidate]
    repair_round: int
    plan_version: int
    evidence: list[Evidence]
    weather: dict[str, Any] | None
    tool_call_count: int


class CriticOutput(TypedDict, total=False):
    critique: CritiqueReport | None
    repair_applied: bool
    repair_round: int
    plan_version: int
    phase: RunPhase
    selected_plan: PlanCandidate | None
    verifier: VerifierResult | None
    last_observation: dict[str, Any] | None
    evidence: list[Evidence]
    tool_call_count: int
    outcome: str | None
    reason: str
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class ReflectionState(TypedDict, total=False):
    turn_budget: dict[str, Any]
    turn_id: int
    phase: RunPhase
    approval_decision: str | None
    requirement_reference_at: str | None
    started_at: float
    run_id: str
    user_id: str
    trip_spec: TripSpec
    selected_plan: PlanCandidate | None
    action_results: list[ActionResult]
    memory_delta: Annotated[list[MemoryProposal], operator.add]
    reflection_done: bool
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


class ReflectionInput(TypedDict, total=False):
    turn_budget: dict[str, Any]
    turn_id: int
    phase: RunPhase
    approval_decision: str | None
    reflection_done: bool
    requirement_reference_at: str | None
    started_at: float
    run_id: str
    user_id: str
    trip_spec: TripSpec
    selected_plan: PlanCandidate | None
    action_results: list[ActionResult]


class ReflectionOutput(TypedDict, total=False):
    memory_delta: Annotated[list[MemoryProposal], operator.add]
    reflection_done: bool
    artifacts: Annotated[list[AgentArtifact], operator.add]
    trace: list[dict[str, Any]]


def _compile(
    state_schema: type,
    input_schema: type,
    node: Callable[..., Awaitable[dict[str, Any]]],
    output_schema: type,
):
    graph: Any = StateGraph(
        state_schema,
        input_schema=input_schema,
        output_schema=output_schema,
    )

    async def run(state):
        return await node(state)

    graph.add_node("run", run)  # type: ignore[call-overload]
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph.compile()


def requirement_subgraph(node):
    return _compile(RequirementState, RequirementInput, node, RequirementOutput)


def discovery_subgraph(node):
    return _compile(DiscoveryState, DiscoveryInput, node, DiscoveryOutput)


def advocate_subgraph(node):
    return _compile(AdvocateState, AdvocateInput, node, AdvocateOutput)


def critic_subgraph(node):
    return _compile(CriticState, CriticInput, node, CriticOutput)


def reflection_subgraph(node):
    return _compile(ReflectionState, ReflectionInput, node, ReflectionOutput)
