"""A read step looks at the page it was given before leaving for one it invented."""

import json
from datetime import date
from unittest.mock import AsyncMock

import pytest
from plango.app import create_app
from plango.graph import (
    BrowserDecision,
    TaskDecision,
    _asks_if_current_card_holds,
    _card_hold_summary,
    _complete_settled_card,
    _kept,
    _page_assembly_results,
    _settle_existing_card,
    artifact,
)
from plango.task import Calculation, RequirementOutput, enforce_delivery_contract
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


@pytest.mark.parametrize("operation,kwargs", [
    ("click", {"idx": 0}),
    ("type", {"idx": 0, "text": "4"}),
])
async def test_unread_write_looks_at_the_current_tab(tmp_path, monkeypatch, operation, kwargs):
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="read", browser=BrowserDecision(operation=operation, **kwargs)))
    update = await node.ainvoke(read_state())
    assert update.get("action_proposal") is None
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


async def test_unusable_draft_review_names_the_validation_error(tmp_path, monkeypatch):
    from plango_harness.agent.contracts import ConstraintCheck, VerifierResult
    from test_preparation_outcome import prepared_state

    nodes = graph_nodes(tmp_path, monkeypatch)
    state, _ = prepared_state()
    state["trip_spec"] = state["execution_goal"]["requirements"]
    state["verifier"] = VerifierResult(
        plan_id="another-plan", hard_constraints_pass=True, evidence_complete=False,
        unknown_evidence=[ConstraintCheck(name="queue", kind="unknown", passed=None)])
    state.pop("execution_goal")
    update = await nodes["first"].ainvoke(state)
    assert update["outcome"] == "PARTIAL_FAILED"
    assert "draft_verifier_plan_mismatch" in update["reason"]
    assert "没有可交付的核验草案" not in update["reason"]
    assert update["trace"][-1]["event"] == "draft_review_unusable"


async def test_approved_preparation_still_reads_the_visible_page(tmp_path, monkeypatch):
    from test_preparation_outcome import prepared_state

    nodes = graph_nodes(tmp_path, monkeypatch)
    update = await nodes["first"].ainvoke(prepared_state()[0])
    assert update["browser_next"]["operation"] == "extract"


def test_sparse_card_edit_settles_without_a_new_origin():
    spec = TripSpec(goal="已有需求卡", visit_date=date(2026, 10, 11), budget=415)
    task = TaskDecision(operation="plan", requirements=RequirementOutput(visit_date=date(2026, 11, 11)))
    settled = _settle_existing_card(task, {"trip_spec": spec, "input_text": "只把日期改成 2026-11-11，其他字段不要动。"})
    assert settled is not None
    assert settled.visit_date == date(2026, 11, 11)
    assert settled.budget == 415


def test_first_plan_and_itinerary_request_do_not_settle_the_card():
    task = TaskDecision(
        operation="plan",
        requirements=RequirementOutput(party_size=3, required_activities=["餐厅"]),
    )
    assert _settle_existing_card(task, {"input_text": "帮我排个行程"}) is None
    spec = TripSpec(goal="已有需求卡", party_size=2)
    assert _settle_existing_card(task, {"trip_spec": spec, "input_text": "按当前卡再排一版"}) is None


def test_confirming_an_existing_card_does_not_replan():
    spec = TripSpec(goal="已有需求卡", party_size=2, location={"name": "渡口码头", "latitude": 31.2, "longitude": 121.4})
    task = TaskDecision(operation="plan", requirements=RequirementOutput(location_name="渡口码头"))
    settled = _settle_existing_card(task, {"trip_spec": spec, "input_text": "关掉再打开后，地点名和人数还在吗？"})
    assert settled is not None
    assert settled.party_size == 2
    assert settled.location.name == "渡口码头"


async def test_sparse_card_edit_completes_from_decide(tmp_path, monkeypatch):
    spec = TripSpec(goal="已有需求卡", visit_date=date(2026, 10, 11), budget=415, per_person_budget=74)
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="plan", requirements=RequirementOutput(visit_date=date(2026, 11, 11))))
    update = await node.ainvoke(read_state(
        input_text="只把日期改成 2026-11-11，其他字段不要动。",
        trip_spec=spec,
        previous_spec=spec,
        browser_observation={"ok": True, "outcome": "observed", "snapshot_id": "seen",
                             "url": "https://fixture.invalid/card", "command_id": "card"},
        browser_artifacts=[{
            "artifact_id": "page:card", "type": "browser_page", "source": "browser",
            "snapshot_id": "seen", "url": "https://fixture.invalid/card",
            "data": {"text": "需求卡当前日期 2026-10-11，总预算 415。"},
        }],
    ))
    assert update["outcome"] == "SUCCEEDED"
    assert update["trip_spec"].visit_date == date(2026, 11, 11)
    assert update["trip_spec"].budget == 415


