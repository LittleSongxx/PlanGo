"""Task outcomes are derived from observed facts, never from a model's finish flag."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

from plango_harness.agent.contracts import Evidence, PlanStop, TripSpec
from pydantic import BaseModel, ConfigDict, Field, model_validator

PLANNING = re.compile(r"行程|出行规划|路线规划|(?:帮我|给我|请)(?:做|制定)?规划|规划(?:一下|一份|重庆|出游|旅游|路线)|安排.{0,12}(?:半天|一天|游玩)")
BROWSER = re.compile(
    r"浏览器|网页|页面|网站|菜单|点菜|团购|比价|比较|对比|挑选|推荐|外卖|购物|下单|预约|预订|订位|订座|取号|排队号|送花|攻略|截图|图片|图像|照片|支付|付款|取消订单|https?://"
)
WRITE = re.compile(r"预约|预订|订位|订座|取号|领号|下单|提交|支付|付款|发送|取消订单|购买|(?:帮我|替我|给我|请|然后|再|并|后)(?:直接)?买|(?:^|[，,；;。])\s*买")
REASONING = re.compile(r"比价|比较|对比|挑选|推荐|最便宜|最佳|哪家|差价|差额|(?:计算|算一下|算算).{0,80}(?:总价|价格|费用)")
READ_REQUEST = re.compile(r"读取|提取|识别|查看|看看|打开|访问|浏览|滚动|点击|输入|填写|切换")
ANALYSIS = re.compile(r"(?:判断|分析|核算|核对).{0,24}(?:适用|条款|规则|总价|费用|预算)|(?:根据|依据|基于|按).{0,30}(?:资料|条款|规则|给定路线).{0,40}(?:判断|确认|计算|核算)|(?:能否|能不能|是否|能确认).{0,12}(?:不超预算|超预算|够用|适用)|(?:给出|算出).{0,8}(?:总价|总费用)")
CURRENT_PAGE = re.compile(r"(?:当前|这个|此)(?:浏览器)?(?:中|上|里)?的?(?:页面|网页|页)|本(?:页面|网页|页)")


def intent_text(text: str) -> str:
    """Exclude prohibitions from routing without removing them from the model's task."""
    clauses = re.split(r"[，。；！？\n]|(?:但是|不过|但|然后)", text)
    return "，".join(re.sub(r"(?:不要|不用|不必|无需|不需要|禁止|避免|别|不(?=查询|登录|下单|支付|付款|提交|预约|预订|订位|订座|取号|领号|发送|购买|取消订单)).*$", "", clause) for clause in clauses)


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
    plan_verification: Literal["verified", "draft"] = "verified"
    pending_checks: list[dict[str, Any]] = Field(default_factory=list)


def preparation_click_forbidden(state, operation, target):
    preparing = (state.get("execution_goal") or {}).get("kind") == "itinerary_preparation" or (state.get("browser_task_context") or {}).get("kind") in {"prepare", "planning"}
    return preparing and operation == "click" and (not target or target.get("tag") != "a" or not target.get("href"))


ReadField = Literal["merchant", "address", "menu", "recommended_dishes", "offers", "offer_conditions"]
_READ_FIELDS: dict[ReadField, tuple[str, str]] = {
    "merchant": ("门店身份", r"(?:门店|商家|餐厅|店铺)(?:的)?(?:信息|详情|名称|身份|地址)|店名"),
    "address": ("门店地址", r"地址"),
    "menu": ("菜单详情", r"菜单|餐单|菜价|\bmenu\b"),
    "recommended_dishes": ("推荐菜", r"推荐菜|网友推荐"),
    "offers": ("套餐条目", r"套餐|团购|优惠"),
    "offer_conditions": ("套餐使用条件", r"(?:套餐|团购).{0,8}(?:条件|规则|须知)|使用条件|使用规则|购买须知"),
}


class ReadGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["page_read", "menu_read"]
    request: str
    source: Literal["browser", "user_image"] = "browser"
    required_fields: list[ReadField] = Field(default_factory=list)

    @model_validator(mode="after")
    def requested_fields(self):
        # Old checkpoints carry only request/kind; derive the same bounded contract without a migration.
        if not self.required_fields and self.source == "browser":
            for field, (_, pattern) in _READ_FIELDS.items():
                requested = False
                for clause in re.split(r"[，。；！？\n]", self.request):
                    if field == "address":
                        clause = re.sub(r"(?:网页|页面|来源|链接|URL)(?:的)?地址", "", clause, flags=re.I)
                        if "merchant" not in self.required_fields and not re.search(r"门店|商家|餐厅|餐馆|饭店|店铺|这家店|本店|该店", clause):
                            continue
                    if re.search(pattern, clause, re.I):
                        requested = not bool(re.match(r"\s*(?:不要|不用|无需|不需要|不必|别|取消)", clause))
                if requested:
                    self.required_fields.append(field)
            if self.kind == "menu_read" and not self.required_fields:
                self.required_fields.append("menu")
        return self


class ExecutionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["page_read", "menu_read", "itinerary_preparation", "planning_draft"]
    status: Literal["satisfied", "needs_evidence", "mismatch", "unsupported"]
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class FormControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idx: int = Field(ge=0, le=179)
    input_type: str = Field(max_length=40)
    name: str = Field(default="", max_length=256)
    label: str = Field(default="", max_length=500)
    value: str | bool | list[str]
    disabled: bool = False

    @model_validator(mode="after")
    def bounded_value(self):
        values = [self.value] if isinstance(self.value, str) else self.value if isinstance(self.value, list) else []
        if len(values) > 80 or any(len(value) > 2000 for value in values):
            raise ValueError("form_value_too_large")
        if self.input_type in {"password", "file", "hidden"}:
            raise ValueError("sensitive_form_control")
        return self


