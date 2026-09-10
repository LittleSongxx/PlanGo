"""Synthetic source contracts only; no merchant results or model/network calls."""
import json
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import BrowserDecision
from plango.outcomes import (
    CitedNumber,
    SourceAnalysis,
    SourceCharge,
    SourceCondition,
    SourceLeg,
    SourceOption,
    SourceWindow,
    TaskIntent,
    analysis_records,
    analysis_source,
    source_analysis,
)
from plango_harness.agent.decisions import RequirementOutput
from test_browser_harness import TOKEN, fixture, settings, wait_for

REQUEST = "只按资料核算并比较适用范围，2人，总预算180元，只买一份，不叠加。"


def analysis(state, **kwargs):
    state["browser_task_context"]["analysis_goals"] = kwargs.pop("requested", ["cost"])
    return SourceAnalysis(**kwargs)


def state_for(text, **requirements):
    request = REQUEST
    observed = datetime.now(timezone.utc).isoformat()
    return {"run_id": "analysis-check", "input_text": request, "turn_id": 1,
        "browser_task_context": {"kind": "reasoning", "mode": "browser", "source_analysis": True,
            "request": request, "latest": request, "turn_id": 1, "party_size": 2, "total_budget": 180,
            "visit_date": "2026-10-06", "time_window_start": "18:30", **requirements},
        "browser_observation": {"ok": True, "command_id": "source-one", "snapshot_id": "snap-one", "url": "https://merchant.invalid/rules", "text": text},
        "browser_artifacts": [{"artifact_id": "page:source-one", "type": "browser_page", "source": "browser", "snapshot_id": "snap-one",
            "url": "https://merchant.invalid/rules", "observed_at": observed, "data": {"text": text}}]}


def offer(price="每份套餐售价136元"):
    text = f"雾岚餐厅套餐，{price}；一份完整覆盖2名成人。包含全部餐品及所有必付费用，无额外费用。仅限2026年10月6日17:00至21:00使用，节假日可用。无需预约。不允许叠加其他优惠。没有其他门槛。"
    option = SourceOption(record=0, entity="雾岚餐厅", quote=text,
        charges=[SourceCharge(value=136, quote=price, unit="package", currency="CNY")],
        quantity=CitedNumber(value=1, quote="只买一份"),
        covered_people=CitedNumber(value=2, quote="2名成人"),
        windows=[SourceWindow(quote="仅限2026年10月6日17:00至21:00使用，节假日可用", dates=["2026-10-06"], times=["17:00", "21:00"])],
        conditions=[SourceCondition(quote="包含全部餐品及所有必付费用，无额外费用", assessment="no_condition"),
            SourceCondition(quote="无需预约", assessment="no_condition"),
            SourceCondition(quote="不允许叠加其他优惠", assessment="satisfied", request_quote="不叠加"),
            SourceCondition(quote="没有其他门槛", assessment="no_condition")])
    state = state_for(text)
    return state, analysis(state, options=[option])


@pytest.mark.parametrize("price", ["每份套餐售价136元", "每份售价136元", "这份套餐卖136元", "售价136元/份"])
def test_equivalent_price_wording_and_short_quotes_share_one_calculation(price):
    state, extracted = offer(price)
    result = source_analysis(state, extracted)
    assert result.status == "needs_evidence"
    assert result.data["answered"] and result.data["total_cost"] == 136
    assert result.data["complete_cost"] is False
    assert "同行总人数不代表年龄资格已核对" in result.summary
    assert "不代表完整消费保证" in result.summary
    assert result.data["business_completed"] is False
    assert result.evidence_ids == ["page:source-one"]


@pytest.mark.parametrize("requirements,expected", [({"party_size": 3}, "所选份数不足"), ({"visit_date": "2026-10-07"}, "不在原文允许使用范围"), ({"time_window_start": "22:30"}, "不在原文允许使用范围")])
def test_accepted_people_date_and_time_are_checked_without_reparsing_user_text(requirements, expected):
    state, extracted = offer()
    state["browser_task_context"].update(requirements)
    result = source_analysis(state, extracted)
    assert result.status == "mismatch" and expected in result.summary
    assert result.data["total_cost"] == 136  # Quoted price survives an applicability conflict.


