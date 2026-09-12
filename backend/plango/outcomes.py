"""Task outcomes are derived from observed facts, never from a model's finish flag."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from plango_harness.agent.contracts import Evidence, PlanStop, TripSpec
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
_READ_FIELD_LABELS: dict[ReadField, str] = {
    "merchant": "门店身份", "address": "门店地址", "menu": "菜单详情",
    "recommended_dishes": "推荐菜", "offers": "套餐条目", "offer_conditions": "套餐使用条件",
}


class ReadGoal(BaseModel):
    """What a read step must produce.

    ``required_fields`` is set by whoever asked for the read. It is never guessed from
    the phrasing of the request: a keyword scan recognised only the vocabulary it was
    written against, held restaurant wording to a stricter bar than anything else, and
    turned a question the page could answer into a missing-field report.
    """

    model_config = ConfigDict(extra="forbid")
    kind: Literal["page_read", "menu_read"]
    request: str
    source: Literal["browser", "user_image"] = "browser"
    required_fields: list[ReadField] = Field(default_factory=list)


class ExecutionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["page_read", "menu_read", "itinerary_preparation", "planning_draft", "task_answer"]
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
    if (state.get("browser_image_turn_id") == state.get("turn_id", 1)
            and str(state.get("browser_image_context") or "").strip()
            and (context.get("mode") != "browser" or context.get("kind") != "extract")):
        return ReadGoal(kind="page_read", request=task_text({**state, "browser_task_context": context}), source="user_image").model_dump(mode="json")
    if context.get("mode") != "browser" or context.get("kind") != "extract":
        return None
    request = task_text({**state, "browser_task_context": context})
    # The task owner states the source and kind; they are not inferred from wording.
    source = context.get("read_source") or "browser"
    kind = context.get("read_kind") or "page_read"
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
    goal = state.get("execution_goal") or {}
    if isinstance(goal, dict) and goal.get("kind") == "itinerary_preparation":
        return None
    raw = goal if isinstance(goal, dict) and goal.get("kind") in {"page_read", "menu_read"} else read_goal(state, state.get("browser_task_context") or {})
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
            if item.get("url") != observation.get("url") or item.get("snapshot_id") != observation.get("snapshot_id"):
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
        observed = [field for field in _READ_FIELD_LABELS if field in observed_fields]
        partial = ["offer_conditions"] if partial_conditions and "offer_conditions" not in observed_fields else []
        summary = "已读取有来源的" + "、".join(_READ_FIELD_LABELS[field] for field in observed) if observed else "尚未读取到请求的结构化内容"
        if partial:
            summary += "；已保留页面展示的部分套餐条件"
        summary += "；仍缺" + "、".join(_READ_FIELD_LABELS[field] for field in missing) if missing else "；请求的读取范围已覆盖"
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


def _form_control(form, aliases, input_type=None):
    values = []
    for control in form.controls:
        labels = {re.sub(r"[\s:：*（）()]", "", value).casefold() for value in [control.name, *control.label.split(" / ")]}
        if not control.disabled and labels & aliases and isinstance(control.value, str):
            values.append(control)
    if len(values) == 1:
        return values[0]
    if input_type:
        typed = [control for control in form.controls if not control.disabled and control.input_type == input_type and isinstance(control.value, str)]
        return typed[0] if len(typed) == 1 else None
    return None


def _form_identity(compact, name, address):
    if not name or not address or name not in compact or address not in compact:
        return False
    first = compact.find(name)
    return first >= 0 and not re.search(r"并非|不是|非本次", compact[:first])


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
                compact = re.sub(r"\s+", " ", form.context_text).strip()
                name = re.sub(r"\s+", " ", stop.name).strip()
                address = re.sub(r"\s+", " ", stop.address).strip()
                if not _form_identity(compact, name, address):
                    continue
                elements = {element.get("idx"): element for element in data.get("elements") or [] if isinstance(element, dict)}
                if any(control.idx not in elements or elements[control.idx].get("tag") not in {"input", "textarea", "select"}
                       or elements[control.idx].get("input_type") != control.input_type
                       or elements[control.idx].get("disabled") is True for control in form.controls if not control.disabled):
                    continue
                controls = {
                    "party_size": _form_control(form, {"人数", "用餐人数", "就餐人数", "预约人数", "同行人数", "party_size", "partysize", "guests", "people"}, "number"),
                    "date": _form_control(form, {"日期", "预约日期", "预订日期", "到店日期", "用餐日期", "visit_date", "reservation_date", "date"}, "date"),
                    "time": _form_control(form, {"时间", "预约时间", "预订时间", "到店时间", "用餐时间", "visit_time", "reservation_time", "time"}, "time"),
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
                           and elements[index].get("input_type") in {"submit", "image"} and not elements[index].get("disabled")]
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


def task_text(state: dict[str, Any]) -> str:
    context = state.get("browser_task_context") or {}
    if context.get("mode") != "browser":
        return str(state.get("input_text") or "")
    return (
        str(context.get("request") or "")
        + "\n后续修改（后文优先）：\n"
        + "\n".join(context.get("edits", []))
    )


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
