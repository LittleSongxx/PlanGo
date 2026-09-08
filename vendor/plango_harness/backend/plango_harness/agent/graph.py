from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import ormsgpack
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send, interrupt
from plango_harness.agent.contracts import (
    ActionResult,
    ActionStatus,
    AgentArtifact,
    Evidence,
    Location,
    PlaceCandidate,
    PlanCandidate,
    PlanDraft,
    PlanDraftStop,
    RunPhase,
    TripSpec,
    VerifierResult,
)
from plango_harness.agent.decisions import SupervisorDecision
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import PlanGoState
from plango_harness.agent.subagents import (
    AdvocateAgent,
    CriticAgent,
    DiscoveryAgent,
    PlannerAgent,
    ReflectionAgent,
    RequirementAgent,
)
from plango_harness.agent.subgraphs import (
    advocate_subgraph,
    critic_subgraph,
    discovery_subgraph,
    reflection_subgraph,
    requirement_subgraph,
)
from plango_harness.domain.planning import PlanEngine, ToolBudgetExceeded, compile_plan_draft, goal_errors
from plango_harness.memory.repository import MemoryRepository
from plango_harness.persistence.actions import ActionLedger
from plango_harness.persistence.runs import RunRepository
from plango_harness.providers.actions import ActionProvider
from plango_harness.providers.world import WorldProvider
from plango_harness.tools.registry import ToolContext, ToolRegistry


@dataclass
class GraphDeps:
    model: ModelAdapter
    world: WorldProvider
    planner: PlanEngine
    memory: MemoryRepository
    runs: RunRepository
    tools: ToolRegistry
    action_provider: ActionProvider
    ledger: ActionLedger | None = None
    agent_mode: Literal["multi", "single"] = "multi"
    max_turns: int = 12
    max_repair_rounds: int = 2
    max_context_tokens: int = 6000
    max_tool_calls: int = 48
    max_run_seconds: int = 300
    max_model_tokens: int = 12000
    node_timeout_seconds: int = 60

    def tool_context(self, state: PlanGoState, *, approved: bool = False) -> ToolContext:
        if hasattr(self.world, "bind_run_state"):
            self.world.bind_run_state(state)
        return ToolContext(
            world=self.world,
            planner=self.planner,
            memory=self.memory,
            runs=self.runs,
            ledger=self.ledger or ActionLedger(self.runs),
            action_provider=self.action_provider,
            run_id=state["run_id"],
            user_id=state["user_id"],
            trip_spec=state.get("trip_spec"),
            selected_plan=state.get("selected_plan"),
            approved=approved,
            tool_call_count=int(state.get("tool_call_count", 0)),
            max_tool_calls=self.max_tool_calls,
        )


