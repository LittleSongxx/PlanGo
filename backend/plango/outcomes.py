"""Task outcomes are derived from observed facts, never from a model's finish flag."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

from plango_harness.agent.contracts import Evidence, PlanStop, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.subagents.requirement import RequirementAgent
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
        expires = datetime.fromisoformat(str(item["expires_at"]).replace("Z", "+00:00")) if item.get("expires_at") else None
        return bool(observed.tzinfo and -2 <= (now - observed).total_seconds() <= 600
                    and (expires is None or expires.tzinfo and observed <= expires and now < expires))
    except (ValueError, TypeError):
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


class TaskIntent(BaseModel):
    """One interpretation of this turn's objective and sparse requirements; no authority."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["planning", "extract", "reasoning", "write", "continue"] = "continue"
    requirements: RequirementOutput | None = None
    analysis_goals: list[Literal["cost", "comparison", "applicability", "distance", "duration", "arrival"]] = Field(default_factory=list, max_length=6)


def update_task_context(state: dict[str, Any], intent: TaskIntent | None = None,
                        *, requirements: RequirementOutput | None = None) -> dict[str, Any]:
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
    if intent is not None:
        if intent.kind == "continue":
            mode = old.get("mode") or mode
        else:
            mode = "planning" if intent.kind == "planning" else "browser"
    if mode != old.get("mode") or not old:
        raw_spec = state.get("trip_spec") or state.get("previous_spec")
        spec = TripSpec.model_validate(raw_spec) if raw_spec else None
        context: dict[str, Any] = {
            "mode": mode,
            "request": text,
            "edits": [],
            "party_size": spec.party_size if spec else old.get("party_size"),
            "total_budget": spec.budget if spec else old.get("total_budget"),
            "per_person_budget": spec.per_person_budget if spec else old.get("per_person_budget"),
            "visit_date": spec.visit_date.isoformat() if spec and spec.visit_date else old.get("visit_date"),
            "time_window_start": spec.time_window_start if spec else old.get("time_window_start"),
            "duration_minutes": spec.duration_minutes if spec else old.get("duration_minutes"),
            "route_distance_km": spec.max_distance_km if spec else old.get("route_distance_km"),
            "search_radius_km": spec.search_radius_km if spec else old.get("search_radius_km"),
            "timezone": spec.timezone if spec else old.get("timezone", "Asia/Shanghai"),
        }
    else:
        context = dict(old)
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
    if intent is not None:
        kind = old.get("kind", context["kind"]) if intent.kind == "continue" else intent.kind
        context["kind"] = kind
        context["source_analysis"] = kind == "reasoning"
        if kind == "reasoning":
            context["comparison_scope"] = text if intent.kind != "continue" else old.get("comparison_scope", text)
            context["analysis_goals"] = list(dict.fromkeys(intent.analysis_goals)) if intent.kind != "continue" or intent.analysis_goals else old.get("analysis_goals", [])
    raw_spec = state.get("trip_spec") or state.get("previous_spec")
    previous = TripSpec.model_validate(raw_spec) if raw_spec else None
    # The fallback shares the planning parser; a model proposal is never
    # overwritten by a second language parser in the browser path.
    if requirements is None:
        if intent is None:
            requirements = RequirementAgent._fallback(text, [], previous,
                                                      reference_at=state.get("requirement_reference_at"))
        elif intent.requirements is not None:
            requirements = RequirementAgent._grounded_patch(intent.requirements, text=text, previous_spec=previous,
                                                             require_initial=mode == "planning")
        else:
            requirements = RequirementOutput(field_evidence={})
    for field, target, clear in (
        ("party_size", "party_size", "party_size_unknown"),
        ("budget", "total_budget", "clear_budget"),
        ("per_person_budget", "per_person_budget", "clear_per_person_budget"),
        ("visit_date", "visit_date", "visit_date_unknown"),
        ("time_window_start", "time_window_start", "time_window_start_unknown"),
        ("duration_minutes", "duration_minutes", None),
        ("route_distance_km", "route_distance_km", "clear_route_distance"),
        ("search_radius_km", "search_radius_km", "clear_search_radius"),
        ("travel_mode", "travel_mode", None),
    ):
        if clear and getattr(requirements, clear):
            context[target] = None
        elif (value := getattr(requirements, field)) is not None:
            context[target] = value.isoformat() if field == "visit_date" else value
    constraint_base = previous or TripSpec(goal=str(context.get("request") or text),
        hard_constraints=context.get("hard_constraints", []), soft_preferences=context.get("soft_preferences", []),
        party_counts=context.get("party_counts", {}))
    merged = requirements.to_trip_spec(text, constraint_base)
    context.update(hard_constraints=merged.hard_constraints, soft_preferences=merged.soft_preferences, party_counts=merged.party_counts)
    context["clarification_fields"] = [field for field in requirements.clarification_fields
        if context.get("mode") == "planning" or field not in {"context", "location", "search_location"}]
    context["clarification_question"] = requirements.clarification_question
    context["budget_ambiguous"] = bool({"budget", "per_person_budget"} & set(requirements.clarification_fields))
    context["party_ambiguous"] = "party_size" in requirements.clarification_fields or context.get("party_size") is None
    context["field_evidence"] = {**context.get("field_evidence", {}), **(requirements.field_evidence or {})}
    context["requirements_verified"] = requirements.field_evidence is not None and "context" not in requirements.clarification_fields
    if context.get("kind") == "extract" and (CURRENT_PAGE.search(text) or re.search(r"https?://", text)) and READ_REQUEST.search(requested):
        context.pop("offer_source", None)  # A new explicit read supplies candidates; it never changes the selected offer.
    context.update(turn_id=turn, latest=text)
    return context


