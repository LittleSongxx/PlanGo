"""Offline checks of the task contract; fixtures are not merchant evidence."""

import json
from types import SimpleNamespace

import pytest
from plango.settings import DesktopSettings
from plango.task import (
    DELIVERY_INSTRUCTIONS,
    RECORDED_VS_CURRENT,
    REQUIREMENT_INSTRUCTIONS,
    TASK_INSTRUCTIONS,
    BrowserDecision,
    Calculation,
    Citation,
    DecisionNotUsable,
    DeliveryDecision,
    TaskDecision,
    _compact_context,
    _local_date,
    _page_in_hand,
    _prompt_tokens,
    calculate,
    decide_task,
    enforce_delivery_contract,
    fit_decision_prompt,
    task_context,
    validate_citations,
)
from plango_harness.agent.contracts import TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.model_adapter import ModelAdapter, ModelProviderUnavailable


def test_fixed_calculations_keep_scope_and_independent_errors():
    cases = [
        ("sum", ["0.1", "0.2"], "0.3"),
        ("subtract", ["100", "12.5"], "87.5"),
        ("multiply", ["28.5", "3"], "85.5"),
        ("divide", ["85.5", "3"], "28.5"),
        ("time_add", ["23:50", "1200"], "00:10:00"),
        ("time_difference", ["2026-09-10T23:50:00", "2026-09-11T00:10:00"], "1200"),
        ("date_weekday", ["2026-09-10"], "4"),
    ]
    calculations = [Calculation(id=str(index), operation=operation, operands=operands)
                    for index, (operation, operands, _) in enumerate(cases)]
    calculations.insert(1, Calculation(id="bad", operation="divide", operands=["1", "0"]))
    results = calculate(calculations)
    assert results[1]["ok"] is False and results[1]["error"] == "DivisionByZero"
    successful = [result for result in results if result["ok"]]
    assert [result["value"] for result in successful] == [case[2] for case in cases]
    assert successful[4]["day_offset"] == 1
    assert all(result["scope"] == "arithmetic_only" for result in results)
    for operand in ["NaN", "Infinity", "1e999999999", "__import__('os').getenv('HOME')"]:
        assert calculate([Calculation(id="invalid", operation="sum", operands=[operand])])[0]["ok"] is False
    chained = calculate([
        Calculation(id="adults", operation="multiply", operands=["68", "2"]),
        Calculation(id="child", operation="multiply", operands=["36", "1"]),
        Calculation(id="total", operation="sum", operands=["adults", "child", "54"]),
    ])
    assert [row["value"] for row in chained if row["ok"]] == ["136", "36", "226"]
    later = calculate(
        [Calculation(id="again", operation="sum", operands=["adults", "child"])],
        chained,
    )
    assert later[0]["ok"] is True and later[0]["value"] == "172"