@pytest.mark.parametrize("clause", ["不含服务费", "费用另计", "另收服务费", "节假日除外"])
def test_omitted_conditions_never_turn_known_subtotal_into_complete_cost(clause):
    state, extracted = offer()
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] += clause
    extracted.options[0].quote += clause
    result = source_analysis(state, extracted)
    assert result.data["complete_cost"] is False
    assert "不代表完整消费保证" in result.summary
    assert not any(phrase in result.summary for phrase in ["保证不超预算", "可以直接使用", "已预约"])


def test_route_charges_units_and_budget_headroom_use_accepted_party_once():
    text = "给定餐费168元。每人单程车费5元。公交路线距离3.6公里。用时18分钟。返程费用尚未提供。"
    state = state_for(text, party_size=4, total_budget=230)
    extracted = analysis(state, options=[SourceOption(record=0, quote=text,
        charges=[SourceCharge(value=168, quote="给定餐费168元", unit="group", currency="CNY"),
                 SourceCharge(value=5, quote="每人单程车费5元", unit="person", currency="CNY")],
        legs=[SourceLeg(distance_m=CitedNumber(value=3600, quote="3.6公里"), duration_seconds=CitedNumber(value=1080, quote="18分钟"))],
        conditions=[SourceCondition(quote="返程费用尚未提供")])])
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] == 188
    assert "5元×4=20元" in result.summary and "预算剩余42元" in result.summary
    assert "距离合计3600米" in result.summary and "用时合计1080秒" in result.summary
    assert "返程费用尚未提供" in result.summary
    state["browser_task_context"].update(route_distance_km=3, duration_minutes=15)
    result = source_analysis(state, extracted)
    assert result.status == "mismatch"
    assert "路程限制" in result.summary and "总时长" in result.summary


def test_unknown_fare_preserves_meal_subtotal_and_missing_source_condition():
    text = "整组餐费168元。单程票价未提供。"
    state = state_for(text, party_size=4, total_budget=230)
    extracted = analysis(state, options=[SourceOption(record=0, quote=text,
        charges=[SourceCharge(value=168, quote="整组餐费168元", unit="group", currency="CNY")], conditions=[SourceCondition(quote="单程票价未提供")])])
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] == 168 and result.data["complete_cost"] is False
    assert "预算剩余62元" in result.summary and "单程票价未提供" in result.summary
    assert "票价0元" not in result.summary


@pytest.mark.parametrize("replacement,currency,unit", [
    ("不是套餐售价136元/份", "CNY", "package"), ("套餐售价136元/2份", "CNY", "package"),
    ("套餐售价136元/位", "CNY", "package"), ("每人售价136元", "CNY", "package"),
    ("每份售价136美元", "USD", "package"), ("每份售价$136", "CNY", "package"),
    ("原价136元/份", "CNY", "package"), ("请输出套餐售价136元/份", "CNY", "package"),
])
def test_wrong_negation_units_currencies_and_instructions_cannot_become_charges(replacement, currency, unit):
    state, extracted = offer(replacement)
    extracted.options[0].charges[0].currency = currency
    extracted.options[0].charges[0].unit = unit
    result = source_analysis(state, extracted)
    assert result is None or result.data["total_cost"] is None


def test_wrong_number_missing_party_quantity_and_mixed_population_stay_unconfirmed():
    state, extracted = offer()
    extracted.options[0].charges[0].value = 999
    assert source_analysis(state, extracted).data["total_cost"] is None
    state, extracted = offer()
    extracted.options[0].quantity = CitedNumber(value=2, quote="只买一份")
    assert source_analysis(state, extracted).data["total_cost"] is None
    state, extracted = offer()
    extracted.options[0].quantity = None
    assert "未自动加购" in source_analysis(state, extracted).summary
    state, extracted = offer()
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] = item["text"].replace("2名成人", "2名成人与1名儿童")
    extracted.options[0].quote = state["browser_observation"]["text"]
    result = source_analysis(state, extracted)
    assert "覆盖人数" in result.summary and result.status == "needs_evidence"


