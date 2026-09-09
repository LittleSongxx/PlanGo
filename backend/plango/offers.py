"""Derive same-merchant offer decisions from one saved observation; never execute or store facts."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .supply import _INSTRUCTION, _RELATED
from .world import _grounded_people, _grounded_price, dianping_preview_data, table_data

_DATE = r"(\d{4}-\d{2}-\d{2})"
_DAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def offer_hash(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = Decimal(str(value))
        return number.quantize(Decimal(".01"), rounding=ROUND_HALF_UP) if number.is_finite() and 0 <= number <= 1_000_000 else None
    except InvalidOperation:
        return None


def _date(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _observed(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def _face_value(quote):
    values = {Decimal(match[1] or match[2]) for match in re.finditer(
        r"(?:面值|抵用金额)\s*[:：]?\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*元?|(?<![\d.])(\d+(?:\.\d+)?)\s*元(?:代金|抵用|现金)券", quote
    )}
    return _amount(float(next(iter(values)))) if len(values) == 1 else None


def _rules(quote, offer_name, merchant, visit, today, reasons, missing):
    # ponytail: bounded literal clauses; extend only against verified merchant wording, unfamiliar clauses remain unknown.
    header = re.search(r"(?:完整)?(?:使用规则|购买须知|使用须知)\s*[:：]\s*", quote)
    if not header or re.search(r"未|无|缺|不", quote[max(0, header.start() - 4):header.start()]):
        missing.append("完整使用规则尚未取得")
    rule_text = quote[:header.start()] + "\n" + quote[header.end():] if header else quote
    clauses = [part.strip(" ：:") for part in re.split(r"[\n。；;]", rule_text) if part.strip(" ：:")]
    known = set()
    ranges = []
    weekday_sets = []
    holiday_allowed = []
    no_fees = []
    stacking = set()
    for clause in clauses:
        period = re.fullmatch(r"(?:有效期|使用日期)\s*[:：]?\s*" + _DATE + r"\s*(?:至|到|~|～)\s*" + _DATE, clause)
        days = re.fullmatch(r"(?:仅限?|限)?周([一二三四五六日天])至周([一二三四五六日天])(?:可用|使用)?", clause)
        if period and _date(period[1]) and _date(period[2]) and period[1] <= period[2]:
            known.add("validity")
            ranges.append((_date(period[1]), _date(period[2])))
        elif days and _DAY[days[1]] <= _DAY[days[2]]:
            known.add("weekdays")
            weekday_sets.append(set(range(_DAY[days[1]], _DAY[days[2]] + 1)))
        elif re.fullmatch(r"每天(?:可用|通用)|周一至周日", clause):
            known.add("weekdays")
            weekday_sets.append(set(range(7)))
        elif re.fullmatch(r"(?:法定)?节假日(?:可用|通用|适用)", clause):
            known.add("holidays")
            holiday_allowed.append(True)
        elif re.fullmatch(r"(?:法定)?节假日(?:不可用|不适用|除外)", clause):
            known.add("holidays")
            holiday_allowed.append(False)
        elif re.fullmatch(r"不可(?:与其他优惠同用|叠加使用|叠加)|不与其他优惠同享|每次限用1(?:份|张)", clause):
            known.add("stacking")
            stacking.add(False)
        elif re.fullmatch(r"可(?:与其他优惠同用|叠加使用)|可与其他优惠同享", clause):
            known.add("stacking")
            stacking.add(True)
        elif re.fullmatch(r"无额外费用|不收取其他费用|已含所有费用", clause):
            known.add("fees")
            no_fees.append(True)
        elif re.fullmatch(r"无需预约|不需要预约", clause):
            known.add("reservation")
        elif re.fullmatch(r"(?:适用|可供|限)(?:\d{1,2}|[一二两三四五六七八九十双])人(?:使用|用餐)?", clause):
            known.add("people")
        elif re.fullmatch(r"仅限本店使用|本店适用", clause):
            known.add("merchant")
        elif (branch := re.fullmatch(r"(?:适用门店|仅限门店)\s*[:：]\s*(.+)", clause)):
            known.add("merchant")
            if branch[1] != merchant:
                reasons.append("条款指定其他门店，不适用于当前门店")
        elif re.fullmatch(r"随时退|过期自动退|不可退|未使用可退", clause):
            pass
        elif clause == offer_name or re.fullmatch(r"(?:" + re.escape(offer_name) + r"\s*)?(?:售价|现价|套餐价|优惠价|价格|原价|门市价|人均|每人|每位)\s*[:：]?\s*[¥￥]?\s*\d+(?:\.\d+)?\s*元?", clause):
            pass
        elif header:
            missing.append("尚未核对的条款：" + clause[:100])
    for key, label in [("validity", "使用有效期"), ("weekdays", "可用星期"), ("holidays", "节假日规则"), ("stacking", "优惠叠加规则"), ("fees", "额外收费规则"), ("reservation", "预约要求")]:
        if key not in known:
            missing.append(label + "未明确")
    if len(set(ranges)) > 1:
        missing.append("使用有效期存在多个不同范围")
    elif ranges:
        start, end = ranges[0]
        if end < today:
            reasons.append("优惠已超过条款有效期")
        if visit and not start <= visit <= end:
            reasons.append("到店日期不在优惠有效期内")
    if len({tuple(sorted(days)) for days in weekday_sets}) > 1:
        missing.append("可用星期条款存在冲突")
    elif visit and weekday_sets and visit.weekday() not in weekday_sets[0]:
        reasons.append("到店日期不符合可用星期")
    if False in holiday_allowed:
        missing.append("条款排除节假日，尚未核对所选日期的节假日属性")
    if len(set(holiday_allowed)) > 1:
        missing.append("节假日规则相互冲突")
    if len(stacking) > 1:
        missing.append("优惠叠加规则相互冲突")
    return bool(no_fees) and not any("条款" in item and "收费" in item for item in missing)


def compare_offers(artifact, constraints, *, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    data = artifact.get("data") or {}
    places = data.get("places") or []
    merchant = places[0] if len(places) == 1 else {}
    name, address = str(merchant.get("name") or ""), str(merchant.get("address") or "")
    text = str(data.get("text") or "")
    searchable = text + "\n" + "\n".join(" | ".join(map(str, row)) for table in data.get("tables") or [] for row in table.get("rows") or [] if isinstance(row, list))
    related = _RELATED.search(text)
    primary_text = text[:related.start()] if related else searchable
    source_missing = []
    try:
        url = urlsplit(str(artifact.get("url") or ""))
        valid_url = url.scheme in {"http", "https"} and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        valid_url = False
    observed = _observed(artifact.get("observed_at"))
    expires = observed + timedelta(minutes=10) if observed else None
    explicit_expiry = _observed(artifact.get("expires_at"))
    if expires and explicit_expiry:
        expires = min(expires, explicit_expiry)
    artifact_id = str(artifact.get("artifact_id") or "")
    command_id = artifact_id.removeprefix("page:") if artifact_id.startswith("page:") else ""
    if artifact.get("source") != "browser" or artifact.get("type") != "browser_page" or not valid_url or not command_id:
        source_missing.append("缺少可核对的浏览器页面来源")
    merchant_quote = str(merchant.get("quote") or "")
    if not name or not address or name not in merchant_quote or address not in merchant_quote or merchant_quote not in primary_text or _INSTRUCTION.search(merchant_quote):
        source_missing.append("当前页面的唯一门店名称与地址尚未核对")
    if not observed or not expires or not observed <= now <= expires:
        source_missing.append("页面来源已过期或观测时间无效，需重新读取；旧时间保持不变")
    party = constraints.get("party_size")
    party = party if type(party) is int and 1 <= party <= 12 else None
    visit = _date(constraints.get("visit_date"))
    budget = _amount(constraints.get("budget", constraints.get("total_budget")))
    per_budget = _amount(constraints.get("per_person_budget"))
    zone_name = str(constraints.get("timezone") or "Asia/Shanghai")
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = None
    today = now.astimezone(zone or timezone.utc).date()
    # Reuse deterministic DOM/preview parsers so their labelled prices survive stray, unlabelled crossed-out numbers.
    preview = dianping_preview_data({**artifact, **data})
    literal = [*table_data(data).offers, *(preview.offers if preview else [])]
    entries = []
    for index, raw in enumerate((data.get("offers") or [])[:30]):
        offer_name, quote = str(raw.get("name") or ""), str(raw.get("quote") or "")
        missing, reasons = list(source_missing), []
        grounded = bool(offer_name and offer_name in quote and quote and quote in primary_text and not _INSTRUCTION.search(quote))
        if not grounded:
            missing.append("优惠条目缺少同店连续原文依据")
        trusted = next((item for item in literal if item.name == offer_name and item.quote == quote), None)
        price = _amount(raw.get("price")) if grounded else None
        if price is not None and not (trusted and trusted.price == float(price)) and not _grounded_price(float(price), quote):
            price = None
        original = _amount(raw.get("original_price")) if grounded else None
        if original is not None and not (trusted and trusted.original_price == float(original)) and not _grounded_price(float(original), quote, original=True):
            original = None
        face = _face_value(quote) if grounded else None
        if re.search(r"[$€£]|\b(?:USD|EUR|GBP)\b", quote):
            price = original = face = None
            missing.append("币种未确认为人民币")
        kind = "voucher" if re.search(r"代金券|抵用券|现金券", offer_name) else "package" if re.search(r"套餐|[单双一二两三四五六七八九十\d]人餐", offer_name) else "single_item" if re.search(r"单品|单份", offer_name + str(raw.get("unit") or "")) else "unknown"
        per_person = price is not None and _grounded_price(float(price), quote, per_person=True)
        price_basis = "per_person" if per_person else "per_package" if kind == "package" else kind
        counts = [count for count in range(1, 101) if grounded and _grounded_people(count, quote)]
        people = counts[0] if len(counts) == 1 else None
        if party is None:
            missing.append("用餐人数尚未确认")
        if visit is None:
            missing.append("到店日期尚未确认")
        elif visit < today:
            reasons.append("到店日期已过去")
        if zone is None:
            missing.append("行程时区未能核对")
        if price is None:
            missing.append("当前售价缺少明确依据")
        if kind == "package":
            if people is None:
                missing.append("套餐适用人数未明确或存在冲突")
            elif party is not None and party > people:
                reasons.append(f"套餐仅明确覆盖{people}人，不能认定足够{party}人；加人收费及额外点单未核验")
            elif party is not None and party < people:
                missing.append("实际人数少于套餐标注人数，使用人数下限尚未核验")
        else:
            missing.append("代金券或单项优惠不能替代完整用餐清单，尚不明确全部餐费")
        no_fees = _rules(quote, offer_name, name, visit, today, reasons, missing) if grounded else False
        coverage = kind == "package" and party is not None and people == party
        known_cost = price * party if per_person and price is not None and party else price
        total = known_cost if coverage and no_fees and not source_missing and grounded and not missing else None
        checks = []
        if budget is not None:
            checks.append(False if known_cost is not None and known_cost > budget else total <= budget if total is not None else None)
        if per_budget is not None:
            checks.append(total <= per_budget * party if total is not None and party else None)
        within = False if False in checks else True if checks and all(value is True for value in checks) else None
        if within is False:
            reasons.append("已知费用已超出确认的预算")
        status = "unknown" if source_missing or not grounded else "ineligible" if reasons else "unknown" if missing else "eligible"
        reference = {"command_id": command_id, "offer_index": index, "offer_hash": offer_hash(raw)}
        entries.append({"offer_index": index, "offer_hash": reference["offer_hash"], "source_ref": reference, "grounded": grounded and not source_missing,
                        "name": offer_name, "kind": kind, "price_basis": price_basis, "status": status, "price": float(price) if price is not None else None,
                        "face_value": float(face) if face is not None else None, "original_price": float(original) if original is not None else None,
                        "people": people, "known_cost": float(known_cost) if known_cost is not None else None, "total_cost": float(total) if total is not None else None,
                        "within_budget": within, "reasons": list(dict.fromkeys(reasons)), "missing_rules": list(dict.fromkeys(missing)), "quote": quote})
    return {"merchant": {"name": name, "address": address},
            "source": {"artifact_id": artifact_id, "command_id": command_id, "valid": not source_missing, "url": str(artifact.get("url") or "") if valid_url else "",
                       "observed_at": artifact.get("observed_at"), "expires_at": expires.isoformat() if expires else None},
            "constraints": {"party_size": party, "visit_date": visit.isoformat() if visit else None, "timezone": zone_name,
                            "budget": float(budget) if budget is not None else None, "per_person_budget": float(per_budget) if per_budget is not None else None},
            "entries": entries, "summary": f"已核对{name or '待确认门店'}的{len(entries)}项优惠；适用结论仅限已取得的条款。",
            "limitations": ["已知售价是购买一份优惠的支出，不代表全部餐费；不自动叠加或加购。", "适用性核对不代表可预约、已购买或业务完成。", *source_missing]}
