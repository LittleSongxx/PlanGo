"""Synthetic provider replies test revalidation within one durable run and preserve source times."""

import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.task import TaskDecision
from plango_harness.agent.contracts import Evidence, PlaceCandidate, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps
from test_browser_harness import TOKEN, settings, wait_for
from test_browser_navigation import browser_driver
from test_preparation_outcome import prepared_state


def canonical_place():
    return PlaceCandidate(place_id="amap:fixture", name="雾岚餐厅", address="重庆市渝中区邹容路1号", category="餐厅", latitude=29.56, longitude=106.57, source="amap", average_price=50)


def test_explicit_revalidation_continues_the_same_paused_run_and_keeps_original_event(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    async def actor(schema, *, fallback, **kwargs):
        if schema is not TaskDecision:
            return fallback
        context = json.loads(kwargs['user'])
        if context['turn_id'] > 1:
            if not context['tool_results']:
                return TaskDecision(operation='refresh_place')
            return TaskDecision(operation='plan', requirements=RequirementOutput(refresh_sources=True))
        return TaskDecision(operation='plan', requirements=RequirementOutput(party_size=3, budget=300,
            visit_date=datetime.fromisoformat(context['reference_at']).date(), time_window_start='18:30', required_activities=['餐厅']))
    app.state.runtime.model.structured = actor
    lookup = AsyncMock(return_value=canonical_place())
    app.state.runtime.world_service.provider.amap.get_place = lookup
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "今天18:30，3人吃饭，帮我规划一个餐厅行程，总预算300元", "browser_session_id": "fixture-desktop",
            "location_context": {"city": "重庆", "longitude": 106.57, "latitude": 29.56, "source": "manual"}, "selected_poi": {"poi_id": "fixture", "name": "雾岚餐厅", "longitude": 106.57, "latitude": 29.56}}).json()["run_id"]
        command, respond, _ = browser_driver(client, run_id)
        fields = {"places": [{**canonical_place().model_dump(mode="json"), "open_minute": 0, "close_minute": 1440}],
                  "routes": {"amap:fixture": {"driving_min": 0, "walking_min": 0, "transit_min": 0, "distance_km": 0}}}
        respond(command("extract"), fields=fields)
        before = wait_for(client, run_id, lambda value: bool(value.get("draft_review")))
        original = before["state"]["selected_poi"]
        origin = before["state"]["trip_spec"]["location"]
        lookup.reset_mock()
        changed = client.post(f"/api/v1/runs/{run_id}/messages", json={"text": "重新核验这家门店，其他要求不变"})
        assert changed.status_code == 202, changed.text
        respond(command("extract"), fields=fields)
        after = wait_for(client, run_id, lambda value: value["state"].get("turn_id", 1) > 1 and bool(value.get("draft_review")))
        fresh = after["state"]["selected_poi"]
        lookup.assert_awaited_once_with("amap:fixture", refresh=True)
        assert after["run_id"] == before["run_id"]
        assert after["state"]["trip_spec"]["location"] == origin
        assert fresh["place_id"] == original["place_id"] and fresh["evidence_ids"] != original["evidence_ids"]
        assert fresh["evidence"]["observed_at"] > original["evidence"]["observed_at"]
        assert after["state"]["tool_call_count"] > before["state"]["tool_call_count"]
        assert after["draft_review"]["can_prepare"]
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
        assert events[0]["payload"]["selected_poi"] == original
        refreshes = [event for event in events if event["event_type"] == "SELECTED_POI_REFRESHED"]
        assert len(refreshes) == 1 and refreshes[0]["payload"]["selected_poi"]["evidence"] == fresh["evidence"]


@pytest.mark.parametrize("case", ["expired", "fresh", "explicit_refresh", "unavailable", "wrong_id", "budget"])
async def test_graph_entry_refresh_is_once_budgeted_and_never_renews_old_fact(tmp_path, monkeypatch, case):
    import plango.graph as module

    runtime = create_app(settings(tmp_path), token=TOKEN).state.runtime
    runtime.model.structured = AsyncMock(return_value=TaskDecision(operation="refresh_place"))
    lookup = AsyncMock(return_value=None if case == "unavailable" else canonical_place().model_copy(update={"place_id": "amap:wrong"}) if case == "wrong_id" else canonical_place())
    runtime.world_service.provider.amap.get_place = lookup
    deps = GraphDeps(model=runtime.model, tools=runtime.tools, world=runtime.world_service.provider, planner=None, memory=None, runs=None, action_provider=None)
    actual = module.build_graph
    nodes = {}

    def build(deps, *, extension, **kwargs):
        def capture(graph):
            extension(graph)
            nodes["context"] = graph.nodes["browser_decide" if case == "explicit_refresh" else "task_prepare_plan"].runnable
        return actual(deps, extension=capture, **kwargs)

    monkeypatch.setattr(module, "build_graph", build)
    module.build_desktop_graph(runtime, deps, None)
    state, _ = prepared_state()
    previous = TripSpec.model_validate(state["execution_goal"]["requirements"])
    now = datetime.now(timezone.utc)
    fact = Evidence(evidence_id="old-source", source="amap", source_ref="https://fixture.invalid/poi", observed_at=now - timedelta(minutes=20), expires_at=now + timedelta(minutes=5) if case in {"fresh", "explicit_refresh"} else now - timedelta(minutes=10), payload=canonical_place().model_dump(mode="json"))
    selected = {**canonical_place().model_dump(mode="json"), "evidence_ids": [fact.evidence_id], "evidence": fact.model_dump(mode="json")}
    state["selected_plan"].stops[0].locked = True
    state.update(user_id="fixture", turn_id=2, input_text="改成后天，其他要求不变" if case == "explicit_refresh" else "预算改为300元", selected_poi=selected,
                 previous_spec=previous, evidence=[fact], tool_call_count=deps.max_tool_calls if case == "budget" else 3,
                 browser_task_context={"mode": "planning", "kind": "planning", "turn_id": 1}, requirement_reference_at=now.isoformat())
    state["execution_goal"] = None
    old = copy.deepcopy(state)
    if case == "budget":
        with pytest.raises(ValueError, match="selected_poi_refresh_tool_budget_exhausted"):
            await nodes["context"].ainvoke(state)
        lookup.assert_not_awaited()
        return
    updated = await nodes["context"].ainvoke(state)
    if case == "fresh":
        lookup.assert_not_awaited()
        assert "selected_poi" not in updated
        return
    lookup.assert_awaited_once_with("amap:fixture", refresh=True)
    assert updated["tool_call_count"] == 4
    assert state["selected_plan"] == old["selected_plan"] and state["previous_spec"] == old["previous_spec"]
    if case in {"unavailable", "wrong_id"}:
        assert updated["selected_poi"]["evidence"] == old["selected_poi"]["evidence"]
        assert updated["selected_poi"]["refresh_error"]
        assert "evidence" not in updated
    else:
        assert updated["selected_poi"]["evidence"]["observed_at"] > selected["evidence"]["observed_at"]
        assert all(item.evidence_id != fact.evidence_id for item in updated["evidence"])
    await nodes["context"].ainvoke({**state, **updated})
    assert lookup.await_count == 1