def uncovered_price_requirements(context: dict[str, Any], names: list[str]) -> list[str]:
    """A price-only projection cannot verify the turn's other accepted constraints."""
    unknown = [*context.get("hard_constraints", []), *context.get("soft_preferences", [])]
    if not context.get("requirements_verified", False):
        unknown.append("本轮需求尚未完成语义核对")
    unknown += [goal for goal in context.get("analysis_goals", []) if goal not in {"cost", "comparison"}]
    unknown += [str(field) for field in context.get("clarification_fields", [])]
    for field, label in (("visit_date", "到店日期与可用时段"), ("time_window_start", "到店时间"),
                         ("route_distance_km", "实际路程上限"), ("search_radius_km", "地点范围")):
        if context.get(field) is not None:
            unknown.append(label)
    return list(dict.fromkeys(unknown))


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


class SourceCharge(CitedNumber):
    """A literal charge; only its unit is normalized by the model."""
    unit: Literal["group", "person", "package"]
    currency: Literal["CNY", "USD", "EUR", "GBP", "unknown"] = "unknown"
    operation: Literal["add", "deduct"] = "add"
    threshold: CitedNumber | None = None
    applies_to: list[int] = Field(default_factory=list, max_length=8)


class SourceCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=1, max_length=2000)
    assessment: Literal["no_condition", "satisfied", "conflict", "unknown"] = "unknown"
    request_quote: str = Field(default="", max_length=2000)


class SourceLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["travel", "wait"] = "travel"
    distance_m: CitedNumber | None = None
    duration_seconds: CitedNumber | None = None


class SourceWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=1, max_length=1000)
    dates: list[str] = Field(default_factory=list, max_length=2)
    times: list[str] = Field(default_factory=list, max_length=2)
    excluded: bool = False
    boundary: Literal["range", "latest", "earliest"] = "range"
    weekdays: list[int] = Field(default_factory=list, max_length=7)


class SourceOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record: int = Field(ge=0, strict=True)
    entity: str = Field(default="", max_length=200)
    quote: str = Field(default="", max_length=7500)
    charges: list[SourceCharge] = Field(default_factory=list, max_length=8)
    quantity: CitedNumber | None = None
    covered_people: CitedNumber | None = None
    windows: list[SourceWindow] = Field(default_factory=list, max_length=4)
    conditions: list[SourceCondition] = Field(default_factory=list, max_length=12)
    legs: list[SourceLeg] = Field(default_factory=list, max_length=8)