def test_confirming_card_ignores_refresh_and_read():
    spec = TripSpec(
        goal="已有需求卡",
        party_size=3,
        location={"name": "云阶码头", "latitude": 31.2, "longitude": 121.4},
    )
    # The turn reads the card back, so the sparse proposal states no new value.
    # The list is wording on purpose: `_REOPEN_TURN` used to decide this, and its
    # last two entries never appeared in it.
    for text in (
        "关掉再打开后，地点名和人数还在吗？",
        "重启之后人数还保存着吗？",
        "重新打开后核对一下日期。",
        "关了再开，地点名有没有丢？",
        "退出程序再进来后，硬约束和日期还在吗？",
        "重新进入程序，出行方式和路程上限有没有丢？",
    ):
        state = {"trip_spec": spec, "input_text": text}
        assert _asks_if_current_card_holds(state, RequirementOutput()), text
        assert _asks_if_current_card_holds(
            state, RequirementOutput(party_size_unknown=True, visit_date_unknown=True)
        ), text
    for mutating in ("把人数改成 5，其余保持原样。", "只改日期。"):
        state = {"trip_spec": spec, "input_text": mutating}
        assert not _asks_if_current_card_holds(state, RequirementOutput(party_size=5)), mutating
        assert not _asks_if_current_card_holds(state, RequirementOutput(visit_date=date(2026, 11, 11))), mutating
        assert not _asks_if_current_card_holds(state, RequirementOutput(clear_budget=True)), mutating
    # A refresh request asks for the page again, so it is not answered from the card.
    refresh = TaskDecision(
        operation="plan", requirements=RequirementOutput(refresh_sources=True, location_name="云阶码头")
    )
    reread = {"trip_spec": spec, "input_text": "重新打开后核对一下人数。"}
    assert not _asks_if_current_card_holds(reread, refresh.requirements)
    plain = TaskDecision(operation="plan", requirements=RequirementOutput(location_name="云阶码头"))
    assert _asks_if_current_card_holds(reread, plain.requirements)
    settled = _settle_existing_card(plain, reread)
    assert settled is not None
    assert settled.party_size == 3


async def test_confirming_read_completes_from_the_seeded_card(tmp_path, monkeypatch):
    spec = TripSpec(
        goal="已有需求卡",
        party_size=3,
        location={"name": "云阶码头", "latitude": 31.2, "longitude": 121.4},
    )
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="read", browser=BrowserDecision(operation="snapshot")))
    update = await node.ainvoke(read_state(
        input_text="关掉再打开后，地点名和人数还在吗？",
        trip_spec=spec,
        previous_spec=spec,
    ))
    assert update["outcome"] == "SUCCEEDED"
    assert update["trip_spec"].party_size == 3
    assert update.get("browser_next", {}).get("operation") != "snapshot"
    summary = update["execution_outcome"]["summary"]
    assert "人数是 3" in summary
    assert "地点名是 云阶码头" in summary
    assert "冻结" not in summary and "持久化" not in summary


async def test_confirming_answer_does_not_deliver_model_essay(tmp_path, monkeypatch):
    spec = TripSpec(
        goal="已有需求卡",
        party_size=3,
        location={"name": "云阶码头", "latitude": 31.2, "longitude": 121.4},
    )
    node = decide_node(tmp_path, monkeypatch, TaskDecision(
        operation="answer",
        answer="冻结世界无法确认重启后是否持久化，当前页只是关闭前快照。",
    ))
    update = await node.ainvoke(read_state(
        input_text="关掉再打开后，地点名和人数还在吗？",
        trip_spec=spec,
        previous_spec=spec,
    ))
    assert update["outcome"] == "SUCCEEDED"
    summary = update["execution_outcome"]["summary"]
    assert "人数是 3" in summary
    assert "地点名是 云阶码头" in summary
    assert "冻结" not in summary
    assert "持久化" not in summary


