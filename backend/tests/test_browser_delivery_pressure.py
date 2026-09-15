"""When a command's pages close, the run delivers what it read instead of nothing.

Reading is what the turn is for, but a turn that spends its whole allowance and then
reports "reached the browser step budget" with no answer is a worse outcome than an
honest answer built from the pages already on file.
"""

import json
from dataclasses import replace

import pytest
from plango.app import create_app
from plango.graph import (
    BrowserDecision,
    TaskDecision,
    _asks_for_a_browser_step,
    _browser_step_limit,
    _browser_steps_used,
    _delivery_pressure,
    _unchanged_reads,
)
from plango.task import RequirementOutput
from plango_harness.agent.graph import GraphDeps
from test_browser_harness import TOKEN, settings


def graph_nodes(tmp_path, monkeypatch, decision=None):
    import plango.graph as module

    runtime = create_app(settings(tmp_path), token=TOKEN).state.runtime
    if decision is not None:
        runtime.model.structured = decision
    deps = GraphDeps(model=runtime.model, tools=runtime.tools, world=runtime.world_service.provider,
                     planner=None, memory=None, runs=None, action_provider=None)
    actual, nodes = module.build_graph, {}

    def build(deps, *, extension, **kwargs):
        def capture(graph):
            extension(graph)
            nodes["operate"] = graph.nodes["browser_operate"].runnable
            nodes["decide"] = graph.nodes["browser_decide"].runnable
        return actual(deps, extension=capture, **kwargs)

    monkeypatch.setattr(module, "build_graph", build)
    module.build_desktop_graph(runtime, deps, None)
    return runtime, deps, nodes


def test_the_allowance_is_measured_per_command_not_for_the_run_life(tmp_path, monkeypatch):
    _, deps, _ = graph_nodes(tmp_path, monkeypatch)
    assert _browser_step_limit(deps) == 12
    assert _browser_steps_used({"browser_steps": 25, "turn_budget": {"browser_baseline": 13}}) == 12
    assert _browser_steps_used({"browser_steps": 13}) == 13, "a state with no grant still counts from zero"


def test_the_limit_is_configurable(tmp_path, monkeypatch):
    _, deps, _ = graph_nodes(tmp_path, monkeypatch)
    assert _browser_step_limit(replace(deps, max_browser_steps=30)) == 30, "paging through a listing needs more than one page"
    assert _browser_step_limit(replace(deps, max_browser_steps=99, max_tool_calls=4)) == 4, "never more than the run's tool budget"


async def test_an_exhausted_command_hands_over_a_reason_and_spends_nothing(tmp_path, monkeypatch):
    runtime, deps, nodes = graph_nodes(tmp_path, monkeypatch)
    from plango.browser import run_context

    token = run_context.set({"run_id": "pressure", "turn_id": 1, "browser_calls": 0, "places": {}})
    state = {"run_id": "pressure", "turn_id": 1, "browser_steps": 12,
             "turn_budget": {"id": "user:1", "grant_seq": 1, "browser_baseline": 0},
             "tool_call_count": 0, "browser_next": BrowserDecision(operation="extract").model_dump(),
             "browser_task_context": {"mode": "browser", "kind": "task", "operation": "read"}}
    update = await nodes["operate"].ainvoke(state)
    assert update.get("outcome") is None and "browser_steps" not in update
    note = update["browser_task_context"]["tool_results"][-1]
    assert note["tool"] == "browser_step_budget"
    assert "只能用已经读到的资料作答" in note["note"], "the reason has to say what to do instead"
    run_context.reset(token)


def test_a_page_read_twice_unchanged_is_named_as_no_new_information(tmp_path, monkeypatch):
    runtime, deps, _ = graph_nodes(tmp_path, monkeypatch)
    context = {"read_versions": ["v1", "v2", "v2", "v2"]}
    assert _unchanged_reads(context) == 2
    assert _unchanged_reads({"read_versions": ["v1", "v2", "v3"]}) == 0
    assert _unchanged_reads({}) == 0
    pressure = _delivery_pressure({"browser_task_context": context, "browser_steps": 3}, deps)
    assert pressure["tool"] == "browser_page_unchanged" and "连续 3 次读到完全相同的内容" in pressure["note"]


def test_a_progressing_page_is_not_treated_as_stalled(tmp_path, monkeypatch):
    runtime, deps, _ = graph_nodes(tmp_path, monkeypatch)
    assert _delivery_pressure({"browser_task_context": {"read_versions": ["v1", "v2"]}, "browser_steps": 2}, deps) is None


def test_which_decisions_would_spend_a_step():
    assert _asks_for_a_browser_step(TaskDecision(operation="read"))
    assert _asks_for_a_browser_step(TaskDecision(operation="read", browser=BrowserDecision(operation="click", idx=0)))
    assert not _asks_for_a_browser_step(TaskDecision(operation="answer", answer="已读取测试页面"))
    assert not _asks_for_a_browser_step(TaskDecision(operation="plan", requirements=RequirementOutput(clarification_needed=True, clarification_fields=["visit_date"], clarification_question="请确认日期")))


async def test_a_step_asked_for_after_the_note_ends_in_a_delivery_not_a_silent_failure(tmp_path, monkeypatch):
    """The model was told the pages are closed and asked for one anyway."""
    asked = []

    async def still_reading(schema, *, fallback, **kwargs):
        asked.append(json.loads(kwargs["user"]))
        if schema is TaskDecision:
            return TaskDecision(operation="read", browser=BrowserDecision(operation="extract"))
        return fallback

    runtime, deps, nodes = graph_nodes(tmp_path, monkeypatch, still_reading)
    state = {"run_id": "pressure", "turn_id": 1, "browser_steps": 12,
             "turn_budget": {"id": "user:1", "grant_seq": 1, "browser_baseline": 0},
             "tool_call_count": 0, "browser_observation": {},
             "browser_task_context": {"mode": "browser", "kind": "task", "operation": "read", "request": "读一下这家店",
                                      "tool_results": [{"tool": "browser_step_budget", "ok": False, "used": 12, "limit": 12,
                                                        "note": "本次指令的浏览器步数已用尽（12/12）。"}]}}
    update = await nodes["decide"].ainvoke(state)
    assert update["phase"] == "PARTIAL_FAILED" and update["outcome"] == "PARTIAL_FAILED"
    assert "浏览器资料已经读到本次指令的上限" in update["reason"]
    assert "可以让我只读其中一家" in update["reason"], "a stalled run has to say what would unstick it"
    assert len(asked) == 1, "the note buys exactly one more decision, not a second one"
    assert any(row.get("tool") == "browser_step_budget" for row in asked[0]["tool_results"]), "the note was in front of the model"