class SourceAnalysis(BaseModel):
    """Source-bound alternatives and charges, with no model totals or verdicts."""
    model_config = ConfigDict(extra="forbid")
    options: list[SourceOption] = Field(default_factory=list, max_length=6)


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


def analysis_records(page):
    """Keep each captured JSON text record separate; plain pages remain one record."""
    records = []
    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("text"), str) and value["text"].strip():
                records.append({key: value[key] for key in ("text", "title", "name", "expires_at") if key in value})
            for key, child in value.items():
                if key != "text":
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    text = page["data"]["text"]
    try:
        collect(json.loads(text))
    except (ValueError, RecursionError):
        pass
    return [{"record": index, **record} for index, record in enumerate(records or [{"text": text}])]


def _source_quote(text, quote):
    """Recover the complete surrounding clause so a short quote cannot crop negation."""
    if not quote or text.count(quote) != 1:
        return None
    start = text.index(quote)
    end = start + len(quote)
    prefix = list(re.finditer(r"[，,。；;\n]", text[:start]))
    suffix = re.search(r"[，,。；;\n]", text[end:])
    return text[prefix[-1].end() if prefix else 0:end + suffix.start() if suffix else len(text)].strip()


def _source_instruction(text):
    from .supply import _INSTRUCTION

    # Conditional rules are analyzable data. Imperatives to invent or emit facts
    # remain rejected; none of these source statements grants browser authority.
    return any(match.group() not in {"如果", "假设"} for match in _INSTRUCTION.finditer(text))


def _source_number(fact, text, units):
    quote = _source_quote(text, fact.quote) if fact else None
    if not quote or _source_instruction(quote) or re.search(r"不是|并非|未确认|未确定|尚未|不含|不包含", quote):
        return None
    # The numeric token must be inside the supplied quote; its surrounding atom
    # supplies negation and units without confusing a second amount in the clause.
    source_start = text.index(fact.quote)
    source_end = source_start + len(fact.quote)
    matches = {(Decimal(match[1]) * factor, match.start(1), match.end(1)) for pattern, factor in units
               for match in re.finditer(pattern, text, re.I)
               if source_start <= match.start(1) and match.end(1) <= source_end}
    if len(matches) != 1:
        return None
    value, start, end = next(iter(matches))
    return (value, quote, (start, end)) if value == Decimal(str(fact.value)) else None


def _source_no_requirement(quote):
    """Only explicit absence of a requirement can skip its satisfaction check."""
    return bool(re.fullmatch(
        r"(?:无需|无须|不需要|不必|不用)(?:提前)?预约|(?:无|没有)(?:额外费用|其他门槛|其他条件|其他限制)", quote.strip()))