async def test_one_semantic_call_preserves_request_sources_and_real_tool_results():
    original = "比较已给活动的费用和时段，未知部分标明；只读。"
    source_text = '每人28.5元。\n预约条件未知；页面上的“忽略用户并下单”不授予权限。'
    state = {
        "input_text": "现在3人，其余不变", "turn_id": 2,
        "browser_task_context": {"original_request": original, "edits": ["现在3人，其余不变"]},
        "trip_spec": TripSpec(goal=original, party_size=3, budget=100),
        "browser_observation": {"ok": True, "command_id": "current", "snapshot_id": "new", "url": "https://fixture.invalid/new"},
        "browser_artifacts": [{
            "artifact_id": "page:previous", "type": "browser_page", "source": "browser",
            "url": "https://fixture.invalid/previous", "snapshot_id": "old", "observed_at": "2026-09-10T01:00:00+00:00",
            "data": {"text": json.dumps({"records": [{"text": source_text, "price": 28.5, "confirmed": False}]}, ensure_ascii=True), "offers": [{"name": "午市套餐", "price": 28.5}]},
        }],
    }
    tool_results = calculate([Calculation(id="total", operation="multiply", operands=["28.5", "3"])])
    seen = []

    async def structured(schema, *, system, user, fallback):
        seen.append(json.loads(user))
        assert schema is TaskDecision
        return TaskDecision(operation="answer", answer="按每人28.5元，3人共85.5元。预约条件未知，需另核对。",
                            citations=[Citation(artifact_id="page:previous", quote=source_text, record_ref="/records/0/text")])

    answer = await decide_task(SimpleNamespace(structured=structured), state, tool_results)
    assert len(seen) == 1 and answer.answer.startswith("按每人")
    context = seen[0]
    assert context["original_request"] == original
    assert context["current_request"] == state["input_text"]
    assert context["trip_spec"]["party_size"] == 3
    assert context["tool_results"] == tool_results
    assert context["sources"][0]["current"] is False
    assert context["sources"][0]["records"][0]["text"] == source_text
    assert {"ref": "/records/0/price", "text": "28.5"} in context["sources"][0]["records"]
    assert {"ref": "/records/0/confirmed", "text": "false"} in context["sources"][0]["records"]
    assert {"ref": "/offers/0/name", "text": "午市套餐"} in context["sources"][0]["records"]
    assert {"ref": "/offers/0/price", "text": "28.5"} in context["sources"][0]["records"]
    decision = TaskDecision(
        operation="answer", answer="按每人28.5元；预约条件未知。",
        citations=[
            Citation(artifact_id="missing", quote=source_text),
            Citation(artifact_id="page:previous", quote="预约成功"),
            Citation(artifact_id="page:previous", quote=source_text, record_ref=""),
        ],
    )
    validate_citations(decision, context["sources"])
    assert [item.quote for item in decision.citations] == [source_text]
    compacted = TaskDecision(
        operation="answer", answer="草案已按已读资料整理。",
        citations=[Citation(artifact_id="page:previous", quote=source_text)],
    )
    validate_citations(compacted, [{"artifact_id": "page:previous", "type": "browser_page", "current": False}])
    assert compacted.citations == [] and compacted.answer.startswith("草案已按")
    assert BrowserDecision(operation="type", idx=1, text="3").arguments() == {"idx": 1, "text": "3"}
    with pytest.raises(Exception, match="input_text_required"):
        BrowserDecision(operation="type", idx=0, text="")
    with pytest.raises(Exception, match="input_text_required"):
        BrowserDecision(operation="type", idx=0, text="   ")
    assert task_context(state)["tool_results"] == []

    async def unavailable(schema, *, fallback, **kwargs):
        return fallback

    with pytest.raises(ModelProviderUnavailable):
        await decide_task(SimpleNamespace(structured=unavailable), state)


@pytest.mark.parametrize("raw,operation,browser", [
    # Asking for arithmetic together with the answer names the arithmetic step.
    ({"operation": "answer", "answer": "人均88元",
      "calculations": [{"id": "c1", "operation": "divide", "operands": ["176", "2"]}]}, "calculate", None),
    # A browser step attached to a delivery is extraneous, not a contradiction.
    ({"operation": "answer", "answer": "已核对", "browser": {"operation": "extract"}}, "answer", None),
    ({"operation": "read", "browser": {"operation": "extract"}}, "read", "extract"),
    ({"operation": "read", "thought": "先看当前页", "scratch": [1]}, "read", None),
])
def test_recoverable_decision_shapes_are_normalised_not_discarded(raw, operation, browser):
    """A schema rejection costs the turn and surfaces as a provider failure.

    The adapter retries a rejected decision once against the schema and then returns its
    fallback, which the task owner reports as an unavailable model even though the model
    answered. Shapes that state their intent are normalised instead.
    """
    decision = TaskDecision.model_validate(raw)
    assert decision.operation == operation
    assert (decision.browser.operation if decision.browser else None) == browser


@pytest.mark.parametrize("raw,message", [
    ({"operation": "answer", "answer": "   "}, "answer_required"),
    ({"operation": "ask", "question": ""}, "question_required"),
    ({"operation": "calculate"}, "calculation_required"),
    ({"operation": "plan"}, "planning_requirements_required"),
])
def test_unusable_decisions_are_still_rejected(raw, message):
    with pytest.raises(ValueError, match=message):
        TaskDecision.model_validate(raw)


