"""Actual specialist nodes receive current canonical fields, while checkpoint audit history remains."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from langgraph.graph import END, START, StateGraph
from plango_harness.agent.contracts import (
    AdvocateReport,
    Evidence,
    Location,
    PlaceCandidate,
    TripSpec,
    VerifierResult,
)
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import PlanGoState, initial_state, planning_reset
from test_browser_harness import settings


async def test_current_spec_and_current_run_turn_reports_reach_real_specialist_nodes(tmp_path):
    model = ModelAdapter(settings(tmp_path))
    seen = []

    async def structured(schema, *, fallback, **kwargs):
        seen.append((schema.__name__, kwargs["user"]))
        if schema is AdvocateReport:
            return AdvocateReport(run_id="model-cannot-assign-owner", turn_id=1, role="model-cannot-assign-role", verdict="accept", preferred_place_ids=["fixture-place"], evidence_ids=["fixture-evidence"], rationale="CURRENT_REPORT")
        return fallback

    model.structured = structured
    deps = GraphDeps(model=model, tools=SimpleNamespace(schemas=lambda: []), world=SimpleNamespace(), planner=None, memory=None, runs=None, action_provider=None)
    nodes = {}

    def capture(graph):
        nodes.update({name: graph.nodes[name].runnable for name in ("advocate_worker", "synthesis", "critic")})

    build_graph(deps, extension=capture)
    spec = TripSpec(goal="原需求预算300元；用户补充：取消预算 OLD_GOAL_MARKER", budget=None, per_person_budget=None, party_size=3,
                    soft_preferences=["安静"], hard_constraints=["不含花生"], location=Location(name="重庆", latitude=29.56, longitude=106.57),
                    required_activities=["餐厅"], time_window_start="18:30")
    old_report = AdvocateReport(role="预算", turn_id=1, verdict="accept", rationale="OLD_REPORT_300")
    state = initial_state(run_id="current-run", user_id="fixture", input_text="不设预算，其他要求不变")
    state.update(trip_spec=spec, advocate_reports=[old_report], trace=[{"event": "historical-user-budget", "payload": {"budget": 300}}],
                 clarification={"kind": "draft_review", "interrupt_id": "old-draft-approval"})
    resetting = StateGraph(PlanGoState)
    resetting.add_node("reset", lambda current: {**planning_reset(current), "turn_id": 2})
    resetting.add_edge(START, "reset")
    resetting.add_edge("reset", END)
    updated = await resetting.compile().ainvoke(state)
    assert updated["advocate_reports"] == [old_report], "operator.add with [] preserves audit reports"
    assert updated["trace"] == state["trace"]
    assert updated["clarification"] is None, "A new clarification cannot inherit the old draft approval binding"
    assert updated["previous_spec"].goal == spec.goal

    now = datetime.now(timezone.utc)
    place = PlaceCandidate(place_id="fixture-place", name="样本餐厅", category="餐厅", latitude=29.56, longitude=106.57, average_price=31, source="browser", evidence_ids=["fixture-evidence"])
    fact = Evidence(evidence_id="fixture-evidence", source="browser", source_ref="https://fixture.invalid/menu", observed_at=now, expires_at=now + timedelta(minutes=10), payload={"place_id": place.place_id}, claim="本店菜品不含花生。")
    updated.update(trip_spec=spec, place_candidates=[place], evidence=[fact], advocate_role="预算")
    generated = await nodes["advocate_worker"].ainvoke(updated)
    current = generated["advocate_reports"][0]
    assert (current.run_id, current.turn_id, current.role) == ("current-run", 2, "预算")
    foreign = AdvocateReport(run_id="another-run", turn_id=2, role="预算", verdict="reject", rationale="FOREIGN_REPORT_300")
    updated["advocate_reports"] += [foreign.model_dump(mode="json"), current.model_dump(mode="json")]
    synthesized = await nodes["synthesis"].ainvoke(updated)
    selected = synthesized["selected_plan"]
    critique = await nodes["critic"].ainvoke({**updated, **synthesized, "verifier": VerifierResult(plan_id=selected.plan_id)})
    assert [name for name, _ in seen] == ["AdvocateReport", "PlanDraft"]
    assert critique["critique"].verdict == "pass"
    for _, user in seen:
        canonical = json.loads(user.splitlines()[0].removeprefix("TripSpec："))
        assert canonical["goal"] == spec.goal
        assert canonical["budget"] is None and canonical["per_person_budget"] is None
        assert canonical["party_size"] == 3 and canonical["time_window_start"] == "18:30"
        assert canonical["soft_preferences"] == ["安静"] and canonical["hard_constraints"] == ["不含花生"]
        assert "OLD_REPORT_300" not in user and "FOREIGN_REPORT_300" not in user
    planner_input = next(user for name, user in seen if name == "PlanDraft")
    assert "CURRENT_REPORT" in planner_input
    assert updated["advocate_reports"][0] == old_report and updated["trace"] == state["trace"]