class ObservedForm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    form_id: str = Field(min_length=1, max_length=200)
    action_url: str = Field(max_length=8192)
    context_text: str = Field(max_length=6000)
    controls: list[FormControl] = Field(max_length=80)
    submit_indices: list[int] = Field(max_length=20)
    truncated: bool = False

    @model_validator(mode="after")
    def unique_complete_controls(self):
        ids = [control.idx for control in self.controls]
        if self.truncated or len(ids) != len(set(ids)) or len(self.submit_indices) != len(set(self.submit_indices)):
            raise ValueError("incomplete_or_ambiguous_form")
        if any(not 0 <= index <= 179 for index in self.submit_indices):
            raise ValueError("invalid_form_submit_index")
        return self


def read_goal(state, context):
    if context.get("mode") != "browser" or context.get("kind") != "extract":
        return None
    request = task_text({**state, "browser_task_context": context})
    requests = [str(context.get("request") or ""), *(str(text) for text in context.get("edits", []))]
    boundaries = [index for index, text in enumerate(requests) if CURRENT_PAGE.search(text)
                  and re.match(r"\s*(?:请)?(?:只|仅)(?:需(?:要)?|要)?(?:读取?|查看|提取)", text)]
    if boundaries:
        # Preserve all history, but only the latest explicit scope and later
        # edits define the active checklist, including across a paused restart.
        active = requests[boundaries[-1]:]
        request = active[0] + ("\n后续修改（后文优先）：\n" + "\n".join(active[1:]) if len(active) > 1 else "")
    source = "user_image" if re.search(r"截图|图片|上传", request) and not (CURRENT_PAGE.search(request) or re.search(r"网页|浏览器|网站|https?://", request)) else "browser"
    kind = context.get("read_kind") or ("menu_read" if re.search(r"菜单|餐单|菜价|\bmenu\b", request, re.I) else "page_read")
    if source == "user_image" and re.search(r"文字|OCR", request, re.I):
        kind = "page_read"
    return ReadGoal.model_validate({"kind": kind, "request": request, "source": source}).model_dump(mode="json")


def form_evidence(observation):
    dom = (observation.get("fields") or {}).get("dom") or {}
    forms = dom.get("forms", []) if isinstance(dom, dict) else []
    if not isinstance(forms, list) or len(forms) > 20:
        return {"forms": [], "forms_error": "unsupported_form_capture"}
    try:
        parsed = [ObservedForm.model_validate(form) for form in forms]
        ids = [form.form_id for form in parsed]
        if len(ids) != len(set(ids)) or any(not identity.startswith(str(observation.get("snapshot_id")) + ":") for identity in ids):
            raise ValueError("form_snapshot_mismatch")
        return {"forms": [form.model_dump(mode="json") for form in parsed]}
    except (ValueError, TypeError):
        return {"forms": [], "forms_error": "incomplete_or_invalid_form_capture"}


def _fresh_artifact(item, now):
    try:
        observed = datetime.fromisoformat(str(item.get("observed_at") or "").replace("Z", "+00:00"))
        return bool(observed.tzinfo and -2 <= (now - observed).total_seconds() <= 600)
    except ValueError:
        return False


def browser_manual_error(observation):
    dom = (observation.get("fields") or {}).get("dom")
    gate = dom.get("manual_gate") if isinstance(dom, dict) else None
    try:
        page = urlsplit(str(observation.get("url") or ""))
        if page.hostname == "verify.meituan.com" and page.path == "/v2/app/general_page":
            gate = "captcha"
    except ValueError:
        pass
    return "authentication_required" if gate == "login" else "captcha_required" if gate == "captcha" else None


def _read_page_fields(data, title):
    """Count literal merchant fields, retaining recommendation/condition previews as narrower reads."""
    from .world import _grounded_price

    text = str(data.get("text") or "")
    tables = [table for table in data.get("tables") or [] if isinstance(table, dict)]
    original = text + "\n" + "\n".join(" | ".join(map(str, row)) for table in tables for row in table.get("rows") or [] if isinstance(row, list))
    fields: set[ReadField] = set()
    menu_rows = {" | ".join(map(str, row)) for table in tables
                 if any(str(header).strip() in {"菜品", "菜名", "餐品", "菜肴"} for header in table.get("headers") or [])
                 for row in table.get("rows") or [] if isinstance(row, list)}

    def grounded(row):
        return (isinstance(row, dict) and isinstance(row.get("quote"), str) and bool(row["quote"])
                and row["quote"] in original and isinstance(row.get("name"), str) and bool(row["name"])
                and row["name"] in row["quote"])

    for place in data.get("places") or []:
        if grounded(place):
            fields.add("merchant")
            if place.get("address") and place["address"] in place["quote"]:
                fields.add("address")
    for row in data.get("menu") or []:
        if not grounded(row):
            continue
        # A heading after the dish (e.g. '菜单(9) 去App查看') does not supply its menu context.
        sections = []
        for occurrence in re.finditer(re.escape(row["name"]), text):
            headings = list(re.finditer(r"推荐菜|网友推荐|菜单|餐单|团购套餐|团购|代金券", text[:occurrence.start()]))
            sections.append(headings[-1].group() if headings else "菜单" if re.search(r"菜单|餐单", title) else "")
        if any(section in {"推荐菜", "网友推荐"} for section in sections):
            fields.add("recommended_dishes")
        if (row["quote"] in menu_rows or any(section in {"菜单", "餐单"} for section in sections)
                or (not any(sections) and row.get("price") is not None and _grounded_price(row["price"], row["quote"]))):
            fields.add("menu")
    offers = [row for row in data.get("offers") or [] if grounded(row)]
    partial_conditions = False
    if offers:
        fields.add("offers")
        details = []
        for row in offers:
            conditions = [condition for condition in row.get("conditions") or []
                          if isinstance(condition, str) and condition.strip() and condition in row["quote"]
                          and not re.search(r"(?:App|APP|详情|待确认|未知)", condition)]
            partial_conditions |= bool(conditions)
            details.append(bool(conditions and re.search(r"使用条件|使用规则|使用须知|购买须知", row["quote"])))
        if all(details):
            fields.add("offer_conditions")
    return fields, partial_conditions and "offer_conditions" not in fields