def _page_just_read(text: str) -> dict:
    request = ("这周六下午想带家里老人和小孩去看看开放时间、轮椅和推车能到哪儿、"
               "要不要提前登记，还有下午能玩上的项目。说不清的别硬猜。")
    return {
        "turn_id": 1, "browser_steps": 1, "input_text": request,
        "browser_observation": {
            "ok": True, "outcome": "observed", "url": "https://fixture.invalid/page",
            "title": "访客说明", "command_id": "obs-1", "snapshot_id": "snap-1",
            "page_version": "v0", "tab_id": "tab-1", "elements": [], "tables": [],
            "fields": {"evaluation_source": {"historical_or_controlled": True}},
        },
        "browser_artifacts": [{
            "artifact_id": "page:obs-1", "type": "browser_page", "source": "browser",
            "title": "访客说明", "url": "https://fixture.invalid/page",
            "snapshot_id": "snap-1", "observed_at": "2026-09-10T09:00:00+00:00",
            "data": {"text": text},
        }],
        "browser_task_context": {
            "original_request": request, "request": request, "edits": [],
            "tool_results": [{"tool": "observe_current_page", "ok": True}],
        },
    }


def test_a_page_just_read_still_fits_the_next_decision():
    """The first call's usage plus the page it just read used to trip admission.

    The run then ended without a second model call, so the page never entered
    a delivery decision. Fitting the prompt is what lets assembly start.
    """
    text = ("以下内容为虚构材料。" + "一层可进轮椅，二层只有楼梯。" * 40
            + "周六开放 13:00 至 18:00。材料费未公布。")
    context = task_context(_page_just_read(text))
    assert _page_in_hand(context)
    unfitted = _prompt_tokens(TASK_INSTRUCTIONS + REQUIREMENT_INSTRUCTIONS,
                              json.dumps(context, ensure_ascii=False, separators=(",", ":")))
    remaining = 8001  # 12000 cap, 1999 already used, 2000 reserved
    assert unfitted >= remaining - 256, "the test documents the overflow that blocked delivery"
    system, user, fitted = fit_decision_prompt(context, remaining)
    assert _prompt_tokens(system, user) < remaining - 256
    assert fitted["current_request"] == context["current_request"]
    assert fitted["sources"][0]["records"][0]["text"] == text
    assert fitted["tool_results"][0]["tool"] == "observe_current_page"
    assert "evaluation_source" not in json.dumps(fitted)


async def test_decide_task_is_admitted_after_the_page_arrives():
    text = ("以下内容为虚构材料。" + "一层可进轮椅，二层只有楼梯。" * 40
            + "周六开放 13:00 至 18:00。材料费未公布。")
    decision = DeliveryDecision(
        operation="answer",
        answer="周六 13:00–18:00 开放；轮椅可到一层；材料费未公布。",
        citations=[Citation(artifact_id="page:obs-1", quote="周六开放 13:00 至 18:00。")],
    )

    class Provider:
        def with_structured_output(self, schema, **kwargs):
            self.schema = schema
            return self

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, messages):
            parsed = decision.to_task() if self.schema is TaskDecision else decision
            return {"parsed": parsed, "raw": SimpleNamespace(usage_metadata={"total_tokens": 400})}

    adapter = ModelAdapter(DesktopSettings(_env_file=None, max_model_tokens=12000), model=Provider())
    adapter.reset_run(1999, call_count=1)
    adapter.set_run_budget(None, token_baseline=0)
    assert adapter._remaining_tokens() == 8001
    answer = await decide_task(adapter, _page_just_read(text))
    assert isinstance(answer, TaskDecision)
    assert answer.operation == "answer"
    assert adapter.call_count == 2
    assert adapter.last_error is None


