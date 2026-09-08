"""Task outcomes are derived from observed facts, never from a model's finish flag."""

from __future__ import annotations

import json
import math
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from plango_harness.agent.contracts import PlanStop, TripSpec
from pydantic import BaseModel, ConfigDict, Field

PLANNING = re.compile(r"行程|出行规划|路线规划|(?:帮我|给我|请)(?:做|制定)?规划|规划(?:一下|一份|重庆|出游|旅游|路线)|安排.{0,12}(?:半天|一天|游玩)")
BROWSER = re.compile(
    r"浏览器|网页|网站|菜单|点菜|团购|比价|比较|对比|挑选|推荐|外卖|购物|下单|预约|预订|订位|订座|取号|排队号|送花|攻略|截图|支付|付款|取消订单|https?://"
)
WRITE = re.compile(r"预约|预订|订位|订座|取号|领号|下单|提交|支付|付款|发送|取消订单|购买")
REASONING = re.compile(r"比价|比较|对比|挑选|推荐|最便宜|最佳|哪家|差价|差额|(?:计算|算一下|算算).{0,80}(?:总价|价格|费用)")


class ExecutionGoal(BaseModel):
    """The approved itinerary carried into browser preparation, never a page-write grant."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["itinerary_preparation"] = "itinerary_preparation"
    run_id: str
    plan_id: str
    plan_version: int = Field(ge=1)
    approval_id: str
    request: str
    requirements: TripSpec
    stops: list[PlanStop] = Field(min_length=1)


def update_task_context(state: dict[str, Any]) -> dict[str, Any]:
    text = str(state.get("input_text") or "")
    old = dict(state.get("browser_task_context") or {})
    turn = int(state.get("turn_id") or 1)
    if old.get("turn_id") == turn and old.get("latest") == text:
        return old
    mode = (
        "planning"
        if PLANNING.search(text)
        else "browser"
        if BROWSER.search(text) or REASONING.search(text)
        else old.get("mode", "planning")
    )
    if mode != old.get("mode") or not old:
        context: dict[str, Any] = {
            "mode": mode,
            "request": text,
            "edits": [],
            "party_size": None,
            "total_budget": None,
            "per_person_budget": None,
        }
    else:
        context = old
        if text != context.get("latest"):
            context["edits"] = [*context.get("edits", []), text]
    # An explicit new objective replaces task kind; a field-only edit retains it.
    if mode == "planning":
        context["kind"] = "planning"
    elif WRITE.search(text) and not re.search(
        r"不(?:要|用|再).{0,4}(?:预约|下单|支付)|别.{0,3}(?:预约|下单)", text
    ):
        context["kind"] = "write"
    elif REASONING.search(text):
        context["kind"] = "reasoning"
        context["comparison_scope"] = text
    elif BROWSER.search(text):
        context["kind"] = "extract"
    context.setdefault("kind", "planning" if mode == "planning" else "extract")
    edits, spans, invalid = price_fields(text)
    context.update(edits)
    context["budget_ambiguous"] = "budget" in invalid
    if "party_size" in edits:
        context["party_ambiguous"] = edits["party_size"] is None
    context.update(turn_id=turn, latest=text)
    return context


def price_fields(text: str) -> tuple[dict[str, Any], list[tuple[int, int]], set[str]]:
    """Parse a bounded set of explicit financial edits; preserve unsupported clauses."""
    edits: dict[str, Any] = {}
    spans: list[tuple[int, int]] = []
    invalid: set[str] = set()
    modifier = r"\s*(?:(?:改为|改成|改|调整到|设为|最多|不超过|控制在|为|是|[:：])\s*)?"
    number = r"(\d+(?:\.\d+)?)\s*元?"
    patterns = [
        (
            "total_budget",
            r"(?:总预算|总额(?:上限)?|总价(?:上限)?|合计预算|预算总额)" + modifier + number,
        ),
        ("per_person_budget", r"(?:人均预算|每人预算|人均上限|每人上限)" + modifier + number),
        ("per_person_budget", r"(?:人均|每人)\s*(?:最多|不超过|至多|限制在)\s*" + number),
        ("per_person_budget", r"(?:人均|每人)\s*" + number + r"(?:以内|以下|之内)"),
    ]
    events: list[tuple[int, str, float | None, tuple[int, int]]] = []
    for field, pattern in patterns:
        for match in re.finditer(pattern, text):
            value = float(match[1])
            if math.isfinite(value) and 0 <= value <= 1_000_000:
                events.append((match.start(), field, value, match.span()))
            else:
                invalid.add("budget")
    for field, pattern in [
        ("total_budget", r"取消(?:总预算|总额限制)|总预算不限|不设总预算"),
        ("per_person_budget", r"取消人均预算|人均预算不限|不设人均预算"),
    ]:
        events.extend((m.start(), field, None, m.span()) for m in re.finditer(pattern, text))
    for _, field, budget_value, span in sorted(events):
        edits[field] = budget_value
        spans.append(span)
    digits = dict(zip("一二两三四五六七八九十", [1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10]))
    for match in re.finditer(
        r"(?<![\d-])(\d{1,2}|一|二|两|三|四|五|六|七|八|九|十)(?:个|位)?人", text
    ):
        count = int(match[1]) if match[1].isdigit() else digits[match[1]]
        edits["party_size"] = count if 1 <= count <= 12 else None
        spans.append(match.span())
    for match in re.finditer(
        r"人数(?:还|尚)?(?:没|未)(?:有)?确定|人数未知|先别按.{0,6}(?:人)?算|不确定.{0,5}(?:几个人|人数)",
        text,
    ):
        edits["party_size"] = None
        spans.append(match.span())
    return edits, spans, invalid


def uncovered_price_requirements(context: dict[str, Any], names: list[str]) -> list[str]:
    """Whitelist fulfilled atoms; any unhandled request remains explicitly incomplete."""
    unknown: list[str] = []
    for request in [str(context.get("request") or ""), *context.get("edits", [])]:
        text = request
        _, spans, _ = price_fields(text)
        for start, end in sorted(spans, reverse=True):
            text = text[:start] + " " * (end - start) + text[end:]
        for name in sorted(names, key=len, reverse=True):
            text = text.replace(name, "")
        if re.search(r"只(?:比|比较|核对)价格|其余条件(?:暂时)?不用考虑", text):
            unknown.clear()
            text = re.sub(r"只(?:比|比较|核对)价格|其余条件(?:暂时)?不用考虑", "", text)
        # These phrases request only arithmetic/ranking or refer to the observed objects.
        text = re.sub(
            r"说明推荐理由|推荐(?:最便宜|便宜)?(?:的)?一家|最便宜|更便宜|便宜|最省钱|不超预算|预算内|人均价格|人均价|总价格|总价|价格|费用|(?:说明|计算)?(?:差价|差额)|差多少|核对|比较|对比|比价|推荐|选择|挑选|帮我选|帮我们选",
            "",
            text,
        )
        text = re.sub(
            r"当前(?:页面|网页)|这个(?:页面|网页)|页面(?:上|中|的)?|网页(?:上|中|的)?|这两家(?:餐厅|店)?|两家(?:餐厅|店)?|餐厅|商家|套餐|一家|哪个|哪家|那个",
            "",
            text,
        )
        text = re.sub(
            r"算一下|算算|计算|各自|给出|请|帮我|帮我们|麻烦|我们|我想|想要|我们有|一共|合计|共|一下|谢谢|以及|和|与|并|及|再|的|吧|先|按|做|选|又",
            "",
            text,
        )
        text = re.sub(r'[\s，,。；;：:、！？!?（）()“”"\'<>→—-]', "", text)
        if text and text not in unknown:
            unknown.append(text[:160])
    return unknown


def task_text(state: dict[str, Any]) -> str:
    context = state.get("browser_task_context") or {}
    if context.get("mode") != "browser":
        return str(state.get("input_text") or "")
    return (
        str(context.get("request") or "")
        + "\n后续修改（后文优先）：\n"
        + "\n".join(context.get("edits", []))
    )


def _amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = Decimal(str(value))
    return (
        result.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        if result.is_finite() and result >= 0
        else None
    )


def price_comparison(state: dict[str, Any]) -> dict[str, Any] | None:
    context = state.get("browser_task_context") or {}
    goal = task_text(state)
    if context.get("kind") != "reasoning" or not re.search(
        r"价格|人均|预算|便宜|比价|省钱|费用|总价", goal
    ):
        return None
    party = context.get("party_size")
    total_cap = _amount(context.get("total_budget"))
    per_cap = _amount(context.get("per_person_budget"))
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact in reversed(state.get("browser_artifacts") or []):
        if artifact.get("source") != "browser" or artifact.get("type") == "price_comparison":
            continue
        for place in (artifact.get("data") or {}).get("places", []):
            name, quote = str(place.get("name") or ""), str(place.get("quote") or "")
            price = _amount(place.get("average_price"))
            if (
                not name
                or name in seen
                or price is None
                or place.get("price_unit") not in {"人均", "每人", "per_person"}
                or not quote
                or not artifact.get("url")
            ):
                continue
            if re.search(r"[$€£]|\b(?:USD|EUR|GBP)\b", quote):
                continue
            seen.add(name)
            total = price * party if isinstance(party, int) and party > 0 else None
            checks: list[bool | None] = []
            if per_cap is not None:
                checks.append(price <= per_cap)
            if total_cap is not None:
                checks.append(total <= total_cap if total is not None else None)
            fits = False if False in checks else None if None in checks else True
            entries.append(
                {
                    "name": name,
                    "unit_price": float(price),
                    "total": float(total) if total is not None else None,
                    "within_budget": fits,
                    "source_url": artifact["url"],
                    "quote": quote,
                    "evidence_id": f"{artifact['artifact_id']}:{name}",
                }
            )
    scope = str(context.get("comparison_scope") or context.get("request") or goal)
    named = [
        row
        for row in entries
        if row["name"] in scope
        and not re.search(r"(?:排除|换掉|不要|不选|不考虑)\s*" + re.escape(row["name"]), scope)
    ]
    page_scope = bool(
        re.search(
            r"(?:比较|对比|比价)(?:一下)?(?:当前|这个)?(?:页面|网页)(?:上|中|的)*(?:这)?(?:两|2|各|所有)?家?(?:餐厅|商家|店)",
            scope,
        )
    )
    if named:
        entries = named
    elif not page_scope:
        return None
    if len(entries) < 2:
        return None
    if context.get("budget_ambiguous"):
        for entry in entries:
            entry["within_budget"] = None
    entries.sort(key=lambda row: row["unit_price"])
    uncovered = uncovered_price_requirements(context, [row["name"] for row in entries])
    unresolved = bool(
        uncovered or context.get("budget_ambiguous") or context.get("party_ambiguous")
    )
    eligible = [row for row in entries if row["within_budget"] is True]
    recommendation = eligible[0]["name"] if eligible and not unresolved else None
    basis = "总价" if party else "人均"
    difference = Decimal(str(entries[-1]["unit_price"])) - Decimal(str(entries[0]["unit_price"]))
    if party:
        difference *= party
    summary = "；".join(
        f"{row['name']}人均¥{row['unit_price']:g}"
        + (f"，{party}人合计¥{row['total']:g}" if party else "")
        + ("，超出已设预算" if row["within_budget"] is False else "")
        for row in entries
    )
    summary += f"。已观测选项{basis}相差¥{difference:g}。"
    if recommendation:
        summary += f"按已知价格与预算，优先选择{recommendation}。"
    elif unresolved:
        summary += "仍有要求尚未核验，以上仅为价格计算，暂不能给出最终推荐。"
    else:
        summary += "目前没有可确认满足预算的选项，需要补充人数或继续查找。"
    complete = (
        (total_cap is None or party is not None)
        and bool(recommendation)
        and not unresolved
    )
    limitations = ["仅比较已观测的人均标价；不是全网最低价，也不代表预约或履约成功。"]
    if uncovered:
        limitations.append("尚未核验的要求：" + "；".join(uncovered) + "。以下只完成价格计算。")
    if context.get("budget_ambiguous"):
        limitations.append("新的预算金额或单位尚未明确，不能据此推荐；请说明总额还是人均。")
    return {
        "artifact_id": f"comparison:{state['run_id']}:{state.get('turn_id', 1)}",
        "type": "price_comparison",
        "source": "browser",
        "title": "已观测餐厅价格比较",
        "complete": complete,
        "data": {
            "basis": "per_person",
            "party_size": party,
            "total_budget": float(total_cap) if total_cap is not None else None,
            "per_person_budget": float(per_cap) if per_cap is not None else None,
            "entries": entries,
            "recommendation": recommendation,
            "savings": float(difference),
            "summary": summary,
            "limitations": limitations,
        },
    }


def browser_context(state: dict[str, Any], budget: int = 7500) -> str:
    """Keep valid JSON and prior-page facts; discard duplicated page bulk first."""
    observation = state.get("browser_observation") or {}
    context = state.get("browser_task_context") or {}
    value: dict[str, Any] = {
        "task": {k: v for k, v in context.items() if k != "turn_id"},
        "current_request": state.get("input_text"),
        "execution_goal": state.get("execution_goal"),
        "step": state.get("browser_steps"),
        "artifacts": [
            {
                "artifact_id": a.get("artifact_id"),
                "url": a.get("url"),
                "data": {
                    k: v
                    for k, v in (a.get("data") or {}).items()
                    if k in {"menu", "offers", "places"}
                },
            }
            for a in state.get("browser_artifacts", [])
        ],
        "observation": {k: observation.get(k) for k in ["url", "title", "snapshot_id", "elements"]},
        "page_excerpt": str(observation.get("text") or "")[:1800],
        "skill": str(state.get("browser_skill_context") or "")[:1500],
        "image_excerpt": str(state.get("browser_image_context") or "")[:1200],
    }

    def encode():
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    for key in ("page_excerpt", "skill", "image_excerpt"):
        if len(encode()) > budget:
            value[key] = ""
    while len(encode()) > budget and len(value["observation"].get("elements") or []) > 20:
        value["observation"]["elements"] = value["observation"]["elements"][
            : len(value["observation"]["elements"]) // 2
        ]
    # Never silently remove user constraints or source identities to fit a prompt.
    if len(encode()) > budget:
        raise ValueError("browser_context_requires_narrower_scope")
    return encode()