def read_outcome(state, now=None):
    raw = state.get("execution_goal") or read_goal(state, state.get("browser_task_context") or {})
    if not raw or raw.get("kind") not in {"page_read", "menu_read"}:
        return None
    goal = ReadGoal.model_validate(raw)
    now = now or datetime.now(timezone.utc)
    observation = state.get("browser_observation") or {}
    if browser_manual_error(observation):
        return ExecutionOutcome(kind=goal.kind, status="needs_evidence", summary="页面需要人工登录或验证；尚未取得目标内容。",
                                data={"scope": "read_only", "business_completed": False, "missing_fields": goal.required_fields})
    evidence = []
    observed_fields: set[ReadField] = set()
    partial_conditions = False
    visual = current_visual_observation(state)
    for item in state.get("browser_artifacts", []):
        data = item.get("data") or {}
        if goal.source == "user_image":
            # An uploaded file is immutable content, not a live merchant observation.
            # Reuse retains its original timestamp and requires the current turn's exact image binding.
            if (item.get("type") == "image" and item.get("source") == "user" and str(data.get("text") or "").strip()
                    and item.get("artifact_id") == "image:" + str(state.get("processed_image_hash") or "")
                    and data.get("text") == state.get("browser_image_context") and state.get("browser_image_turn_id") == state.get("turn_id", 1)):
                evidence.append(item["artifact_id"])
            continue
        if not _fresh_artifact(item, now):
            continue
        if item.get("source") != "browser" or not item.get("url"):
            continue
        if goal.required_fields and item.get("type") == "browser_page":
            if CURRENT_PAGE.search(goal.request) and (
                    item.get("url") != observation.get("url") or item.get("snapshot_id") != observation.get("snapshot_id")):
                continue
            fields, partial = _read_page_fields(data, item.get("title") or "")
            observed_fields.update(fields)
            partial_conditions |= partial
            if fields:
                evidence.append(item["artifact_id"])
        elif goal.kind == "page_read":
            if visual is not None and item is visual:
                evidence.append(item["artifact_id"])
            elif (item.get("type") == "browser_page" and item.get("snapshot_id") == observation.get("snapshot_id")
                    and item.get("url") == observation.get("url") and str(data.get("text") or "").strip()):
                evidence.append(item["artifact_id"])
    if goal.source == "browser" and goal.required_fields:
        missing = [field for field in goal.required_fields if field not in observed_fields]
        observed = [field for field in _READ_FIELDS if field in observed_fields]
        partial = ["offer_conditions"] if partial_conditions and "offer_conditions" not in observed_fields else []
        summary = "已读取有来源的" + "、".join(_READ_FIELDS[field][0] for field in observed) if observed else "尚未读取到请求的结构化内容"
        if partial:
            summary += "；已保留页面展示的部分套餐条件"
        summary += "；仍缺" + "、".join(_READ_FIELDS[field][0] for field in missing) if missing else "；请求的读取范围已覆盖"
        return ExecutionOutcome(kind=goal.kind, status="needs_evidence" if missing else "satisfied", summary=summary + "。未公开价格仍为未知。",
                                evidence_ids=list(dict.fromkeys(evidence)), data={"scope": "read_only", "business_completed": False,
                                "required_fields": goal.required_fields, "observed_fields": observed, "missing_fields": missing, "partial_fields": partial})
    return ExecutionOutcome(
        kind=goal.kind, status="satisfied" if evidence else "needs_evidence", evidence_ids=list(dict.fromkeys(evidence)),
        summary=("已保留用户图片的识别文字；实时商家和价格信息仍需另行核对。" if goal.source == "user_image" else "已读取有来源的菜单条目；未公开价格仍为未知。" if goal.kind == "menu_read" else "已读取有来源的页面或图像文字，内容已保留。") if evidence else
                ("当前观测中没有可追溯菜单条目；页面读取或截图本身不能代替菜单结果。" if goal.kind == "menu_read" else "当前页面没有可交付的文字观测，请继续读取或人工核对。"),
        data={"scope": "image_text" if goal.source == "user_image" else "read_only", "business_completed": False,
              **({"merchant_verified": False, "price_verified": False} if goal.source == "user_image" else {})},
    )


def _form_control(form, aliases):
    values = []
    for control in form.controls:
        labels = {re.sub(r"[\s:：*（）()]", "", value).casefold() for value in [control.name, *control.label.split(" / ")]}
        if not control.disabled and labels & aliases and isinstance(control.value, str):
            values.append(control)
    return values[0] if len(values) == 1 else None


