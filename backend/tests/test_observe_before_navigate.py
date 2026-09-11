"""A read step looks at the page it was given before leaving for one it invented."""

import json
from datetime import date
from unittest.mock import AsyncMock

import pytest
from plango.app import create_app
from plango.graph import BrowserDecision, TaskDecision, _kept, _page_assembly_results, artifact
from plango.task import RequirementOutput
from plango_harness.agent.contracts import TripSpec
from plango_harness.agent.graph import GraphDeps
from test_browser_harness import TOKEN, settings
from test_offer_applicability import page


def decide_node(tmp_path, monkeypatch, decision):
    return graph_nodes(tmp_path, monkeypatch, decision)["decide"]


def graph_nodes(tmp_path, monkeypatch, decision=None):
    import plango.graph as module

    runtime = create_app(settings(tmp_path), token=TOKEN).state.runtime
    if decision is not None:
        runtime.model.structured = decision if callable(decision) else AsyncMock(return_value=decision)
    deps = GraphDeps(model=runtime.model, tools=runtime.tools, world=runtime.world_service.provider,
                     planner=None, memory=None, runs=None, action_provider=None)
    actual, nodes = module.build_graph, {}

    def build(deps, *, extension, **kwargs):
        def capture(graph):
            extension(graph)
            nodes["decide"] = graph.nodes["browser_decide"].runnable
            nodes["first"] = graph.nodes["browser_first"].runnable
        return actual(deps, extension=capture, **kwargs)

    monkeypatch.setattr(module, "build_graph", build)
    module.build_desktop_graph(runtime, deps, None)
    nodes["decide"].structured = runtime.model.structured
    return nodes


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


async def test_answering_before_any_page_is_read_looks_at_the_current_tab(tmp_path, monkeypatch):
    """A finished-looking answer is the other way to skip the open tab."""
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="answer", answer="精选双人餐售价98元，适用2人。"))
    update = await node.ainvoke(read_state())
    assert update.get("outcome") is None
    assert update.get("execution_outcome") is None
    assert update["browser_next"]["operation"] == "extract"
    assert update["browser_task_context"]["operation"] == "read"
    note = update["browser_task_context"]["tool_results"][-1]
    assert note["tool"] == "observe_current_page" and note["ok"] is True


async def test_confirmed_comparison_constraints_are_kept_when_planning(tmp_path, monkeypatch):
    """A later plan must not treat the already-saved comparison numbers as unknown."""
    observed = page()
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="plan",
        requirements=RequirementOutput(
            goal="整理行程草案", party_size_unknown=True, visit_date_unknown=True,
            clarification_needed=True, clarification_fields=["party_size", "visit_date"],
            clarification_question="人数未确认，不能核算或生成执行计划",
        ),
    ))
    context = read_state()["browser_task_context"]
    update = await node.ainvoke(read_state(
        input_text="整理这份行程草案，保留来源和待核验事项。",
        browser_observation={"ok": True, "outcome": "observed", "snapshot_id": "seen",
                             "url": observed["url"], "command_id": "fixture-command"},
        browser_artifacts=[observed],
        browser_task_context={**context, "request": "整理这份行程草案，保留来源和待核验事项。",
                              "party_size": 3, "visit_date": "2026-09-12", "total_budget": 120,
                              "party_ambiguous": False, "budget_ambiguous": False},
    ))
    output = update["requirement_proposal"]["output"]
    assert output["party_size"] == 3 and output["party_size_unknown"] is False
    assert output["visit_date"] == "2026-09-12" and output["visit_date_unknown"] is False
    assert output["budget"] == 120
    assert "party_size" not in output["clarification_fields"]
    assert "trip_spec" not in update
    assert update["browser_task_context"]["operation"] == "plan"


async def test_ask_with_kept_sources_does_not_require_a_live_tab(tmp_path, monkeypatch):
    """A new turn clears the observation. Kept sources are enough to ask."""
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="ask", question="180是总预算还是每人预算？"))
    update = await node.ainvoke(read_state(
        turn_id=2, browser_observation={},
        browser_artifacts=[{"artifact_id": "page:old", "type": "browser_page",
                            "data": {"text": "材料费每人28.5元"}}]))
    assert update["clarification"]["question"] == "180是总预算还是每人预算？"
    assert update.get("browser_next") is None


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
                                            "url": "https://merchant.invalid/shop/1", "command_id": "c1"},
                       browser_artifacts=[{"artifact_id": "page:c1", "type": "browser_page",
                                           "data": {"text": "套餐 98 元"}}])
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


