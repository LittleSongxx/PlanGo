"""Offline field coverage regressions from the real merchant read; no business execution.

The requested fields are stated by the caller. These tests cover what the observation is
allowed to count as covering them: only current, unexpired, same-page evidence.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from plango.outcomes import read_outcome

REQUEST = "读取当前页面示例餐厅的真实门店地址、菜单和套餐使用条件；遇到登录请暂停供我人工接管，不登录、不下单。"
FULL_FIELDS = ["merchant", "address", "menu", "offers", "offer_conditions"]


def state(data, request=REQUEST, *, kind="menu_read", fields=None):
    return {
        "execution_goal": {"kind": kind, "request": request, "source": "browser",
                           "required_fields": FULL_FIELDS if fields is None else fields},
        "browser_observation": {"url": "https://fixture.invalid/shop/1", "snapshot_id": "read-1"},
        "browser_artifacts": [{"artifact_id": "page:read-1", "type": "browser_page", "source": "browser",
                               "url": "https://fixture.invalid/shop/1", "snapshot_id": "read-1", "title": "示例餐厅",
                               "observed_at": datetime.now(timezone.utc).isoformat(), "data": data}],
    }


def test_recommendations_do_not_cover_the_requested_fields():
    data = {"text": "推荐菜\n清蒸鱼\n菜单(9)\n去App查看菜单详情", "menu": [{"name": "清蒸鱼", "price": None, "quote": "清蒸鱼"}]}
    outcome = read_outcome(state(data))
    assert outcome.status == "needs_evidence"
    assert outcome.data["observed_fields"] == ["recommended_dishes"]
    assert outcome.data["missing_fields"] == FULL_FIELDS
    assert "推荐菜" in outcome.summary and outcome.data["business_completed"] is False
    # Asking only for what the page shows is satisfied by the same observation.
    assert read_outcome(state(data, "读取网页推荐菜", kind="page_read", fields=["recommended_dishes"])).status == "satisfied"


def test_merchant_and_offer_previews_do_not_complete_hidden_menu_and_rules():
    place = "示例餐厅\n地址：重庆市示例路1号"
    offer = "双人套餐\n周一至周日\n随时退\n98元"
    data = {"text": place + "\n团购套餐\n" + offer + "\n推荐菜\n清蒸鱼\n菜单(9)\n去App查看菜单详情",
            "places": [{"name": "示例餐厅", "address": "重庆市示例路1号", "quote": place}],
            "menu": [{"name": "清蒸鱼", "price": None, "quote": "清蒸鱼"}],
            "offers": [{"name": "双人套餐", "price": 98, "conditions": ["周一至周日", "随时退"], "quote": offer}]}
    outcome = read_outcome(state(data))
    assert outcome.status == "needs_evidence"
    assert outcome.data["missing_fields"] == ["menu", "offer_conditions"]
    assert outcome.data["partial_fields"] == ["offer_conditions"]
    assert set(outcome.data["observed_fields"]) == {"merchant", "address", "recommended_dishes", "offers"}
    assert "部分套餐条件" in outcome.summary

    data["tables"] = [{"headers": ["菜品", "价格"], "rows": [["清蒸鱼", "时价"]]}]
    data["menu"] = [{"name": "清蒸鱼", "price": None, "quote": "清蒸鱼 | 时价"}]
    data["text"] = data["text"].replace(offer, offer + "\n使用须知：周一至周日")
    data["offers"][0]["quote"] = offer + "\n使用须知：周一至周日"
    complete = read_outcome(state(data))
    assert complete.status == "satisfied" and complete.data["missing_fields"] == []
    assert complete.data["partial_fields"] == []


@pytest.mark.parametrize("text,price,expected", [
    ("清蒸鱼", None, "needs_evidence"),
    ("菜单\n清蒸鱼，价格待确认", None, "satisfied"),
    ("清蒸鱼 68元", 68, "satisfied"),
    ("推荐菜\n清蒸鱼 68元\n菜单(9)\n去App查看详情", 68, "needs_evidence"),
])
def test_menu_requires_actual_menu_context_or_explicit_price(text, price, expected):
    data = {"text": text, "menu": [{"name": "清蒸鱼", "price": price, "quote": text.split("\n")[1] if text.startswith(("菜单", "推荐菜")) else text}]}
    assert read_outcome(state(data, "读取当前网页菜单", fields=["menu"])).status == expected


def test_forged_missing_and_stale_fields_do_not_fill_requested_scope():
    data = {"text": "菜单\n清蒸鱼\n双人套餐98元", "menu": [{"name": "清蒸鱼", "quote": "清蒸鱼", "price": None}],
            "places": [{"name": "示例餐厅", "address": "重庆市示例路1号", "quote": "示例餐厅 重庆市示例路1号"}],
            "offers": [{"name": "双人套餐", "quote": "双人套餐98元", "conditions": ["使用须知：节假日通用"]}]}
    value = state(data)
    outcome = read_outcome(value)
    assert outcome.data["missing_fields"] == ["merchant", "address", "offer_conditions"]
    value["browser_artifacts"][0]["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    assert read_outcome(value).data["observed_fields"] == []


def test_generic_page_read_keeps_its_existing_text_scope():
    outcome = read_outcome(state({"text": "帮助中心：账号设置说明"}, "读取当前网页内容", kind="page_read", fields=[]))
    assert outcome.status == "satisfied" and outcome.data == {"scope": "read_only", "business_completed": False}


def test_a_page_without_a_merchant_block_does_not_cover_an_address_request():
    """A source URL is not a street address; only an observed place block covers it."""
    value = state({"text": "账号设置说明"}, "读取当前网页的商家信息与地址", kind="page_read", fields=["merchant", "address"])
    outcome = read_outcome(value)
    assert outcome.status == "needs_evidence"
    assert outcome.data["missing_fields"] == ["merchant", "address"]


@pytest.mark.parametrize("changed", [{"url": "https://fixture.invalid/shop/2"}, {"snapshot_id": "previous-snapshot"}])
def test_current_page_cannot_borrow_fields_from_another_url_or_snapshot(changed):
    value = state({"text": "菜单\n清蒸鱼", "menu": [{"name": "清蒸鱼", "quote": "清蒸鱼", "price": None}]})
    other = deepcopy(value["browser_artifacts"][0])
    other.update(artifact_id="page:another", **changed)
    quote = "示例餐厅 地址：重庆市示例路1号\n双人套餐98元\n使用须知：周一至周日"
    other["data"] = {"text": quote, "places": [{"name": "示例餐厅", "address": "重庆市示例路1号", "quote": quote}],
                     "offers": [{"name": "双人套餐", "quote": quote, "conditions": ["周一至周日"]}]}
    value["browser_artifacts"].append(other)
    outcome = read_outcome(value)
    assert outcome.status == "needs_evidence"
    assert outcome.data["observed_fields"] == ["menu"]
    assert outcome.data["missing_fields"] == ["merchant", "address", "offers", "offer_conditions"]
    assert outcome.evidence_ids == ["page:read-1"]