def preparation_outcome(state, now=None):
    raw = state.get("execution_goal") or {}
    if raw.get("kind") != "itinerary_preparation":
        return None
    goal = ExecutionGoal.model_validate(raw)
    if state.get("browser_receipt_pending") or state.get("approval_decision") == "reject" or any(
        (item.get("status") if isinstance(item, dict) else getattr(item, "status", None)) in {"UNKNOWN", "RUNNING"}
        for item in state.get("action_results", [])
    ):
        return ExecutionOutcome(kind=goal.kind, status="needs_evidence", summary="仍有未确认操作或审批被拒绝，不能把表单准备认定为完成。")
    now = now or datetime.now(timezone.utc)
    current = state.get("selected_plan")
    current_id = current.get("plan_id") if isinstance(current, dict) else getattr(current, "plan_id", None)
    current_version = current.get("version") if isinstance(current, dict) else getattr(current, "version", None)
    if current_id != goal.plan_id or current_version != goal.plan_version or goal.run_id != state.get("run_id"):
        return ExecutionOutcome(kind=goal.kind, status="needs_evidence", summary="准备目标与当前行程版本不一致，需要重新准备。")
    if goal.requirements.visit_date is None or goal.requirements.party_size is None:
        return ExecutionOutcome(kind=goal.kind, status="needs_evidence", summary="还需要确认到店日期和人数，再继续核对预约表单。")
    expected_date = goal.requirements.visit_date.isoformat()
    entries: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    pages = [item for item in state.get("browser_artifacts", []) if item.get("type") == "browser_page" and item.get("source") == "browser"
             and item.get("url") and item.get("snapshot_id") and item.get("page_version") and _fresh_artifact(item, now)]
    for stop in goal.stops:
        if not stop.address:
            issues.append({"place_id": stop.place_id, "status": "unknown", "detail": "缺少可信分店地址，不能只按同名商家核验"})
            continue
        matches = []
        mismatch = False
        differences = []
        matching_form_count = 0
        for page in pages:
            data = page.get("data") or {}
            if data.get("forms_error"):
                continue
            for raw_form in data.get("forms") or []:
                try:
                    form = ObservedForm.model_validate(raw_form)
                except (ValueError, TypeError):
                    continue
                if not form.form_id.startswith(page["snapshot_id"] + ":"):
                    continue
                # Merchant identity must be inside this native form, not a page header/footer or another form.
                compact = re.sub(r"\s+", " ", form.context_text).strip()
                name = re.escape(re.sub(r"\s+", " ", stop.name).strip())
                address = re.escape(re.sub(r"\s+", " ", stop.address).strip())
                # ponytail: explicit form identity headers only; other layouts need source-specific adapters.
                header = r"^(?:(?:预约信息|预订信息|预约表单|预订表单)[:：]?\s+)?(?:(?:商家|门店|餐厅|店铺|预约商家|预约门店)[:：]\s*)?"
                identity = header + name + r"[\s,，]+(?:(?:地址|门店地址|商家地址|餐厅地址)[:：]\s*)?" + address + r"(?=$|[\s,，])"
                if not re.search(identity, compact):
                    continue
                elements = {element.get("idx"): element for element in data.get("elements") or [] if isinstance(element, dict)}
                if any(control.idx not in elements or elements[control.idx].get("tag") not in {"input", "textarea", "select"}
                       or elements[control.idx].get("input_type") != control.input_type
                       or elements[control.idx].get("disabled") is True for control in form.controls if not control.disabled):
                    continue
                controls = {
                    "party_size": _form_control(form, {"人数", "用餐人数", "就餐人数", "预约人数", "同行人数", "party_size", "partysize", "guests", "people"}),
                    "date": _form_control(form, {"日期", "预约日期", "预订日期", "到店日期", "用餐日期", "visit_date", "reservation_date", "date"}),
                    "time": _form_control(form, {"时间", "预约时间", "预订时间", "到店时间", "用餐时间", "visit_time", "reservation_time", "time"}),
                }
                expected_time = f"{stop.start_minute // 60:02d}:{stop.start_minute % 60:02d}"
                if any(control is None for control in controls.values()):
                    continue
                values = {key: control.value.strip() for key, control in controls.items()}
                try:
                    source, action = urlsplit(page["url"]), urlsplit(form.action_url)
                except ValueError:
                    continue
                if action.scheme not in {"http", "https"} or action.username or action.password or (action.scheme, action.netloc) != (source.scheme, source.netloc):
                    continue
                submits = [index for index in form.submit_indices if index in elements and elements[index].get("tag") in {"button", "input"}
                           and elements[index].get("input_type") in {"submit", "image"} and not elements[index].get("disabled")
                           and re.sub(r"\s+", "", str(elements[index].get("text") or elements[index].get("name") or ""))
                           in {"预约", "提交预约", "确认预约", "立即预约", "预订", "提交预订", "确认预订", "立即预订", "订位", "确认订位", "取号", "确认取号"}]
                if len(submits) != 1:
                    continue
                matching_form_count += 1
                if values["party_size"] != str(goal.requirements.party_size) or values["date"] != expected_date or values["time"] not in {expected_time, expected_time + ":00"}:
                    mismatch = True
                    differences.append({"form_id": form.form_id, "evidence_id": page["artifact_id"], "snapshot_id": page["snapshot_id"],
                                        "page_version": page["page_version"], "tab_id": page.get("tab_id"), "url": page["url"],
                                        "expected": {"party_size": str(goal.requirements.party_size), "date": expected_date, "time": expected_time},
                                        "observed": values, "field_indices": {key: control.idx for key, control in controls.items()}})
                    continue
                matches.append({"place_id": stop.place_id, "form_id": form.form_id, "source_url": page["url"], "snapshot_id": page["snapshot_id"],
                                "page_version": page["page_version"], "evidence_id": page["artifact_id"], "submit_idx": submits[0],
                                "party_size": goal.requirements.party_size, "visit_date": expected_date, "visit_time": expected_time,
                                "timezone": goal.requirements.timezone, "time_source": "approved_plan"})
        if len(matches) == 1 and not mismatch:
            entries.append(matches[0])
        else:
            issues.append({"place_id": stop.place_id, "status": "mismatch" if mismatch else "unknown",
                           "detail": "表单值与目标不一致" if mismatch else "缺少完整且唯一的同商家表单、人数、日期、时间或预约入口", "differences": differences, "matching_form_count": matching_form_count})
    ready = not issues and len(entries) == len(goal.stops)
    return ExecutionOutcome(kind=goal.kind, status="satisfied" if ready else "mismatch" if any(item["status"] == "mismatch" for item in issues) else "needs_evidence",
                            summary=("已核对表单参数；计划的待核验事项仍需确认，尚未提交。" if goal.plan_verification == "draft" else "已核对表单显示的商家、地址、人数、日期、时间和预约入口；尚未提交，具体操作仍须单独审批。") if ready else "行程准备尚未完成：需要补齐或修正同一预约表单中的商家、人数、日期和时间信息。",
                            evidence_ids=list(dict.fromkeys(entry["evidence_id"] for entry in entries)),
                            data={"scope": "ready_to_review" if ready else "preparation_incomplete", "business_completed": False, "plan_verification": goal.plan_verification, "pending_checks": goal.pending_checks, "entries": entries, "issues": issues})