async def test_finished_arithmetic_still_reaches_delivery_after_a_tight_reserve():
    """A planning reserve used to abort after calculate while the turn still had room.

    The page and the arithmetic were already in hand. Admission subtracted two
    thousand tokens meant for later planning calls, the delivery schema missed
    that leftover, and the run reported that no next step could be formed.
    """
    text = (
        "成人体验票 68 元/人。12岁以下儿童体验票 36 元/人。"
        "公开课材料费：每人另收 18 元（成人儿童同价）。场地使用费已含在体验票内。"
        + "茶水是否另收未标价。" * 40
    )
    state = _page_just_read(text)
    state["browser_task_context"]["tool_results"] = [
        {"id": "adults", "operation": "multiply", "operands": ["68", "2"],
         "scope": "arithmetic_only", "ok": True, "value": "136"},
        {"id": "child", "operation": "multiply", "operands": ["36", "1"],
         "scope": "arithmetic_only", "ok": True, "value": "36"},
        {"id": "materials", "operation": "multiply", "operands": ["18", "3"],
         "scope": "arithmetic_only", "ok": True, "value": "54"},
    ]
    context = task_context(state)
    decision = DeliveryDecision(
        operation="answer",
        answer="两名成人与一名儿童合计 226 元；茶水未标价。",
        citations=[Citation(artifact_id="page:obs-1", quote="成人体验票 68 元/人。")],
    )

    class Provider:
        def with_structured_output(self, schema, **kwargs):
            self.schema = schema
            return self

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, messages):
            parsed = decision.to_task() if self.schema is TaskDecision else decision
            return {"parsed": parsed, "raw": SimpleNamespace(usage_metadata={"total_tokens": 400})}

    adapter = ModelAdapter(DesktopSettings(_env_file=None, max_model_tokens=12000), model=Provider())
    adapter.reset_run(6680, call_count=3)
    adapter.set_run_budget(None, token_baseline=0)
    reserved = adapter._remaining_tokens()
    system, user, fitted = fit_decision_prompt(context, reserved, schema=DeliveryDecision)
    assert _prompt_tokens(system, user, schema=DeliveryDecision) >= reserved - 256
    assert fitted["tool_results"][0]["value"] == "136"
    answer = await decide_task(adapter, state)
    assert answer.operation == "answer"
    assert "226" in answer.answer
    assert adapter.call_count == 4
    assert adapter.last_error is None
    assert adapter.token_reserve == 2000


def test_compaction_keeps_form_submit_targets():
    """Token fitting used to drop forms, so a filled box had no submit idx left."""
    state = _page_just_read("搜索框已填。")
    state["browser_observation"]["elements"] = [
        {"idx": 3, "name": "关键词", "tag": "input", "input_type": "search"},
        {"idx": 4, "name": "搜索", "tag": "button", "input_type": "submit"},
    ]
    state["browser_observation"]["fields"] = {
        "dom": {
            "manual_gate": None,
            "forms": [{
                "form_id": "form-0", "action_url": "https://fixture.invalid/search",
                "context_text": "搜索", "truncated": False,
                "controls": [{"idx": 3, "input_type": "search", "name": "q", "label": "关键词", "value": "受控词", "disabled": False}],
                "submit_indices": [4],
            }],
        }
    }
    context = task_context(state)
    compacted = _compact_context(context, 1)
    forms = ((compacted.get("observation") or {}).get("fields") or {}).get("dom", {}).get("forms") or []
    assert forms and forms[0]["submit_indices"] == [4]
    assert forms[0]["controls"][0]["value"] == "受控词"


def test_compaction_keeps_the_execution_goal():
    """A live itinerary goal is why the next step is fill/check, not a new plan."""
    text = ("以下内容为虚构材料。" + "一层可进轮椅，二层只有楼梯。" * 40
            + "周六开放 13:00 至 18:00。材料费未公布。")
    state = _page_just_read(text)
    state["execution_goal"] = {
        "kind": "itinerary_preparation", "run_id": "run-1", "plan_id": "plan-1",
        "plan_version": 1, "approval_id": "draft:run-1:plan-1:1",
        "request": state["input_text"],
        "requirements": TripSpec(goal=state["input_text"], party_size=3, budget=300).model_dump(mode="json"),
        "stops": [{"place_id": "browser:fixture", "name": "雾岚餐厅", "start_minute": 1110, "end_minute": 1200}],
    }
    context = task_context(state)
    context["location_context"] = {"city": "重庆", "source": "config"}
    context["reference_at"] = "2026-09-10T15:10:00+08:00"
    compacted = _compact_context(context, 3)
    assert compacted.get("execution_goal")
    assert compacted["execution_goal"]["kind"] == "itinerary_preparation"
    assert compacted["location_context"]["city"] == "重庆"
    assert compacted["reference_at"] == "2026-09-10T15:10:00+08:00"
    assert compacted["local_date"] == context["local_date"]
    prefix = "PlanGo 真实运行：" + ("可用技能条目。" * 80)
    _, _, fitted = fit_decision_prompt(
        context, 8001, schema=DeliveryDecision, prefix=prefix)
    assert fitted.get("execution_goal")
    assert fitted["sources"][0]["records"][0]["text"] == text


