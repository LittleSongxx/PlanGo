"""Offline extraction fixtures: deterministic arithmetic and source boundaries, no model/network."""
from datetime import datetime, timedelta, timezone

import pytest
from plango.outcomes import (
    CitedNumber,
    SourceAnalysis,
    analysis_source,
    source_analysis,
    update_task_context,
)


def state_for(request, text):
    observed = datetime.now(timezone.utc).isoformat()
    state = {"run_id": "analysis-check", "input_text": request, "turn_id": 1,
             "browser_observation": {"ok": True, "command_id": "source-one", "snapshot_id": "snap-one", "url": "https://merchant.invalid/rules", "text": text},
             "browser_artifacts": [{"artifact_id": "page:source-one", "type": "browser_page", "source": "browser", "snapshot_id": "snap-one",
                 "url": "https://merchant.invalid/rules", "observed_at": observed, "data": {"text": text}}]}
    state["browser_task_context"] = update_task_context(state)
    return state


def offer():
    fields = dict(meal_coverage="包含全部餐品及所有必付费用", fees_included="包含全部餐品及所有必付费用，无额外费用",
                  validity="仅限2026年10月6日17:00至21:00使用，节假日可用", reservation="无需预约", stacking="不允许叠加其他优惠", other_limits="没有其他门槛")
    text = "雾岚餐厅晚餐套餐，套餐售价136元/份；一份完整覆盖2名成人。" + "。".join(fields.values()) + "。"
    extracted = SourceAnalysis(**fields, package_price=CitedNumber(value=136, quote="套餐售价136元/份"), covered_people=CitedNumber(value=2, quote="一份完整覆盖2名成人"))
    request = "只按这份资料判断适用性，2人，2026年10月6日18:30，总预算180元，只买一份，不叠加。"
    return state_for(request, text), extracted


def test_complete_quotes_produce_conditional_total_without_assuming_adult_eligibility():
    state, extracted = offer()
    result = source_analysis(state, extracted)
    assert result.status == "satisfied"
    assert result.data["total_cost"] == 136
    assert "若同行2人均符合条款中的成人范围" in result.summary
    assert "仅说明同行人数不代表已确认年龄资格" in result.summary
    assert result.data["business_completed"] is False
    assert result.evidence_ids == ["page:source-one"]


@pytest.mark.parametrize("old,new,expected", [("2人", "3人", "覆盖2名成人"), ("10月6日18:30", "10月7日18:30", "不在原文使用范围"), ("18:30", "22:30", "不在原文使用范围")])
def test_people_and_date_time_conflicts_are_computed_from_cited_values(old, new, expected):
    state, extracted = offer()
    state["input_text"] = state["input_text"].replace(old, new)
    state["browser_task_context"] = update_task_context({**state, "turn_id": 2})
    result = source_analysis(state, extracted)
    assert result.status == "mismatch" and result.data["total_cost"] is None
    assert expected in result.summary


@pytest.mark.parametrize("clause", ["不含服务费", "费用另计", "另收服务费"])
def test_an_omitted_extra_fee_clause_cannot_be_hidden_by_the_extractor(clause):
    state, extracted = offer()
    state["browser_observation"]["text"] += clause
    state["browser_artifacts"][0]["data"]["text"] += clause
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] is None and "所有必付费用未确认全含" in result.summary


def test_unknown_transit_fare_keeps_known_meal_and_budget_headroom_without_zero_fare():
    text = "完整餐费已确认为168元。公交路线距离3.6公里，用时18分钟；单程标准票价未提供，不包含返程。"
    state = state_for("4人，根据给定路线能确认不超预算吗？总预算230元。", text)
    extracted = SourceAnalysis(meal_total=CitedNumber(value=168, quote="完整餐费已确认为168元"),
        distance_m=CitedNumber(value=3600, quote="公交路线距离3.6公里"), duration_seconds=CitedNumber(value=1080, quote="用时18分钟"))
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] is None
    assert "剩余62元" in result.summary and "3600米（3.6公里）" in result.summary and "1080秒（18分钟）" in result.summary
    assert "不能按0元计算" in result.summary and "当前不能确认完整总价" in result.summary
    text += "单程标准票价5元/人。"
    state = state_for("4人，根据给定路线能确认不超预算吗？总预算230元。", text)
    extracted.fare_per_person = CitedNumber(value=5, quote="单程标准票价5元/人")
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] == 188 and "4人交通费小计20元" in result.summary
    assert "只含已给出的餐费与单程标准票价" in result.summary


def test_wrong_quotes_units_and_source_identity_cannot_be_promoted():
    state, extracted = offer()
    extracted.package_price = CitedNumber(value=999, quote="套餐售价136元/份")
    assert source_analysis(state, extracted) is None
    state, extracted = offer()
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] = item["text"].replace("套餐售价136元/份", "每人售价136元")
    extracted.package_price = CitedNumber(value=136, quote="每人售价136元")
    assert source_analysis(state, extracted) is None
    state, extracted = offer()
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] += "节假日除外。"
    assert source_analysis(state, extracted).data["total_cost"] is None
    state, extracted = offer()
    state["browser_artifacts"][0]["snapshot_id"] = "other-page"
    assert analysis_source(state) is None
    state, extracted = offer()
    state["browser_artifacts"][0]["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    assert analysis_source(state) is None
    state, extracted = offer()
    state["browser_task_context"]["kind"] = "write"
    assert source_analysis(state, extracted) is None


@pytest.mark.parametrize("replacement", ["不是套餐售价136元/份", "套餐售价136元/2份", "套餐售价136元/位"])
def test_source_prefix_and_package_unit_cannot_be_cropped_away(replacement):
    state, extracted = offer()
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] = item["text"].replace("套餐售价136元/份", replacement)
    if "不是" not in replacement:
        extracted.package_price.quote = replacement
    assert source_analysis(state, extracted) is None


def test_multiple_offers_or_merchants_are_not_joined_and_return_fare_is_not_one_way():
    state, extracted = offer()
    for item in [state["browser_observation"], state["browser_artifacts"][0]["data"]]:
        item["text"] = item["text"].replace("套餐售价", "套餐A售价").replace("一份完整覆盖", "套餐B一份完整覆盖")
    extracted.package_price.quote = "套餐A售价136元/份"
    assert source_analysis(state, extracted) is None
    state, extracted = offer()
    state["browser_artifacts"][0]["data"]["places"] = [{"name": "甲餐厅"}, {"name": "乙餐厅"}]
    assert source_analysis(state, extracted) is None
    text = "完整餐费168元。往返票价5元/人。"
    state = state_for("4人，根据给定路线能确认不超预算吗？总预算230元。", text)
    extracted = SourceAnalysis(meal_total=CitedNumber(value=168, quote="完整餐费168元"), fare_per_person=CitedNumber(value=5, quote="票价5元/人"))
    result = source_analysis(state, extracted)
    assert result.data["total_cost"] is None and "单程标准票价未明确" in result.summary
