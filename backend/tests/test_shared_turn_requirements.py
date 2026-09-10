"""One cited turn patch feeds browser context and the real planning subgraph."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plango.settings import DesktopSettings
from plango.task import TASK_INSTRUCTIONS, TaskDecision
from plango_harness.agent.contracts import Location, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import initial_state


@pytest.mark.parametrize(("text", "fields", "count", "budget"), [
    ("参与者改为四位成人", {"party_size": 4}, 4, 180),
    ("人数不要改为4人，其余不变", {}, 2, 180),
    ("不设预算", {"clear_budget": True, "clear_per_person_budget": True}, 2, None),
])
async def test_one_proposal_survives_browser_context_checkpoint_and_planning(text, fields, count, budget):
    previous = TripSpec(goal="原任务", party_size=2, budget=180,
                        location=Location(name="原起点", latitude=29.56, longitude=106.57))
    output = RequirementOutput(**fields, field_evidence={field: text for field in fields})
    state = initial_state(run_id="controlled-turn", user_id="test", input_text=text)
    state.update(turn_id=2, previous_spec=previous, trip_spec=previous,
                 browser_task_context={"kind": "reasoning", "mode": "browser", "request": "比较原资料",
                                       "turn_id": 1, "party_size": 2, "total_budget": 180})
    state["requirement_proposal"] = {"turn_id": 2, "input_text": text, "output": output.model_dump(mode="json")}
    model = SimpleNamespace(structured=AsyncMock(side_effect=AssertionError("Do not reinterpret this turn")))
    deps = GraphDeps(model=model, world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=AsyncMock()),
                     planner=None, memory=None, runs=None, action_provider=None)
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
    result = await nodes["requirements"].ainvoke(state)
    assert (result["trip_spec"].party_size, result["trip_spec"].budget) == (count, budget)
    assert result["trip_spec"].location == previous.location and result["clarification"] is None
    model.structured.assert_not_awaited()


async def test_stale_proposal_is_not_reused_for_a_new_input():
    text = "改为3人"
    model = SimpleNamespace(structured=AsyncMock(return_value=RequirementOutput(
        party_size=3, field_evidence={"party_size": text})))
    deps = GraphDeps(model=model, world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=AsyncMock()),
                     planner=None, memory=None, runs=None, action_provider=None)
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
    state = initial_state(run_id="controlled", user_id="test", input_text=text)
    state.update(turn_id=3, previous_spec=TripSpec(goal="原需求", party_size=2),
                 requirement_proposal={"turn_id": 2, "input_text": "改为4人", "output": {"party_size": 4}})
    result = await nodes["requirements"].ainvoke(state)
    assert result["trip_spec"].party_size == 3
    model.structured.assert_awaited_once()


async def test_combined_schema_admission_keeps_reported_usage_and_run_limit():
    import json

    text = "参与者改为四位成人，其他不变"
    expected = TaskDecision(operation="plan", requirements=RequirementOutput(
        party_size=4, field_evidence={"party_size": "参与者改为四位成人"}))
    invoke = AsyncMock(return_value={"parsed": expected, "raw": SimpleNamespace(usage_metadata={"total_tokens": 731})})
    adapter = ModelAdapter(DesktopSettings(max_model_tokens=12000), model=SimpleNamespace(
        with_structured_output=lambda *args, **kwargs: SimpleNamespace(ainvoke=invoke)))
    previous = TripSpec(goal="原任务", party_size=2, budget=180)
    payload = json.dumps({"input": text, "previous_requirements": previous.model_dump(mode="json"),
                          "previous_goal": {"mode": "planning", "request": previous.goal}}, ensure_ascii=False)
    result = await adapter.structured(TaskDecision, system=TASK_INSTRUCTIONS, user=payload, fallback=TaskDecision(operation="read"))
    assert result == expected and invoke.await_count == 1
    assert adapter.total_tokens == 731 and adapter.token_limit == 12000
    assert adapter.call_records[-1]["schema"] == "TaskDecision"
    assert adapter._structured_schema is None
    fallback = TaskDecision(operation="read")
    again = await adapter.structured(TaskDecision, system=TASK_INSTRUCTIONS, user="未裁剪资料" * 3000, fallback=fallback)
    assert again == expected and invoke.await_count == 2
    assert adapter.total_tokens == 1462




async def test_planning_merges_current_accepted_values_but_invalidates_against_previous_turn():
    previous = TripSpec(goal='原行程', party_size=2, budget=180,
                        location=Location(name='原起点', latitude=29.56, longitude=106.57))
    state = initial_state(run_id='same-turn-stages', user_id='fixture', input_text='按刚才修改的条件安排')
    state.update(turn_id=2, previous_spec=previous, trip_spec=previous.model_copy(update={'party_size': 4}),
        browser_task_context={'original_request': '原始请求'},
        requirement_proposal={'turn_id': 2, 'input_text': state['input_text'],
            'output': RequirementOutput(time_window_start='19:00', planning_source='browser').model_dump(mode='json')})
    model = SimpleNamespace(structured=AsyncMock(side_effect=AssertionError('Do not reinterpret accepted parameters')))
    deps = GraphDeps(model=model, world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: []),
                     planner=None, memory=None, runs=None, action_provider=None)
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes['requirements'].runnable))
    result = await nodes['requirements'].ainvoke(state)
    assert result['trip_spec'].party_size == 4 and result['trip_spec'].budget == 180
    assert {'party_size', 'time_window_start'} <= {item['field'] for item in result['requirement_patch']}
    assert result['requirement_refresh']['supply']
    assert result['browser_task_context'] == {'original_request': '原始请求', 'planning_source': 'browser'}