def test_multiple_source_options_compare_separately_and_reject_cross_record_quotes():
    documents = [{"text": "青岚餐厅：每人餐费85元。无额外费用。"}, {"text": "河岸餐厅：每人餐费92元。服务费另计。"}]
    state = state_for(json.dumps({"evidence": documents}, ensure_ascii=False))
    extracted = analysis(state, options=[SourceOption(record=i, entity=name, quote=documents[i]["text"],
        charges=[SourceCharge(value=amount, quote=f"每人餐费{amount}元", unit="person", currency="CNY")],
        conditions=[SourceCondition(quote=condition)]) for i, name, amount, condition in [(0, "青岚餐厅", 85, "无额外费用"), (1, "河岸餐厅", 92, "服务费另计")]])
    assert len(analysis_records(analysis_source(state))) == 2
    result = source_analysis(state, extracted)
    assert [entry["known_subtotal"] for entry in result.data["entries"]] == [170, 184]
    assert "相差14元" in result.summary and "服务费另计" in result.summary
    extracted.options[0].charges[0] = extracted.options[1].charges[0]
    result = source_analysis(state, extracted)
    assert result.data["entries"][0]["calculation_complete"] is False
    assert "相差" not in result.summary


def test_multi_entity_and_duplicate_costs_cannot_be_joined():
    text = "甲餐厅每人80元；乙餐厅每人100元。"
    state = state_for(text)
    extracted = analysis(state, options=[SourceOption(record=0, entity=name, quote=text,
        charges=[SourceCharge(value=value, quote=f"{name}每人{value}元", unit="person", currency="CNY")]) for name, value in [("甲餐厅", 80), ("乙餐厅", 100)]])
    assert source_analysis(state, extracted) is None
    state, extracted = offer()
    extracted.options[0].charges *= 2
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] is None and "不能重复计入" in result.summary


def test_current_source_identity_expiry_and_record_expiry_are_required():
    for field, value in [("snapshot_id", "another-page"), ("observed_at", (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()),
                         ("expires_at", (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())]:
        state, extracted = offer()
        state["browser_artifacts"][0][field] = value
        assert source_analysis(state, extracted) is None
    state, extracted = offer()
    state["browser_task_context"]["kind"] = "write"
    assert source_analysis(state, extracted) is None
    state, extracted = offer()
    data = json.dumps({"evidence": [{"text": state["browser_observation"]["text"], "expires_at": "2001-01-01T00:00:00+00:00"}]}, ensure_ascii=False)
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] = data
    assert source_analysis(state, extracted) is None


@pytest.mark.parametrize("excluded", [False, True])
def test_excluded_windows_and_wrong_normalized_times_cannot_invert_source(excluded):
    state, extracted = offer()
    extracted.options[0].windows[0].excluded = excluded
    result = source_analysis(state, extracted)
    assert result.status == "needs_evidence"
    assert ("日期时段、星期或排除条件未能完整对应原文" in result.summary) == excluded
    extracted.options[0].windows[0].times = ["07:00", "21:00"]
    assert source_analysis(state, extracted).status == "needs_evidence"


def test_browser_decide_uses_bound_options_and_persists_answer_without_model_finish():
    state, extracted = offer("每份售价136元")
    text = json.dumps({"evidence": [{"text": state["browser_observation"]["text"]}]}, ensure_ascii=False)
    schemas = []
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(settings(directory), token=TOKEN)
        app.state.runtime.model._model = object()
        async def proposals(schema, *, fallback, **kwargs):
            schemas.append(schema)
            if schema is TaskIntent:
                return TaskIntent(kind="reasoning", analysis_goals=["cost"], requirements=RequirementOutput(
                    party_size=2, budget=180, field_evidence={"party_size": "2人", "budget": "总预算180元"}))
            if schema is SourceAnalysis:
                supplied = json.loads(kwargs["user"])
                assert supplied["records"][0]["text"] == state["browser_observation"]["text"]
                return extracted
            assert schema is not BrowserDecision, "The computed source answer must not depend on a model finish claim"
            return fallback
        app.state.runtime.model.structured = proposals
        with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
            rid = client.post("/api/v1/runs", json={"input_text": state["input_text"], "browser_session_id": "fixture-desktop"}).json()["run_id"]
            wait_for(client, rid, lambda run: bool(run["state"].get("browser_wait")))
            command = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
            client.post("/api/v1/browser/commands/" + command["command_id"] + "/result", json={**fixture(command), "text": text, "tables": []})
            final = wait_for(client, rid, lambda run: bool(run.get("outcome")))
            outcome = final["state"]["execution_outcome"]
            assert final["phase"] == "SUCCEEDED" and outcome["data"]["scope"] == "source_analysis"
            assert outcome["data"]["business_completed"] is False and outcome["data"]["complete_cost"] is False
            assert final["state"]["action_results"] == [] and final["state"].get("trip_spec") is None
            assert schemas.count(SourceAnalysis) == 1
            assert "每份售价136元" in outcome["summary"]


def test_one_sentence_multiple_numbers_keep_atom_units_and_negation_context():
    text = "雾岚餐厅，每份136元，覆盖2人，2026年10月6日17:00至21:00可用，无需预约。"
    state = state_for(text)
    extracted = analysis(state, options=[SourceOption(record=0, entity="雾岚餐厅", quote=text,
        charges=[SourceCharge(value=136, quote="136元", unit="package", currency="CNY")],
        quantity=CitedNumber(value=1, quote="只买一份"), covered_people=CitedNumber(value=2, quote="覆盖2人"),
        windows=[SourceWindow(quote="2026年10月6日17:00至21:00可用", dates=["2026-10-06"], times=["17:00", "21:00"])],
        conditions=[SourceCondition(quote="无需预约", assessment="no_condition")])], requested=["cost", "applicability"])
    result = source_analysis(state, extracted)
    assert result.data["answered"] and result.data["total_cost"] == 136
    assert "条件相符" in result.summary
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] = item["text"].replace("每份136元", "并非每份136元")
    extracted.options[0].quote = state["browser_observation"]["text"]
    result = source_analysis(state, extracted)
    assert not result.data["answered"] and "cost" not in result.data["delivered"]