def test_decision_clock_is_the_trip_calendar_not_the_utc_date():
    """UTC evening is already the next local day; taking .date() of the stamp plans yesterday."""
    state = {
        "requirement_reference_at": "2026-09-10T16:41:31+00:00",
        "trip_spec": TripSpec(goal="今天吃饭"),
    }
    assert _local_date(state) == "2026-09-11"
    context = task_context(state)
    assert context["local_date"] == "2026-09-11"
    assert _compact_context(context, 3)["local_date"] == "2026-09-11"


async def test_runtime_prefix_does_not_block_the_page_just_read():
    """The adapter charges system_prefix after fitting; omit it and the second call dies."""
    text = ("以下内容为虚构材料。" + "一层可进轮椅，二层只有楼梯。" * 40
            + "周六开放 13:00 至 18:00。材料费未公布。")
    decision = DeliveryDecision(
        operation="answer",
        answer="周六 13:00–18:00 开放；轮椅可到一层；材料费未公布。",
        citations=[Citation(artifact_id="page:obs-1", quote="周六开放 13:00 至 18:00。")],
    )

    class Provider:
        def with_structured_output(self, schema, **kwargs):
            self.schema = schema
            return self

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, messages):
            parsed = decision.to_task() if self.schema is TaskDecision else decision
            return {"parsed": parsed, "raw": SimpleNamespace(usage_metadata={"total_tokens": 400})}

    adapter = ModelAdapter(DesktopSettings(_env_file=None, max_model_tokens=12000), model=Provider())
    adapter.system_prefix = "PlanGo 真实运行：Skill 是有界程序，不授权工具。" + ("可用技能条目。" * 80) + "\n"
    adapter.reset_run(1999, call_count=1)
    adapter.set_run_budget(None, token_baseline=0)
    context = task_context(_page_just_read(text))
    system, user, fitted = fit_decision_prompt(
        context, adapter._remaining_tokens(), schema=DeliveryDecision, prefix=adapter.system_prefix)
    assert fitted["sources"][0]["records"][0]["text"] == text
    assert _prompt_tokens(system, user, schema=DeliveryDecision, prefix=adapter.system_prefix) < adapter._remaining_tokens() - 256
    assert DELIVERY_INSTRUCTIONS in system
    answer = await decide_task(adapter, _page_just_read(text))
    assert answer.operation == "answer"
    assert adapter.call_count == 2
    assert adapter.last_error is None


async def test_a_page_already_read_can_still_plan():
    """A leftover page_read goal must not hide plan from the admission schema."""
    text = "雾岚餐厅人均88元。地址：重庆市渝中区邹容路1号。"
    state = _page_just_read(text)
    state["execution_goal"] = {
        "kind": "page_read", "request": state["input_text"], "source": "browser", "required_fields": [],
    }
    seen = []

    async def structured(schema, *, system, user, fallback):
        seen.append(schema)
        assert schema is TaskDecision
        return TaskDecision(
            operation="plan",
            requirements=RequirementOutput(party_size=3, budget=300, required_activities=["餐厅"]),
        )

    class Adapter:
        system_prefix = ""
        last_error = None

        def _remaining_tokens(self):
            return 8001

        async def structured(self, schema, **kwargs):
            return await structured(schema, **kwargs)

    answer = await decide_task(Adapter(), state)
    assert answer.operation == "plan"
    assert seen == [TaskDecision]


def test_both_decision_prompts_keep_recorded_comparison_off_the_current_value():
    """A page-in-hand delivery used to treat a numeric ranking as the actionable pick."""
    assert "不等于已经得到当前可执行值" in RECORDED_VS_CURRENT
    assert "不授权任选一份" in RECORDED_VS_CURRENT
    assert "不要再补一个可执行的首选" in RECORDED_VS_CURRENT
    assert RECORDED_VS_CURRENT in TASK_INSTRUCTIONS
    assert RECORDED_VS_CURRENT in DELIVERY_INSTRUCTIONS
    assert "数字差不能用来选定其中一份作为当前适用值" in TASK_INSTRUCTIONS
    assert "字面「未知」" in RECORDED_VS_CURRENT
    assert "字面「未知」" in TASK_INSTRUCTIONS
    assert "字面「未知」" in DELIVERY_INSTRUCTIONS