def test_dom_tables_from_a_snapshot_look_still_assemble_listings():
    """A first look that only snapshotted still has the DOM table it already walked."""
    observation = {
        "ok": True, "command_id": "snapshot-command", "url": "http://127.0.0.1:8765/offer-sample.html",
        "title": "顺风123(观音桥大融城店) · PlanGo 本地测试页面",
        "text": "顺风123(观音桥大融城店)\n地址：观音桥步行街8号附5号大融城6楼6-010\n团购套餐",
        "tables": [{
            "headers": ["团购套餐", "售价", "面值", "适用人数", "使用说明"],
            "rows": [
                ["50元代金券", "¥47", "¥50", "未标注", "随时退；过期自动退。"],
                ["精选双人餐", "¥98", "", "2人", "周一至周日；随时退。"],
                ["招牌冷面鸡", "¥19.9", "", "单品", "周一至周日；随时退。"],
            ],
        }],
    }
    observed = artifact(observation)
    assert [item["name"] for item in observed["data"]["offers"]] == ["50元代金券", "精选双人餐", "招牌冷面鸡"]
    assert [item.get("people") for item in observed["data"]["offers"]] == [None, 2, None]
    assert observed["data"]["places"][0]["name"] == "顺风123(观音桥大融城店)"
    assert observed["data"]["places"][0]["address"] == "观音桥步行街8号附5号大融城6楼6-010"
    rows = _page_assembly_results({
        "browser_observation": observation,
        "browser_artifacts": [observed],
        "browser_task_context": {"tool_results": []},
        "trip_spec": TripSpec(goal="核对当前页套餐", party_size=3, visit_date=date(2026, 9, 12), budget=120),
    })
    assert rows[0]["tool"] == "compare_offers" and rows[0]["ok"] is True
    assert [entry["name"] for entry in rows[0]["result"]["entries"]] == ["50元代金券", "精选双人餐", "招牌冷面鸡"]
    package = rows[0]["result"]["entries"][1]
    assert package["status"] == "ineligible" and any("不能认定" in reason for reason in package["reasons"])
    assert rows[0]["result"]["merchant"]["name"] == "顺风123(观音桥大融城店)"


def test_two_labeled_addresses_do_not_invent_a_shop():
    observation = {
        "ok": True, "command_id": "two-addresses", "url": "https://merchant.invalid/list",
        "title": "青竹餐厅(江北店) · 目录",
        "text": "青竹餐厅(江北店)\n地址：江畔路8号\n白鹭餐厅\n地址：长江路10号",
        "tables": [],
    }
    assert artifact(observation)["data"]["places"] == []


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


async def test_page_read_entry_without_preparation_delivers_the_draft(tmp_path, monkeypatch):
    from plango_harness.agent.contracts import ConstraintCheck, VerifierResult
    from test_preparation_outcome import prepared_state

    nodes = graph_nodes(tmp_path, monkeypatch)
    state, _ = prepared_state()
    plan = state["selected_plan"]
    state["trip_spec"] = state["execution_goal"]["requirements"]
    state["verifier"] = VerifierResult(
        plan_id=plan.plan_id, hard_constraints_pass=True, evidence_complete=False,
        unknown_evidence=[ConstraintCheck(name="queue", kind="unknown", passed=None)])
    state.pop("execution_goal")
    update = await nodes["first"].ainvoke(state)
    assert update["browser_next"]["operation"] == "finish"
    assert update["clarification"]["kind"] == "draft_review"
    assert update["execution_outcome"]["data"]["business_completed"] is False
    assert update.get("outcome") is None


async def test_approved_preparation_still_reads_the_visible_page(tmp_path, monkeypatch):
    from test_preparation_outcome import prepared_state

    nodes = graph_nodes(tmp_path, monkeypatch)
    update = await nodes["first"].ainvoke(prepared_state()[0])
    assert update["browser_next"]["operation"] == "extract"