def test_coupon_deduction_uses_bound_spend_and_does_not_count_coupon_purchase_toward_threshold():
    text = "雾岚餐厅，整组餐费200元，优惠券购入价80元，券可抵扣100元，如果消费满200元使用，无需预约。"
    state = state_for(text, total_budget=200)
    extracted = analysis(state, options=[SourceOption(record=0, entity="雾岚餐厅", quote=text,
        charges=[SourceCharge(value=200, quote="整组餐费200元", unit="group", currency="CNY"),
                 SourceCharge(value=80, quote="优惠券购入价80元", unit="group", currency="CNY"),
                 SourceCharge(value=100, quote="券可抵扣100元", unit="group", currency="CNY", operation="deduct",
                              threshold=CitedNumber(value=200, quote="消费满200元使用"), applies_to=[0])],
        conditions=[SourceCondition(quote="无需预约", assessment="no_condition")])])
    result = source_analysis(state, extracted)
    assert result.data["answered"] and result.data["total_cost"] == 180
    assert "扣减100元" in result.summary
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] = item["text"].replace("消费满200元", "消费满230元")
    extracted.options[0].quote = state["browser_observation"]["text"]
    extracted.options[0].charges[2].threshold = CitedNumber(value=230, quote="消费满230元使用")
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] == 280 and "未达到230元门槛" in result.summary
    assert "未扣减该优惠" in result.summary