def preparation_correction(state, outcome=None):
    """Propose one explicit input correction only after the full native form identity check."""
    outcome = outcome or preparation_outcome(state)
    if outcome is None or outcome.status != "mismatch":
        return None
    issues = outcome.data.get("issues") or []
    if len(issues) != 1 or issues[0].get("matching_form_count") != 1 or len(issues[0].get("differences") or []) != 1:
        return None
    difference = issues[0]["differences"][0]
    observation = state.get("browser_observation") or {}
    if (observation.get("ok") is not True or observation.get("outcome") != "observed"
            or difference["evidence_id"] != "page:" + str(observation.get("command_id"))
            or any(difference.get(key) != observation.get(key) for key in ("snapshot_id", "page_version", "tab_id", "url"))):
        return None
    changed = [key for key, value in difference["expected"].items() if difference["observed"].get(key) not in ({value, value + ":00"} if key == "time" else {value})]
    if len(changed) != 1:
        return None
    field = changed[0]
    index = difference["field_indices"][field]
    element: dict[str, Any] = next((item for item in observation.get("elements") or [] if item.get("idx") == index), {})
    if element.get("tag") != "input" or element.get("disabled") or element.get("input_type") not in {"text", {"party_size": "number", "date": "date", "time": "time"}[field]}:
        return None
    label = {"party_size": "人数", "date": "预约日期", "time": "预约时间"}[field]
    return {"idx": index, "text": difference["expected"][field],
            "rationale": f"同一门店表单的{label}当前为{difference['observed'][field]}，已确认计划要求{difference['expected'][field]}；仅提出该字段修改，等待批准，不提交。"}


def draft_review(state):
    from plango_harness.agent.contracts import PlanCandidate, VerifierResult

    plan = PlanCandidate.model_validate(state["selected_plan"])
    verifier = VerifierResult.model_validate(state["verifier"])
    if verifier.plan_id != plan.plan_id:
        raise ValueError("draft_verifier_plan_mismatch")
    spec = TripSpec.model_validate(state["trip_spec"])
    blockers = []
    if (state.get("selected_poi") or {}).get("refresh_error"):
        blockers.append("所选门店详情重新核验未成功，请重试核验后再准备")
    if not verifier.hard_constraints_pass:
        blockers.append("计划存在已知约束冲突，请先修改")
    if spec.visit_date is None:
        blockers.append("尚未明确到店日期")
    if spec.party_size is None:
        blockers.append("尚未明确人数")
    evidence = [Evidence.model_validate(row) for row in state.get("evidence", [])]
    for stop in plan.stops:
        if not stop.address or not any(
            item.evidence_id in stop.evidence_ids and item.source in {"amap", "browser"} and item.source_ref
            and item.observed_at is not None and item.expires_at is not None and not item.expired
            and item.payload.get("place_id") == stop.place_id and item.payload.get("name") == stop.name
            and item.payload.get("address") == stop.address for item in evidence
        ):
            blockers.append(f"{stop.name}缺少当前可信门店及地址来源")
    identity = f"draft:{state['run_id']}:{plan.plan_id}:{plan.version}"
    return {"kind": "draft_review", "interrupt_id": identity, "plan_id": plan.plan_id, "plan_version": plan.version,
            "can_prepare": not blockers, "preparation_blockers": blockers, "scope": "draft_ready",
            "unknowns": [check.model_dump(mode="json") for check in verifier.unknown_evidence],
            "conflicts": [check.model_dump(mode="json") for check in verifier.hard_violations]}


def draft_outcome(state):
    review = draft_review(state)
    return ExecutionOutcome(kind="planning_draft", status="satisfied",
                            summary="已形成待核验草案，可修改或分享；营业、排队等未确认信息保留标注，尚不具备自动执行条件。",
                            data={**review, "business_completed": False, "execution_allowed": False})


