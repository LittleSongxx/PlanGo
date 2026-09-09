"""Controlled model-output regressions, not a language-quality evaluation."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plango_harness.agent.contracts import Location, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.requirements import requirement_delta
from plango_harness.agent.state import initial_state
from plango_harness.agent.subagents.requirement import RequirementAgent
from pydantic import ValidationError


def original():
    return TripSpec(goal="原已确认行程", party_size=2, budget=280, per_person_budget=140,
                    duration_minutes=120, visit_date=date(2026, 9, 11), time_window_start="18:30",
                    location=Location(name="原起点", latitude=29.5, longitude=106.5),
                    search_radius_km=.5, max_distance_km=2, must_visit_place_ids=["controlled:chosen"])


async def test_model_patch_survives_absent_fallback_fields_and_keeps_other_state():
    previous = original()
    text = "这回凑齐四位；我们周六见；其余不变"
    proposal = RequirementOutput(party_size=4, visit_date=date(2026, 9, 12),
                                field_evidence={"party_size": "这回凑齐四位", "visit_date": "我们周六见"})
    fallback = RequirementAgent._fallback(text, [], previous, reference_at="2026-09-09T12:00:00+08:00")
    assert fallback.party_size is None and fallback.visit_date is None
    model = SimpleNamespace(structured=AsyncMock(return_value=proposal))
    patch = await RequirementAgent(model).run(text, [], previous, reference_at="2026-09-09T12:00:00+08:00")
    edited = patch.to_trip_spec(text, previous)
    assert edited.party_size == 4 and edited.visit_date == date(2026, 9, 12)
    assert not patch.clarification_needed
    assert {item["field"] for item in requirement_delta(previous, edited)[0]} == {"party_size", "visit_date"}
    assert edited.must_visit_place_ids == previous.must_visit_place_ids and edited.location == previous.location


@pytest.mark.parametrize("proposal", [RequirementOutput(party_size=4), RequirementOutput(party_size=4, field_evidence={}),
                                     RequirementOutput(), RequirementOutput(field_evidence={})])
async def test_missing_model_provenance_never_uses_rules_or_creates_an_initial_success(proposal):
    previous = original()
    text = "人数改成4人，预算改成300元"
    agent = RequirementAgent(SimpleNamespace(structured=AsyncMock(return_value=proposal)))
    patch = await agent.run(text, [], previous)
    assert patch.to_trip_spec(text, previous).model_dump(exclude={"goal"}) == previous.model_dump(exclude={"goal"})
    initial = await agent.run(text, [])
    assert initial.clarification_needed


@pytest.mark.parametrize(("text", "fields"), [
    ("活动压缩为一个半小时", {"duration_minutes": 90}),
    ("活动留九十分钟", {"duration_minutes": 90}),
    ("全程留一百二十分钟", {"duration_minutes": 120}),
    ("找店以起点500m以内为范围", {"search_radius_km": .5}),
    ("到店实际走的路别多于1500米", {"route_distance_km": 1.5}),
    ("每个人花费至多一百元", {"per_person_budget": 100}),
])
def test_units_use_field_local_evidence(text, fields):
    previous = original()
    proposal = RequirementOutput(**fields, field_evidence={field: text for field in fields})
    patch = RequirementAgent._stabilize_explicit_fields(proposal, RequirementOutput(), text=text, previous_spec=previous)
    assert not patch.clarification_needed
    edited = patch.to_trip_spec(text, previous)
    target = "max_distance_km" if "route_distance_km" in fields else next(iter(fields))
    assert getattr(edited, target) == next(iter(fields.values()))
    assert edited.budget == previous.budget and edited.party_size == previous.party_size


def test_unmentioned_negated_and_ambiguous_fields_cannot_become_updates():
    previous = original()
    for text, proposal in [
        ("不要把人均预算改成120元，其他不变", RequirementOutput(field_evidence={})),
        ("预算先记300元，究竟是总额还是每人还没定", RequirementOutput(
            budget=300, clarification_needed=True, clarification_fields=["budget", "per_person_budget"],
            clarification_question="300元是总额还是每人？", field_evidence={"budget": "预算先记300元"})),
        ("其他不变", RequirementOutput(budget=120, party_size=1, field_evidence={"budget": "其他不变"})),
        ("不要把人数改成4人", RequirementOutput(party_size=4, field_evidence={"party_size": "人数改成4人"})),
        ("全程留2小时", RequirementOutput(duration_minutes=60, field_evidence={"duration_minutes": "全程留2小时"})),
    ]:
        fallback = RequirementAgent._fallback(text, [], previous)
        patch = RequirementAgent._stabilize_explicit_fields(proposal, fallback, text=text, previous_spec=previous)
        assert patch.to_trip_spec(text, previous).model_dump(exclude={"goal"}) == previous.model_dump(exclude={"goal"})
    assert patch.clarification_needed


def test_clear_is_explicit_separate_and_persists_across_roundtrip():
    previous = original()
    text = "取消实际路程上限；总预算也不限制了；其余不变"
    proposal = RequirementOutput(clear_route_distance=True, clear_budget=True,
                                field_evidence={"clear_route_distance": "取消实际路程上限", "clear_budget": "总预算也不限制了"})
    patch = RequirementAgent._stabilize_explicit_fields(proposal, RequirementOutput(), text=text, previous_spec=previous)
    restored = TripSpec.model_validate_json(patch.to_trip_spec(text, previous).model_dump_json())
    assert restored.max_distance_km is None and restored.budget is None
    assert restored.search_radius_km == previous.search_radius_km and restored.per_person_budget == previous.per_person_budget
    followup = "总共留180分钟；其他不变"
    output = RequirementOutput(duration_minutes=180, field_evidence={"duration_minutes": "总共留180分钟"})
    final = RequirementAgent._stabilize_explicit_fields(output, RequirementOutput(), text=followup, previous_spec=restored).to_trip_spec(followup, restored)
    assert final.duration_minutes == 180 and final.budget is None and final.max_distance_km is None
    assert final.location == previous.location and final.must_visit_place_ids == previous.must_visit_place_ids
    ambiguous = RequirementOutput(budget=300, clear_budget=True,
                                 field_evidence={"budget": "总额300元", "clear_budget": "总额300元"})
    checked = RequirementAgent._stabilize_explicit_fields(ambiguous, RequirementOutput(), text="总额300元", previous_spec=previous)
    assert checked.clarification_needed and checked.to_trip_spec("总额300元", previous).budget == previous.budget


async def test_compiled_node_retains_spec_while_clarifying_one_field():
    previous = original()
    text = "这次凑四位；花销暂记300元，还没定这是全体还是每个人"
    proposal = RequirementOutput(party_size=4, budget=300, clarification_needed=True,
                                clarification_fields=["budget", "per_person_budget"],
                                clarification_question="300元是全体还是每人预算？",
                                field_evidence={"party_size": "这次凑四位", "budget": "花销暂记300元"})
    execute = AsyncMock()
    deps = GraphDeps(model=SimpleNamespace(structured=AsyncMock(return_value=proposal)),
                     world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=execute),
                     planner=None, memory=None, runs=None, action_provider=None)
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
    state = initial_state(run_id="controlled", user_id="controlled", input_text=text)
    state["previous_spec"] = previous
    result = await nodes["requirements"].ainvoke(state)
    spec = result["trip_spec"]
    assert result["clarification"]["fields"] == ["budget", "per_person_budget"]
    assert spec.party_size == 4 and spec.budget == previous.budget and spec.per_person_budget == previous.per_person_budget
    assert spec.location == previous.location and spec.must_visit_place_ids == previous.must_visit_place_ids
    execute.assert_not_awaited()


@pytest.mark.parametrize("values", [{"party_size": True}, {"duration_minutes": 120.5}, {"budget": float("inf")},
                                   {"search_radius_km": float("nan")}, {"route_distance_km": True},
                                   {"time_window_start": "25:00"}, {"timezone": "Invalid/Zone"}])
def test_requirement_numeric_boundaries_reject_invalid_values(values):
    with pytest.raises(ValidationError):
        RequirementOutput(**values)


@pytest.mark.parametrize("clock", ["3点99分", "25:00", "25点", "-3点", "下午-3点", "3点-1分", "18:99"])
async def test_invalid_fallback_clock_clarifies_without_overwriting_previous_time(clock):
    previous = original()
    text = f"开始改为{clock}，其余不变"
    agent = RequirementAgent(SimpleNamespace(structured=AsyncMock(side_effect=lambda schema, *, fallback, **kwargs: fallback)))
    patch = await agent.run(text, [], previous)
    assert patch.time_window_start is None and not patch.time_window_start_unknown
    assert patch.clarification_needed and "time_window_start" in patch.clarification_fields
    assert patch.to_trip_spec(text, previous).model_dump(exclude={"goal"}) == previous.model_dump(exclude={"goal"})