def test_card_hold_summary_skips_trip_spec_defaults():
    spec = TripSpec(goal="已有需求卡", party_size=3, budget=280)
    summary = _card_hold_summary(spec)
    assert "人数是 3" in summary
    assert "总预算是 280" in summary
    assert "出行方式" not in summary
    assert "时长" not in summary


def test_card_hold_summary_skips_mirrored_search_radius():
    spec = TripSpec(goal="已有需求卡", travel_mode="transit", max_distance_km=3, search_radius_km=3)
    summary = _card_hold_summary(spec)
    assert "路程上限是 3" in summary
    assert "出行方式是 公交" in summary
    assert "搜索半径" not in summary
    distinct = TripSpec(goal="已有需求卡", search_radius_km=1.6, per_person_budget=85)
    assert "搜索半径是 1.6" in _card_hold_summary(distinct)


def test_itinerary_card_keeps_planning_after_a_scalar_write():
    spec = TripSpec(goal="已有行程", budget=400, required_activities=["展览", "餐厅"])
    task = TaskDecision(operation="plan", requirements=RequirementOutput(budget=500))
    state = {"trip_spec": spec, "input_text": "预算改为500元"}
    settled = _settle_existing_card(task, state)
    assert settled is not None and settled.budget == 500
    assert _complete_settled_card(task, state, settled) is False


def test_answer_echoing_the_user_supplied_figures_is_a_delivery():
    """The run that answers a clarification is not the turn that stated the number."""
    task = TaskDecision(operation="answer", answer="已按3人、总预算180元继续。")
    out = enforce_delivery_contract(
        task,
        {
            "current_request": "总预算",
            "original_request": "我们3人，预算100，算已知材料费。",
            "question_being_answered": "180是总预算还是每人预算？",
            "sources": [{"records": [{"text": "材料费每人28.5元；配送费用尚未给出。"}]}],
            "tool_results": [],
        },
    )
    assert out.answer.startswith("已按")


def _quantity_page_state(**extra):
    observed = {
        "artifact_id": "page:seen",
        "type": "browser_page",
        "source": "browser",
        "url": "https://fixture.invalid/ledger",
        "snapshot_id": "seen",
        "data": {"text": "预收 240 元。扣留 50 元。退还金额按两数相减。"},
    }
    context = read_state()["browser_task_context"]
    return read_state(
        input_text="退还后还能拿回多少？",
        browser_observation={
            "ok": True,
            "outcome": "observed",
            "snapshot_id": "seen",
            "url": "https://fixture.invalid/ledger",
            "command_id": "seen",
        },
        browser_artifacts=[observed],
        browser_task_context={**context, "request": "退还后还能拿回多少？", "tool_results": []},
        **extra,
    )


async def test_quantity_answer_is_retried_until_calculate(tmp_path, monkeypatch):
    calls = []

    async def structured(schema, *, system, user, fallback):
        calls.append(json.loads(user))
        if len(calls) < 3:
            # Two stated figures, one of which the page never prints: the answer is
            # a derivation, so the calculator is what has to produce it.
            return TaskDecision(operation="answer", answer="预收 240 元扣掉 50 元，还能拿回 190 元。")
        return TaskDecision(
            operation="calculate",
            calculations=[Calculation(id="remain", operation="subtract", operands=["240", "50"])],
        )

    node = decide_node(tmp_path, monkeypatch, structured)
    update = await node.ainvoke(_quantity_page_state())
    assert len(calls) == 3
    assert calls[1]["required_operation"] == "calculate"
    assert update.get("outcome") != "PARTIAL_FAILED"
    results = update["browser_task_context"]["tool_results"]
    assert any(row.get("scope") == "arithmetic_only" and row.get("ok") and row.get("value") == "190" for row in results)


async def test_quantity_answer_gives_up_after_calculate_retries(tmp_path, monkeypatch):
    calls = []

    async def structured(schema, *, system, user, fallback):
        calls.append(user)
        return TaskDecision(operation="answer", answer="预收 240 元扣掉 50 元，还能拿回 190 元。")

    node = decide_node(tmp_path, monkeypatch, structured)
    update = await node.ainvoke(_quantity_page_state())
    assert len(calls) == 4
    assert update["outcome"] == "PARTIAL_FAILED"
    assert update["trace"][-1]["event"] == "task_decision_unusable"
    assert update["trace"][-1]["payload"]["error"] == "quantity_requires_calculate"
