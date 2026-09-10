"""A read step looks at the page it was given before leaving for one it invented."""

import json
from datetime import date
from unittest.mock import AsyncMock

import pytest
from plango.app import create_app
from plango.graph import BrowserDecision, TaskDecision, _kept, _page_assembly_results
from plango_harness.agent.contracts import TripSpec
from plango_harness.agent.graph import GraphDeps
from test_browser_harness import TOKEN, settings
from test_offer_applicability import page


def decide_node(tmp_path, monkeypatch, decision):
    import plango.graph as module

    runtime = create_app(settings(tmp_path), token=TOKEN).state.runtime
    runtime.model.structured = decision if callable(decision) else AsyncMock(return_value=decision)
    deps = GraphDeps(model=runtime.model, tools=runtime.tools, world=runtime.world_service.provider,
                     planner=None, memory=None, runs=None, action_provider=None)
    actual, nodes = module.build_graph, {}

    def build(deps, *, extension, **kwargs):
        def capture(graph):
            extension(graph)
            nodes["decide"] = graph.nodes["browser_decide"].runnable
        return actual(deps, extension=capture, **kwargs)

    monkeypatch.setattr(module, "build_graph", build)
    module.build_desktop_graph(runtime, deps, None)
    return nodes["decide"]


def read_state(**extra):
    return {"run_id": "observe", "user_id": "fixture", "turn_id": 1, "browser_steps": 1,
            "input_text": "看看这家店周五中午的套餐怎么算", "browser_observation": {}, "browser_artifacts": [],
            "browser_task_context": {"mode": "browser", "kind": "task", "operation": "read",
                                     "request": "看看这家店周五中午的套餐怎么算", "edits": [], "tool_results": []},
            **extra}


async def test_invented_address_is_replaced_by_reading_the_current_page(tmp_path, monkeypatch):
    """The user gave no address, so a search engine is a guess.

    Whatever they already had open is the one thing we know is relevant to the request,
    and leaving before looking at it throws that away for nothing.
    """
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="read", browser=BrowserDecision(operation="navigate", url="https://map.baidu.com/search/foo")))
    update = await node.ainvoke(read_state())
    assert update["browser_next"]["operation"] == "extract"
    assert update["browser_next"]["url"] is None
    note = update["browser_task_context"]["tool_results"][-1]
    assert note["tool"] == "observe_current_page" and note["ok"] is True
    assert note["requested_url"] == "https://map.baidu.com/search/foo", "The model still learns what was skipped"


async def test_asking_before_any_page_is_read_looks_at_the_current_tab(tmp_path, monkeypatch):
    """Asking the user to paste a document is the other way to skip the open tab."""
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="ask", question="请提供这两份步行记录的具体信息。"))
    update = await node.ainvoke(read_state())
    assert update.get("clarification") is None
    assert update["browser_next"]["operation"] == "extract"
    assert update["browser_task_context"]["operation"] == "read"
    note = update["browser_task_context"]["tool_results"][-1]
    assert note["tool"] == "observe_current_page" and note["ok"] is True


async def test_a_draft_edit_may_still_ask_the_user(tmp_path, monkeypatch):
    """A selected place is already the task; looking at a blank tab does not help."""
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="ask", question="180元是总额还是人均？"))
    state = read_state(selected_poi={"place_id": "fixture:shop", "name": "斑榕砂锅铺"})
    update = await node.ainvoke(state)
    assert update["clarification"]["question"] == "180元是总额还是人均？"
    assert update.get("browser_next") is None


async def test_an_address_the_user_supplied_is_opened_directly(tmp_path, monkeypatch):
    target = "https://merchant.invalid/shop/77"
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="read", browser=BrowserDecision(operation="navigate", url=target)))
    state = read_state()
    state["input_text"] = "打开 " + target + " 看看套餐"
    state["browser_task_context"]["request"] = state["input_text"]
    update = await node.ainvoke(state)
    assert update["browser_next"]["operation"] == "navigate"
    assert update["browser_next"]["url"] == target


async def test_an_address_the_user_did_not_write_is_not_opened_after_a_page_is_observed(tmp_path, monkeypatch):
    """Looking once does not authorize leaving for a guessed search page."""
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="read", browser=BrowserDecision(operation="navigate", url="https://other.invalid/next")))
    state = read_state(browser_observation={"ok": True, "outcome": "observed", "snapshot_id": "seen",
                                            "url": "https://merchant.invalid/shop/1", "command_id": "c1"})
    update = await node.ainvoke(state)
    assert update["browser_next"]["operation"] == "extract"
    assert update["browser_next"]["url"] is None
    assert update["browser_task_context"]["tool_results"][-1]["requested_url"] == "https://other.invalid/next"


@pytest.mark.parametrize("state,results,expected", [
    ({}, [], "，本轮没有读到资料也没有完成计算"),
    ({}, [{"scope": "arithmetic_only", "ok": False}], "，本轮没有读到资料也没有完成计算"),
    ({"browser_artifacts": [{"artifact_id": "page:1"}]}, [], "，本轮没有读到资料也没有完成计算"),
    ({"browser_observation": {"ok": True, "command_id": "1"},
      "browser_artifacts": [{"artifact_id": "page:1"}]}, [], "，已保留读到的资料"),
    ({}, [{"scope": "arithmetic_only", "ok": True}], "，已保留计算结果"),
    ({"browser_observation": {"ok": True, "command_id": "1"},
      "browser_artifacts": [{"artifact_id": "page:1"}]}, [{"scope": "arithmetic_only", "ok": True}],
     "，已保留读到的资料和计算结果"),
])
def test_a_failure_notice_names_only_what_the_turn_obtained(state, results, expected):
    """Claiming retained sources and calculations after reading and computing nothing is
    a false statement about our own state, and it hides the real defect from the user."""
    assert _kept(state, results) == expected


def test_current_page_offers_are_assembled_for_the_next_decision():
    observed = page()
    state = {
        "browser_observation": {"ok": True, "command_id": "fixture-command"},
        "browser_artifacts": [observed],
        "browser_task_context": {"tool_results": []},
        "trip_spec": TripSpec(goal="核对当前页套餐", party_size=2, visit_date=date(2026, 9, 10), budget=120),
    }
    rows = _page_assembly_results(state)
    assert rows[0]["tool"] == "compare_offers" and rows[0]["ok"] is True
    entry = rows[0]["result"]["entries"][0]
    assert entry["listed_price"] == 98 and entry["name"]


async def test_assembled_offers_reach_the_decision_prompt(tmp_path, monkeypatch):
    captured = []

    async def actor(schema, **kwargs):
        captured.append(json.loads(kwargs["user"]))
        return TaskDecision(operation="answer", answer="已按页面核对套餐标价。")

    observed = page()
    node = decide_node(tmp_path, monkeypatch, actor)
    state = read_state(
        browser_observation={"ok": True, "outcome": "observed", "snapshot_id": "seen",
                             "url": observed["url"], "command_id": "fixture-command"},
        browser_artifacts=[observed],
        trip_spec=TripSpec(goal="核对当前页套餐", party_size=2, visit_date=date(2026, 9, 10), budget=120),
    )
    update = await node.ainvoke(state)
    assert update.get("outcome") == "SUCCEEDED"
    assert captured[0]["tool_results"][0]["tool"] == "compare_offers"
    assert captured[0]["tool_results"][0]["result"]["entries"][0]["listed_price"] == 98
