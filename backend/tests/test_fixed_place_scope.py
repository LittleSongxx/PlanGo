"""Frozen 21-candidate topology exercised through real compiled nodes with synthetic facts only."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from plango.planning import BrowserPlanEngine
from plango.settings import DesktopSettings
from plango_harness.agent.contracts import Evidence, PlaceCandidate, PlanCandidate, TripSpec
from plango_harness.agent.graph import GraphDeps, _fixed_place_scope, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import initial_state
from plango_harness.providers.world import Supply
from plango_harness.tools.registry import ToolRegistry

FROZEN = Path(__file__).resolve().parent / "frozen_four_person_state.json"


def frozen_state():
    return json.loads(FROZEN.read_text())["state"]


async def measure_scope(mode="fresh", *, scoped=True):
    frozen = frozen_state()
    original = deepcopy(frozen)
    places = [PlaceCandidate.model_validate(raw) for raw in frozen["place_candidates"]]
    prior = PlanCandidate.model_validate(frozen["selected_plan"])
    fixed_id = prior.stops[0].place_id
    assert len(places) == 21 and not prior.stops[0].locked
    now = datetime.now(timezone.utc)
    spec = TripSpec.model_validate(frozen["trip_spec"]).model_copy(update={"party_size": 2, "visit_date": now.date(), "time_window_start": "18:30"})
    facts = []
    for index, place in enumerate(places):
        expires = now - timedelta(minutes=1) if (mode == "noise_expired" and place.place_id != fixed_id) or (mode in {"target_expired", "target_failed", "date"} and place.place_id == fixed_id) else now + timedelta(minutes=10)
        proof = Evidence(evidence_id=f"fixture-identity:{index}", source="amap", source_ref="https://fixture.invalid/identity", observed_at=now - timedelta(minutes=2), expires_at=expires,
                         payload={"place_id": place.place_id, "name": place.name, "address": place.address})
        places[index] = place.model_copy(update={"evidence_ids": [proof.evidence_id]})
        facts.append(proof)
    weather = Evidence(evidence_id="weather:fixture", source="amap", source_ref="https://fixture.invalid/weather", observed_at=now, expires_at=now + timedelta(minutes=10), payload={"weather": "受控天气"})
    failure = Evidence(evidence_id="fixture-global-failure", source="amap", source_ref="fixture", confidence=0, payload={"error_kind": "controlled_previous_failure"})
    declaration = Evidence(evidence_id="fixture-user-declaration", source="user", source_ref="fixture", payload={"kind": "merchant_identity_confirmation"})
    facts += [weather, failure, declaration]
    prompts, refreshed, weather_dates, route_calls, supply_calls = [], [], [], [], []

    async def structured(schema, *, fallback, **kwargs):
        prompts.append((schema.__name__, kwargs["user"]))
        return fallback

    async def refresh_place(place):
        refreshed.append(place.place_id)
        if mode == "target_failed":
            return place.model_copy(update={"evidence_ids": [], "price_known": False}), failure
        proof = Evidence(evidence_id="fixture-refreshed:" + place.place_id, source="amap", source_ref="https://fixture.invalid/identity", observed_at=now, expires_at=now + timedelta(minutes=10), payload={"place_id": place.place_id, "name": place.name})
        return place.model_copy(update={"evidence_ids": [proof.evidence_id]}), proof

    async def get_weather(location, visit_date, zone):
        weather_dates.append(visit_date.isoformat())
        return {"visit_date": visit_date.isoformat(), "weather": "受控天气"}, weather

    async def estimate_route(origin, place, **kwargs):
        route_calls.append(place.place_id)
        return {"driving_min": None, "walking_min": None, "transit_min": None, "distance_km": None}, Evidence(
            evidence_id="route:controlled-failure", source="amap", source_ref="https://fixture.invalid/route", confidence=0, payload={"destination_place_id": place.place_id, "error_kind": "controlled_route_missing"})

    async def get_supply(place_id, minute):
        supply_calls.append(place_id)
        return Supply(place_id=place_id, open_now=None, reservable=None, seats_left=None, estimated_wait_min=None, source="unknown")

    model = ModelAdapter(DesktopSettings(openai_api_key="", amap_webservice_key=""))
    model.structured = structured
    world = SimpleNamespace(strict_location=True, source="amap", requirement_origin=AsyncMock(return_value=(None, spec.location, {"source": "user"})),
                            refresh_place=refresh_place, get_weather=get_weather, get_place=AsyncMock(return_value=places[0]), estimate_route=estimate_route, get_supply=get_supply)
    deps = GraphDeps(model=model, world=world, tools=ToolRegistry(), planner=BrowserPlanEngine(world), memory=None, runs=SimpleNamespace(save_plan=AsyncMock()), action_provider=None)
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update({name: graph.nodes[name].runnable for name in ("requirements", "supervisor", "discovery", "advocate_worker", "verify")}))
    fields = {"party_size": 3, **({"visit_date": (now.date() + timedelta(days=1)).isoformat()} if mode == "date" else {})}
    state = initial_state(run_id="fixed-place-controlled", user_id="fixture", input_text="人数改为3人，其他要求不变")
    state.update(previous_spec=spec, previous_plan=prior, place_candidates=places, evidence=facts, weather={"weather": "受控天气"},
                 structured_requirement_edit={"turn_id": 2, "fields": fields}, turn_id=2, trace=deepcopy(frozen["trace"]),
                 turn_budget={"id": "controlled", "model_baseline": 117705, "tool_baseline": 50}, model_token_count=117705, tool_call_count=50)
    budget_before = deepcopy(state["turn_budget"])
    trace_before = deepcopy(state["trace"])
    def bypass(_state, _spec, candidate_rows, evidence_rows, _refresh):
        return candidate_rows, evidence_rows
    try:
        with patch("plango_harness.agent.graph._fixed_place_scope", _fixed_place_scope if scoped else bypass):
            update = await nodes["requirements"].ainvoke(state)
        assert state["trace"] == trace_before and state["turn_budget"] == budget_before
        state.update(update)
        decision = await nodes["supervisor"].ainvoke(state)
        if decision["next_action"] == "discover":
            state.update(await nodes["discovery"].ainvoke(state))
        assert state["requirement_refresh"]["discovery"] is False
        assert len(state["place_candidates"]) == (1 if scoped else 21)
        assert all(item.place_id == fixed_id for item in state["place_candidates"]) if scoped else True
        assert any(item.evidence_id == failure.evidence_id for item in state["evidence"])
        assert any(item.evidence_id == declaration.evidence_id for item in state["evidence"])
        if scoped:
            assert all(identity == fixed_id for identity in refreshed)
        state["advocate_role"] = "体验"
        await nodes["advocate_worker"].ainvoke(state)
        prompt = next(text for schema, text in prompts if schema == "AdvocateReport")
        if scoped:
            assert fixed_id in prompt
            assert all(place.place_id not in prompt for place in places if place.place_id != fixed_id)
        state["selected_plan"] = prior.model_copy(update={"party_size": 3})
        verified = await nodes["verify"].ainvoke(state)
        assert route_calls == [fixed_id] and supply_calls == [fixed_id]
        assert not verified["verifier"].executable
        assert any("route" in item.name for item in verified["verifier"].unknown_evidence), "Missing routes cannot become zero-minute verified trips"
        assert state["turn_budget"] == budget_before and state["model_token_count"] == 117705
        assert frozen == original
        if mode == "date":
            assert weather_dates == [fields["visit_date"]]
        elif mode == "fresh":
            # One attending role needs one perspective, so synthesis follows directly.
            assert not refreshed and not weather_dates and decision["next_action"] == "synthesize"
        return {"mode": mode, "scope_enabled": scoped, "frozen_candidates": 21, "current_candidates": len(state["place_candidates"]),
                "current_evidence": len(state["evidence"]), "identity_refresh_attempts": len(refreshed), "weather_reads": len(weather_dates),
                "advocate_input_chars": len(prompt), "route_reads": len(route_calls), "supply_reads": len(supply_calls),
                "tool_delta_including_verification": verified["tool_call_count"] - 50, "model_network_calls": 0,
                "historical_trace_preserved": frozen == original, "turn_budget_preserved": state["turn_budget"] == budget_before,
                "route_unknown_preserved": not verified["verifier"].executable}
    finally:
        await model.close()


@pytest.mark.parametrize("mode", ["fresh", "noise_expired", "target_expired", "target_failed", "date"])
async def test_fixed_scope_reaches_refresh_specialists_and_verification(mode):
    measured = await measure_scope(mode)
    if mode in {"target_expired", "target_failed", "date"}:
        assert measured["identity_refresh_attempts"] == 1
    else:
        assert measured["identity_refresh_attempts"] == 0


@pytest.mark.parametrize("case", ["optional", "required", "ordered", "multiple_fixed", "unfixed", "discovery", "missing_target", "multi_stop", "excluded"])
def test_general_or_changed_plans_keep_their_full_candidate_scope(case):
    state = frozen_state()
    original = deepcopy(state)
    spec = TripSpec.model_validate(state["trip_spec"])
    places = [PlaceCandidate.model_validate(raw) for raw in state["place_candidates"]]
    evidence = [Evidence.model_validate(raw) for raw in state["evidence"]]
    refresh = {"discovery": case == "discovery"}
    changes = {"optional": {"optional_activities": ["公园"]}, "required": {"required_activities": ["餐厅", "公园"]},
               "ordered": {"activity_order": ["餐厅", "公园"]}, "multiple_fixed": {"must_visit_place_ids": [places[0].place_id, places[1].place_id]},
               "unfixed": {"must_visit_place_ids": []}, "excluded": {"excluded_activities": ["餐厅"]}}
    spec = spec.model_copy(update=changes.get(case, {}))
    if case == "missing_target":
        places = places[1:]
    if case == "multi_stop":
        state["previous_plan"]["stops"].append({**state["previous_plan"]["stops"][0], "place_id": places[1].place_id})
    before = deepcopy(state)
    kept, facts = _fixed_place_scope(state, spec, places, evidence, refresh)
    assert kept == places and facts == evidence and state == before
    assert frozen_state() == original, "The frozen real-run file is never updated by this controlled check"
