"""Normalize requirement changes before deciding which observations to invalidate."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import Evidence, TripSpec


def preservation_instruction(text: str) -> bool:
    return bool(re.fullmatch(r"(?:其他|其它|其余|剩余|原(?:有|先)?)(?:的)?(?:要求|条件|约束|安排|需求|偏好)?(?:都|全部|仍然|仍)?(?:保持)?(?:不变|照旧|保留)", text.strip()))


def temporal_patch(text: str, previous: TripSpec | None, reference_at: str | datetime | None = None) -> dict[str, Any]:
    zone = previous.timezone if previous else "Asia/Shanghai"
    result: dict[str, Any] = {}
    explicit_zone = re.search(r"(?:时区|timezone)\s*[:：]?\s*(UTC|[A-Za-z_]+/[A-Za-z0-9_+/-]+)", text, re.I)
    if explicit_zone:
        candidate = explicit_zone.group(1)
        try:
            ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            result.update(clarification_needed=True, clarification_fields=["timezone"], clarification_question="请提供有效的 IANA 时区。")
        else:
            zone = candidate
            result["timezone"] = zone
    elif "北京时间" in text:
        zone = "Asia/Shanghai"
        result["timezone"] = zone
    reference = datetime.fromisoformat(reference_at) if isinstance(reference_at, str) else reference_at
    reference = reference or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    today = reference.astimezone(ZoneInfo(zone)).date()
    dates: list[tuple[int, date | None]] = []
    for match in re.finditer(r"(?<!\d)(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})日?(?!\d)", text):
        try:
            value = date(*(int(piece) for piece in match.groups()))
        except ValueError:
            value = None
        dates.append((match.end(), value))
    for match in re.finditer(r"大后天|后天|明天|明早|明晚|今天|今早|今晚", text):
        offset = 3 if match[0] == "大后天" else 2 if match[0] == "后天" else 1 if match[0].startswith("明") else 0
        dates.append((match.end(), today + timedelta(days=offset)))
    unresolved = re.search(r"(?:日期|日子|哪天)\s*(?:还|仍然|仍|先|暂时)?(?:待定|没定|不确定|再说|不限)|(?:改成?|改为|换到?)?\s*(?:周末|下周|星期[一二三四五六日天]|周[一二三四五六日天])", text)
    if dates:
        _, chosen = max(dates, key=lambda item: item[0])
        result["visit_date"] = chosen
        result["visit_date_unknown"] = chosen is None
    elif unresolved:
        result["visit_date_unknown"] = True
    if result.get("visit_date_unknown"):
        result.update(clarification_needed=True, clarification_fields=["visit_date"], clarification_question="请确认具体出行日期；尚未把模糊日期当作今天。")
    if re.search(r"(?:出发|开始|到店)?时间\s*(?:还|先|暂时)?(?:待定|没定|不确定|再说|不限)", text):
        result["time_window_start_unknown"] = True
    return result


def requirement_delta(previous: TripSpec | None, current: TripSpec, *, explicit_unknown: set[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    before = previous.model_dump(mode="json") if previous else {}
    after = current.model_dump(mode="json")
    unknown = explicit_unknown or set()
    # The merged contract is authoritative. goal is conversation history and
    # time_window is a mirror, neither should force a second provider search.
    changed = {name for name, value in after.items() if name not in {"goal", "time_window"} and before.get(name) != value}
    patch = [{"field": name, "operation": "unknown" if name in unknown else "clear" if after[name] is None else "set", "value": after[name], "source": "requirement_agent"} for name in after if name in changed or name in unknown]
    if previous is None:
        return patch, dict(discovery=True, weather=True, supply=True, routes=True)
    location = bool(changed & {"location", "search_location"})
    temporal = bool(changed & {"visit_date", "timezone", "time_window_start", "duration_minutes"})
    party = bool(changed & {"party", "party_size", "party_counts"})
    new_activities = set(current.required_activities + current.optional_activities) - set(previous.required_activities + previous.optional_activities)
    stricter_place = (current.indoor_required and not previous.indoor_required) or (current.outdoor_required and not previous.outdoor_required)
    stricter_place |= current.max_distance_km is not None and (previous.max_distance_km is None or current.max_distance_km < previous.max_distance_km)
    discovery = location or bool(new_activities) or bool(stricter_place) or "must_visit_place_ids" in changed
    transport = "travel_mode" in changed
    return patch, {"discovery": discovery, "weather": location or temporal, "supply": discovery or temporal or party or transport, "routes": location or temporal or party or transport}


def retain_evidence(rows: list[Any], refresh: dict[str, bool]) -> list[Evidence]:
    if refresh["discovery"]:
        return []
    kept = []
    for raw in rows:
        item = Evidence.model_validate(raw)
        fields = set(item.payload)
        source = f"{item.evidence_id} {item.source_ref}".lower()
        supply = bool(fields & {"open_now", "reservable", "seats_left", "estimated_wait_min"}) or "supply" in source
        weather = bool(fields & {"rain_probability", "temperature", "weather"}) or "weather" in source
        route = "destination_place_id" in fields or "route" in source
        if not item.expired and not (refresh["supply"] and supply or refresh["weather"] and weather or refresh["routes"] and route):
            kept.append(item)
    return kept