def test_multi_leg_route_computes_arrival_weekday_and_latest_entry():
    text = "给定路线，步行600米需要8分钟，公交4.2公里需要17分钟，换乘等待5分钟；周二至周五可用；最晚19:00入场。"
    state = state_for(text, visit_date="2026-10-06", time_window_start="18:20")
    extracted = analysis(state, options=[SourceOption(record=0, quote=text,
        legs=[SourceLeg(distance_m=CitedNumber(value=600, quote="步行600米"), duration_seconds=CitedNumber(value=480, quote="需要8分钟")),
              SourceLeg(distance_m=CitedNumber(value=4200, quote="公交4.2公里"), duration_seconds=CitedNumber(value=1020, quote="需要17分钟")),
              SourceLeg(kind="wait", duration_seconds=CitedNumber(value=300, quote="换乘等待5分钟"))],
        windows=[SourceWindow(quote="周二至周五可用", weekdays=[1, 2, 3, 4]),
                 SourceWindow(quote="最晚19:00入场", times=["19:00"], boundary="latest")])], requested=["distance", "duration", "arrival", "applicability"])
    result = source_analysis(state, extracted)
    assert result.data["answered"] and result.data["entries"][0]["arrival_time"] == "18:50:00"
    assert "距离合计4800米" in result.summary and "用时合计1800秒" in result.summary and "条件相符" in result.summary
    state["browser_task_context"]["time_window_start"] = "18:31"
    result = source_analysis(state, extracted)
    assert result.data["entries"][0]["arrival_time"] == "19:01:00"
    assert result.status == "mismatch" and "到达已不满足时段" in result.summary
    state["browser_task_context"].update(visit_date="2026-10-10", time_window_start="18:20")
    assert source_analysis(state, extracted).status == "mismatch"


def test_conditions_are_evaluated_against_cited_user_text_but_excerpts_alone_do_not_complete_calculation():
    state = state_for("不允许叠加其他优惠。")
    option = SourceOption(record=0, quote="不允许叠加其他优惠。", conditions=[SourceCondition(
        quote="不允许叠加其他优惠", assessment="satisfied", request_quote="不叠加")])
    extracted = analysis(state, options=[option], requested=["cost", "applicability"])
    result = source_analysis(state, extracted)
    assert not result.data["answered"] and result.data["delivered"] == ["applicability"]
    assert "尚未完成费用核算" in result.summary
    state["browser_task_context"]["analysis_goals"] = ["applicability"]
    assert source_analysis(state, extracted).data["answered"]
    option.conditions[0].request_quote = "可以叠加"
    result = source_analysis(state, extracted)
    assert result.status == "needs_evidence" and "条件尚待核对" in result.summary


def test_incomplete_option_cannot_be_hidden_by_an_answered_neighbor():
    documents = [{"text": "甲餐厅每人80元。"}, {"text": "乙餐厅每人费用未提供。"}]
    state = state_for(json.dumps({"evidence": documents}, ensure_ascii=False))
    extracted = analysis(state, options=[SourceOption(record=0, quote=documents[0]["text"], charges=[SourceCharge(value=80, quote="每人80元", unit="person", currency="CNY")]),
        SourceOption(record=1, quote=documents[1]["text"], conditions=[SourceCondition(quote="乙餐厅每人费用未提供")])], requested=["comparison"])
    result = source_analysis(state, extracted)
    assert not result.data["answered"] and "尚未完成选项比较" in result.summary


def test_separate_opening_intervals_are_alternatives_not_impossible_conjunction():
    text = "餐厅午市11:00至14:00，晚市17:00至21:00；周二至周日可用。"
    state = state_for(text)
    extracted = analysis(state, options=[SourceOption(record=0, quote=text, windows=[
        SourceWindow(quote="午市11:00至14:00", times=["11:00", "14:00"]),
        SourceWindow(quote="晚市17:00至21:00", times=["17:00", "21:00"]),
        SourceWindow(quote="周二至周日可用", weekdays=[1, 2, 3, 4, 5, 6]),
    ])], requested=["applicability"])
    result = source_analysis(state, extracted)
    assert result.data["answered"] and result.status == "satisfied"
    state["browser_task_context"]["time_window_start"] = "15:00"
    assert source_analysis(state, extracted).status == "mismatch"


def test_one_option_can_bind_record_without_echoing_the_entire_source():
    state, extracted = offer()
    extracted.options[0].quote = ""
    result = source_analysis(state, extracted)
    assert result.data["answered"] and result.data["total_cost"] == 136
    assert result.data["entries"][0]["quotes"]["source"] == state["browser_observation"]["text"]