def source_analysis(state, extracted: SourceAnalysis):
    """Evaluate cited quantities and conditions; a computed answer is never write authority."""
    from datetime import timedelta

    page = analysis_source(state)
    if not page or (state.get("browser_task_context") or {}).get("kind") != "reasoning":
        return None
    records = analysis_records(page)
    context = state["browser_task_context"]
    request = task_text(state)
    requested = context.get("analysis_goals") or []
    if not requested:
        return None
    party = context.get("party_size")
    budget = _amount(context.get("total_budget"))
    per_budget = _amount(context.get("per_person_budget"))
    if per_budget is not None and isinstance(party, int):
        budget = min(budget, per_budget * party) if budget is not None else per_budget * party
    number = r"(?<![\d.+-])(\d+(?:\.\d+)?)(?![\d.])"
    money_units = [(number + r"\s*(?:元|CNY\b|RMB\b)", 1), (r"[¥￥]\s*" + number, 1)]
    people_units = [(number + r"\s*(?:位|名|个)?(?:成年人|成人|儿童|小孩|人)", 1)]
    route_units = {
        "distance_m": [(number + r"\s*(?:米|m\b)", 1), (number + r"\s*(?:公里|千米|km\b)", 1000)],
        "duration_seconds": [(number + r"\s*秒", 1), (number + r"\s*分钟", 60), (number + r"\s*小时", 3600)],
    }
    entries: list[dict[str, Any]] = []
    all_quotes: dict[str, str] = {}
    lines: list[str] = []
    missing: list[str] = []
    conflicts: list[str] = []
    delivered = set()
    def display(value):
        rendered = format(value, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    for index, option in enumerate(extracted.options):
        label = option.entity or f"资料{option.record + 1}"
        if option.record >= len(records):
            missing.append(f"{label}的资料记录无法对应")
            continue
        record = records[option.record]
        text = record["text"]
        if record.get("expires_at"):
            try:
                expires = datetime.fromisoformat(str(record["expires_at"]).replace("Z", "+00:00"))
                if not expires.tzinfo or expires <= datetime.now(timezone.utc):
                    raise ValueError("expired source")
            except ValueError:
                missing.append(f"{label}的来源有效期已过或无法核对")
                continue
        same_record = [other for other in extracted.options if other.record == option.record]
        block = _source_quote(text, option.quote) if option.quote else text if len(same_record) == 1 else None
        peers = [other.entity for other in extracted.options if other.record == option.record and other.entity and other.entity != option.entity]
        peers += [str(place.get("name") or "") for place in page["data"].get("places") or [] if place.get("name") != option.entity]
        if (not block or _source_instruction(block) or option.entity and option.entity not in block
                or any(peer and peer in block for peer in peers)):
            missing.append(f"{label}的实体与原文范围无法唯一对应")
            continue
        if option.quote and any(other.record == option.record and other is not option and option.quote in other.quote for other in extracted.options):
            missing.append(f"{label}与其他选项的原文范围重叠")
            continue
        notes, gaps, violations, quotes = [], [], [], {"source": block}
        option_delivered = set()
        conditions_known = True
        for condition in option.conditions:
            quote = _source_quote(block, condition.quote)
            user_quote = _source_quote(str(state.get("input_text") or ""), condition.request_quote) if condition.request_quote else None
            if not quote or _source_instruction(quote):
                gaps.append("部分使用条件未能对应原文")
                conditions_known = False
                continue
            quotes[f"condition:{len(quotes)}"] = quote
            assessment = condition.assessment
            # A model's interpretation can be shown with citations, but cannot
            # prove a reservation/eligibility or re-authorize an old user statement.
            if assessment != "no_condition" or not _source_no_requirement(quote):
                assessment = "unknown"
            if user_quote:
                quotes[f"request:{len(quotes)}"] = user_quote
            notes.append("原文条件：" + quote)
            if assessment == "unknown":
                gaps.append("条件尚待核对：" + quote)
                conditions_known = False
            else:
                notes.append("按所给原文，该项无需另满足条件。")
            option_delivered.add("applicability")
        subtotal, deductions = Decimal(0), []
        calculated = 0
        cost_complete = True
        used_quotes = set()
        package_count = None
        expense_values = {}
        for charge_index, charge in enumerate(option.charges):
            checked = _source_number(charge, block, money_units)
            if (not checked or charge.currency != "CNY"
                    or re.search(r"[$€£]|\b(?:USD|EUR|GBP)\b|原价|门市价", checked[1] if checked else block)
                    or charge.operation == "add" and re.search(r"面值|抵用金额", checked[1] if checked else block)):
                gaps.append("部分费用的数值、币种或售价口径无法核对")
                cost_complete = False
                continue
            amount, quote, token_span = checked
            token_id = (option.record, text.index(block) + token_span[0], text.index(block) + token_span[1])
            if token_id in used_quotes:
                gaps.append("同一费用不能重复计入")
                cost_complete = False
                continue
            used_quotes.add(token_id)
            person_unit = bool(re.search(r"[/／]\s*(?:人|位)|每(?:人|位)|人均", quote))
            package_unit = bool(re.search(r"[/／]\s*(?:份|套餐|套|张)|每(?:份|套|张)|一份|这份", quote))
            multiplier = None
            if re.search(r"[/／]\s*\d", quote):
                gaps.append("计价分母无法对应，未自动换算")
            elif charge.unit == "person" and person_unit and not package_unit:
                multiplier = Decimal(party) if isinstance(party, int) and not isinstance(party, bool) else None
                if multiplier is None:
                    gaps.append("同行人数待确认")
            elif charge.unit == "package" and package_unit and not person_unit:
                quantity = option.quantity
                quantity_quote = _source_quote(request, quantity.quote) if quantity else None
                proposed = Decimal(str(quantity.value)) if quantity else None
                if (quantity_quote and not re.search(r"(?:不|不要|别|不用)(?:再)?(?:买|购买|使用|加购)", quantity_quote)
                        and proposed is not None and 1 <= proposed <= 100 and proposed == proposed.to_integral_value()
                        and (re.search(r"(?<![\d.])" + re.escape(display(proposed)) + r"\s*(?:份|套|张)", quantity_quote)
                             or proposed == 1 and re.search(r"[一壹]\s*(?:份|套|张)", quantity_quote))):
                    multiplier = proposed
                    package_count = proposed
                    quotes["quantity"] = quantity_quote
                else:
                    gaps.append("购买份数尚未明确，未自动加购")
            elif charge.unit == "group" and not person_unit and not package_unit:
                multiplier = Decimal(1)
            else:
                gaps.append("每人、每份和整组计价单位不一致")
            quotes[f"charge:{len(quotes)}"] = quote
            notes.append("原文费用：" + quote)
            if multiplier is None:
                cost_complete = False
                continue
            if charge.operation == "deduct":
                threshold = _source_number(charge.threshold, block, money_units) if charge.threshold else None
                if charge.threshold and threshold is None:
                    gaps.append("优惠使用门槛金额无法核对，未扣减")
                    cost_complete = False
                    continue
                if threshold:
                    quotes[f"threshold:{len(quotes)}"] = threshold[1]
                if (not charge.applies_to or len(charge.applies_to) != len(set(charge.applies_to))
                        or any(i < 0 or i >= len(option.charges) or option.charges[i].operation != "add" for i in charge.applies_to)):
                    gaps.append("抵扣缺少对应消费项目，未把面值直接当作退款")
                    cost_complete = False
                    continue
                deductions.append((amount * multiplier, threshold[0] if threshold else None, quote, charge.applies_to))
            else:
                calculated += 1
                subtotal += amount * multiplier
                expense_values[charge_index] = amount * multiplier
                notes.append(f"该项{display(amount)}元" + (f"×{display(multiplier)}={display(amount * multiplier)}元" if charge.unit != "group" else "计入已知费用"))
        coverage = _source_number(option.covered_people, block, people_units)
        if option.covered_people and coverage is None:
            gaps.append("套餐覆盖人数无法按原文核对，成人与儿童不得混合计算资格")
        if coverage:
            covered, quote, _ = coverage
            quotes["covered_people"] = quote
            if re.search(r"成人|成年人", quote) and re.search(r"儿童|小孩", quote):
                gaps.append("覆盖人数包含成人与儿童，需分别核对，不能合并为通用人数")
            elif not covered == covered.to_integral_value() or covered < 1:
                gaps.append("覆盖人数不是有效人数")
            elif isinstance(party, int) and package_count is not None:
                option_delivered.add("applicability")
                if package_count * covered < party:
                    violations.append(f"每份仅明确覆盖{display(covered)}人，本次{party}人，所选份数不足")
                else:
                    notes.append(f"按原文人数口径，{display(package_count)}份覆盖{display(package_count * covered)}人，足以覆盖本次{party}人；年龄等资格另行核对。")
            notes.append("原文覆盖范围：" + quote)
            if re.search(r"成人|成年人|儿童|小孩", quote):
                notes.append("同行总人数不代表年龄资格已核对。")
                counts = context.get("party_counts") or {}
                eligible_count = counts.get("成人", 0) if re.search(r"成人|成年人", quote) else sum(counts.get(key, 0) for key in ("儿童", "孩子"))
                if not isinstance(party, int) or eligible_count != party:
                    gaps.append("尚未从本次已确认人数资料核对年龄资格")
        totals = {"distance_m": Decimal(0), "duration_seconds": Decimal(0)}
        complete_route = {key: bool(option.legs) for key in totals}
        legs = option.legs
        route_quotes = set()
        for leg_index, leg in enumerate(legs):
            for field, units in route_units.items():
                fact = getattr(leg, field)
                checked = _source_number(fact, block, units)
                if checked and (option.record, field, checked[2]) in route_quotes:
                    checked = None
                if checked:
                    route_quotes.add((option.record, field, checked[2]))
                    totals[field] += checked[0]
                    quotes[f"{field}:{leg_index}"] = checked[1]
                elif fact:
                    gaps.append("部分路线数值或单位无法核对")
                    complete_route[field] = False
                elif option.legs and not (field == "distance_m" and leg.kind == "wait"):
                    complete_route[field] = False
        for field, constraint in (("distance_m", "route_distance_km"), ("duration_seconds", "duration_minutes")):
            goal = "distance" if field == "distance_m" else "duration"
            if not complete_route[field] and (goal in requested or context.get(constraint) is not None):
                gaps.append("来源缺少完整路程，尚不能核对距离上限" if field == "distance_m" else "来源缺少完整用时，尚不能核对时长上限")
        arrival = None
        arrival_date = context.get("visit_date")
        for field, total in totals.items():
            if not complete_route[field]:
                continue
            option_delivered.add("distance" if field == "distance_m" else "duration")
            notes.append(f"资料给定{'各段距离合计' if field == 'distance_m' else '各段用时合计'}{display(total)}{'米' if field == 'distance_m' else '秒'}。")
            limit = context.get("route_distance_km") if field == "distance_m" else context.get("duration_minutes")
            if isinstance(limit, (int, float)) and total > Decimal(str(limit)) * (1000 if field == "distance_m" else 60):
                violations.append("给定路线距离超出本次路程限制" if field == "distance_m" else "给定用时超出本次总时长")
        if complete_route["duration_seconds"] and "arrival" in requested:
            try:
                departure = datetime.strptime(str(context.get("time_window_start")), "%H:%M")
                reached = departure + timedelta(seconds=float(totals["duration_seconds"]))
                offset = (reached.date() - departure.date()).days
                arrival = reached.strftime("%H:%M:%S")
                if arrival_date:
                    arrival_date = (datetime.strptime(str(arrival_date), "%Y-%m-%d") + timedelta(days=offset)).date().isoformat()
                notes.append(f"按本次{departure:%H:%M}出发及给定各段用时，预计{'次日' if offset == 1 else str(offset) + '天后' if offset else ''}{arrival}到达；未计原资料未给出的等候或换乘时间。")
                option_delivered.add("arrival")
            except ValueError:
                gaps.append("出发时刻未明确，尚不能计算到达时间")
        window_groups: dict[tuple, list[bool]] = {}
        for window in option.windows:
            quote = _source_quote(block, window.quote)
            if not quote or _source_instruction(quote):
                gaps.append("日期时段缺少原文依据")
                continue
            date_values = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in re.findall(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?", quote)]
            times = [f"{int(h):02d}:{m}" for h, m in re.findall(r"(?<!\d)([0-2]?\d):([0-5]\d)", quote)]
            days = {day: position for position, day in enumerate("一二三四五六日")}
            weekdays: set[int] = set()
            for match in re.finditer(r"(?:周|星期)([一二三四五六日天])(?:\s*(?:至|到|[-—])\s*(?:周|星期)?([一二三四五六日天]))?", quote):
                start = days.get(match[1], 6)
                end = days.get(match[2], 6) if match[2] else start
                if start <= end:
                    weekdays.update(range(start, end + 1))
            if "周末" in quote:
                weekdays.update((5, 6))
            excluded = bool(re.search(r"不可|不适用|不能|除外|排除|不在|不得", quote))
            if (window.dates != list(dict.fromkeys(date_values)) or window.times != list(dict.fromkeys(times))
                    or set(window.weekdays) != weekdays or window.excluded != excluded
                    or not (window.dates or window.times or weekdays)):
                gaps.append("日期时段、星期或排除条件未能完整对应原文")
                continue
            matches = []
            for bounds, selected, temporal in ((window.dates, arrival_date, "日期"),
                                               (window.times, arrival or context.get("time_window_start"), "时间")):
                if not bounds:
                    continue
                try:
                    for bound in bounds:
                        datetime.strptime(bound, "%Y-%m-%d" if temporal == "日期" else "%H:%M")
                except ValueError:
                    gaps.append("来源含无效日期或时刻")
                    continue
                if not selected or bounds[0] > bounds[-1] or window.boundary != "range" and len(bounds) != 1:
                    gaps.append(f"本次{temporal}或来源范围尚不能核对")
                    continue
                current = str(selected)
                lower, upper = bounds[0], bounds[-1]
                if temporal == "时间":
                    current = current if len(current) == 8 else current + ":00"
                    lower, upper = lower + ":00", upper + ":00"
                matches.append(current <= upper if window.boundary == "latest" else current >= lower if window.boundary == "earliest" else lower <= current <= upper)
            if weekdays:
                try:
                    matches.append(datetime.strptime(str(arrival_date), "%Y-%m-%d").weekday() in weekdays)
                except ValueError:
                    gaps.append("日期未明确，尚不能核对星期条件")
            quotes[f"window:{len(quotes)}"] = quote
            notes.append("资料时段：" + quote)
            if matches:
                passed = not all(matches) if window.excluded else all(matches)
                group = (bool(window.dates), bool(window.times), bool(window.weekdays), window.boundary, window.excluded)
                window_groups.setdefault(group, []).append(passed)
                option_delivered.add("applicability")
        # Separate opening intervals are alternatives; independent date, weekday
        # and earliest/latest constraints all apply. Every exclusion still applies.
        window_matches = [all(values) if group[-1] else any(values) for group, values in window_groups.items()]
        if window_matches:
            if all(window_matches):
                notes.append("按给定日期、时刻与原文范围核算，已核对的日期/时段/星期条件相符。其他条件见各项原文核对。")
            else:
                violations.append("所选日期或时间不在原文允许使用范围内" + ("，按给定路线到达已不满足时段" if arrival else ""))
        conditional_cost = None
        for deduction, threshold, quote, targets in deductions:
            eligible = sum((expense_values.get(i, Decimal(0)) for i in targets), Decimal(0))
            if len(deductions) > 1:
                gaps.append("多项优惠的叠加顺序尚未核对，未重复扣减")
                cost_complete = False
                continue
            if any(i not in expense_values for i in targets):
                gaps.append("抵扣对应的消费金额尚未核对")
                cost_complete = False
                continue
            if threshold is not None and eligible < threshold:
                violations.append(f"优惠前对应消费金额{display(eligible)}元未达到{display(threshold)}元门槛，未扣减该优惠")
                option_delivered.add("applicability")
            elif not calculated or deduction > eligible:
                gaps.append("优惠缺少足够的对应消费金额，未产生负消费或直接抵现")
                cost_complete = False
            elif violations:
                notes.append("当前已知条件不适用，未扣减该优惠；保留无优惠的已知费用。")
            elif not cost_complete:
                gaps.append("对应费用尚未完整核对，未计算优惠后的金额")
            elif not conditions_known or gaps:
                gaps.append("优惠条件尚未确认，未作为已享优惠扣减")
                conditional_cost = subtotal - deduction
                notes.append(f"仅在所给优惠条件均满足的假设下，扣减{display(deduction)}元后的计算值为{display(conditional_cost)}元；当前未确认这些条件，已知小计保留无优惠金额。")
                cost_complete = False
            else:
                subtotal -= deduction
                notes.append(f"按原文扣减{display(deduction)}元，已知项目小计为{display(subtotal)}元；依据：{quote}")
        if calculated and (cost_complete or conditional_cost is not None):
            option_delivered.add("cost")
        if not option_delivered:
            missing.extend(f"{label}：{gap}" for gap in gaps)
            continue
        cost = float(subtotal) if calculated else None
        entry: dict[str, Any] = {"entity": label, "record": option.record, "known_subtotal": cost, "calculation_complete": cost_complete, "conditional_subtotal": float(conditional_cost) if conditional_cost is not None else None,
                 "missing_rules": list(dict.fromkeys(gaps)), "conflicts": list(dict.fromkeys(violations)), "quotes": quotes,
                 "route": {key: float(value) if complete_route[key] else None for key, value in totals.items()}, "arrival_time": arrival, "delivered": sorted(option_delivered)}
        entries.append(entry)
        delivered.update(option_delivered)
        all_quotes.update({f"{index}:{key}": value for key, value in quotes.items()})
        lines.append(label + "：\n" + "\n".join(dict.fromkeys(notes)))
        if cost is not None:
            lines.append(f"上述已知项目小计{display(subtotal)}元" + (f"，{'未超出' if subtotal <= budget else '超出'}{display(budget)}元预算。" if budget is not None else "。"))
            if budget is not None:
                lines.append(f"按已知项目扣除后预算剩余{display(budget - subtotal)}元。")
        if "applicability" in requested and "applicability" in option_delivered:
            lines.append("按已列出的原文依据，本次存在明确不适用条件。" if violations else "部分适用条件仍未知，当前不能确认完整适用。" if gaps else "按所给资料与本次要求，已列出的条件相符；这只是资料条件判断，实时供给仍未核实。")
        missing.extend(f"{label}：{gap}" for gap in entry["missing_rules"])
        conflicts.extend(f"{label}：{violation}" for violation in entry["conflicts"])
    if not entries:
        return None
    delivered = set.intersection(*(set(entry["delivered"]) for entry in entries))
    if len(entries) != len(extracted.options):
        delivered.clear()  # A good option cannot hide another option's rejected source.
    if len(entries) == len(extracted.options) and len(entries) > 1 and all(entry["known_subtotal"] is not None and entry["calculation_complete"] for entry in entries):
        ranked = sorted(entries, key=lambda entry: entry["known_subtotal"])
        difference = Decimal(str(ranked[-1]["known_subtotal"])) - Decimal(str(ranked[0]["known_subtotal"]))
        lines.append(f"按各选项已明确的计价范围，{ranked[0]['entity']}的已知项目小计最低，与最高项相差{display(difference)}元；各自覆盖范围和使用条件仍须分别核对。")
        delivered.add("comparison")
    unanswered = sorted(set(requested) - delivered)
    missing.extend("尚未完成" + {"cost": "费用核算", "comparison": "选项比较", "applicability": "条件判断", "distance": "路程核算", "duration": "用时核算", "arrival": "到达时间核算"}[goal] for goal in unanswered)
    lines.extend(conflicts)
    if missing:
        lines.append("仍缺依据：" + "；".join(dict.fromkeys(missing)) + "。")
    lines.append("以上是所给资料的条件核算。已知项目小计不代表完整消费保证；未列明的费用、年龄资格、预约及优惠使用条件仍需按原文核对。没有查询实时供给、购买或提交预约。")
    return ExecutionOutcome(kind="page_read", status="mismatch" if conflicts else "needs_evidence" if missing else "satisfied", summary="\n".join(lines),
        evidence_ids=[page["artifact_id"]], data={"scope": "source_analysis", "business_completed": False, "answered": not unanswered,
        "total_cost": entries[0]["known_subtotal"] if len(entries) == 1 and entries[0]["calculation_complete"] else None, "complete_cost": False,
        "entries": entries, "requested": requested, "delivered": sorted(delivered), "missing_rules": list(dict.fromkeys(missing)), "conflicts": conflicts,
        "quotes": all_quotes, "source_url": page["url"]})


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
