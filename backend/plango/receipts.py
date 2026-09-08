"""Recognize explicit page confirmations, not settlement or external reconciliation."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

_ALLOWED_SITES = (
    "dianping.com",
    "meituan.com",
    "amap.com",
    "xiaohongshu.com",
    "baidu.com",
    "douyin.com",
)
_LABELS = {
    "queue": r"(?:排队号|排队编号|取号编号|取号码)",
    "reservation": r"(?:预约编号|预约单号|预约号|预订编号|预订单号|预订号)",
    "order": r"(?:订单编号|订单号)",
}
_SUCCESS = {
    "queue": r"取号成功|排队成功",
    "reservation": r"预约成功|预订成功|订位成功|订座成功",
    "order": r"下单成功|订单提交成功|订单创建成功",
    "cancel": r"取消成功|撤销成功|(?:订单|预约|预订|排队)已取消",
}
_GOALS = {
    "queue": r"取号|排队|排号|领号",
    "reservation": r"预约|预订|订位|订座",
    "order": r"下单|提交订单|创建订单|购买",
}
_REFERENCE = r"([A-Za-z0-9][A-Za-z0-9_-]{1,63})(?![A-Za-z0-9_.*-])"
_BAD_REFERENCE = {"UNKNOWN", "PENDING", "NULL", "NONE", "UNDEFINED", "TBD", "NA", "N/A"}
_REFERENCE_GOALS = {
    "queue": r"(?:排队(?:编号|号)?|取号(?:编号|码)?)",
    "reservation": r"(?:预约(?:编号|单号|号)?|预订(?:编号|单号|号)?)",
    "order": r"订单(?:编号|号)?",
}
_DOCUMENTATION = re.compile(
    r"指南|教程|攻略|帮助|说明|须知|注意事项|查询方法|操作流程|如何|怎样|怎么"
)
# Queue waiting and unpaid orders can still be created; these statuses mean the creation itself is unconfirmed.
_UNCONFIRMED = re.compile(
    r"失败|未成功|不成功|尚未成功|未完成|无法确认|尚未确认|未确认|待确认|等待确认|等待商家|"
    r"待商家确认|正在(?:取号|预约|预订|下单|提交|取消)|(?:预约|取号|下单|取消|提交|支付)中|"
    r"处理中|待提交|请稍候|状态未知|已失效|已过期|未取消|取消未成功|"
    r"(?:未|没有|并非|不是|不代表|无法|不能).{0,8}(?:成功|已取消)|"
    r"(?:如果|若|是否|可能|即将|预计).{0,8}(?:成功|已取消)|"
    r"模拟|示例|演示|样例|\b(?:mock|demo|example|sample|pending|failed|unconfirmed)\b",
    re.IGNORECASE,
)


def _host(url: object) -> str | None:
    if not isinstance(url, str) or len(url) > 8192:
        return None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        return (
            host
            if any(host == item or host.endswith("." + item) for item in _ALLOWED_SITES)
            else None
        )
    except ValueError:
        return None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def verify_receipt(before: dict, after: dict, goal: str) -> dict | None:
    """Return a verbatim, fresh page confirmation with a reference, otherwise None.

    Supported proof is deliberately limited to Chinese confirmation phrases and ASCII
    receipt references on a permitted site. Its scope is always page_confirmation:
    payment, amount, party and shop are not verified by this text parser.
    """
    if not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(goal, str):
        return None
    if len(goal) > 4000 or re.search(r"支付|付款|扣款|结算", goal):
        return None
    if re.search(
        r"(?:不要|无需|不必|别|禁止|勿).{0,5}(?:取消|撤销|预约|预订|下单|购买|取号|排队)", goal
    ):
        return None
    kinds = [kind for kind, pattern in _GOALS.items() if re.search(pattern, goal)]
    cancelling = bool(re.search(r"取消|撤销", goal))
    if cancelling:
        # Cancellation of an order may say only '订单', without the create verb '下单'.
        kinds = [
            kind
            for kind, pattern in {
                "queue": r"排队|取号|排号",
                "reservation": r"预约|预订|订位|订座",
                "order": r"订单",
            }.items()
            if re.search(pattern, goal)
        ]
    if len(kinds) != 1:
        return None
    kind = "cancel" if cancelling else kinds[0]
    if (
        before.get("ok") is not True
        or after.get("ok") is not True
        or after.get("outcome") != "observed"
    ):
        return None
    host = _host(after.get("url"))
    if not host or host != _host(before.get("url")):
        return None
    if not before.get("tab_id") or before.get("tab_id") != after.get("tab_id"):
        return None
    if (
        not before.get("snapshot_id")
        or not after.get("snapshot_id")
        or before["snapshot_id"] == after["snapshot_id"]
    ):
        return None
    old_time, new_time = _timestamp(before.get("observed_at")), _timestamp(after.get("observed_at"))
    if not old_time or not new_time or new_time <= old_time:
        return None
    old_text, text = before.get("text"), after.get("text")
    if (
        not isinstance(old_text, str)
        or not isinstance(text, str)
        or max(len(old_text), len(text)) > 100000
    ):
        return None
    if _UNCONFIRMED.search(text):
        return None
    labels = _LABELS[kinds[0]]
    requested_references = {
        match.group(1).upper()
        for match in re.finditer(_REFERENCE_GOALS[kinds[0]] + r"\s*[:：#]?\s*" + _REFERENCE, goal)
    }
    references = list(re.finditer(labels + r"\s*[:：#]?\s*" + _REFERENCE, text))
    confirmations = list(re.finditer(_SUCCESS[kind], text))
    candidates: dict[str, str] = {}
    for reference in references:
        value = reference.group(1)
        if value.upper() in _BAD_REFERENCE or re.fullmatch(r"[Xx_*-]+", value):
            continue
        if requested_references and requested_references != {value.upper()}:
            continue
        # A named reference must belong to the confirmation, not a distant history section.
        for status in confirmations:
            line_start = text.rfind("\n", 0, status.start()) + 1
            line_end = text.find("\n", status.end())
            if _DOCUMENTATION.search(text[line_start : line_end if line_end >= 0 else None]):
                continue
            start, end = min(reference.start(), status.start()), max(reference.end(), status.end())
            if end - start > 160:
                continue
            if re.search(r"[?？]", text[max(0, status.start() - 8) : status.end() + 8]):
                continue
            prior = bool(
                re.search(
                    r"(?<![A-Za-z0-9_-])" + re.escape(value) + r"(?![A-Za-z0-9_-])",
                    old_text,
                    re.IGNORECASE,
                )
            )
            if prior != cancelling:
                continue
            if cancelling and not re.search(
                labels + r"\s*[:：#]?\s*" + re.escape(value) + r"(?![A-Za-z0-9_-])",
                old_text,
                re.IGNORECASE,
            ):
                continue
            if cancelling and re.search(_SUCCESS["cancel"], old_text):
                # An already-cancelled record is not confirmation of this new action.
                continue
            candidates[value] = text[start:end]
    if len(candidates) != 1:
        return None
    receipt_reference, quote = next(iter(candidates.items()))
    return {
        "scope": "page_confirmation",
        "kind": kind,
        "reference": receipt_reference,
        "quote": quote,
        "source_url": after["url"],
        "observed_at": after["observed_at"],
    }