def test_coupon_is_not_deducted_before_weekday_and_coverage_verification():
    text = "餐费100元，券抵扣20元，周一可用。"
    state = state_for(text, visit_date="2026-10-06")  # Tuesday.
    extracted = analysis(state, options=[SourceOption(record=0,
        charges=[SourceCharge(value=100, quote="餐费100元", unit="group", currency="CNY"),
                 SourceCharge(value=20, quote="券抵扣20元", unit="group", currency="CNY", operation="deduct", applies_to=[0])],
        windows=[SourceWindow(quote="周一可用", weekdays=[0])])], requested=["cost", "applicability"])
    result = source_analysis(state, extracted)
    assert result.status == "mismatch" and result.data["total_cost"] == 100
    assert result.data["entries"][0]["conditional_subtotal"] is None
    assert "未扣减该优惠" in result.summary and "扣减20元后的" not in result.summary


@pytest.mark.parametrize("kind", ["cost", "duration"])
def test_same_numeric_source_cannot_be_counted_twice_by_changing_quote_length(kind):
    text = "完整餐费100元。给定路段需要10分钟。"
    state = state_for(text)
    if kind == "cost":
        option = SourceOption(record=0, charges=[
            SourceCharge(value=100, quote="100元", unit="group", currency="CNY"),
            SourceCharge(value=100, quote="完整餐费100元", unit="group", currency="CNY")])
    else:
        option = SourceOption(record=0, legs=[
            SourceLeg(duration_seconds=CitedNumber(value=600, quote="10分钟")),
            SourceLeg(duration_seconds=CitedNumber(value=600, quote="给定路段需要10分钟"))])
    result = source_analysis(state, analysis(state, options=[option], requested=[kind]))
    assert result is None or not result.data["answered"]
    if result:
        assert result.data["total_cost"] != 200
        assert result.data["entries"][0]["route"]["duration_seconds"] != 1200


def test_model_no_condition_cannot_waive_reservation_or_promote_discount():
    text = "餐费100元，券可抵扣20元，须提前一天预约。"
    state = state_for(text)
    extracted = analysis(state, options=[SourceOption(record=0,
        charges=[SourceCharge(value=100, quote="餐费100元", unit="group", currency="CNY"),
                 SourceCharge(value=20, quote="券可抵扣20元", unit="group", currency="CNY", operation="deduct", applies_to=[0])],
        conditions=[SourceCondition(quote="须提前一天预约", assessment="no_condition")])], requested=["cost", "applicability"])
    result = source_analysis(state, extracted)
    assert result.status == "needs_evidence" and result.data["total_cost"] is None
    assert result.data["entries"][0]["known_subtotal"] == 100
    assert result.data["entries"][0]["conditional_subtotal"] == 80
    assert "该项无需另满足条件" not in result.summary
    assert "未作为已享优惠扣减" in result.summary and "假设下" in result.summary
    state["browser_task_context"]["edits"] = ["已提前一天预约", "刚才说错了，并没有预约"]
    state["input_text"] = "刚才说错了，并没有预约"
    extracted.options[0].conditions[0] = SourceCondition(quote="须提前一天预约", assessment="satisfied", request_quote="已提前一天预约")
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] is None and "已提前一天预约" not in result.summary


@pytest.mark.parametrize("requested,constraints,leg", [
    (["distance"], {"route_distance_km": 2}, SourceLeg(duration_seconds=CitedNumber(value=600, quote="预计10分钟"))),
    (["duration"], {"duration_minutes": 30}, SourceLeg(distance_m=CitedNumber(value=1000, quote="路程1公里"))),
])
def test_missing_requested_route_dimension_cannot_be_hidden_by_another_number(requested, constraints, leg):
    state = state_for("预计10分钟。路程1公里。", **constraints)
    result = source_analysis(state, analysis(state, options=[SourceOption(record=0, legs=[leg])], requested=requested))
    assert not result.data["answered"] and result.status == "needs_evidence"
    assert "尚不能核对" in result.summary


