"""Controlled restart/current-page binding regression; no live merchant or browser."""

from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import BrowserDecision
from plango.outcomes import read_goal
from plango.world import PageData
from plango_harness.agent.graph import GraphDeps
from test_browser_harness import TOKEN, fixture, settings, wait_for


@pytest.mark.parametrize("target", ["当前页面", "这个页面", "本页", "此网页"])
def test_new_explicit_current_page_read_after_restart_does_not_reuse_previous_tab_or_scope(tmp_path, target):
    config = settings(tmp_path)
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(create_app(config, token=TOKEN), headers=headers) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取当前网页菜单和套餐使用条件", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        wait_for(client, rid, lambda value: bool(value["state"].get("browser_wait")))
        command = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        assert client.post(f"/api/v1/browser/commands/{command['command_id']}/result", json=fixture(command)).status_code == 200
        old = wait_for(client, rid, lambda value: value["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert old["state"]["browser_observation"]["tab_id"] == "fixture-tab"
    app = create_app(config, token=TOKEN)
    place = "雾岚餐厅\n地址：重庆市青竹路8号"
    offer = "双人套餐\n售价98元"

    async def extract(schema, *, fallback, **kwargs):
        if schema is PageData:
            return PageData.model_validate({"places": [{"name": "雾岚餐厅", "address": "重庆市青竹路8号", "quote": place}],
                                           "offers": [{"name": "双人套餐", "price": 98, "people": 2, "quote": offer}]})
        return fallback

    app.state.runtime.model.structured = extract
    with TestClient(app, headers=headers) as client:
        text = f"只读取{target}可见的门店名称、地址和优惠预览，保留售价并列出缺失的规则；不进入App、不购买、不下单。"
        response = client.post(f"/api/v1/runs/{rid}/messages", json={"text": text, "request_id": "new-current-page"})
        assert response.status_code == 202, response.text
        wait_for(client, rid, lambda value: (value["state"].get("browser_wait") or {}).get("command_id") not in {None, command["command_id"]})
        fresh = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        assert fresh["operation"] == "extract" and fresh.get("tab_id") is None
        assert fresh.get("expected_snapshot_id") is None and fresh.get("approved_action_id") is None
        result = {**fixture(fresh), "tab_id": "new-current-tab", "title": "雾岚餐厅", "text": place + "\n团购套餐\n" + offer, "tables": []}
        assert client.post(f"/api/v1/browser/commands/{fresh['command_id']}/result", json=result).status_code == 200
        done = wait_for(client, rid, lambda value: value["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == "SUCCEEDED", done
        assert done["state"]["browser_observation"]["tab_id"] == "new-current-tab"
        assert done["state"]["execution_goal"]["request"] == text
        assert set(done["state"]["execution_goal"]["required_fields"]) == {"merchant", "address", "offers"}
        assert not done["state"].get("action_results")
        events = client.get(f"/api/v1/runs/{rid}/events").json()["events"]
        assert any(event["event_type"] == "RUN_CREATED" and "菜单" in event["payload"]["input_text"] for event in events)


def test_later_budget_edits_keep_the_last_explicit_read_scope_without_deleting_history():
    narrow = "仅读取本页的门店名称、地址和优惠预览，不下单。"
    context = {"mode": "browser", "kind": "extract", "read_kind": "page_read", "request": "读取当前网页完整菜单和套餐使用条件",
               "edits": [narrow, "总预算改成150元，人数改成3人"], "latest": "总预算改成150元，人数改成3人"}
    before = deepcopy(context)
    goal = read_goal({"input_text": context["latest"]}, context)
    assert goal["request"].startswith(narrow) and "150元" in goal["request"]
    assert "菜单" not in goal["request"] and set(goal["required_fields"]) == {"merchant", "address", "offers"}
    assert context == before


async def test_old_paused_read_goal_is_corrected_at_decision_without_a_new_turn_or_budget(tmp_path, monkeypatch):
    import plango.graph as module

    runtime = create_app(settings(tmp_path), token=TOKEN).state.runtime
    runtime.model.structured = AsyncMock(return_value=BrowserDecision(operation="finish"))
    deps = GraphDeps(model=runtime.model, tools=runtime.tools, world=runtime.world_service.provider, planner=None, memory=None, runs=None, action_provider=None)
    actual = module.build_graph
    nodes = {}

    def capture(deps, *, extension, **kwargs):
        def extend(graph):
            extension(graph)
            nodes["decide"] = graph.nodes["browser_decide"].runnable
        return actual(deps, extension=extend, **kwargs)

    monkeypatch.setattr(module, "build_graph", capture)
    module.build_desktop_graph(runtime, deps, None)
    narrow = "只读取当前页面门店名称和地址，不下单。"
    old_request = "读取完整菜单和套餐使用条件"
    quote = "雾岚餐厅 地址：重庆市青竹路8号"
    observation = {"url": "https://fixture.invalid/current", "snapshot_id": "rebound", "tab_id": "new-current", "text": quote}
    state = {"run_id": "fixture", "input_text": narrow, "turn_id": 6, "turn_budget": {"id": "original-budget"},
             "browser_steps": 1, "browser_observation": observation,
             "browser_task_context": {"mode": "browser", "kind": "extract", "read_kind": "page_read", "request": old_request, "edits": [narrow]},
             "execution_goal": {"kind": "menu_read", "source": "browser", "request": old_request, "required_fields": ["menu", "offer_conditions"]},
             "browser_artifacts": [{"artifact_id": "page:rebound", "type": "browser_page", "source": "browser", **observation,
                 "observed_at": datetime.now(timezone.utc).isoformat(), "data": {"text": quote, "places": [{"name": "雾岚餐厅", "address": "重庆市青竹路8号", "quote": quote}]}}]}
    before = deepcopy(state)
    result = await nodes["decide"].ainvoke(state)
    assert result["phase"] == "SUCCEEDED" and result["execution_goal"]["request"] == narrow
    assert set(result["execution_goal"]["required_fields"]) == {"merchant", "address"}
    assert "turn_id" not in result and "turn_budget" not in result
    assert state == before
