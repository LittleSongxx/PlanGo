"""A read step looks at the page it was given before leaving for one it invented."""

from unittest.mock import AsyncMock

import pytest
from plango.app import create_app
from plango.graph import BrowserDecision, TaskDecision, _kept
from plango_harness.agent.graph import GraphDeps
from test_browser_harness import TOKEN, settings


def decide_node(tmp_path, monkeypatch, decision):
    import plango.graph as module

    runtime = create_app(settings(tmp_path), token=TOKEN).state.runtime
    runtime.model.structured = AsyncMock(return_value=decision)
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


async def test_navigation_is_allowed_once_a_page_has_been_observed(tmp_path, monkeypatch):
    """The rule is about looking first, not about refusing to navigate."""
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="read", browser=BrowserDecision(operation="navigate", url="https://other.invalid/next")))
    state = read_state(browser_observation={"ok": True, "outcome": "observed", "snapshot_id": "seen",
                                            "url": "https://merchant.invalid/shop/1", "command_id": "c1"})
    update = await node.ainvoke(state)
    assert update["browser_next"]["operation"] == "navigate"
    assert update["browser_next"]["url"] == "https://other.invalid/next"


@pytest.mark.parametrize("state,results,expected", [
    ({}, [], "，本轮没有读到资料也没有完成计算"),
    ({}, [{"scope": "arithmetic_only", "ok": False}], "，本轮没有读到资料也没有完成计算"),
    ({"browser_artifacts": [{"artifact_id": "page:1"}]}, [], "，已保留读到的资料"),
    ({}, [{"scope": "arithmetic_only", "ok": True}], "，已保留计算结果"),
    ({"browser_artifacts": [{"artifact_id": "page:1"}]}, [{"scope": "arithmetic_only", "ok": True}],
     "，已保留读到的资料和计算结果"),
])
def test_a_failure_notice_names_only_what_the_turn_obtained(state, results, expected):
    """Claiming retained sources and calculations after reading and computing nothing is
    a false statement about our own state, and it hides the real defect from the user."""
    assert _kept(state, results) == expected