@pytest.mark.parametrize("condition", ["持会员卡无需预约", "工作日无需预约"])
def test_conditional_exemption_cannot_be_cropped_into_unconditional_waiver(condition):
    text = f"餐费100元，券可抵扣20元，{condition}。"
    state = state_for(text)
    extracted = analysis(state, options=[SourceOption(record=0,
        charges=[SourceCharge(value=100, quote="餐费100元", unit="group", currency="CNY"),
                 SourceCharge(value=20, quote="券可抵扣20元", unit="group", currency="CNY", operation="deduct", applies_to=[0])],
        conditions=[SourceCondition(quote="无需预约", assessment="no_condition")])], requested=["cost", "applicability"])
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] is None and result.status == "needs_evidence"
    assert result.data["entries"][0]["known_subtotal"] == 100
    assert condition in result.summary and "该项无需另满足条件" not in result.summary


def test_short_price_block_keeps_parent_identity_shared_rules_and_per_person_subtotal():
    text = "雾岚餐厅，双享套餐售价136元/套。周二17:00至21:00可用。服务费和预约规则未提供。"
    state = state_for(text, party_size=3)
    state["browser_artifacts"][0]["data"]["places"] = [{"name": "雾岚餐厅"}]
    option = SourceOption(record=0, entity="雾岚餐厅", quote="双享套餐售价136元/套",
        charges=[SourceCharge(value=136, quote="售价136元/套", unit="group", currency="CNY")],
        quantity=CitedNumber(value=1, quote="只买一份"),
        windows=[SourceWindow(quote="周二17:00至21:00可用", times=["17:00", "21:00"], weekdays=[1])],
        conditions=[SourceCondition(quote="服务费和预约规则未提供")])
    result = source_analysis(state, analysis(state, options=[option], requested=["cost", "applicability"]))
    assert result.data["answered"] and result.status == "needs_evidence"
    assert result.data["total_cost"] == 136 and result.data["entries"][0]["per_person_subtotal"] == pytest.approx(136 / 3)
    assert "条件相符" in result.summary and "每人45.33元" in result.summary
    assert "均摊不证明套餐足够覆盖3人" in result.summary and "服务费和预约规则未提供" in result.summary
    option.entity = "双享套餐"
    option.quote = "雾岚餐厅，双享套餐售价136元/套"
    assert source_analysis(state, analysis(state, options=[option])).data["total_cost"] == 136
    option.quantity = None
    result = source_analysis(state, analysis(state, options=[option]))
    assert result.data["total_cost"] is None and not result.data["answered"]
    assert result.data["entries"][0]["known_prices"][0]["amount"] == 136
    assert "原文费用：双享套餐售价136元/套" in result.summary


def test_record_rule_scope_does_not_let_another_option_supply_price_or_window():
    text = "甲方案每人60元。周二17:00至20:00可用。乙方案每人90元。周三18:00至22:00可用。"
    state = state_for(text)
    options = [SourceOption(record=0, entity=name, quote=f"{name}每人{amount}元",
        charges=[SourceCharge(value=amount, quote=f"每人{amount}元", unit="person", currency="CNY")])
        for name, amount in [("甲方案", 60), ("乙方案", 90)]]
    options[0].windows = [SourceWindow(quote="周三18:00至22:00可用", times=["18:00", "22:00"], weekdays=[2])]
    options[0].charges[0] = options[1].charges[0]
    result = source_analysis(state, analysis(state, options=options, requested=["cost", "applicability", "comparison"]))
    assert not result.data["answered"] and result.status == "needs_evidence"
    assert "相差" not in result.summary and "不在原文允许使用范围" not in result.summary
    assert "日期时段缺少原文依据" in result.summary


def test_another_options_unconditional_rule_cannot_enable_a_discount():
    text = "甲方案餐费100元，券抵扣20元。乙方案餐费80元。无需预约。"
    state = state_for(text)
    options = [SourceOption(record=0, entity="甲方案", quote="甲方案餐费100元，券抵扣20元",
        charges=[SourceCharge(value=100, quote="餐费100元", unit="group", currency="CNY"),
                 SourceCharge(value=20, quote="券抵扣20元", unit="group", currency="CNY", operation="deduct", applies_to=[0])],
        conditions=[SourceCondition(quote="无需预约", assessment="no_condition")]),
        SourceOption(record=0, entity="乙方案", quote="乙方案餐费80元",
            charges=[SourceCharge(value=80, quote="餐费80元", unit="group", currency="CNY")])]
    result = source_analysis(state, analysis(state, options=options))
    assert result.data["entries"][0]["known_subtotal"] == 100
    assert result.data["entries"][0]["conditional_subtotal"] == 80
    assert "未作为已享优惠扣减" in result.summary


