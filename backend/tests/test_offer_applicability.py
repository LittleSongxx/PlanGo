"""Synthetic same-merchant rules plus saved preview structure; no merchant transaction claims."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from plango.graph import artifact
from plango.offers import compare_offers, offer_hash
from plango.world import _grounded_price, dianping_preview_data
from test_dianping_preview import observation

NOW = datetime(2026, 9, 9, 4, tzinfo=timezone.utc)
CONSTRAINTS = {"party_size": 2, "visit_date": "2026-09-10", "budget": 120}
RULES = "完整使用规则：\n适用2人\n有效期2026-09-01至2026-09-30\n周一至周日可用\n法定节假日通用\n不可与其他优惠同用\n无额外费用\n无需预约\n仅限本店使用"


def page(*, quote=None, price=98, original_price=None, name="精选双人餐"):
    quote = quote or f"{name}\n售价{price}元\n{RULES}"
    merchant = {"name": "青竹餐厅(江北店)", "address": "江畔路8号", "quote": "青竹餐厅(江北店) 地址江畔路8号"}
    return {"artifact_id": "page:fixture-command", "type": "browser_page", "source": "browser", "url": "https://merchant.invalid/shop/one",
            "title": "青竹餐厅(江北店)", "observed_at": NOW.isoformat(),
            "data": {"text": merchant["quote"] + "\n" + quote, "places": [merchant],
                     "offers": [{"name": name, "price": price, "original_price": original_price, "people": 2, "quote": quote}]}}


def test_full_literal_rules_and_constraint_recalculation():
    observed = page()
    before = deepcopy(observed)
    result = compare_offers(observed, CONSTRAINTS, now=NOW)
    entry = result["entries"][0]
    assert entry["status"] == "eligible"
    assert entry["grounded"] is True and result["source"]["valid"] is True
    assert entry["total_cost"] == 98 and entry["within_budget"] is True
    assert entry["source_ref"] == {"command_id": "fixture-command", "offer_index": 0, "offer_hash": offer_hash(observed["data"]["offers"][0])}
    assert result["source"]["expires_at"] == (NOW + timedelta(minutes=10)).isoformat()
    assert compare_offers(observed, {**CONSTRAINTS, "party_size": 3}, now=NOW)["entries"][0]["status"] == "ineligible"
    assert compare_offers(observed, {**CONSTRAINTS, "party_size": 3}, now=NOW)["entries"][0]["total_cost"] is None
    assert compare_offers(observed, {**CONSTRAINTS, "budget": 90}, now=NOW)["entries"][0]["within_budget"] is False
    assert compare_offers(observed, {**CONSTRAINTS, "per_person_budget": 40}, now=NOW)["entries"][0]["status"] == "ineligible"
    assert compare_offers(observed, {**CONSTRAINTS, "visit_date": "2026-10-01"}, now=NOW)["entries"][0]["status"] == "ineligible"
    assert compare_offers(observed, {**CONSTRAINTS, "visit_date": None}, now=NOW)["entries"][0]["status"] == "unknown"
    assert observed == before, "Comparison cannot rewrite original observations or constraints"


def test_real_preview_shape_preserves_sale_face_value_and_unknown_rules():
    obs = observation(observed_at=NOW.isoformat())
    observed = artifact(obs, dianping_preview_data(obs).model_dump())
    result = compare_offers(observed, CONSTRAINTS, now=NOW)
    assert [entry["price"] for entry in result["entries"]] == [47, 98, 19.9]
    assert [entry["original_price"] for entry in result["entries"]] == [None, None, None]
    assert result["entries"][0]["face_value"] == 50
    assert all(entry["status"] == "unknown" and entry["total_cost"] is None for entry in result["entries"])
    changed = compare_offers(observed, {**CONSTRAINTS, "party_size": 3}, now=NOW)["entries"]
    assert changed[1]["status"] == "ineligible" and changed[1]["total_cost"] is None
    assert "不能认定足够3人" in changed[1]["reasons"][0]
    assert all("完整使用规则尚未取得" in entry["missing_rules"] for entry in changed)


@pytest.mark.parametrize("omit", ["有效期2026-09-01至2026-09-30", "周一至周日可用", "法定节假日通用", "不可与其他优惠同用", "无额外费用", "无需预约"])
def test_an_unpublished_rule_category_keeps_applicability_unknown(omit):
    """A missing published category cannot support an eligible claim."""
    quote = "精选双人餐\n售价98元\n" + RULES.replace(omit, "")
    entry = compare_offers(page(quote=quote), CONSTRAINTS, now=NOW)["entries"][0]
    assert entry["status"] == "unknown"
    assert entry["missing_rules"], "The unpublished category must still be reported"
    assert entry["known_cost"] == 98
    # Only the fee clause bears on the full cost; the others do not invent it.
    assert entry["total_cost"] == (None if omit == "无额外费用" else 98)


def test_absent_rules_section_withholds_any_applicability_claim():
    quote = "精选双人餐\n售价98元\n" + RULES.replace("完整使用规则：", "")
    entry = compare_offers(page(quote=quote), CONSTRAINTS, now=NOW)["entries"][0]
    assert entry["status"] == "unknown", "With no terms located there is no basis to claim it applies"
    assert entry["listed_price"] == 98 and entry["price"] == 98
    assert "完整使用规则尚未取得" in entry["missing_rules"]


@pytest.mark.parametrize("rule", ["另收服务费10元", "会员专享", "周三不可用", "节假日通用除春节外", "无额外费用但需付茶位费", "每桌限用一份，超过人数另外计费"])
def test_unhandled_restrictions_are_not_silently_discarded(rule):
    entry = compare_offers(page(quote="精选双人餐\n售价98元\n" + RULES + "\n" + rule), CONSTRAINTS, now=NOW)["entries"][0]
    assert entry["status"] == "unknown"
    assert any(rule in missing for missing in entry["missing_rules"])
    leading = compare_offers(page(quote="精选双人餐\n售价98元\n" + rule + "\n" + RULES), CONSTRAINTS, now=NOW)["entries"][0]
    assert leading["status"] == "unknown", "Restrictions before the full-rules header must not be discarded"


def test_weekdays_expiry_other_branch_and_holiday_exclusion():
    weekday = page(quote="精选双人餐\n售价98元\n" + RULES.replace("周一至周日可用", "仅周一至周五可用"))
    assert compare_offers(weekday, {**CONSTRAINTS, "visit_date": "2026-09-12"}, now=NOW)["entries"][0]["status"] == "ineligible"
    expired = page(quote="精选双人餐\n售价98元\n" + RULES.replace("2026-09-30", "2026-09-08"))
    assert compare_offers(expired, CONSTRAINTS, now=NOW)["entries"][0]["status"] == "ineligible"
    other = page(quote="精选双人餐\n售价98元\n" + RULES.replace("仅限本店使用", "适用门店：青竹餐厅(解放碑店)"))
    assert compare_offers(other, CONSTRAINTS, now=NOW)["entries"][0]["status"] == "ineligible"
    holiday = page(quote="精选双人餐\n售价98元\n" + RULES.replace("法定节假日通用", "法定节假日不可用"))
    assert compare_offers(holiday, CONSTRAINTS, now=NOW)["entries"][0]["status"] == "unknown", "Do not invent a current holiday calendar"


@pytest.mark.parametrize("change", ["stale", "future", "two_merchants", "missing_address", "ungrounded_quote", "image", "foreign_offer", "private_url"])
def test_unverified_source_never_yields_eligibility(change):
    observed = page()
    if change == "stale":
        observed["observed_at"] = (NOW - timedelta(minutes=11)).isoformat()
    elif change == "future":
        observed["observed_at"] = (NOW + timedelta(minutes=1)).isoformat()
    elif change == "two_merchants":
        observed["data"]["places"].append({"name": "另一家店", "address": "不同地址", "quote": "另一家店"})
    elif change == "missing_address":
        observed["data"]["places"][0]["address"] = None
    elif change == "ungrounded_quote":
        observed["data"]["text"] = "没有原文"
    elif change == "image":
        observed["source"] = "user"
    elif change == "foreign_offer":
        observed["data"]["text"] = observed["data"]["text"].replace("精选双人餐", "附近推荐\n另一家店\n精选双人餐")
    elif change == "private_url":
        observed["url"] = "https://user:password@merchant.invalid/private"
    result = compare_offers(observed, CONSTRAINTS, now=NOW)
    assert result["entries"][0]["status"] == "unknown"
    assert result["entries"][0]["grounded"] is False
    assert result["entries"][0]["total_cost"] is None
    assert result["source"]["observed_at"] == observed["observed_at"]
    assert "password" not in result["source"]["url"]


@pytest.mark.parametrize("quote,price,original,expected", [
    ("50元代金券 面值50元", 50, None, None),
    ("50元代金券 面值50元 售价47元", 50, None, None),
    ("50元代金券 面值50元 售价47元", 47, None, 47),
    ("精选双人餐 原价128元 售价98元", 128, 128, None),
    ("精选双人餐 原价128元 售价98元", 98, 128, 98),
])
def test_sale_face_value_and_original_price_are_distinct(quote, price, original, expected):
    name = "50元代金券" if "代金券" in quote else "精选双人餐"
    entry = compare_offers(page(quote=quote, price=price, original_price=original, name=name), CONSTRAINTS, now=NOW)["entries"][0]
    assert entry["price"] == expected
    assert entry["original_price"] == original
    assert entry["face_value"] == (50 if "代金券" in quote else None)
    assert entry["status"] == "unknown"
    assert _grounded_price(50, "50元代金券") is False


def test_single_item_and_voucher_never_become_complete_meal_cost():
    for name in ["冷面鸡单品优惠", "50元代金券"]:
        observed = page(name=name, quote=name + "\n售价47元\n" + RULES, price=47)
        observed["data"]["offers"][0]["people"] = None
        entry = compare_offers(observed, CONSTRAINTS, now=NOW)["entries"][0]
        assert entry["known_cost"] == 47 and entry["total_cost"] is None
        assert any("不能替代完整消费清单" in item for item in entry["missing_rules"])


def test_per_person_quote_is_not_mislabelled_as_package_total():
    observed = page(quote="精选双人餐\n人均98元\n" + RULES)
    entry = compare_offers(observed, CONSTRAINTS, now=NOW)["entries"][0]
    assert entry["price_basis"] == "per_person" and entry["price"] == 98
    assert entry["known_cost"] == entry["total_cost"] == 196
    assert entry["within_budget"] is False and entry["status"] == "ineligible"
    package = compare_offers(page(), CONSTRAINTS, now=NOW)["entries"][0]
    assert package["price_basis"] == "per_package" and package["total_cost"] == 98


def test_china_midnight_and_explicit_timezone_use_local_visit_date():
    now = datetime(2026, 9, 9, 16, 1, tzinfo=timezone.utc)  # September 10 just after midnight in Chongqing.
    observed = page()
    observed["observed_at"] = now.isoformat()
    china = compare_offers(observed, {**CONSTRAINTS, "visit_date": "2026-09-09"}, now=now)["entries"][0]
    assert china["status"] == "ineligible" and "到店日期已过去" in china["reasons"]
    utc = compare_offers(observed, {**CONSTRAINTS, "visit_date": "2026-09-09", "timezone": "UTC"}, now=now)["entries"][0]
    assert utc["status"] == "eligible"
    observed = page(quote="精选双人餐\n售价98元\n" + RULES.replace("2026-09-30", "2026-09-09"))
    observed["observed_at"] = now.isoformat()
    expired = compare_offers(observed, CONSTRAINTS, now=now)["entries"][0]
    assert "优惠已超过条款有效期" in expired["reasons"]
