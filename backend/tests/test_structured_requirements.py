"""Offline, isolated SQLite/browser fixtures; these checks prove no merchant fulfillment."""

import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from plango.app import create_app
from plango.browser import run_context
from plango.requirements import RequirementEdit
from plango.world import BrowserWorld
from plango_harness.agent.contracts import Location, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.requirements import requirement_delta
from plango_harness.agent.state import PlanGoState, initial_state
from pydantic import ValidationError
from test_browser_harness import TOKEN, fixture, settings


def test_structured_fields_reject_invalid_or_unbounded_input():
    for fields in ({"party_size": True}, {"party_size": 13}, {"budget": True}, {"budget": -1}, {"budget": float("inf")}, {"max_distance_km": 51}, {"visit_date": "2026-02-30"}, {"time_window_start": "25:00"}, {"location_name": None}, {"travel_mode": None}, {"unexpected": "value"}):
        with pytest.raises(ValidationError):
            RequirementEdit(expected_version=1, fields=fields)
    cleared = RequirementEdit(expected_version=1, fields={"budget": None, "per_person_budget": None})
    assert cleared.fields.model_dump(exclude_unset=True) == {"budget": None, "per_person_budget": None}
    original = TripSpec(goal="受控范围编辑", max_distance_km=2)
    for radius in (1, 5, None):
        _, refresh = requirement_delta(original, original.model_copy(update={"max_distance_km": radius}))
        assert refresh["discovery"], "Expanding the search must fetch candidates absent from the old smaller radius."


def test_search_radius_reaches_existing_amap_request_and_cache_key(tmp_path):
    async def exercise():
        config = settings(tmp_path).model_copy(update={"amap_webservice_key": "offline-transport-only"})
        world = BrowserWorld(config, None, None)
        world.amap._get = AsyncMock(return_value={"status": "1", "pois": []})
        token = run_context.set({"world_source": "amap"})
        try:
            for radius in (2, 5, 5, None, 50, 100):
                spec = TripSpec(goal="受控搜索范围", max_distance_km=radius)
                world.bind_run_state({"trip_spec": spec})
                await world.search_places("餐厅", spec.location)
            calls = world.amap._get.call_args_list
            assert [call.args[1]["radius"] for call in calls] == [2000, 5000, 50000]
        finally:
            run_context.reset(token)
            await world.close()
    asyncio.run(exercise())