def update_task_context(state: dict[str, Any]) -> dict[str, Any]:
    text = str(state.get("input_text") or "")
    requested = intent_text(text)
    old = dict(state.get("browser_task_context") or {})
    turn = int(state.get("turn_id") or 1)
    if old.get("turn_id") == turn and old.get("latest") == text:
        return old
    analysis = bool(ANALYSIS.search(requested))
    explicit_browser = WRITE.search(requested) or REASONING.search(requested) or READ_REQUEST.search(requested) or analysis
    mode = (
        "planning"
        if PLANNING.search(requested) and not (analysis and not re.search(r"规划|制定|安排", requested))
        else "browser"
        if explicit_browser and (BROWSER.search(requested) or WRITE.search(requested) or REASONING.search(requested) or analysis)
        else old.get("mode") or ("browser" if BROWSER.search(requested) else "planning")
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
    elif WRITE.search(requested):
        context["kind"] = "write"
    elif REASONING.search(requested) or analysis:
        context["kind"] = "reasoning"
        context["comparison_scope"] = text
        context["source_analysis"] = analysis
    elif BROWSER.search(text):
        context["kind"] = "extract"
        context["read_kind"] = "menu_read" if re.search(r"菜单|餐单|菜价|\bmenu\b", text, re.I) and not re.search(r"(?:不要|不用|别).{0,4}菜单", text) else "page_read"
    context.setdefault("kind", "planning" if mode == "planning" else "extract")
    edits, spans, invalid = price_fields(text)
    context.update(edits)
    context["budget_ambiguous"] = "budget" in invalid
    if "party_size" in edits:
        context["party_ambiguous"] = edits["party_size"] is None
    if context.get("kind") == "extract" and (CURRENT_PAGE.search(text) or re.search(r"https?://", text)) and READ_REQUEST.search(requested):
        context.pop("offer_source", None)  # A new explicit read supplies candidates; it never changes the selected offer.
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


class CitedNumber(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: float = Field(ge=0, allow_inf_nan=False)
    quote: str = Field(min_length=1, max_length=1000)


class SourceAnalysis(BaseModel):
    """Extraction only: the model cannot assign applicability or calculate totals."""
    model_config = ConfigDict(extra="forbid")
    package_price: CitedNumber | None = None
    covered_people: CitedNumber | None = None
    meal_total: CitedNumber | None = None
    fare_per_person: CitedNumber | None = None
    distance_m: CitedNumber | None = None
    duration_seconds: CitedNumber | None = None
    meal_coverage: str = Field(default="", max_length=1000)
    fees_included: str = Field(default="", max_length=1000)
    validity: str = Field(default="", max_length=1000)
    reservation: str = Field(default="", max_length=1000)
    stacking: str = Field(default="", max_length=1000)
    other_limits: str = Field(default="", max_length=1000)


def analysis_source(state):
    """Analyze the current durable read, never a stale/other-page or model-only fact."""
    if not (state.get("browser_task_context") or {}).get("source_analysis"):
        return None
    observation = state.get("browser_observation") or {}
    if not observation.get("ok") or browser_manual_error(observation) or not observation.get("command_id"):
        return None
    for item in reversed(state.get("browser_artifacts") or []):
        if (item.get("source") == "browser" and item.get("type") == "browser_page"
                and item.get("artifact_id") == "page:" + observation["command_id"]
                and item.get("snapshot_id") == observation.get("snapshot_id") and item.get("url") == observation.get("url")
                and _fresh_artifact(item, datetime.now(timezone.utc))):
            text = str((item.get("data") or {}).get("text") or "")
            if text.strip() and len(text) <= 7500 and text == observation.get("text"):
                return item
    return None


def source_analysis(state, extracted: SourceAnalysis):
    """A bounded calculation over cited literal fields, not a merchant guarantee."""
    from .supply import _INSTRUCTION

    page = analysis_source(state)
    if not page or (state.get("browser_task_context") or {}).get("kind") != "reasoning":
        return None
    text = page["data"]["text"]
    documents: list[str] = []
    def find_text(value):
        if isinstance(value, dict):
            documents.extend(item for key, item in value.items() if key == "text" and isinstance(item, str))
            for item in value.values():
                find_text(item)
        elif isinstance(value, list):
            for item in value:
                find_text(item)
    try:
        find_text(json.loads(text))
    except (ValueError, RecursionError):
        pass
    if len(documents) > 1 or len(page["data"].get("offers") or []) > 1:
        return None  # A single analysis cannot silently join separate source records/offers.
    if extracted.package_price and (len(page["data"].get("places") or []) > 1 or len(set(re.findall(r"套餐\s*[A-Za-z一二三四五六七八九十\d]+", text))) > 1):
        return None
    number = r"(\d+(?:\.\d+)?)"
    patterns = {
        "package_price": [(r"(?:售价|套餐价|套餐价格)\s*[:：]?\s*[¥￥]?\s*" + number + r"\s*元\s*[/／]\s*(?:份|套餐)(?=$|[\s，。；;、：:}\"])", 1),
                          (r"每份套餐(?:售价|价格)?\s*[:：]?\s*[¥￥]?\s*" + number + r"\s*元(?!\s*[/／])", 1)],
        "covered_people": [(r"(?:覆盖|适用|可供|供)\s*" + number + r"\s*(?:位|名)?(?:成年人|成人|人)", 1)],
        "meal_total": [(r"(?:完整|全部|总)(?:餐费|用餐费用)(?:已)?(?:确认|确定)?(?:为|是|[:：])?\s*[¥￥]?\s*" + number + r"\s*元", 1)],
        "fare_per_person": [(r"单程(?:标准)?(?:票价|车费)\s*[:：]?\s*[¥￥]?\s*" + number + r"\s*元\s*[/／]\s*人", 1),
                            (r"(?:每人|每位)单程(?:标准)?(?:票价|车费)\s*[:：]?\s*[¥￥]?\s*" + number + r"\s*元", 1)],
        "distance_m": [(number + r"\s*(?:米|m\b)", 1), (number + r"\s*(?:公里|千米|km\b)", 1000)],
        "duration_seconds": [(number + r"\s*秒", 1), (number + r"\s*分钟", 60)],
    }
    values, citations = {}, {}
    for key, expressions in patterns.items():
        fact = getattr(extracted, key)
        if not fact or fact.quote not in text or _INSTRUCTION.search(fact.quote) or re.search(r"[$€£]|\b(?:USD|EUR|GBP)\b", fact.quote):
            continue
        if key == "package_price" and re.search(r"人均|每人|每位|元\s*[/／]\s*人", fact.quote):
            continue
        if key == "covered_people" and re.search(r"(?:成人|成年人).{0,12}(?:儿童|小孩)", text):
            continue  # Mixed age composition needs an explicit group breakdown.
        matches = [(match, factor) for pattern, factor in expressions for match in re.finditer(pattern, text, re.I)]
        quoted = list(re.finditer(re.escape(fact.quote), text))
        observed = {Decimal(match[1]) * factor for match, factor in matches
                    if not re.search(r"不是|并非|未确认|不含|往返|人均|每人|每位", re.split(r"[。；;\n]", text[:match.start()])[-1][-20:])
                    and not _INSTRUCTION.search(re.split(r"[。；;\n]", text[:match.start()])[-1] + match.group())
                    and len(quoted) == 1 and quoted[0].start() <= match.start(1) and match.end(1) <= quoted[0].end()}
        if len(matches) == 1 and observed == {Decimal(str(fact.value))}:
            # A short model quote can identify the numeric span, while the
            # original full match still proves its label/unit and preserves it.
            values[key], citations[key] = Decimal(str(fact.value)), matches[0][0].group()
    def clause(key, positive, negative=""):
        quote = getattr(extracted, key)
        valid = bool(quote and quote in text and not _INSTRUCTION.search(quote) and re.search(positive, quote)
                     and not (negative and re.search(negative, text))
                     and not any(re.search(r"(?:不是|并非|未确认|未|不)\s*$", text[max(0, match.start() - 8):match.start()])
                                 for match in re.finditer(re.escape(quote), text)))
        if valid:
            citations[key] = quote
        return valid

    context = state["browser_task_context"]
    request = task_text(state)
    party = context.get("party_size")
    budget = _amount(context.get("total_budget"))
    per_budget = _amount(context.get("per_person_budget"))
    if per_budget is not None and isinstance(party, int):
        budget = min(budget, per_budget * party) if budget is not None else per_budget * party
    missing, conflicts, lines = [], [], []
    total = None
    def display(value):
        rendered = format(value, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    if re.search(r"路线|公交|交通|票价|车费", request) and "meal_total" in values:
        meal, fare = values["meal_total"], values.get("fare_per_person")
        lines.append(f"资料给出的完整餐费为{display(meal)}元。")
        if "distance_m" in values:
            lines.append(f"给定路线{display(values['distance_m'])}米（{display(values['distance_m'] / 1000)}公里）。")
        if "duration_seconds" in values:
            lines.append(f"给定用时{display(values['duration_seconds'])}秒（{display(values['duration_seconds'] / 60)}分钟）。")
        if fare is None:
            missing.append("单程标准票价未明确，不能按0元计算")
        if not isinstance(party, int):
            missing.append("同行人数待确认")
        if fare is not None and isinstance(party, int):
            total = meal + fare * party
            lines.append(f"单程标准票价{display(fare)}元/人，{party}人交通费小计{display(fare * party)}元；与餐费合计{display(total)}元。")
        if budget is not None:
            lines.append(f"总预算{display(budget)}元，扣除餐费后剩余{display(budget - meal)}元用于交通等支出。")
        lines.append("上述核算只含已给出的餐费与单程标准票价；返程及其他未说明费用另需核对。")
    elif "package_price" in values and re.search(r"套餐|适用|条款", request):
        price, people = values["package_price"], values.get("covered_people")
        adult_only = bool(re.search(r"成人|成年人", citations.get("covered_people", "")))
        people_unit = "名成人" if adult_only else "人"
        lines.append(f"资料中的套餐售价为{display(price)}元/份。")
        if people is not None:
            lines.append(f"一份条款明确覆盖{display(people)}{people_unit}。")
        if people is None or not isinstance(party, int):
            missing.append("套餐覆盖人数或同行人数尚未明确")
        elif people != party:
            conflicts.append(f"一份仅明确覆盖{display(people)}{people_unit}，本次{party}人，不能认定按该份数适用或足够")
        if not re.search(r"(?:仅|只)(?:买|购买|购入|使用|用)?(?:一|1)份", request):
            missing.append("购买份数尚未明确，未自动加购")
        if not clause("meal_coverage", r"(?:全部|所有)餐品|完整(?:覆盖|用餐|餐费)", r"不含.{0,8}(?:餐品|餐费)|餐品另点"):
            missing.append("完整餐品覆盖范围未明确")
        if not clause("fees_included", r"(?:包含|已含|包括).{0,25}(?:全部|所有)(?:必付)?费用|无额外费用|无服务费(?:或|和|及)其他加收|不收取(?:其他|任何额外)费用",
                      r"(?:不含|未含|不包括).{0,12}(?:费|加收)|(?:费|加收).{0,6}(?:另计|另收)|(?:另收|加收).{0,12}(?:费|元)"):
            missing.append("所有必付费用未确认全含，不能把套餐售价当完整餐费")
        if not clause("reservation", r"无需预约|不需要预约|不用预约"):
            missing.append("预约要求未满足或未明确")
        if not (clause("stacking", r"(?:不允许|不可|不能|不与).{0,12}(?:叠加|同享|同用)") and re.search(r"不叠加|不与.{0,8}(?:同用|同享)", request)):
            missing.append("本次优惠叠加条件尚未核对")
        if not clause("other_limits", r"(?:没有|无|不设)其他(?:门槛|条件|限制)"):
            missing.append("其他使用门槛尚未核对")
        validity = extracted.validity
        date_pattern = r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?"
        def dates(value):
            try:
                return list(dict.fromkeys(datetime(int(y), int(m), int(d)).date() for y, m, d in re.findall(date_pattern, value)))
            except ValueError:
                return []
        date_values, requested_dates = dates(validity), dates(str(context.get("latest") or context.get("request") or ""))
        times = re.findall(r"(?<!\d)([0-2]?\d):([0-5]\d)", validity)
        requested_times = re.findall(r"(?<!\d)([0-2]?\d):([0-5]\d)", str(context.get("latest") or context.get("request") or ""))
        minutes = [int(h) * 60 + int(m) for h, m in times]
        current_minute = int(requested_times[-1][0]) * 60 + int(requested_times[-1][1]) if requested_times else None
        if not (clause("validity", r"使用|可用|有效|适用|只限|仅限") and 1 <= len(date_values) <= 2 and len(requested_dates) == 1
                and len(minutes) == 2 and 0 <= minutes[0] <= minutes[1] < 1440 and current_minute is not None):
            missing.append("具体使用日期或时段尚不能按原文核对")
        elif not (date_values[0] <= requested_dates[0] <= date_values[-1] and minutes[0] <= current_minute <= minutes[1]):
            conflicts.append("所选日期或时间不在原文使用范围内")
        if (not re.search(r"不(?:再)?另设节假日限制|节假日(?:可用|通用)|当日(?:允许使用|可用)", validity)
                or re.search(r"(?:节假日|周末|周[一二三四五六日天])(?:不可用|不适用|除外)|仅限周", text)):
            missing.append("节假日或当日使用条件未明确")
        if not missing and not conflicts:
            total = price
            lines.append((f"若同行{party}人均符合条款中的成人范围，" if adult_only else "按本次人数、份数与日期时段，") + "且遵守上述所给条款条件，可按一份核算。")
        if adult_only:
            lines.append("条款的人数范围是成人；仅说明同行人数不代表已确认年龄资格。")
    else:
        return None
    if budget is not None:
        lines.append(f"已知范围合计{display(total)}元，{'未超出' if total <= budget else '超出'}{display(budget)}元预算。" if total is not None
                     else f"当前不能确认完整总价不超过{display(budget)}元预算。")
    lines.extend(conflicts)
    if missing:
        lines.append("仍缺依据：" + "；".join(missing) + "。")
    lines.append("以上是按所给资料的条件分析；没有重新验证实时商家供给，不代表已购买、预约或完成交易。原文见当前资料卡。")
    return ExecutionOutcome(kind="page_read", status="mismatch" if conflicts else "needs_evidence" if missing else "satisfied", summary="\n".join(lines),
        evidence_ids=[page["artifact_id"]], data={"scope": "source_analysis", "business_completed": False, "answered": True,
        "total_cost": float(total) if total is not None else None, "missing_rules": missing, "conflicts": conflicts,
        "facts": {key: float(value) for key, value in values.items()}, "quotes": citations, "source_url": page["url"]})


def current_visual_observation(state: dict[str, Any]) -> dict[str, Any] | None:
    """Find actual visual evidence for this turn's current DOM page, not just a spent counter."""
    turn = state.get("turn_id", 1)
    if state.get("browser_vision_turn") != turn:
        return None
    observation = state.get("browser_observation") or {}
    for item in state.get("browser_artifacts", []):
        data = item.get("data") or {}
        screenshot = data.get("screenshot") or {}
        if (item.get("type") == "browser_visual" and item.get("source") == "browser"
                and item.get("turn_id", turn) == turn and data.get("scope") == "visual_observation"
                and isinstance(data.get("visual_text"), str) and data["visual_text"].strip()
                and item.get("observed_at") and screenshot.get("screenshot_id")
                and item.get("url") == screenshot.get("url") == observation.get("url")
                and item.get("snapshot_id") == screenshot.get("snapshot_id") == observation.get("snapshot_id")
                and screenshot.get("page_version") == observation.get("page_version")
                and all(observation.get(key) for key in ("url", "snapshot_id", "page_version"))):
            return item
    return None


def browser_context(state: dict[str, Any], budget: int = 7500) -> str:
    """Keep valid JSON and prior-page facts; discard duplicated page bulk first."""
    observation = state.get("browser_observation") or {}
    context = state.get("browser_task_context") or {}
    value: dict[str, Any] = {
        "task": {k: v for k, v in context.items() if k != "turn_id"},
        "current_request": state.get("input_text"),
        "memory_context": state.get("memory_context", []),
        "execution_goal": state.get("execution_goal"),
        "execution_outcome": state.get("execution_outcome"),
        "vision": {
            "used_this_turn": state.get("browser_vision_turn") == state.get("turn_id", 1),
            "remaining_captures": 0 if state.get("browser_vision_turn") == state.get("turn_id", 1) else 1,
            "has_current_reading": current_visual_observation(state) is not None,
            "scope": "read_only_observation; existing visual_text is in artifacts",
        },
        "step": state.get("browser_steps"),
        "artifacts": [
            {
                "artifact_id": a.get("artifact_id"),
                "url": a.get("url"),
                "data": {
                    k: v
                    for k, v in (a.get("data") or {}).items()
                    if k in {"menu", "offers", "places", "visual_text", "limitations", "scope"}
                },
            }
            for a in state.get("browser_artifacts", [])
        ],
        "observation": {**{k: observation.get(k) for k in ["url", "title", "snapshot_id", "page_version", "elements"]},
                        "fields": {"dom": form_evidence(observation)}},
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