def _short_reason(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _trace(state: PlanGoState, event: str, **payload: Any) -> list[dict[str, Any]]:
    raw_phase = state.get("phase") or RunPhase.CREATED
    phase = raw_phase.value if isinstance(raw_phase, RunPhase) else str(raw_phase)
    expected_phase = {
        "requirements_ready": RunPhase.REQUIREMENTS_READY.value,
        "discovery_complete": RunPhase.RESEARCHING.value,
        "advocate_complete": RunPhase.RESEARCHING.value,
        "plan_synthesized": RunPhase.PLAN_DRAFTED.value,
        "plan_verified": RunPhase.REVIEWING.value,
        "critic_complete": RunPhase.REVIEWING.value,
        "plan_repair_selected": RunPhase.REPLANNING.value,
        "clarification_requested": RunPhase.REQUIREMENTS_READY.value,
        "memory_reflected": phase if phase != RunPhase.CREATED.value else RunPhase.SUCCEEDED.value,
    }.get(event)
    if expected_phase:
        phase = expected_phase
    agent = {
        "memory_retrieved": "memory",
        "requirements_ready": "requirement",
        "discovery_complete": "discovery",
        "advocate_complete": f"advocate:{payload.get('role', 'specialist')}",
        "plan_synthesized": "planner",
        "plan_verified": "verifier",
        "critic_complete": "critic",
        "plan_repair_selected": "critic",
        "approval_requested": "supervisor",
        "approval_resolved": "approval",
        "actions_executed": "executor",
        "memory_reflected": "reflection",
        "supervisor_decision": "supervisor",
        "replan_requested": "supervisor",
        "advocate_fanout_started": "supervisor",
        "clarification_received": "requirement",
        "clarification_requested": "supervisor",
        "run_finalized": "runtime",
    }.get(event, "runtime")
    return [
        {
            "event": event,
            "phase": phase,
            "agent_id": agent,
            "payload": payload,
            # A monotonic stage clock is not serializable across processes;
            # wall time is sufficient for relative audit latency and is kept
            # as metadata only.
            "ts": time.time(),
        }
    ]


def _current_advocate_reports(state: PlanGoState) -> list[Any]:
    """Return only reports from the active planning turn.

    Reports remain in the checkpoint for audit history, but stale reports must
    not suppress a fresh fan-out after a user edit or replan.
    """
    turn_id = int(state.get("turn_id", 1))
    current: list[Any] = []
    for report in state.get("advocate_reports", []):
        report_turn = (
            getattr(report, "turn_id", None)
            if not isinstance(report, dict)
            else report.get("turn_id")
        )
        if int(report_turn or 1) == turn_id:
            current.append(report)
    return current


def _dedupe_evidence(rows: list[Any]) -> list[Evidence]:
    """Keep the newest occurrence of each evidence ID in the active turn."""
    by_id: dict[str, Evidence] = {}
    for raw in rows:
        evidence = Evidence.model_validate(raw) if isinstance(raw, dict) else raw
        by_id[evidence.evidence_id] = evidence
    return list(by_id.values())


def _place_candidates(rows: list[Any] | None) -> list[PlaceCandidate]:
    """Normalize checkpoint-deserialized places before specialist/planner use."""
    return [
        PlaceCandidate.model_validate(row) if isinstance(row, dict) else row
        for row in (rows or [])
    ]


def _replacement_exclusions(plan: PlanCandidate, verifier: VerifierResult) -> set[str]:
    issues = [*verifier.hard_violations, *verifier.unknown_evidence]
    excluded = {stop.place_id for stop in plan.stops if any(
        check.name.endswith(":" + stop.place_id) and not check.name.startswith("distance:")
        for check in issues
    )}
    for index, stop in enumerate(plan.stops):
        if any(check.name == "distance:" + stop.place_id for check in issues):
            # A route is a pair: replacing its bad origin can repair the leg
            # without discarding a destination that meets its own constraints.
            if index == 0 or plan.stops[index - 1].place_id not in excluded:
                excluded.add(stop.place_id)
    return excluded


def _can_repair_price_gap(state: PlanGoState, max_rounds: int) -> bool:
    verifier = state.get("verifier")
    if isinstance(verifier, dict):
        verifier = VerifierResult.model_validate(verifier)
    return bool(verifier and verifier.unknown_evidence
                and all(check.name.startswith("price:") for check in verifier.unknown_evidence)
                and not state.get("repair_applied") and state.get("repair_round", 0) < max_rounds)


def _planning_spec(spec: TripSpec, weather: dict[str, Any] | None) -> TripSpec:
    """Apply a conservative weather preference without rewriting user intent."""
    if not weather or not weather.get("rain"):
        return spec
    text = " ".join([spec.goal, *spec.hard_constraints, *spec.soft_preferences]).lower()
    if any(word in text for word in ("户外", "露营", "公园", "公园优先")):
        return spec
    preferences = list(dict.fromkeys([*spec.soft_preferences, "室内优先"]))
    return spec.model_copy(update={"soft_preferences": preferences})


_CHECKPOINT_TYPES = (
    "ActionItem",
    "ActionProposal",
    "ActionResult",
    "ActionStatus",
    "AdvocateReport",
    "ConstraintCheck",
    "CritiqueReport",
    "Evidence",
    "Location",
    "MemoryProposal",
    "PartyMember",
    "PlaceCandidate",
    "PlanCandidate",
    "PlanDraft",
    "PlanDraftStop",
    "PlanStop",
    "RunPhase",
    "TripSpec",
    "VerifierResult",
    "AgentArtifact",
)


def _rename_legacy_contracts(code: int, data: bytes) -> ormsgpack.Ext:
    # R0 read compatibility: only the former exact contract type allowlist is mapped.
    # Keep until retained pre-PlanGo checkpoints have expired; never rewrite payload text.
    if code not in {0, 1, 2, 3, 4, 5, 7}:
        return ormsgpack.Ext(code, data)
    value = ormsgpack.unpackb(data, ext_hook=_rename_legacy_contracts, option=ormsgpack.OPT_NON_STR_KEYS)
    if code != 7 and isinstance(value, list) and value and value[0] == "planora.agent.contracts":
        if len(value) < 3 or value[1] not in _CHECKPOINT_TYPES or code not in {0, 5}:
            raise ValueError("unsupported_legacy_checkpoint_contract")
        value[0] = "plango_harness.agent.contracts"
    return ormsgpack.Ext(code, ormsgpack.packb(value, option=ormsgpack.OPT_NON_STR_KEYS))


class ContractCheckpointSerializer(JsonPlusSerializer):
    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        kind, payload = data
        if kind == "msgpack" and b"planora.agent.contracts" in payload:
            value = ormsgpack.unpackb(payload, ext_hook=_rename_legacy_contracts, option=ormsgpack.OPT_NON_STR_KEYS)
            data = kind, ormsgpack.packb(value, option=ormsgpack.OPT_NON_STR_KEYS)
        return super().loads_typed(data)


def checkpoint_serializer() -> JsonPlusSerializer:
    return ContractCheckpointSerializer(
        allowed_msgpack_modules=[("plango_harness.agent.contracts", name) for name in _CHECKPOINT_TYPES]
    )


def _fallback_decision(state: PlanGoState, deps: GraphDeps) -> SupervisorDecision:
    """A deterministic escape hatch for local tests and provider outages."""
    spec = state.get("trip_spec")
    if not spec:
        return SupervisorDecision(next_action="requirements", reason="缺少结构化目标")
    if isinstance(spec, dict):
        spec = TripSpec.model_validate(spec)
    if state.get("clarification"):
        return SupervisorDecision(next_action="ask_user", reason="需求仍需用户澄清")
    if str(state.get("phase")) in {
        RunPhase.INFEASIBLE.value,
        RunPhase.FAILED.value,
        RunPhase.CANCELLED.value,
    }:
        return SupervisorDecision(next_action="finish", reason=state.get("reason") or "任务已结束")
    if (state.get("last_observation") or {}).get("refresh_discovery"):
        return SupervisorDecision(next_action="discover", reason="编辑影响供给事实，需要刷新搜索")
    if (state.get("last_observation") or {}).get("weather_changed") and not state.get("place_candidates"):
        return SupervisorDecision(next_action="discover", reason="天气观测变化，需要重新搜索")
    if not state.get("place_candidates"):
        return SupervisorDecision(next_action="discover", reason="需要获取候选地点和证据")
    if deps.agent_mode == "multi" and (spec.party_size or 1) > 1 and not _current_advocate_reports(state):
        return SupervisorDecision(next_action="advocate", reason="多人目标需要独立偏好评估")
    if not state.get("selected_plan"):
        return SupervisorDecision(next_action="synthesize", reason="需要汇总候选和角色报告")
    if not state.get("verifier"):
        return SupervisorDecision(next_action="verify", reason="候选计划尚未通过确定性校验")
    verifier = state.get("verifier")
    if isinstance(verifier, dict):
        verifier = VerifierResult.model_validate(verifier)
    if verifier is not None and not verifier.executable:
        # Freshness is the first safety gate.  A stale/unknown observation
        # must ask for clarification before critic can label the plan
        # infeasible because of a secondary hard-constraint check.
        if not verifier.evidence_complete:
            if _can_repair_price_gap(state, deps.max_repair_rounds):
                return SupervisorDecision(next_action="critic", reason="先尝试省略缺价备选或选用已观测的替代地点")
            return SupervisorDecision(next_action="ask_user", reason="证据未完整或已过期")
        if not verifier.hard_constraints_pass:
            critique = state.get("critique")
            critique_verdict = (
                critique.get("verdict")
                if isinstance(critique, dict)
                else getattr(critique, "verdict", "")
            )
            if critique_verdict == "ask_user":
                return SupervisorDecision(next_action="ask_user", reason="Critic 需要用户补充约束")
            if state.get("repair_applied"):
                return SupervisorDecision(next_action="finish", reason="修复后硬约束仍未通过")
            if state.get("repair_round", 0) < deps.max_repair_rounds:
                return SupervisorDecision(next_action="critic", reason="硬约束未通过，需要修复")
            return SupervisorDecision(next_action="finish", reason="达到修复上限，返回不可行原因")
        return SupervisorDecision(next_action="ask_user", reason="仍有证据未确认")
    if not state.get("action_proposal"):
        return SupervisorDecision(
            next_action="propose_actions", reason="计划已验证，生成待确认动作"
        )
    if not state.get("approval_decision"):
        return SupervisorDecision(next_action="propose_actions", reason="等待用户确认")
    if state.get("approval_decision") == "approve" and not state.get("execution_started"):
        return SupervisorDecision(next_action="finish", reason="动作已批准，等待执行队列")
    return SupervisorDecision(next_action="finish", reason="任务已有终态")


def _semantic_cycle(trace: list[dict[str, Any]], *, width: int = 4) -> bool:
    """Detect short semantic action cycles, not only adjacent duplicates."""
    actions = [
        str(item.get("payload", {}).get("effective_action"))
        for item in trace
        if item.get("event") == "supervisor_decision"
    ]
    if len(actions) < width:
        return False
    recent = actions[-width:]
    return len(set(recent)) <= width - 2 and recent[0] != recent[-1]


def _advocate_roles(spec: TripSpec) -> list[str]:
    """Start only the roles named by the request; keep one experience view."""
    text = " ".join([spec.goal, *spec.hard_constraints, *spec.soft_preferences]).lower()
    roles = ["体验"]
    if (spec.party_size or 1) > 1 and any(word in text for word in ("孩子", "儿童", "老人", "家庭", "亲子")):
        roles.insert(0, "家庭")
        roles.insert(1, "健康")
    if spec.total_budget < 1_000 or any(word in text for word in ("预算", "便宜", "省钱", "花费")):
        roles.insert(0, "预算")
    return list(dict.fromkeys(roles))


def _replan_refreshes_supply(text: str) -> bool:
    """Location/time/weather edits need fresh observations; soft edits reuse them."""
    # Cancelling a modality does not invalidate the existing venue facts.
    # Other changes in the same edit (weather/location/new goals) still refresh.
    lowered = re.sub(r"(?:取消|去掉|不再要求|不需要|不要求)(?:室内|户外)(?:要求|限制)?", "", text.lower())
    lowered = re.sub(r"(?:排队|等候|等位|等待)时间", "排队", lowered)
    return any(word in lowered for word in ("地点", "位置", "换个地方", "时间", "几点", "下雨", "天气", "室内", "户外", "增加", "新增", "改喝", "改看"))


def build_graph(deps: GraphDeps, checkpointer: Any | None = None, *, extension=None):
    requirement_agent = RequirementAgent(deps.model)
    discovery_agent = DiscoveryAgent(deps.model, deps.world, deps.tools.schemas())
    advocate_agent = AdvocateAgent(deps.model)
    critic_agent = CriticAgent(deps.model)
    planner_agent = PlannerAgent(deps.model)
    reflection_agent = ReflectionAgent(deps.model)
    read_retry = RetryPolicy(initial_interval=0.2, max_interval=2.0, max_attempts=2)

    async def load_memory(state: PlanGoState) -> dict[str, Any]:
        started_at = state.get("started_at") or time.time()
        context = await deps.memory.retrieve(
            state["user_id"],
            state["input_text"],
            limit=8,
            token_budget=deps.max_context_tokens,
        )
        return {
            "memory_context": context,
            "phase": RunPhase.REQUIREMENTS_READY,
            "started_at": started_at,
            "deadline_at": state.get("deadline_at") or started_at + deps.max_run_seconds,
            "timeout_stage": None,
            "trace": _trace(state, "memory_retrieved", count=len(context)),
        }

    async def requirements(state: PlanGoState) -> dict[str, Any]:
        previous_spec = state.get("previous_spec")
        if isinstance(previous_spec, dict):
            previous_spec = TripSpec.model_validate(previous_spec)
        output = await requirement_agent.run(
            state["input_text"],
            state.get("memory_context", []),
            previous_spec,
            state.get("messages", []),
        )
        pending = []
        for field in (state.get("clarification") or {}).get("fields", []):
            if field not in {"budget", "per_person_budget", "duration_minutes", "max_queue_minutes", "max_distance_km"}:
                continue
            cleared = (field == "budget" and output.clear_budget
                       or field == "per_person_budget" and output.clear_per_person_budget
                       or field == "max_queue_minutes" and "低排队" in (output.remove_hard_constraints or [])
                       or field == "max_distance_km" and "距离优先" in (output.remove_hard_constraints or []))
            if getattr(output, field) is None and not cleared:
                pending.append(field)
        if pending:
            output = output.model_copy(update={
                "clarification_needed": True,
                "clarification_fields": list(dict.fromkeys([*output.clarification_fields, *pending])),
                "clarification_question": output.clarification_question or "先前的预算、时长或距离/排队上限仍待确认。",
            })
        spec = output.to_trip_spec(state["input_text"], base=previous_spec)
        location_name = output.location_name or (previous_spec.location.name if previous_spec else "望京")
        resolved_location = None
        tool_call_count = int(state.get("tool_call_count", 0))
        geocode_response: dict[str, Any] | None = None
        ctx = deps.tool_context(state)
        location_origin = None
        if hasattr(deps.world, "requirement_origin"):
            location_name, resolved_location, location_origin = await deps.world.requirement_origin(state, output.location_name, previous_spec)
            if resolved_location is None and location_name:
                geocode_response = await deps.tools.execute("geocode", {"address": location_name}, ctx)
        elif previous_spec is not None and output.location_name is None:
            resolved_location = previous_spec.location
        else:
            geocode_response = await deps.tools.execute("geocode", {"address": location_name}, ctx)
        geocode_observation: dict[str, Any] | None = None
        if geocode_response is not None:
            if geocode_response.get("ok"):
                resolved_location = Location.model_validate(geocode_response.get("result") or {})
            elif geocode_response.get("error") == "tool_budget_exhausted":
                return {
                    "phase": RunPhase.FAILED,
                    "reason": "达到本次运行的 Provider 调用上限",
                    "tool_call_count": ctx.tool_call_count,
                    "last_observation": {"error": "tool_budget_exhausted", "tool": "geocode"},
                }
            else:
                geocode_observation = {
                    "tool": "geocode",
                    "error": geocode_response.get("error", "tool_error"),
                }
        tool_call_count = ctx.tool_call_count
        if resolved_location is None and getattr(deps.world, "strict_location", False):
            answer = interrupt({"type": "clarification", "id": f"clarification:{state['run_id']}:location:{state.get('turn_id',1)}", "question": f"未能取得{location_name or '当前城市'}的真实起点坐标，请提供定位，或配置高德 Key 并明确出发地点。"})
            text = str((answer or {}).get("text", "")) if isinstance(answer, dict) else str(answer or "")
            return await requirements({**state, "input_text": state["input_text"] + "\n" + text})
        if resolved_location is not None:
            spec = spec.model_copy(update={"location": resolved_location})
        result: dict[str, Any] = {
            "trip_spec": spec,
            **({"location_origin": location_origin} if location_origin else {}),
            "tool_call_count": tool_call_count,
            "phase": RunPhase.REQUIREMENTS_READY,
            "clarification": (
                {"question": output.clarification_question, "fields": output.clarification_fields}
                if output.clarification_needed and output.clarification_question
                else None
            ),
            "trace": _trace(state, "requirements_ready", constraints=spec.hard_constraints),
            "artifacts": [
                AgentArtifact(
                    artifact_id=f"requirement:{state['run_id']}:{state.get('turn_id', 1)}",
                    task_id=f"{state['run_id']}:requirements",
                    agent_id="requirement",
                    payload=spec.model_dump(mode="json"),
                    confidence=0.8,
                )
            ],
        }
        if geocode_observation:
            result["last_observation"] = geocode_observation
        if previous_spec is not None and set(spec.required_activities) - {
            place.category for place in _place_candidates(state.get("place_candidates"))
        }:
            # A geocode error cannot clear a missing-goal prerequisite. Cached
            # categories may be reused; the selected plan is still reverified.
            result["last_observation"] = {**(result.get("last_observation") or {}), "refresh_discovery": True}
        return result

    async def discovery(state: PlanGoState) -> dict[str, Any]:
        spec = state.get("trip_spec")
        assert spec is not None
        ctx = deps.tool_context(state)
        if ctx.tool_call_count >= deps.max_tool_calls:
            return {
                "phase": RunPhase.FAILED,
                "reason": "达到本次运行的只读工具调用上限",
                "last_observation": {"error": "tool_budget_exhausted"},
                "trace": _trace(state, "tool_budget_exhausted", limit=deps.max_tool_calls),
            }
        async def search(query: str, _location, limit: int):
            response = await deps.tools.execute(
                "search_places", {"query": query, "limit": limit}, ctx
            )
            # A single transient read failure must not turn a recoverable run
            # into ``INFEASIBLE`` just because the model emitted one query.
            # Retry once at the tool boundary; the shared tool budget still
            # accounts for both attempts.
            error_kind = str(response.get("error", "")).lower()
            if not response.get("ok") and error_kind in {
                "timeout",
                "timeouterror",
                "quota",
                "rate_limit",
                "ratelimit",
                "network_error",
                "network",
                "connectionerror",
                "connection",
            }:
                response = await deps.tools.execute(
                    "search_places", {"query": query, "limit": limit}, ctx
                )
            if not response.get("ok"):
                now = time.time()
                source: Literal["amap", "simulated", "browser"] = (
                    getattr(deps.world, "source", "amap" if type(deps.world).__name__ == "AmapWorldProvider" else "simulated")
                )
                return [], [
                    Evidence(
                        evidence_id=f"tool-error:search_places:{int(now * 1000)}",
                        source=source,
                        source_ref="tool-registry",
                        claim="地点检索工具未返回结果",
                        payload={
                            "error_kind": response.get("error", "tool_error"),
                            "detail": response.get("detail", ""),
                        },
                        confidence=0.0,
                    )
                ]
            result = response.get("result") or {}
            return [PlaceCandidate.model_validate(item) for item in result.get("places", [])], [
                Evidence.model_validate(item) for item in result.get("evidence", [])
            ]

        places, evidence = await discovery_agent.run(spec, search=search)
        evidence = _dedupe_evidence(evidence)
        if ctx.budget_exhausted:
            return {
                "phase": RunPhase.FAILED,
                "reason": "达到本次运行的只读工具调用上限",
                "tool_call_count": ctx.tool_call_count,
                "last_observation": {"error": "tool_budget_exhausted"},
                "trace": _trace(state, "tool_budget_exhausted", limit=deps.max_tool_calls),
                "evidence": evidence,
                "place_candidates": places,
            }
        if ctx.tool_call_count > deps.max_tool_calls:
            return {
                "phase": RunPhase.FAILED,
                "reason": "Discovery 请求超过只读工具调用上限",
                "tool_call_count": deps.max_tool_calls,
                "last_observation": {"error": "tool_budget_exhausted"},
                "trace": _trace(state, "tool_budget_exhausted", limit=deps.max_tool_calls),
            }
        tool_call_count = ctx.tool_call_count
        weather_observation: dict[str, Any] | None = None
        if spec.weather_sensitive and not state.get("next_arguments", {}).get("skip_weather"):
            weather_response = await deps.tools.execute("get_weather", {}, ctx)
            if weather_response.get("ok"):
                weather_observation = dict(
                    (weather_response.get("result") or {}).get("weather") or {}
                )
                weather_evidence = Evidence.model_validate(
                    (weather_response.get("result") or {}).get("evidence", {})
                )
                evidence.append(weather_evidence)
            else:
                source: Literal["amap", "simulated", "browser"] = (
                    getattr(deps.world, "source", "amap" if type(deps.world).__name__ == "AmapWorldProvider" else "simulated")
                )
                evidence.append(
                    Evidence(
                        evidence_id=f"tool-error:get_weather:{int(time.time() * 1000)}",
                        source=source,
                        source_ref="tool-registry",
                        claim="天气工具未返回结果",
                        payload={"error_kind": weather_response.get("error", "tool_error")},
                        confidence=0.0,
                    )
                )
            tool_call_count = ctx.tool_call_count
        evidence = _dedupe_evidence(evidence)
        if not places:
            return {
                "place_candidates": [],
                "candidate_plans": [],
                "tool_call_count": tool_call_count,
                "phase": RunPhase.INFEASIBLE,
                "reason": "Discovery 没有返回可用地点",
                "last_observation": {
                    "error": next(
                        (
                            item.payload.get("error_kind")
                            for item in evidence
                            if item.payload.get("error_kind")
                        ),
                        "empty_result",
                    )
                },
                "weather": weather_observation,
                "trace": _trace(state, "discovery_complete", places=0),
                "artifacts": [
                    AgentArtifact(
                        artifact_id=f"discovery:{state['run_id']}:{state.get('turn_id', 1)}",
                        task_id=f"{state['run_id']}:discovery:{state.get('turn_id', 1)}",
                        agent_id="discovery",
                        status="failed",
                        payload={"places": [], "errors": [e.model_dump(mode="json") for e in evidence]},
                        evidence_ids=[e.evidence_id for e in evidence],
                        confidence=0.0,
                    )
                ],
            }
        return {
            "place_candidates": places,
            "candidate_plans": [],
            "last_observation": {},
            "evidence": evidence,
            "weather": weather_observation,
            "tool_call_count": tool_call_count,
            "phase": RunPhase.RESEARCHING,
            "trace": _trace(state, "discovery_complete", places=len(places)),
            "artifacts": [
                AgentArtifact(
                    artifact_id=f"discovery:{state['run_id']}:{state.get('turn_id', 1)}",
                    task_id=f"{state['run_id']}:discovery:{state.get('turn_id', 1)}",
                    agent_id="discovery",
                    payload={
                        "places": [p.model_dump(mode="json") for p in places],
                        "observations": [e.model_dump(mode="json") for e in evidence],
                    },
                    evidence_ids=[e.evidence_id for e in evidence],
                    confidence=0.85,
                )
            ],
        }

    def advocate_fanout(state: PlanGoState) -> list[Send]:
        spec = state.get("trip_spec")
        assert spec is not None
        if deps.agent_mode == "single" or (spec.party_size or 1) <= 1:
            return [Send("synthesis", {})]
        roles = _advocate_roles(spec)
        places = _place_candidates(state.get("place_candidates"))
        already = {report.role for report in _current_advocate_reports(state)}
        turn_id = int(state.get("turn_id", 1))
        return [
            Send(
                "advocate_worker",
                {
                    "trip_spec": spec,
                    "place_candidates": places,
                    "advocate_role": role,
                    "run_id": state["run_id"],
                    "user_id": state["user_id"],
                    "turn_id": turn_id,
                    "phase": RunPhase.RESEARCHING,
                    "delegated_roles": [role],
                },
            )
            for role in roles
            if role not in already
        ] or [Send("synthesis", {})]

    async def advocate_worker(state: PlanGoState) -> dict[str, Any]:
        role = state.get("advocate_role") or "体验"
        turn_id = int(state.get("turn_id", 1))
        spec = state.get("trip_spec")
        assert spec is not None
        places = _place_candidates(state.get("place_candidates"))
        report = await advocate_agent.run(role, spec, places)
        report = report.model_copy(update={"turn_id": turn_id})
        observed_ids = {place.place_id for place in places}
        invalid_ids = [
            place_id
            for place_id in report.preferred_place_ids
            if place_id not in observed_ids
        ]
        if invalid_ids:
            report = report.model_copy(
                update={
                    "preferred_place_ids": [
                        place_id
                        for place_id in report.preferred_place_ids
                        if place_id in observed_ids
                    ],
                    "concerns": [
                        *report.concerns,
                        f"忽略未观测地点：{','.join(invalid_ids[:3])}",
                    ],
                }
            )
        invalid_evidence = [
            evidence_id
            for evidence_id in report.evidence_ids
            if evidence_id not in {item.evidence_id for item in state.get("evidence", [])}
        ]
        if invalid_evidence:
            report = report.model_copy(
                update={
                    "evidence_ids": [
                        evidence_id
                        for evidence_id in report.evidence_ids
                        if evidence_id not in invalid_evidence
                    ],
                    "concerns": [
                        *report.concerns,
                        f"忽略未观测证据：{','.join(invalid_evidence[:3])}",
                    ],
                }
            )
        return {
            "advocate_reports": [report],
            "delegated_roles": [role],
            "trace": _trace(state, "advocate_complete", role=role, verdict=report.verdict),
            "artifacts": [
                AgentArtifact(
                    artifact_id=f"advocate:{state['run_id']}:{turn_id}:{role}",
                    task_id=f"{state['run_id']}:advocate:{turn_id}:{role}",
                    agent_id=f"advocate:{role}",
                    payload=report.model_dump(mode="json"),
                    evidence_ids=report.evidence_ids,
                    confidence=report.score,
                )
            ],
        }

    async def synthesis(state: PlanGoState) -> dict[str, Any]:
        places = _place_candidates(state.get("place_candidates"))
        advocate_reports = _current_advocate_reports(state)
        if not places:
            return {"phase": RunPhase.INFEASIBLE, "reason": "没有观测地点"}
        spec = state.get("trip_spec")
        assert spec is not None
        planning_spec = _planning_spec(spec, state.get("weather"))
        previous = state.get("previous_spec")
        previous = TripSpec.model_validate(previous) if isinstance(previous, dict) else previous
        prior_plans = state.get("candidate_plans") or []
        prior = PlanCandidate.model_validate(prior_plans[0]) if prior_plans else None
        retained = [stop for stop in prior.stops if stop.category not in spec.excluded_activities] if prior else []
        if spec.activity_order:
            retained.sort(key=lambda stop: spec.activity_order.index(stop.category) if stop.category in spec.activity_order else len(spec.activity_order))
        reuse = bool(
            prior and previous and retained
            and previous.location == spec.location
            and previous.soft_preferences == spec.soft_preferences
            and not goal_errors(spec, retained)
            and all(c.kind == "soft" or c.passed is True for c in prior.checks)
        )
        # Preserve verified venue choices across edits; compilation and fresh
        # verification still enforce every new goal, cost and supply constraint.
        draft = PlanDraft(
            stops=[PlanDraftStop(place_id=s.place_id, duration_minutes=s.requested_dwell_min or s.end_minute - s.start_minute) for s in retained],
            label="保留原有活动",
            rationale="编辑后保留合规地点，重新排序、计价和验证",
        ) if reuse else await planner_agent.run(planning_spec, places, advocate_reports, state.get("evidence", []))
        plan_version = int(state.get("plan_version", 0)) + 1
        draft_errors: list[dict[str, Any]] = []
        selected = compile_plan_draft(
            spec,
            draft,
            places,
            evidence=state.get("evidence", []),
            version=plan_version,
            errors=draft_errors,
        )
        if selected is None:
            fallback_draft = planner_agent._fallback(planning_spec, places, advocate_reports)
            selected = compile_plan_draft(
                spec,
                fallback_draft,
                places,
                evidence=state.get("evidence", []),
                version=plan_version,
            )
            if selected is None:
                return {
                    "phase": RunPhase.INFEASIBLE,
                    "reason": (
                        "Planner Draft 无法编译且没有 fallback 计划"
                        + (
                            f"（{draft_errors[0].get('code')}）"
                            if draft_errors and draft_errors[0].get("code")
                            else ""
                        )
                    ),
                    "draft_errors": draft_errors,
                }
        if hasattr(deps.planner, "preserve_locks"):
            selected = deps.planner.preserve_locks(selected, prior)
            if selected is None:
                return {"phase": RunPhase.REQUIREMENTS_READY, "selected_plan": None,
                        "clarification": {"fields": ["locked_stops"], "question": "新方案未能保留已锁定的地点，请确认调整需求还是解锁该节点。"},
                        "reason": "锁定节点与新方案冲突，未自动丢弃锁定"}
        artifacts = [
            AgentArtifact(
                artifact_id=f"synthesis:{state['run_id']}:{selected.version}",
                task_id=f"{state['run_id']}:planner",
                agent_id="planner",
                payload={
                    "draft": draft.model_dump(mode="json"),
                    "selected_plan_id": selected.plan_id,
                },
                evidence_ids=selected.evidence_ids,
                confidence=0.8,
            )
        ]
        if draft_errors:
            artifacts.append(
                AgentArtifact(
                    artifact_id=f"planner-error:{state['run_id']}:{plan_version}",
                    task_id=f"{state['run_id']}:planner",
                    agent_id="planner",
                    status="failed",
                    payload={"errors": draft_errors},
                    confidence=1.0,
                )
            )
        return {
            "plan_draft": draft,
            "plan_version": plan_version,
            "candidate_plans": [selected],
            "selected_plan": selected,
            "draft_errors": draft_errors,
            "phase": RunPhase.PLAN_DRAFTED,
            "trace": _trace(
                state,
                "plan_synthesized",
                plan_id=selected.plan_id,
                weather_preference=(
                    "室内优先" if planning_spec.soft_preferences != spec.soft_preferences else None
                ),
                plan_reused=reuse,
            ),
            "artifacts": artifacts,
        }

    async def verify(state: PlanGoState) -> dict[str, Any]:
        selected = state.get("selected_plan")
        spec = state.get("trip_spec")
        if not selected or not spec:
            return {"phase": RunPhase.INFEASIBLE, "reason": "缺少待验证计划"}
        ctx = deps.tool_context(state)
        try:
            evaluation = await deps.planner.evaluate(
                spec,
                selected,
                evidence=state.get("evidence", []),
                weather=state.get("weather"),
                on_tool_call=ctx.consume,
            )
        except ToolBudgetExceeded as exc:
            return {
                "phase": RunPhase.FAILED,
                "reason": "达到本次运行的 Provider 调用上限",
                "tool_call_count": ctx.tool_call_count,
                "last_observation": {"error": "tool_budget_exhausted", "tool": exc.tool_name},
                "trace": _trace(state, "tool_budget_exhausted", limit=deps.max_tool_calls),
            }
        await deps.runs.save_plan(state["run_id"], evaluation.plan, evaluation.verifier)
        return {
            "selected_plan": evaluation.plan,
            "verifier": evaluation.verifier,
            "evidence": _dedupe_evidence(
                [*state.get("evidence", []), *deps.planner.last_evidence]
            ),
            "tool_call_count": ctx.tool_call_count,
            "phase": RunPhase.REVIEWING,
            "trace": _trace(
                state,
                "plan_verified",
                hard_constraints_pass=evaluation.verifier.hard_constraints_pass,
                evidence_complete=evaluation.verifier.evidence_complete,
                executable=evaluation.verifier.executable,
            ),
            "artifacts": [
                AgentArtifact(
                    artifact_id=f"verifier:{state['run_id']}:{evaluation.plan.version}",
                    task_id=f"{state['run_id']}:verifier",
                    agent_id="verifier",
                    payload={**evaluation.verifier.model_dump(mode="json"), "observations": [e.model_dump(mode="json") for e in deps.planner.last_evidence]},
                    evidence_ids=evaluation.plan.evidence_ids,
                    confidence=1.0,
                )
            ],
        }

    async def critic(state: PlanGoState) -> dict[str, Any]:
        selected = state.get("selected_plan")
        verifier = state.get("verifier")
        if not selected or not verifier:
            return {"phase": RunPhase.INFEASIBLE, "reason": "缺少待审查计划"}
        spec = state.get("trip_spec")
        assert spec is not None
        report = await critic_agent.run(spec, selected, verifier)
        if _can_repair_price_gap(state, deps.max_repair_rounds):
            report = report.model_copy(update={"verdict": "repair"})
        repair_round = state.get("repair_round", 0) + (1 if report.verdict == "repair" else 0)
        update: dict[str, Any] = {
            "critique": report,
            "repair_round": repair_round,
            "plan_version": int(state.get("plan_version", 0) or 0),
            "phase": RunPhase.REVIEWING,
            "last_observation": {"critic": report.model_dump(mode="json")},
            "trace": _trace(state, "critic_complete", verdict=report.verdict),
            "artifacts": [
                AgentArtifact(
                    artifact_id=f"critic:{state['run_id']}:{state.get('turn_id', 1)}:{repair_round}",
                    task_id=f"{state['run_id']}:critic:{state.get('turn_id', 1)}:{repair_round}",
                    agent_id="critic",
                    payload=report.model_dump(mode="json"),
                    evidence_ids=selected.evidence_ids,
                    confidence=0.85,
                )
            ],
        }
        if report.verdict == "repair":
            # Repair is deterministic: trim one or more grounded stops and
            # re-run the compiler boundary. No new place fact is invented.
            replacement = None
            plan_version = int(state.get("plan_version", 0)) + 1
            ctx = deps.tool_context(state)
            async def evaluate_repair(candidate, observations):
                # Cached reads cost no calls. Reserve actual write boundaries,
                # instead of rejecting a repair based on hypothetical cache misses.
                ctx.max_tool_calls = deps.max_tool_calls - 2 - sum(stop.category == "餐厅" for stop in candidate.stops)
                try:
                    return await deps.planner.evaluate(spec, candidate, evidence=observations, weather=state.get("weather"), on_tool_call=ctx.consume)
                finally:
                    ctx.max_tool_calls = deps.max_tool_calls

            working_plan = selected
            working_verifier = verifier
            repair_evidence = _dedupe_evidence(state.get("evidence", []))
            # A single deletion is not enough when independent violations
            # (for example closed venue + overlong route) coexist. Keep the
            # repair bounded by the configured round budget.
            for _ in range(max(1, deps.max_repair_rounds)):
                if len(working_plan.stops) <= 1:
                    break
                issue_text = " ".join(
                    [
                        f"{item.name} {item.detail}"
                        for item in [
                            *working_verifier.hard_violations,
                            *working_verifier.unknown_evidence,
                        ]
                    ]
                )
                ordered_indices = list(range(len(working_plan.stops) - 1, -1, -1))
                matching_indices = [
                    index
                    for index, stop in enumerate(working_plan.stops)
                    if not stop.locked and (stop.place_id in issue_text or stop.name in issue_text)
                ]
                ordered_indices = list(dict.fromkeys([*matching_indices, *ordered_indices]))
                # Named per-stop violations are independent: first try one
                # grounded batch removal, avoiding a combinatorial sequence
                # of provider reads before the mandatory final verification.
                removal_sets = [set(matching_indices)] if 1 < len(matching_indices) < len(working_plan.stops) else []
                removal_sets += [{index} for index in ordered_indices if not working_plan.stops[index].locked]
                next_plan = None
                next_verifier = None
                for remove_indices in removal_sets:
                    trimmed_stops = [
                        stop
                        for index, stop in enumerate(working_plan.stops)
                        if index not in remove_indices
                    ]
                    if goal_errors(spec, trimmed_stops):
                        continue
                    # Reserve the mandatory verification and proposal/write calls.
                    if ctx.tool_call_count > deps.max_tool_calls - 2 - sum(s.category == "餐厅" for s in trimmed_stops):
                        break
                    trimmed = working_plan.model_copy(
                        update={
                            "stops": trimmed_stops,
                            "version": plan_version,
                            "total_cost": round(
                                sum(stop.estimated_cost for stop in trimmed_stops), 2
                            ),
                        }
                    )
                    try:
                        checked = await evaluate_repair(trimmed, repair_evidence)
                    except ToolBudgetExceeded as exc:
                        update.update(
                            {
                                "phase": RunPhase.FAILED,
                                "reason": "达到本次运行的 Provider 调用上限",
                                "tool_call_count": ctx.tool_call_count,
                                "evidence": repair_evidence,
                                "last_observation": {
                                    "error": "tool_budget_exhausted",
                                    "tool": exc.tool_name,
                                },
                            }
                        )
                        return update
                    repair_evidence = _dedupe_evidence(
                        [*repair_evidence, *deps.planner.last_evidence]
                    )
                    if checked.verifier.executable:
                        replacement = checked.plan.model_copy(update={"version": plan_version})
                        break
                    if next_plan is None:
                        next_plan = checked.plan
                        next_verifier = checked.verifier
                if replacement is not None or next_plan is None or next_verifier is None:
                    break
                working_plan = next_plan
                working_verifier = next_verifier
            if replacement is None:
                places = _place_candidates(state.get("place_candidates"))
                broken_ids = _replacement_exclusions(selected, verifier)
                tried: set[tuple[str, ...]] = {tuple(stop.place_id for stop in selected.stops)}
                for _ in range(deps.max_repair_rounds):
                    pool = [place for place in places if place.place_id not in broken_ids]
                    if not pool:
                        continue
                    failed_optional = {place.category for place in places if place.place_id in broken_ids and place.category in spec.optional_activities}
                    failed_optional |= {stop.category for stop in selected.stops if stop.category in spec.optional_activities} - {stop.category for stop in working_plan.stops}
                    search_spec = spec.model_copy(update={"optional_activities": [category for category in spec.optional_activities if category not in failed_optional]})
                    draft = planner_agent._fallback(search_spec, pool)
                    candidate = compile_plan_draft(spec, draft, pool, evidence=repair_evidence, version=plan_version)
                    if candidate is None or any(stop.locked and stop not in candidate.stops for stop in selected.stops):
                        continue
                    key = tuple(stop.place_id for stop in candidate.stops)
                    if key in tried or ctx.tool_call_count > deps.max_tool_calls - 2 - sum(s.category == "餐厅" for s in candidate.stops):
                        continue
                    tried.add(key)
                    try:
                        checked = await evaluate_repair(candidate, repair_evidence)
                    except ToolBudgetExceeded:
                        update.update(phase=RunPhase.FAILED, reason="剩余工具预算不足以完成修复及审批后动作", tool_call_count=ctx.tool_call_count, evidence=_dedupe_evidence([*repair_evidence, *deps.planner.last_evidence]))
                        return update
                    repair_evidence = _dedupe_evidence([*repair_evidence, *deps.planner.last_evidence])
                    if checked.verifier.executable:
                        replacement = checked.plan
                        break
                    newly_broken = _replacement_exclusions(candidate, checked.verifier) - broken_ids
                    if not newly_broken:
                        break
                    broken_ids.update(newly_broken)
            if replacement is not None and (
                replacement.stops != selected.stops or replacement.total_cost != selected.total_cost
            ):
                update["trace"] = _trace(
                    state, "critic_complete", verdict=report.verdict
                ) + _trace(state, "plan_repair_selected", plan_id=replacement.plan_id)
                update.update(
                    {
                        "selected_plan": replacement,
                        "candidate_plans": [replacement],
                        "plan_version": plan_version,
                        "verifier": None,
                        "phase": RunPhase.REPLANNING,
                        "repair_applied": True,
                    }
                )
            else:
                update.update(
                    {
                        "phase": RunPhase.INFEASIBLE if verifier.evidence_complete else RunPhase.REQUIREMENTS_READY,
                        "reason": "在本次候选与调用预算内未找到合规替代；未满足：" + "、".join(check.name for check in [*working_verifier.hard_violations, *working_verifier.unknown_evidence]),
                        "repair_applied": True,
                    }
                )
                if working_plan is not selected and verifier.evidence_complete:
                    await deps.runs.save_plan(state["run_id"], working_plan, working_verifier)
                    update.update(
                        {
                            "selected_plan": working_plan,
                            "candidate_plans": [working_plan],
                            "verifier": working_verifier,
                            "plan_version": plan_version,
                        }
                    )
            update["tool_call_count"] = ctx.tool_call_count
            update["evidence"] = repair_evidence
            update["artifacts"][0].payload["observations"] = [e.model_dump(mode="json") for e in repair_evidence]
        return update

    async def propose_actions(state: PlanGoState) -> dict[str, Any]:
        selected = state.get("selected_plan")
        verifier = state.get("verifier")
        if not selected or not verifier or not verifier.executable:
            return {"phase": RunPhase.INFEASIBLE, "reason": "计划未通过确定性校验，不能生成写动作"}
        ctx = deps.tool_context(state)
        response = await deps.tools.execute(
            "propose_actions", {"plan": selected.model_dump(mode="json")}, ctx
        )
        if not response.get("ok"):
            return {
                "phase": RunPhase.FAILED,
                "reason": response.get("detail") or response.get("error", "proposal_failed"),
                "tool_call_count": ctx.tool_call_count,
            }
        from plango_harness.agent.contracts import ActionProposal

        proposal = ActionProposal.model_validate(response["result"])
        return {
            "action_proposal": proposal,
            "tool_call_count": ctx.tool_call_count,
            "phase": RunPhase.WAITING_APPROVAL,
            "trace": _trace(
                state,
                "approval_requested",
                proposal_id=proposal.proposal_id,
                actions=len(proposal.actions),
            ),
        }

    async def approval(state: PlanGoState) -> dict[str, Any]:
        proposal = state.get("action_proposal")
        if proposal is None:
            return {"phase": RunPhase.INFEASIBLE, "reason": "没有待确认动作"}
        decision = interrupt(
            {
                "type": "approval",
                "id": f"approval:{state['run_id']}:{proposal.proposal_id}",
                "proposal": proposal.model_dump(mode="json"),
                "allowed": ["approve", "reject", "edit"],
            }
        )
        value = (
            decision if isinstance(decision, str) else (decision or {}).get("decision", "reject")
        )
        value = value if value in {"approve", "reject", "edit"} else "reject"
        selected_candidate = state.get("selected_plan")
        if isinstance(decision, dict) and decision.get("candidate_plan_id"):
            if value != "edit":
                raise ValueError("candidate_selection_requires_edit")
            selected_candidate = next((PlanCandidate.model_validate(p) for p in state.get("candidate_plans", [])
                if PlanCandidate.model_validate(p).plan_id == decision["candidate_plan_id"]
                and PlanCandidate.model_validate(p).version == decision.get("candidate_plan_version")), None)
            if selected_candidate is None:
                raise ValueError("unknown_or_stale_candidate")
        return {
            "selected_plan": selected_candidate,
            "approval_decision": value,
            "consumed_command_id": decision.get("_command_id") if isinstance(decision, dict) else None,
            "interrupt_id": None,
            "pending_message": (decision or {}).get("text", "")
            if isinstance(decision, dict)
            else "",
            "phase": (
                RunPhase.EXECUTING
                if value == "approve"
                else (RunPhase.REPLANNING if value == "edit" else RunPhase.CANCELLED)
            ),
            "trace": _trace(state, "approval_resolved", decision=value, proposal_id=proposal.proposal_id, plan_id=proposal.plan_id, plan_version=proposal.plan_version),
        }

    async def replan(state: PlanGoState) -> dict[str, Any]:
        text = str(state.get("pending_message") or state.get("input_text") or "")
        history = list(state.get("messages", []))
        history.append(HumanMessage(content=text))
        refresh_supply = _replan_refreshes_supply(text)
        return {
            "input_text": text,
            "messages": history,
            "pending_message": None,
            "previous_spec": state.get("trip_spec"),
            "trip_spec": None,
            "plan_draft": None,
            "draft_errors": [],
            "place_candidates": [] if refresh_supply else state.get("place_candidates", []),
            "candidate_plans": [] if refresh_supply else ([state["selected_plan"]] if state.get("selected_plan") else []),
            "advocate_reports": [],
            "delegated_roles": [],
            "selected_plan": None,
            "verifier": None,
            "critique": None,
            "action_proposal": None,
            "action_results": [],
            "memory_delta": [],
            "approval_decision": None,
            "interrupt_id": None,
            "evidence": [] if refresh_supply else state.get("evidence", []),
            "weather": None if refresh_supply else state.get("weather"),
            "execution_goal": None,
            "execution_started": False,
            "reflection_done": False,
            "outcome": None,
            "reason": "",
            "phase": RunPhase.REPLANNING,
            "turn_count": 0,
            "repair_round": 0,
            "repair_applied": False,
            "started_at": state.get("started_at") or time.time(),
            "deadline_at": state.get("deadline_at")
            or (state.get("started_at") or time.time()) + deps.max_run_seconds,
            "timeout_stage": None,
            "turn_id": int(state.get("turn_id", 1)) + 1,
            "next_action": None,
            "next_arguments": {},
            "advocate_role": None,
            "clarification": None,
            "last_observation": {
                "weather_changed": "雨" in str(text) or "天气" in str(text),
                "refresh_discovery": refresh_supply,
            },
            "trace": _trace(state, "replan_requested"),
        }

    async def execute(state: PlanGoState) -> dict[str, Any]:
        proposal = state.get("action_proposal")
        if proposal is None or state.get("approval_decision") != "approve":
            return {"phase": RunPhase.CANCELLED, "reason": "动作未获批准"}
        ctx = deps.tool_context(state, approved=True)
        response = await deps.tools.execute(
            "execute_action", {"proposal": proposal.model_dump(mode="json")}, ctx
        )
        if not response.get("ok"):
            return {
                "phase": RunPhase.PARTIAL_FAILED,
                "reason": response.get("detail") or response.get("error", "execution_failed"),
                "execution_started": True,
                "tool_call_count": ctx.tool_call_count,
            }
        raw_results = response["result"].get("results", [])
        results: list[ActionResult] = []
        for index, action in enumerate(proposal.actions):
            item = raw_results[index] if index < len(raw_results) else {"status": "UNKNOWN"}
            raw_status = str(item.get("status", "UNKNOWN")).upper()
            try:
                status = ActionStatus(raw_status)
            except ValueError:
                status = ActionStatus.UNKNOWN
            results.append(
                ActionResult(
                    action_id=action.action_id,
                    status=status,
                    result=item,
                    error=str(item.get("error")) if item.get("error") else None,
                    retryable=bool(item.get("retryable")),
                    resolution_required=(
                        status == ActionStatus.UNKNOWN
                        or bool(item.get("resolution_required"))
                    ),
                )
            )
        terminal = (
            RunPhase.SUCCEEDED
            if results and all(item.status == ActionStatus.SUCCEEDED for item in results)
            else RunPhase.PARTIAL_FAILED
        )
        return {
            "action_results": results,
            "tool_call_count": ctx.tool_call_count,
            "execution_started": True,
            "phase": terminal,
            "outcome": terminal.value,
            "trace": _trace(state, "actions_executed", count=len(results), proposal_id=proposal.proposal_id, plan_id=proposal.plan_id, plan_version=proposal.plan_version),
        }

    async def reflect(state: PlanGoState) -> dict[str, Any]:
        if state.get("reflection_done"):
            return {}
        spec = state.get("trip_spec")
        if not spec:
            return {"reflection_done": True}
        proposal = await reflection_agent.run(
            spec,
            state.get("selected_plan"),
            state.get("action_results", []),
            f"{state['run_id']}:terminal",
        )
        committed = []
        if proposal:
            committed = await deps.memory.commit(state["user_id"], [proposal])
        return {
            "memory_delta": [proposal] if proposal else [],
            "reflection_done": True,
            "trace": _trace(state, "memory_reflected", committed=len(committed)),
            "artifacts": [
                AgentArtifact(
                    artifact_id=f"reflection:{state['run_id']}",
                    task_id=f"{state['run_id']}:reflection",
                    agent_id="reflection",
                    payload=proposal.model_dump(mode="json") if proposal else {"remember": False},
                    confidence=proposal.confidence if proposal else 0.5,
                )
            ],
        }

    async def prepare_ask_user(state: PlanGoState) -> dict[str, Any]:
        verifier = state.get("verifier")
        question = (state.get("clarification") or {}).get("question") or ("；".join(check.detail for check in verifier.unknown_evidence) if verifier and verifier.unknown_evidence else "还需要补充哪些约束？")
        clarification = {**(state.get("clarification") or {}), "question": question}
        if verifier and verifier.unknown_evidence:
            clarification.setdefault("fields", ["evidence"])
            clarification["reasons"] = [
                {"code": "unknown_price", "place_id": check.name.removeprefix("price:"),
                 "evidence_ids": [item.evidence_id for item in state.get("evidence", [])
                                  if item.payload.get("place_id") == check.name.removeprefix("price:")
                                  and item.payload.get("price_known") is False]}
                for check in verifier.unknown_evidence if check.name.startswith("price:")
            ]
        interrupt_id = f"clarification:{state['run_id']}:{state.get('turn_id', 1)}"
        return {
            "clarification": clarification,
            "candidate_plans": [state["selected_plan"]] if state.get("selected_plan") else [],
            "interrupt_id": interrupt_id,
            "previous_spec": state.get("trip_spec"),
            "phase": RunPhase.REQUIREMENTS_READY,
            "trace": _trace(state, "clarification_requested", question=question),
        }

    async def ask_user(state: PlanGoState) -> dict[str, Any]:
        clarification = state.get("clarification") or {}
        question = clarification.get("question") or "还需要补充哪些约束？"
        interrupt_id = state.get("interrupt_id") or f"clarification:{state['run_id']}:{state.get('turn_id', 1)}"
        answer = interrupt(
            {"type": "clarification", "id": interrupt_id, "question": question}
        )
        text = answer if isinstance(answer, str) else (answer or {}).get("text", "")
        messages = list(state.get("messages", []))
        if text:
            messages.append(HumanMessage(content=text))
        current_spec = state.get("trip_spec")
        return {
            "consumed_command_id": answer.get("_command_id") if isinstance(answer, dict) else None,
            # A clarification answer starts a fresh planning turn while
            # retaining the previous spec as the merge base.
            "input_text": text or state["input_text"],
            "messages": messages,
            "previous_spec": current_spec or state.get("previous_spec"),
            "trip_spec": None,
            "place_candidates": [],
            "candidate_plans": [],
            "plan_draft": None,
            "draft_errors": [],
            "advocate_reports": [],
            "delegated_roles": [],
            "selected_plan": None,
            "verifier": None,
            "critique": None,
            "action_proposal": None,
            "action_results": [],
            "memory_delta": [],
            "approval_decision": None,
            "execution_goal": None,
            "execution_started": False,
            "reflection_done": False,
            "evidence": [],
            "weather": None,
            "clarification": clarification,
            "interrupt_id": None,
            "outcome": None,
            "reason": "",
            "phase": RunPhase.CREATED,
            "turn_count": 0,
            "turn_id": int(state.get("turn_id", 1)) + 1,
            "plan_version": int(state.get("plan_version", 0)),
            "repair_round": 0,
            "repair_applied": False,
            "started_at": state.get("started_at") or time.time(),
            "deadline_at": state.get("deadline_at")
            or (state.get("started_at") or time.time()) + deps.max_run_seconds,
            "timeout_stage": None,
            "last_observation": {
                "weather_changed": "雨" in str(text) or "天气" in str(text)
            },
            "trace": _trace(state, "clarification_received"),
        }

    async def finalize(state: PlanGoState) -> dict[str, Any]:
        raw_phase = state.get("phase") or RunPhase.FAILED
        phase = raw_phase if isinstance(raw_phase, RunPhase) else RunPhase(str(raw_phase))
        if phase in {
            RunPhase.SUCCEEDED,
            RunPhase.CANCELLED,
            RunPhase.PARTIAL_FAILED,
            RunPhase.INFEASIBLE,
            RunPhase.FAILED,
        }:
            return {
                "outcome": phase.value,
                "reason": state.get("reason") or ("计划不可执行" if phase == RunPhase.INFEASIBLE else ""),
                "trace": _trace(state, "run_finalized", phase=phase.value),
            }
        return {
            "phase": RunPhase.FAILED,
            "outcome": "FAILED",
            "reason": state.get("reason") or "未达到终态",
            "trace": _trace(state, "run_finalized", phase="FAILED"),
        }

    async def supervisor(state: PlanGoState) -> dict[str, Any]:
        if int(state.get("model_token_count", 0)) >= deps.max_model_tokens:
            return {
                "phase": RunPhase.FAILED,
                "outcome": RunPhase.FAILED.value,
                "reason": "超过本次运行的模型 token 上限",
                "next_action": "finish",
                "trace": _trace(
                    state,
                    "budget_exhausted",
                    budget="model_tokens",
                    limit=deps.max_model_tokens,
                ),
            }
        started_at = state.get("started_at")
        deadline_at = state.get("deadline_at") or (
            started_at + deps.max_run_seconds if started_at else None
        )
        if deadline_at and time.time() >= deadline_at:
            return {
                "phase": RunPhase.FAILED,
                "outcome": RunPhase.FAILED.value,
                "reason": "超过本次运行时间上限",
                "next_action": "finish",
                "trace": _trace(
                    state,
                    "budget_exhausted",
                    budget="time",
                    limit=deps.max_run_seconds,
                    timeout_stage="supervisor",
                ),
                "timeout_stage": "supervisor",
            }
        turn = int(state.get("turn_count", 0)) + 1
        if turn > deps.max_turns:
            return {
                "phase": RunPhase.FAILED,
                "outcome": "FAILED",
                "reason": "达到最大 Agent 回合数",
                "next_action": "finish",
                "turn_count": turn,
            }
        fallback = _fallback_decision(state, deps)
        verifier = state.get("verifier")
        summary = {
            "phase": str(state.get("phase")),
            "has_spec": bool(state.get("trip_spec")),
            "place_count": len(state.get("place_candidates", [])),
            "candidate_count": len(state.get("candidate_plans", [])),
            "advocate_count": len(_current_advocate_reports(state)),
            "has_selected": bool(state.get("selected_plan")),
            "hard_constraints_pass": verifier.hard_constraints_pass if verifier else None,
            "evidence_complete": verifier.evidence_complete if verifier else None,
            "executable": verifier.executable if verifier else None,
            "has_proposal": bool(state.get("action_proposal")),
            "approval": state.get("approval_decision"),
            "execution_started": bool(state.get("execution_started")),
            "last_observation": state.get("last_observation"),
        }
        # The phase scheduler owns transitions; specialist model calls still
        # produce the typed artifacts consumed by each phase.
        decision = fallback
        summary["routing"] = "constrained_phase_scheduler"
        allowed = {
            "requirements",
            "discover",
            "advocate",
            "synthesize",
            "critic",
            "verify",
            "propose_actions",
            "ask_user",
            "finish",
        }
        requested_action = decision.next_action
        action: str = requested_action if requested_action in allowed else fallback.next_action
        overrides: list[str] = []

        def force(target: str, reason: str) -> None:
            nonlocal action
            if action != target:
                overrides.append(reason)
                action = target

        # Safety routing is deterministic even when the model emits an unsafe
        # command or skips a required prerequisite.
        verifier = state.get("verifier")
        if isinstance(verifier, dict):
            verifier = VerifierResult.model_validate(verifier)
        terminal_phase = str(state.get("phase")) in {
            RunPhase.INFEASIBLE.value,
            RunPhase.FAILED.value,
            RunPhase.CANCELLED.value,
        }
        if not terminal_phase and _semantic_cycle(state.get("trace", [])):
            if state.get("selected_plan") and verifier is None:
                force("verify", "检测到阶段语义循环，直接重新验证")
            elif state.get("selected_plan") and verifier is not None and verifier.executable:
                force("propose_actions", "检测到阶段语义循环，进入动作确认")
            elif state.get("place_candidates") and not state.get("selected_plan"):
                force("synthesize", "检测到阶段语义循环，结束重复检索")
            else:
                force("finish", "检测到阶段语义循环，安全收敛")
            overrides.append("semantic_loop_blocked")
        if terminal_phase:
            force("finish", "当前运行已进入终态")
        # A model may choose a semantically plausible action while skipping a
        # required artifact. Keep the loop agentic, but never permit an
        # impossible transition (for example synthesize with zero candidates).
        if not terminal_phase and not state.get("place_candidates") and action not in {
            "requirements",
            "discover",
            "ask_user",
        }:
            force(
                "finish"
                if state.get("phase") in {RunPhase.INFEASIBLE, RunPhase.FAILED, RunPhase.CANCELLED}
                else "discover",
                "Discovery 没有可用地点"
                if state.get("phase") in {RunPhase.INFEASIBLE, RunPhase.FAILED, RunPhase.CANCELLED}
                else "缺少候选地点",
            )
        if (
            not terminal_phase
            and action == "ask_user"
            and state.get("trip_spec")
            and not state.get("clarification")
            and not state.get("selected_plan")
            and verifier is None
        ):
            # An actionable requirement/edit has no clarification artifact;
            # keep the model from pausing before the required read/planning step.
            force(
                "discover" if not state.get("place_candidates") else "synthesize",
                "没有澄清请求，继续当前规划回合",
            )
        if (
            not terminal_phase
            and state.get("place_candidates")
            and not state.get("selected_plan")
            and action == "discover"
            and any(item.get("event") == "discovery_complete" for item in state.get("trace", [])[-3:])
        ):
            # A model may ask to search again indefinitely after a successful
            # read. Require synthesis unless it explicitly changes the turn.
            force("synthesize", "本轮已完成 Discovery，避免重复检索循环")
        if not terminal_phase and (
            state.get("place_candidates")
            and not state.get("selected_plan")
            and action in {"critic", "verify", "propose_actions", "finish"}
        ):
            force("synthesize", "缺少已选计划")
        spec = state.get("trip_spec")
        if isinstance(spec, dict):
            spec = TripSpec.model_validate(spec)
        if (
            not terminal_phase
            and spec is not None
            and deps.agent_mode == "multi"
            and (spec.party_size or 1) > 1
            and state.get("place_candidates")
            and not _current_advocate_reports(state)
            and action not in {"advocate", "ask_user"}
        ):
            # Fan-out is a required multi-party artifact, not an optional
            # model preference. Without this guard a model can jump from
            # discovery to synthesis and silently lose roles.
            force("advocate", "多人需求缺少 Advocate 报告")
        if not terminal_phase and state.get("selected_plan") and verifier is None:
            repair_pending = any(
                isinstance(item, dict) and item.get("event") == "plan_repair_selected"
                for item in (state.get("trace") or [])
            )
            if (
                repair_pending
                or (
                    not state.get("clarification")
                    and (
                        state.get("repair_applied")
                        or str(state.get("phase")) == RunPhase.REPLANNING.value
                    )
                )
            ):
                # A repaired plan must be re-verified before any pause or
                # write proposal can be considered.
                force("verify", "修复后的计划尚未重新验证")
            elif action not in {"verify", "discover", "requirements", "ask_user"}:
                force("verify", "计划尚未经过 Verifier")
        if not terminal_phase and verifier is not None and not verifier.executable:
            if not verifier.evidence_complete:
                # Missing or stale evidence must be clarified before Critic
                # repair; otherwise a safe freshness failure can be reported
                # as an unrelated hard-constraint infeasibility.
                if _can_repair_price_gap(state, deps.max_repair_rounds):
                    force("critic", "先尝试省略缺价备选或选用已观测的替代地点")
                else:
                    force("ask_user", "证据未完整或已过期")
            elif not verifier.hard_constraints_pass:
                critique = state.get("critique")
                critique_verdict = (
                    critique.get("verdict")
                    if isinstance(critique, dict)
                    else getattr(critique, "verdict", "")
                )
                if critique_verdict == "ask_user":
                    force("ask_user", "Critic 请求用户补充")
                elif state.get("repair_applied") or state.get("repair_round", 0) >= deps.max_repair_rounds:
                    force("finish", "硬约束修复未能达到可执行状态")
                else:
                    force("critic", "硬约束未通过")
            else:
                force("ask_user", "证据仍未完整确认")
        if not terminal_phase and verifier is not None and verifier.executable and not state.get("action_proposal"):
            force("propose_actions", "可执行计划必须先生成 ActionProposal")
        if not terminal_phase and action == "propose_actions" and (verifier is None or not verifier.executable):
            force("verify" if state.get("selected_plan") else "synthesize", "写动作前缺少可执行校验")
        if (
            not terminal_phase
            and action == "finish"
            and state.get("selected_plan") is None
            and state.get("place_candidates")
        ):
            force("synthesize", "结束前仍有未综合候选")
        if (
            not terminal_phase
            and spec is not None
            and not state.get("clarification")
            and (state.get("last_observation") or {}).get("refresh_discovery")
        ):
            # New goals need fresh candidates before advocacy/synthesis. Old
            # trace entries and cached places cannot satisfy this prerequisite.
            force("discover", "当前需求需要刷新 Discovery")
        result: dict[str, Any] = {
            "next_action": action,
            "next_arguments": decision.arguments,
            "turn_count": turn,
            "model_token_count": deps.model.total_tokens,
            "model_call_count": deps.model.call_count,
            "model_fallback_count": deps.model.fallback_count,
            "model_total_latency_ms": round(deps.model.total_latency_ms, 2),
            "model_last_error": deps.model.last_error,
            "model_last_usage": dict(deps.model.last_usage),
            "model_calls": list(deps.model.call_records),
            "trace": _trace(
                state,
                "supervisor_decision",
                requested_action=requested_action,
                effective_action=action,
                override_reason="；".join(overrides),
                model_reason=_short_reason(decision.reason),
                provider=deps.model.metadata.provider,
                model=deps.model.metadata.model,
                prompt_version=deps.model.metadata.prompt_version,
                model_available=deps.model.available,
                model_call_count=deps.model.call_count,
                fallback_count=deps.model.fallback_count,
                model_last_error=deps.model.last_error,
                model_last_latency_ms=deps.model.last_latency_ms,
            ),
        }
        if not terminal_phase and action == "finish" and verifier is not None and not verifier.executable:
            result.update(
                {
                    "phase": RunPhase.INFEASIBLE,
                    "reason": state.get("reason") or "达到修复上限，计划仍不可执行",
                }
            )
        return result

    def after_supervisor(state: PlanGoState) -> str:
        return {
            "requirements": "requirements",
            "discover": "discovery",
            "advocate": "advocate_fanout",
            "synthesize": "synthesis",
            "critic": "critic",
            "verify": "verify",
            "propose_actions": "propose_actions",
            "ask_user": "prepare_ask_user",
            "finish": "finalize",
        }.get(state.get("next_action") or "", "finalize")

    def after_approval(state: PlanGoState) -> str:
        decision = state.get("approval_decision")
        if decision == "approve":
            return "execute"
        if decision == "edit":
            return "replan"
        return "reflect"

    graph = StateGraph(PlanGoState)
    graph.add_node("load_memory", load_memory, retry_policy=read_retry, timeout=30)
    graph.add_node(
        "requirements",
        requirement_subgraph(requirements),
        retry_policy=read_retry,
        timeout=deps.node_timeout_seconds,
    )
    graph.add_node(
        "discovery",
        discovery_subgraph(discovery),
        retry_policy=read_retry,
        timeout=deps.node_timeout_seconds,
    )
    graph.add_node(
        "advocate_worker",
        advocate_subgraph(advocate_worker),
        retry_policy=read_retry,
        timeout=deps.node_timeout_seconds,
    )
    graph.add_node(
        "synthesis", synthesis, retry_policy=read_retry, timeout=deps.node_timeout_seconds
    )
    graph.add_node(
        "verify", verify, retry_policy=read_retry, timeout=max(45, deps.node_timeout_seconds)
    )
    graph.add_node(
        "critic",
        critic_subgraph(critic),
        retry_policy=read_retry,
        timeout=deps.node_timeout_seconds,
    )
    graph.add_node("propose_actions", propose_actions, timeout=30)
    graph.add_node("approval", approval)
    graph.add_node("replan", replan)
    graph.add_node("execute", getattr(deps.tools, "prepare_browser_execution", execute))
    graph.add_node("reflect", reflection_subgraph(reflect))
    graph.add_node("prepare_ask_user", prepare_ask_user)
    graph.add_node("ask_user", ask_user)
    graph.add_node("supervisor", supervisor)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "requirements")
    graph.add_edge("requirements", "supervisor")
    graph.add_edge("discovery", "supervisor")
    graph.add_conditional_edges("supervisor", after_supervisor)

    # LangGraph requires a node for the fan-out target; use a small pass-through
    # node so the graph remains inspectable and the worker branches merge.
    async def advocate_entry(state: PlanGoState) -> dict[str, Any]:
        return {"phase": RunPhase.RESEARCHING, "trace": _trace(state, "advocate_fanout_started")}

    graph.add_node("advocate_fanout", advocate_entry)
    graph.add_conditional_edges(
        "advocate_fanout", advocate_fanout, ["advocate_worker", "synthesis"]
    )
    graph.add_edge("advocate_worker", "synthesis")
    graph.add_edge("synthesis", "verify")
    graph.add_edge("verify", "supervisor")
    graph.add_edge("critic", "supervisor")
    graph.add_edge("propose_actions", "approval")
    graph.add_conditional_edges("approval", after_approval)
    graph.add_edge("execute", "reflect")
    graph.add_edge("reflect", "finalize")
    graph.add_edge("replan", "load_memory")
    graph.add_edge("prepare_ask_user", "ask_user")
    graph.add_edge("ask_user", "requirements")
    graph.add_edge("finalize", END)
    if extension is not None:
        extension(graph)
    return graph.compile(checkpointer=checkpointer or InMemorySaver(serde=checkpoint_serializer()))