def test_origin_clarification_corrects_the_failed_explicit_address_without_losing_other_edits(tmp_path):
    async def exercise():
        requested = []
        corrected = Location(name="受控恢复起点", latitude=29.56, longitude=106.57)

        async def execute(name, arguments, ctx):
            assert name == "geocode"
            requested.append(arguments["address"])
            return {"ok": True, "result": corrected.model_dump(mode="json")} if arguments["address"] == corrected.name else {"ok": False, "error": "ambiguous_location"}

        model = ModelAdapter(settings(tmp_path))
        model.structured = AsyncMock(return_value=RequirementOutput(location_name=corrected.name,
                                      field_evidence={"location_name": "出发地点改为受控恢复起点"}))
        deps = GraphDeps(model=model, world=SimpleNamespace(strict_location=True), tools=SimpleNamespace(schemas=lambda: [], execute=execute), planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
        graph = StateGraph(PlanGoState)
        graph.add_node("requirements", nodes["requirements"])
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", END)
        graph = graph.compile(checkpointer=InMemorySaver())
        state = initial_state(run_id="fixture", user_id="fixture", input_text="修改行程需求；起点：受控无效起点；总预算：未设定")
        state["messages"] += [HumanMessage(content="历史重复句", id="legacy-1"), HumanMessage(content="历史重复句", id="legacy-2"), AIMessage(content="历史答复", id="legacy-assistant")]
        state.update(previous_spec=TripSpec(goal="原任务", budget=300), structured_requirement_edit={"fields": {"location_name": "受控无效起点", "budget": None}, "turn_id": 1})
        config = {"configurable": {"thread_id": "fixture"}}
        waiting = await graph.ainvoke(state, config)
        assert waiting.get("__interrupt__") and model.structured.await_count == 0
        assert "多个匹配" in waiting["__interrupt__"][0].value["question"] and "Key" not in waiting["__interrupt__"][0].value["question"]
        restored = await graph.ainvoke(Command(resume={"text": "出发地点改为受控恢复起点"}), config)
        assert not restored.get("__interrupt__")
        assert restored["trip_spec"].location == corrected and restored["trip_spec"].budget is None
        assert restored["messages"][:-1] == state["messages"] and len(restored["messages"]) == len(state["messages"]) + 1
        assert {item["field"] for item in restored["requirement_patch"]} >= {"location", "budget"}
        assert requested[-1] == corrected.name

    asyncio.run(exercise())


def test_same_run_explicit_edits_and_lock_preserve_history_and_invalidate_approval(tmp_path):
    config = settings(tmp_path)
    app = create_app(config, token=TOKEN)
    requirement_calls = []

    async def choose(schema, *, fallback, **kwargs):
        if schema.__name__ == "RequirementOutput":
            requirement_calls.append(schema.__name__)
        return fallback

    app.state.runtime.model.structured = choose
    geocodes = []

    async def geocode(address):
        geocodes.append(address)
        return Location(name=address, latitude=29.56, longitude=106.57)

    app.state.runtime.world_service.provider.geocode = geocode
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(app, headers=headers) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "2026-09-10 18:30，3人吃饭，帮我规划一个餐厅行程，总预算300元", "browser_session_id": "fixture-desktop",
                                                "location_context": {"city": "重庆", "longitude": 106.57, "latitude": 29.56, "source": "manual"}}).json()["run_id"]
        provider = {"places": [{"place_id": "browser:fixture", "name": "受控样本餐厅", "address": "重庆市渝中区邹容路1号", "category": "餐厅", "latitude": 29.56, "longitude": 106.57, "average_price": 50, "open_minute": 0, "close_minute": 1440}],
                    "routes": {"browser:fixture": {"driving_min": 0, "walking_min": 0, "transit_min": 0, "distance_km": 0}}}

        def finish_turn():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                for command in client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"]:
                    assert command["operation"] in {"extract", "snapshot"}
                    response = client.post(f"/api/v1/browser/commands/{command['command_id']}/result", json={**fixture(command), "snapshot_id": "fixture:" + command["command_id"], "fields": provider})
                    assert response.status_code == 200, response.text
                value = client.get(f"/api/v1/runs/{run_id}").json()
                if value.get("draft_review") and not value.get("command_pending"):
                    # The final projection precedes releasing the worker lease by a few instructions.
                    time.sleep(0.03)
                    return client.get(f"/api/v1/runs/{run_id}").json()
                if value["phase"] in {"FAILED", "INFEASIBLE", "PARTIAL_FAILED"}:
                    raise AssertionError(value)
                time.sleep(0.02)
            raise AssertionError(value)

        original = finish_turn()
        old_review = original["draft_review"]
        calls_before = len(requirement_calls)

        def edit(fields=None, stop_lock=None):
            before = client.get(f"/api/v1/runs/{run_id}").json()
            payload = {"expected_version": before["version"], "fields": fields or {}, **({"stop_lock": stop_lock} if stop_lock else {})}
            reply = client.post(f"/api/v1/runs/{run_id}/requirements", json=payload)
            assert reply.status_code == 202, reply.text
            assert client.post(f"/api/v1/runs/{run_id}/requirements", json=payload).status_code == 409
            after = finish_turn()
            assert after["state"]["messages"][:-1] == before["state"]["messages"]
            return after

        budget = edit({"budget": None, "per_person_budget": None})
        assert budget["state"]["trip_spec"]["budget"] is None
        assert budget["state"]["trip_spec"]["location"] == original["state"]["trip_spec"]["location"]
        assert budget["state"]["requirement_refresh"] == dict(discovery=False, weather=False, supply=False, routes=False)
        assert all(item["source"] == "user_structured" for item in budget["state"]["requirement_patch"])
        assert client.post(f"/api/v1/runs/{run_id}/draft-decision", json={"decision": "prepare", **{key: old_review[key] for key in ("interrupt_id", "plan_id", "plan_version")}}).status_code == 409

        changed = edit({"party_size": 4, "visit_date": "2026-09-11", "time_window_start": "18:45", "travel_mode": "walking"})
        spec = changed["state"]["trip_spec"]
        assert (spec["party_size"], spec["budget"], spec["visit_date"], spec["time_window_start"], spec["travel_mode"]) == (4, None, "2026-09-11", "18:45", "walking")
        assert sum(spec["party_counts"].values()) == 4
        assert changed["state"]["requirement_refresh"] == dict(discovery=False, weather=True, supply=True, routes=True)
        plan = changed["state"]["selected_plan"]
        locked = edit(stop_lock={"plan_id": plan["plan_id"], "plan_version": plan["version"], "place_id": plan["stops"][0]["place_id"], "locked": True})
        assert locked["state"]["selected_plan"]["stops"][0]["locked"] is True
        plan = locked["state"]["selected_plan"]
        unlocked = edit(stop_lock={"plan_id": plan["plan_id"], "plan_version": plan["version"], "place_id": plan["stops"][0]["place_id"], "locked": False})
        assert unlocked["state"]["selected_plan"]["stops"][0]["locked"] is False
        separated = edit({"search_radius_km": .5, "route_distance_km": 2})
        assert separated["state"]["trip_spec"]["search_radius_km"] == .5
        assert separated["state"]["trip_spec"]["max_distance_km"] == 2
        moved = edit({"location_name": "受控新起点", "search_location_name": "受控新商圈", "max_distance_km": 2})
        assert moved["state"]["trip_spec"]["location"]["name"] == "受控新起点"
        assert moved["state"]["trip_spec"]["search_location"]["name"] == "受控新商圈"
        assert geocodes == ["受控新起点", "受控新商圈"]
        expanded = edit({"max_distance_km": 5})
        assert expanded["state"]["requirement_refresh"]["discovery"]
        assert expanded["state"]["trip_spec"]["budget"] is None
        cleared = edit({"max_distance_km": None, "visit_date": None, "time_window_start": None})
        assert all(cleared["state"]["trip_spec"][key] is None for key in ("max_distance_km", "visit_date", "time_window_start", "budget"))
        assert cleared["state"]["trip_spec"]["time_window"]["start"] is None
        assert {item["field"] for item in cleared["state"]["requirement_patch"] if item["operation"] == "unknown"} == {"visit_date", "time_window_start"}
        assert unlocked["state"]["selected_plan"]["version"] > original["state"]["selected_plan"]["version"]
        assert len(requirement_calls) == calls_before, "Explicit fields must not be reinterpreted by the requirement model."
        assert unlocked["state"]["tool_call_count"] >= original["state"]["tool_call_count"]
        assert unlocked["state"]["execution_goal"] is None
        with sqlite3.connect(tmp_path / "runs.sqlite") as database:
            assert database.execute("SELECT count(*) FROM agent_run").fetchone()[0] == 1
            assert database.execute("SELECT count(*) FROM run_event WHERE event_type='REQUIREMENTS_EDITED'").fetchone()[0] == 8
        final_version = cleared["state"]["selected_plan"]["version"]
        # Simulate shutdown after durable acceptance but before the worker consumes the edit.
        app.state.runtime._enqueue_run = AsyncMock()
        accepted = client.post(f"/api/v1/runs/{run_id}/requirements", json={"expected_version": cleared["version"], "fields": {"party_size": 3}})
        assert accepted.status_code == 202
        assert client.get(f"/api/v1/runs/{run_id}").json()["phase"] == "REPLANNING"
    recovered_app = create_app(config, token=TOKEN)
    recovered_app.state.runtime.model.structured = choose
    with TestClient(recovered_app, headers=headers) as client:
        restored = finish_turn()
        final_version = restored["state"]["selected_plan"]["version"]
        assert final_version > cleared["state"]["selected_plan"]["version"]
        assert restored["state"]["trip_spec"]["party_size"] == 3
        assert restored["state"]["trip_spec"]["budget"] is None
        assert restored["state"]["messages"][:-1] == cleared["state"]["messages"]
        assert len(requirement_calls) == calls_before
        with sqlite3.connect(tmp_path / "runs.sqlite") as database:
            database.execute("INSERT INTO agent_action (action_id,run_id,plan_id,plan_version,tool_name,idempotency_key,request_hash,status,arguments_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                             ("fixture-unknown", run_id, restored["state"]["selected_plan"]["plan_id"], final_version, "type", "fixture-unknown", "fixture", "UNKNOWN", "{}"))
        blocked = client.post(f"/api/v1/runs/{run_id}/requirements", json={"expected_version": restored["version"], "fields": {"budget": 0}})
        assert blocked.status_code == 409 and "尚未确认" in blocked.text
        assert client.get(f"/api/v1/runs/{run_id}").json()["version"] == restored["version"]
