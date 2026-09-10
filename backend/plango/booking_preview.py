"""Verify that a visible booking form shows the parameters we asked for, nothing more.

The check is a comparison, not site knowledge: the requested party size, date and time
come from the request URL's own query, and each must be readable in the page's visible
labels captured under request protection. A site whose labels this cannot read reports
the gap rather than any site being special-cased.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .outcomes import ExecutionOutcome, _fresh_artifact

_URL = re.compile(r"https?://[^\s<>\"'`，。；！？（）\[\]()]+", re.I)
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DATE_KEYS = ("date", "day", "start_date", "startdate", "visit_date", "checkin", "arrival")
_TIME_KEYS = ("time", "start_time", "starttime", "hour", "slot")
_PARTY_KEYS = ("pax", "party", "people", "persons", "guests", "size", "covers", "party_size", "num", "seats")


def _requested(url: str) -> tuple[tuple[str, str], int, date, str] | None:
    """Read the page identity plus party, date and time out of the URL's own query.

    The identity is part of the result so a second venue that happens to use the same
    parameters can never stand in for the one that was asked for.
    """
    try:
        page = urlsplit(url)
        if page.scheme != "https" or not page.hostname or page.port not in (None, 443) or page.username or page.password:
            return None
        if page.fragment:
            return None
        pairs = parse_qsl(page.query, keep_blank_values=True, strict_parsing=True, max_num_fields=12)
    except (ValueError, TypeError):
        return None
    if not pairs or len(pairs) != len({key for key, _ in pairs}):
        return None
    values = {key.lower(): value for key, value in pairs}

    def pick(keys):
        found = [values[key] for key in values if any(name == key or name in key.split("_") for name in keys)]
        return found[0] if len(found) == 1 else None

    party, day, at = pick(_PARTY_KEYS), pick(_DATE_KEYS), pick(_TIME_KEYS)
    if party is None or day is None or at is None:
        return None
    if not re.fullmatch(r"[1-9]\d?", party) or not 1 <= int(party) <= 99:
        return None
    if not re.fullmatch(r"[1-9]\d{3}-\d{2}-\d{2}", day) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", at):
        return None
    try:
        return (page.hostname.lower(), page.path), int(party), date.fromisoformat(day), at
    except ValueError:
        return None


def _shows_party(label: str, party: int) -> bool | None:
    counts = {int(match) for match in re.findall(r"\d{1,2}", label)}
    return party in counts if counts else None


def _shows_date(label: str, day: date) -> bool | None:
    """Accept the day and month in any order and either language; the year is optional.

    A visible control commonly omits the year, so a shown year must agree while an
    absent one is not treated as a mismatch.
    """
    text = label.casefold()
    months = {index + 1 for index, name in enumerate(_MONTHS) if name in text}
    months |= {int(match) for match in re.findall(r"(\d{1,2})\s*月", label)}
    days = {int(match) for match in re.findall(r"\b(\d{1,2})\b(?!\s*[:：])", label)}
    days |= {int(match) for match in re.findall(r"(\d{1,2})\s*日", label)}
    years = {int(match) for match in re.findall(r"\b(\d{4})\b", label)}
    if not months or not days:
        return None
    if years and day.year not in years:
        return False
    return day.month in months and day.day in days


def _shows_time(label: str, at: str) -> bool | None:
    text = label.casefold()
    hour, minute = (int(part) for part in at.split(":"))
    for match in re.finditer(r"(\d{1,2})\s*[:：]\s*(\d{2})\s*(am|pm)?", text):
        shown, shown_minute, meridiem = int(match[1]), int(match[2]), match[3]
        if meridiem:
            shown = shown % 12 + (12 if meridiem == "pm" else 0)
        if (shown, shown_minute) == (hour, minute):
            return True
    return False if re.search(r"\d\s*[:：]\s*\d{2}", text) else None


def booking_preview_outcome(state: Mapping[str, Any]) -> ExecutionOutcome | None:
    goal = state.get("execution_goal") or {}
    if not isinstance(goal, dict) or goal.get("kind") != "page_read" or not isinstance(goal.get("request"), str):
        return None
    urls = set(_URL.findall(goal["request"]))
    if len(urls) != 1:
        return None
    target = urls.pop()
    requested = _requested(target)
    if requested is None:
        return None
    _, party, day, at = requested
    observation = state.get("browser_observation") or {}
    if not isinstance(observation, dict):
        observation = {}
    fields = observation.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    preview = fields.get("booking_preview")
    if not isinstance(preview, dict):
        preview = {}
    labels = {key: value if isinstance(value, str) and len(value) <= 120 else ""
              for key in ("merchant_label", "party_label", "date_label", "time_label")
              for value in [preview.get(key)]}
    missing = []
    if (state.get("browser_receipt_pending") or any(
        (action.get("status") if isinstance(action, dict) else getattr(action, "status", None)) in {"UNKNOWN", "RUNNING"}
        for action in state.get("action_results", [])
    )):
        missing.append("unresolved_action")
    dom = fields.get("dom")
    if not isinstance(dom, dict):
        dom = {}
    if observation.get("error_kind") or str(dom.get("manual_gate") or "") in {"login", "captcha"}:
        missing.append("manual_gate")
    if not (observation.get("ok") is True and observation.get("outcome") == "observed"
            and all(isinstance(observation.get(key), str) and observation[key].strip()
                    for key in ("snapshot_id", "page_version", "tab_id"))
            and _fresh_artifact(observation, datetime.now(timezone.utc))):
        missing.append("current_snapshot")
    if not isinstance(observation.get("url"), str) or _requested(observation["url"]) != requested:
        missing.append("request_url")
    if not (isinstance(preview.get("adapter"), str) and preview["adapter"] and preview.get("protected") is True):
        missing.append("protected_capture")
    # A label we cannot read is unverified, which is reported; a label that reads as a
    # different value is a mismatch. The two are never merged into one verdict.
    unread: list[str] = []
    for name, shown in (("party_size", _shows_party(labels["party_label"], party)),
                        ("date", _shows_date(labels["date_label"], day)),
                        ("time", _shows_time(labels["time_label"], at))):
        # These are the parameters under verification: an unreadable label is as
        # unproven as a wrong one, so neither can be reported as confirmed.
        if shown is not True:
            (missing if shown is False else unread).append(name)
    # The URL carries no merchant name. Page identity is already anchored by comparing
    # the observed URL to the one in the request, so a label we cannot compare is only
    # reported; a label that contradicts the place the user picked is a mismatch.
    expected = str((state.get("selected_poi") or {}).get("name") or "").strip()
    shown_merchant = " ".join(labels["merchant_label"].split()).casefold()
    if expected and shown_merchant and shown_merchant != " ".join(expected.split()).casefold():
        missing.append("merchant")
    merchant_verified = bool(expected and shown_merchant and "merchant" not in missing)
    command_id = observation.get("command_id")
    verified = not missing and not unread
    return ExecutionOutcome(
        kind="page_read", status="satisfied" if verified else "needs_evidence",
        summary=(f"已核对网页参数预览：{party}人，{day.isoformat()} {at}；年份来自链接。未查询空位或提交预约。" if verified else
                 "预约参数预览尚缺当前页面的受保护核对证据；未查询空位或提交预约。"),
        evidence_ids=["page:" + command_id] if verified and isinstance(command_id, str) and command_id else [],
        data={"scope": "booking_parameters", "business_completed": False, "availability_checked": False,
              "visible_labels": labels, "requested": {"party_size": party, "date": day.isoformat(), "time": at},
              "source_url": target, "snapshot_id": observation.get("snapshot_id"),
              "page_version": observation.get("page_version"), "tab_id": observation.get("tab_id"),
              "observed_at": observation.get("observed_at"), "missing_evidence": missing,
              "unread_labels": unread, "adapter": preview.get("adapter"),
              "merchant_verified": merchant_verified,
              "field_sources": {"requested": "request_url",
                                "visible_labels": "protected_dom_capture" if preview.get("protected") is True else "unverified_capture",
                                "date_year": "request_url_only"},
              "limitations": ["可见日期控件可能只展示星期、月、日；年份仅来自请求链接，未独立读取可见年份。",
                              "仅核对页面参数草案；未查询空位，未验证锁位或预约可用性，未提交预约。"]},
    )
