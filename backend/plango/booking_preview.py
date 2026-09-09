"""Verify a protected, visible booking-parameter preview without checking availability."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .outcomes import ExecutionOutcome, _fresh_artifact

_PATH = re.compile(r"/en/(?:shops/)?niccolo-chongqing-tealounge/reserve(?:/(?:message|landing))?")
_URL = re.compile(r"https?://[^\s<>\"'`，。；！？（）\[\]()]+", re.I)
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_WEEKDAYS = "Mon Tue Wed Thu Fri Sat Sun".split()


def _parameters(url: str) -> tuple[int, date, str] | None:
    try:
        page = urlsplit(url)
        if page.scheme != "https" or (page.hostname != "www.szuo.com" or page.port not in (None, 443) or page.username or page.password) or not _PATH.fullmatch(page.path) or page.fragment:
            return None
        pairs = parse_qsl(page.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
        if len(pairs) != 3 or {key for key, _ in pairs} != {"pax", "start_date", "start_time"}:
            return None
        values = dict(pairs)
        if not re.fullmatch(r"[1-9]\d?", values["pax"]) or not 1 <= int(values["pax"]) <= 12:
            return None
        if not re.fullmatch(r"[1-9]\d{3}-\d{2}-\d{2}", values["start_date"]):
            return None
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", values["start_time"]):
            return None
        return int(values["pax"]), date.fromisoformat(values["start_date"]), values["start_time"]
    except (ValueError, TypeError):
        return None


def booking_preview_outcome(state: Mapping[str, Any]) -> ExecutionOutcome | None:
    goal = state.get("execution_goal") or {}
    if not isinstance(goal, dict) or goal.get("kind") != "page_read" or not isinstance(goal.get("request"), str):
        return None
    # ponytail: one documented merchant/English format; add adapters only after another page is verified.
    urls = set(_URL.findall(goal["request"]))
    if len(urls) != 1:
        return None
    target = urls.pop()
    requested = _parameters(target)
    if requested is None:
        return None
    party, day, at = requested
    observation = state.get("browser_observation") or {}
    if not isinstance(observation, dict):
        observation = {}
    fields = observation.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    preview = fields.get("booking_preview") if isinstance(fields, dict) else None
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
    if not isinstance(observation.get("url"), str) or _parameters(observation["url"]) != requested:
        missing.append("request_url")
    protected = preview.get("adapter") == "szuo_tealounge_v1" and preview.get("protected") is True
    if not protected:
        missing.append("protected_capture")
    normalized = {key: " ".join(value.split()).casefold() for key, value in labels.items()}
    if normalized["merchant_label"] != "the tea lounge":
        missing.append("merchant")
    pax = re.fullmatch(r"([1-9]\d?) guests?", normalized["party_label"])
    if not pax or int(pax[1]) != party:
        missing.append("party_size")
    expected_date = f"{_WEEKDAYS[day.weekday()]} {_MONTHS[day.month - 1]} {day.day}".casefold()
    if normalized["date_label"] != expected_date:
        missing.append("date")
    clock = re.fullmatch(r"(0?[1-9]|1[0-2]):([0-5]\d)\s*(am|pm)", normalized["time_label"])
    visible_time = f"{int(clock[1]) % 12 + (12 if clock[3] == 'pm' else 0):02d}:{clock[2]}" if clock else None
    if visible_time != at:
        missing.append("time")
    command_id = observation.get("command_id")
    return ExecutionOutcome(
        kind="page_read", status="needs_evidence" if missing else "satisfied",
        summary=("预约参数预览尚缺当前页面的受保护核对证据；未查询空位或提交预约。" if missing else
                 f"已核对网页参数预览：{party}人，{day.isoformat()} {at}；年份来自链接。未查询空位或提交预约。"),
        evidence_ids=["page:" + command_id] if not missing and isinstance(command_id, str) and command_id else [],
        data={"scope": "booking_parameters", "business_completed": False, "availability_checked": False,
              "visible_labels": labels, "requested": {"party_size": party, "date": day.isoformat(), "time": at},
              "source_url": target, "snapshot_id": observation.get("snapshot_id"),
              "page_version": observation.get("page_version"), "tab_id": observation.get("tab_id"),
              "observed_at": observation.get("observed_at"), "missing_evidence": missing,
              "field_sources": {"requested": "request_url", "visible_labels": "protected_dom_capture" if protected else "unverified_capture",
                                "date_year": "request_url_only"},
              "limitations": ["网页日期控件只展示星期、月、日；年份仅来自请求链接，未独立读取可见年份。",
                              "仅核对页面参数草案；未查询空位，未验证锁位或预约可用性，未提交预约。"]},
    )
