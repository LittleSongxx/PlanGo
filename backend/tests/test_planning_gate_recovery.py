"""Offline graph checks: internal gaps do not become new user constraints."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from plango.planning import preserve_locks
from plango_harness.agent.contracts import (
    ConstraintCheck,
    CritiqueReport,
    PlanDraft,
    PlanDraftStop,
    TripSpec,
)
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, _coordinator_decision, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import initial_state
from plango_harness.domain.planning import PlanEvaluation, verify_plan
from plango_harness.settings import Settings
from test_planning_evidence import observed_case


def graph_nodes(*, world=None, planner=None):
    model = ModelAdapter(Settings(runtime_profile="sandbox", agent_mode="single", openai_api_key="", _env_file=None))
    model.structured = AsyncMock(side_effect=AssertionError("No model calls in this regression"))
    deps = GraphDeps(model=model, tools=SimpleNamespace(schemas=lambda: [], execute=AsyncMock()),
                     world=world or SimpleNamespace(), planner=planner or SimpleNamespace(preserve_locks=preserve_locks),
                     memory=None, runs=SimpleNamespace(save_plan=AsyncMock()), action_provider=None, agent_mode="single")
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update({name: graph.nodes[name].runnable
        for name in ("requirements", "synthesis", "supervisor", "critic", "verify")}))
    return deps, nodes


async def test_qualitative_preferences_do_not_create_numeric_hard_limits():
    """A limit is a number in a typed field, never a phrase read out of free text.

    Every explicit condition is still reported per stop; only the typed fields turn into
    the numeric comparisons that can fail a plan.
    """
    _, plan, evidence = observed_case()
    plan.stops[0] = plan.stops[0].model_copy(update={"distance_km": 6, "estimated_wait_min": 40})
    spec = TripSpec(goal="就近且少排队", party_size=1, budget=None, hard_constraints=["距离优先", "少排队"])
    checked = await verify_plan(spec, plan, None, evidence=evidence)
    assert checked.hard_constraints_pass and checked.executable
    assert {"fact:距离优先:restaurant", "fact:少排队:restaurant"} <= {check.name for check in checked.soft_warnings}
    bounded = spec.model_copy(update={"max_distance_km": 5, "max_queue_minutes": 30, "budget": 40})
    checked = await verify_plan(bounded, plan, None, evidence=evidence)
    assert {check.name for check in checked.hard_violations} >= {"distance:restaurant", "queue:restaurant", "budget"}
    # The same wording used for a stricter rule is treated identically: reported, not
    # silently converted into a zero-minute limit.
    no_queue = spec.model_copy(update={"hard_constraints": ["不排队"]})
    relaxed = await verify_plan(no_queue, plan, None, evidence=evidence)
    assert relaxed.hard_constraints_pass and relaxed.executable
    assert any(check.name == "fact:不排队:restaurant" for check in relaxed.soft_warnings)
    strict = await verify_plan(no_queue.model_copy(update={"max_queue_minutes": 0}), plan, None, evidence=evidence)
    assert any(check.name == "queue:restaurant" for check in strict.hard_violations)


async def test_unknown_previous_facts_do_not_trigger_venue_reselection():
    place, prior, evidence = observed_case()
    place = place.model_copy(update={"average_price": 50, "price_known": True})
    prior.checks = [ConstraintCheck(name="supply:restaurant", kind="unknown", passed=None)]
    previous = TripSpec(goal="原计划", party_size=1, budget=300)
    state = initial_state(run_id="reuse", user_id="fixture", input_text="人数改为2人")
    state.update(trip_spec=previous.model_copy(update={"party_size": 2}), previous_spec=previous,
                 previous_plan=prior, place_candidates=[place], evidence=evidence)
    deps, nodes = graph_nodes()
    try:
        result = await nodes["synthesis"].ainvoke(state)
        assert [stop.place_id for stop in result["selected_plan"].stops] == [place.place_id]
        assert result["selected_plan"].total_cost == 100
        deps.model.structured.assert_not_awaited()
        assert state["previous_plan"] == prior and prior.checks[0].passed is None
    finally:
        await deps.model.close()


@pytest.mark.parametrize("failure", ["missing_fixed_place", "missing_locked_place"])
async def test_internal_plan_failures_preserve_constraints_without_requesting_unlock(failure):
    place, prior, evidence = observed_case()
    prior.stops[0] = prior.stops[0].model_copy(update={"place_id": "prior-locked", "locked": True})
    previous = TripSpec(goal="原计划", party_size=1, budget=300)
    spec = previous.model_copy(update={"must_visit_place_ids": ["not-observed"]} if failure == "missing_fixed_place"
                               else {"soft_preferences": ["新偏好"]})
    state = initial_state(run_id="compile", user_id="fixture", input_text="更新草案")
    state.update(trip_spec=spec, previous_spec=previous, previous_plan=prior,
                 place_candidates=[place], evidence=evidence)
    before = deepcopy(state)
    draft = PlanDraft(stops=[PlanDraftStop(place_id=place.place_id)])
    deps, nodes = graph_nodes()
    try:
        with patch("plango_harness.agent.graph.PlannerAgent.run", AsyncMock(return_value=draft)):
            result = await nodes["synthesis"].ainvoke(state)
        assert result["phase"] == "FAILED" and result["clarification"] is None
        assert result["selected_plan"] is None and result["verifier"] is None
        assert "解锁" not in result["reason"] and state == before
        assert result["plan_draft"] == draft
    finally:
        await deps.model.close()


async def test_hard_conflict_is_repaired_while_unrelated_unknown_stays_unknown():
    place, selected, evidence = observed_case()
    kept = selected.stops[0].model_copy(update={"estimated_wait_min": None, "start_minute": 930, "end_minute": 990})
    extra = selected.stops[0].model_copy(update={"place_id": "optional", "name": "可省略活动", "estimated_cost": 100})
    selected = selected.model_copy(update={"stops": [extra, kept], "total_cost": 150})
    spec = TripSpec(goal="保留主活动", party_size=1, budget=100, must_visit_place_ids=[kept.place_id])
    verifier = await verify_plan(spec, selected, None, evidence=evidence)
    assert not verifier.hard_constraints_pass and verifier.unknown_evidence

    async def evaluate(current_spec, plan, *, evidence, **kwargs):
        return PlanEvaluation(plan=plan, verifier=await verify_plan(current_spec, plan, None, evidence=evidence))

    planner = SimpleNamespace(evaluate=evaluate, last_evidence=[])
    deps, nodes = graph_nodes(planner=planner)
    state = initial_state(run_id="repair", user_id="fixture", input_text=spec.goal)
    state.update(trip_spec=spec, selected_plan=selected, verifier=verifier,
                 place_candidates=[place], evidence=evidence, phase="REVIEWING")
    try:
        assert _coordinator_decision(state, deps).next_action == "critic"
        assert (await nodes["supervisor"].ainvoke(state))["next_action"] == "critic"
        with patch("plango_harness.agent.graph.CriticAgent.run", AsyncMock(return_value=CritiqueReport(verdict="ask_user"))):
            result = await nodes["critic"].ainvoke(state)
        assert result["repair_applied"] and result["phase"] == "REPLANNING"
        assert [stop.place_id for stop in result["selected_plan"].stops] == [kept.place_id]
        verified = await nodes["verify"].ainvoke({**state, **result})
        assert verified["verifier"].hard_constraints_pass
        # The unrelated unknown is still reported after the repair, and it no longer
        # withholds the itinerary the repair just made consistent.
        assert verified["verifier"].unknown_evidence and not verified["verifier"].evidence_complete
        assert verified["verifier"].executable and not verified["verifier"].blocking_evidence
        assert "action_proposal" not in result and not state["action_proposal"]
    finally:
        await deps.model.close()


async def test_unresolved_location_proposal_is_not_geocoded_or_written():
    previous = TripSpec(goal="原计划", party_size=2, budget=300)
    proposal = RequirementOutput(location_name="未决起点", search_location_name="未决商圈", party_size=3,
                                 clarification_needed=True, clarification_fields=["location", "search_location"],
                                 clarification_question="请明确出发地与商圈")
    state = initial_state(run_id="location", user_id="fixture", input_text="改地点和人数")
    state.update(previous_spec=previous, requirement_proposal={"turn_id": 1, "input_text": state["input_text"],
                                                             "output": proposal.model_dump(mode="json")})
    world = SimpleNamespace(strict_location=True, requirement_origin=AsyncMock(side_effect=AssertionError("Unresolved origin")))
    deps, nodes = graph_nodes(world=world)
    try:
        result = await nodes["requirements"].ainvoke(state)
        assert result["trip_spec"].location == previous.location
        assert result["trip_spec"].search_location == previous.search_location
        assert result["trip_spec"].party_size == 3
        assert result["clarification"]["fields"] == proposal.clarification_fields
        world.requirement_origin.assert_not_awaited()
        deps.tools.execute.assert_not_awaited()
    finally:
        await deps.model.close()