def test_route_comparison_preserves_known_subtotal_without_zeroing_unknown_wait():
    text = "步行1.2公里需15分钟。公交行车9分钟，等待时间未知。"
    state = state_for(text, time_window_start="18:20", total_budget=None)
    walk = SourceOption(record=0, entity="步行", quote="步行1.2公里需15分钟", legs=[SourceLeg(
        distance_m=CitedNumber(value=1200, quote="1.2公里"), duration_seconds=CitedNumber(value=900, quote="15分钟"))])
    bus = SourceOption(record=0, entity="公交", quote="公交行车9分钟，等待时间未知", legs=[
        SourceLeg(duration_seconds=CitedNumber(value=540, quote="9分钟")), SourceLeg(kind="wait")])
    extracted = analysis(state, options=[walk, bus], requested=["duration", "arrival", "comparison"])
    result = source_analysis(state, extracted)
    assert not result.data["answered"] and result.status == "needs_evidence"
    assert result.data["entries"][0]["arrival_time"] == "18:35:00"
    assert result.data["entries"][1]["arrival_time"] is None
    assert result.data["entries"][1]["route"]["duration_seconds"] is None
    assert result.data["entries"][1]["known_route_subtotal"]["duration_seconds"] == 540
    assert "用时小计540秒" in result.summary and "不能据已知小计" in result.summary
    assert "未知等候不按零" in result.summary and "公交用时最短" not in result.summary
    # Once both alternatives have complete durations, compare that dimension.
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] = item["text"].replace("等待时间未知", "等待4分钟")
    bus.quote = "公交行车9分钟，等待4分钟"
    bus.legs[1].duration_seconds = CitedNumber(value=240, quote="4分钟")
    result = source_analysis(state, extracted)
    assert result.data["answered"] and "公交用时最短，与最长项相差120秒" in result.summary
    assert "公交到达最早" in result.summary


def test_distance_comparison_is_independent_of_missing_duration():
    text = "甲路线长800米。乙路线长1.1公里。"
    state = state_for(text)
    options = [SourceOption(record=0, entity=name, quote=quote,
        legs=[SourceLeg(distance_m=CitedNumber(value=value, quote=quote))])
        for name, quote, value in [("甲路线", "甲路线长800米", 800), ("乙路线", "乙路线长1.1公里", 1100)]]
    result = source_analysis(state, analysis(state, options=options, requested=["distance", "comparison"]))
    assert result.data["answered"] and "甲路线路程最短，与最长项相差300米" in result.summary
    assert all(entry["route"]["duration_seconds"] is None for entry in result.data["entries"])


def test_price_comparison_does_not_complete_an_inferred_route_comparison_with_unknown_wait():
    text = "甲接驳收费20元，行车15分钟。乙接驳收费30元，行车9分钟，等待时间未知。"
    state = state_for(text)
    options = [SourceOption(record=0, entity=name, quote=quote,
        charges=[SourceCharge(value=amount, quote=f"收费{amount}元", unit="group", currency="CNY")],
        legs=[SourceLeg(duration_seconds=CitedNumber(value=duration * 60, quote=f"{duration}分钟"))])
        for name, quote, amount, duration in [("甲接驳", "甲接驳收费20元，行车15分钟", 20, 15),
                                             ("乙接驳", "乙接驳收费30元，行车9分钟，等待时间未知", 30, 9)]]
    options[1].legs.append(SourceLeg(kind="wait"))
    result = source_analysis(state, analysis(state, options=options, requested=["comparison"]))
    assert [entry["known_subtotal"] for entry in result.data["entries"]] == [20, 30]
    assert "已知项目小计最低" in result.summary and "不能据已知小计" in result.summary
    assert not result.data["answered"] and "comparison" not in result.data["delivered"]
    assert all("unit" not in price for entry in result.data["entries"] for price in entry["known_prices"])