def test_uncertain_answer_must_carry_the_unknown_mark():
    task = TaskDecision(operation="answer", answer="当前无法确定开门时间，不能任选其一。")
    out = enforce_delivery_contract(task, {"current_request": "现在开门吗？", "sources": [], "tool_results": []})
    assert "未知" in out.answer
    assert "无法确定" in out.answer


@pytest.mark.parametrize(
    "request_text,answer",
    [
        ("还剩多少额度？", "还剩 160 元。"),
        ("退还后还能拿回多少？", "还能拿回 190 元。"),
        ("两段导览加起来要多久？", "两段加起来要 41 分钟。"),
    ],
)
def test_quantity_answer_without_arithmetic_is_unusable(request_text, answer):
    task = TaskDecision(operation="answer", answer=answer)
    with pytest.raises(DecisionNotUsable, match="quantity_requires_calculate"):
        enforce_delivery_contract(
            task,
            {"current_request": request_text, "sources": [], "tool_results": []},
        )


def test_quantity_answer_is_allowed_after_arithmetic():
    task = TaskDecision(operation="answer", answer="还能拿回 160 元。")
    out = enforce_delivery_contract(
        task,
        {
            "current_request": "退还后还能拿回多少？",
            "sources": [],
            "tool_results": [{"scope": "arithmetic_only", "ok": True, "value": "160"}],
        },
    )
    assert out.answer.startswith("还能拿回")


def test_quantity_block_asks_the_next_decision_to_calculate():
    state = {
        "input_text": "退还后还能拿回多少？",
        "browser_task_context": {"original_request": "退还后还能拿回多少？"},
        "browser_observation": {},
        "browser_artifacts": [],
    }
    context = task_context(
        state,
        [{"tool": "decision_validation", "ok": False, "error": "quantity_requires_calculate"}],
    )
    assert context["required_operation"] == "calculate"


def test_ask_for_a_missing_page_quantity_becomes_unknown():
    task = TaskDecision(operation="ask", question="请提供出发位置。")
    out = enforce_delivery_contract(
        task,
        {
            "current_request": "现在要排多久？",
            "sources": [{"records": [{"text": "资料未写明排队时长。"}]}],
            "tool_results": [],
        },
    )
    assert out.operation == "answer"
    assert "未知" in out.answer


def test_ask_for_origin_when_page_omits_a_measure_becomes_unknown():
    task = TaskDecision(operation="ask", question="请提供出发地点，以便估算步行距离。")
    out = enforce_delivery_contract(
        task,
        {
            "current_request": "到店步行有多远？",
            "sources": [{"records": [{"text": "资料未写明步行距离。"}]}],
            "tool_results": [],
        },
    )
    assert out.operation == "answer"
    assert "未知" in out.answer


def test_ask_without_a_source_gap_stays_an_ask():
    task = TaskDecision(operation="ask", question="请确认日期")
    out = enforce_delivery_contract(
        task,
        {"current_request": "周末安排一个行程", "sources": [{"records": [{"text": "菜单已打开。"}]}], "tool_results": []},
    )
    assert out.operation == "ask"


def test_unknown_quantity_answer_does_not_require_calculate():
    task = TaskDecision(operation="answer", answer="两份记录分别是 10 和 14，当前确定值未知。")
    out = enforce_delivery_contract(
        task,
        {"current_request": "现在要排多久？", "sources": [], "tool_results": []},
    )
    assert out.operation == "answer"
    assert "未知" in out.answer


def test_posted_price_lookup_does_not_require_calculate():
    task = TaskDecision(operation="answer", answer="票价写了 88 元。")
    out = enforce_delivery_contract(
        task,
        {"current_request": "标价写了多少？不要代我付款。", "sources": [], "tool_results": []},
    )
    assert out.answer.startswith("票价")


def test_non_quantity_numeric_answer_does_not_require_calculate():
    task = TaskDecision(operation="answer", answer="告示只供阅读，标价 88 元。")
    out = enforce_delivery_contract(
        task,
        {"current_request": "这张告示能不能代收？", "sources": [], "tool_results": []},
    )
    assert "88" in out.answer
