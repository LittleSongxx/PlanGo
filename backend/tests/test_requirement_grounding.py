"""Typed model proposals and sparse-state contracts; no natural-language parsing oracle."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plango_harness.agent.contracts import Location, OfferReference, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter, ModelProviderUnavailable
from plango_harness.agent.requirements import requirement_delta
from plango_harness.agent.state import initial_state
from plango_harness.agent.subagents.requirement import RequirementAgent, RequirementNotUsable
from plango_harness.settings import Settings
from pydantic import ValidationError


def original():
    return TripSpec(goal="原已确认行程", party_size=2, budget=280, per_person_budget=140,
                    duration_minutes=120, visit_date=date(2026, 9, 11), time_window_start="18:30",
                    location=Location(name="原起点", latitude=29.5, longitude=106.5),
                    search_radius_km=.5, max_distance_km=2, must_visit_place_ids=["controlled:chosen"],
                    selected_offer=OfferReference(command_id="controlled-command", artifact_id="controlled-page",
                                                  offer_index=0, offer_hash="a" * 64, place_id="controlled:chosen"))


async def test_model_patch_keeps_other_state_without_second_semantic_interpretation():
    previous = original()
    text = "这回凑齐四位；我们周六见；其余不变"
    proposal = RequirementOutput(party_size=4, visit_date=date(2026, 9, 12),
                                field_evidence={"party_size": "这回凑齐四位", "visit_date": "我们周六见"})
    model = SimpleNamespace(structured=AsyncMock(return_value=proposal))
    patch = await RequirementAgent(model).run(text, [], previous, reference_at="2026-09-09T12:00:00+08:00")
    edited = patch.to_trip_spec(text, previous)
    assert edited.party_size == 4 and edited.visit_date == date(2026, 9, 12)
    assert not patch.clarification_needed
    assert {item["field"] for item in requirement_delta(previous, edited)[0]} == {"party_size", "visit_date"}
    assert edited.must_visit_place_ids == previous.must_visit_place_ids and edited.location == previous.location


@pytest.mark.parametrize("evidence", [None, {}, {"party_size": "4"}])
async def test_optional_evidence_metadata_does_not_turn_a_valid_edit_into_user_ambiguity(evidence):
    previous = original()
    proposal = RequirementOutput(party_size=4, field_evidence=evidence)
    agent = RequirementAgent(SimpleNamespace(structured=AsyncMock(return_value=proposal)))
    patch = await agent.run("这回凑齐四位，其余不变", [], previous)
    edited = patch.to_trip_spec("本轮修改", previous)
    assert edited.party_size == 4 and not patch.clarification_needed
    assert {item["field"] for item in requirement_delta(previous, edited)[0]} == {"party_size"}


async def test_no_change_and_repeated_known_values_do_not_require_new_citations():
    initial = RequirementOutput().to_trip_spec("先讨论可行方案")
    assert initial.party_size is None and initial.party_counts == {} and initial.budget is None
    previous = original()
    for proposal in (RequirementOutput(), RequirementOutput(party_size=2, budget=280)):
        agent = RequirementAgent(SimpleNamespace(structured=AsyncMock(return_value=proposal)))
        patch = await agent.run("其余不变", [], previous)
        assert not patch.clarification_needed
        assert patch.to_trip_spec("其余不变", previous) == previous


@pytest.mark.parametrize(("text", "fields"), [
    ("活动压缩为一个半小时", {"duration_minutes": 90}),
    ("活动留九十分钟", {"duration_minutes": 90}),
    ("全程留一百二十分钟", {"duration_minutes": 120}),
    ("找店以起点500m以内为范围", {"search_radius_km": .5}),
    ("到店实际走的路别多于1500米", {"route_distance_km": 1.5}),
    ("每个人花费至多一百元", {"per_person_budget": 100}),
])
async def test_model_normalized_units_reach_the_typed_contract(text, fields):
    previous = original()
    proposal = RequirementOutput(**fields, field_evidence={field: text for field in fields})
    patch = await RequirementAgent(SimpleNamespace(structured=AsyncMock(return_value=proposal))).run(text, [], previous)
    assert not patch.clarification_needed
    edited = patch.to_trip_spec(text, previous)
    target = "max_distance_km" if "route_distance_km" in fields else next(iter(fields))
    assert getattr(edited, target) == next(iter(fields.values()))
    assert edited.budget == previous.budget and edited.party_size == previous.party_size


async def test_real_ambiguity_blocks_only_the_named_fields():
    previous = original()
    proposal = RequirementOutput(
        party_size=4, budget=300, clarification_needed=True,
        clarification_fields=["budget", "per_person_budget"],
        clarification_question="300元是总额还是每人？")
    agent = RequirementAgent(SimpleNamespace(structured=AsyncMock(return_value=proposal)))
    patch = await agent.run("这次四位，预算300但口径还没定", [], previous)
    edited = patch.to_trip_spec("本轮修改", previous)
    assert edited.party_size == 4
    assert edited.budget == previous.budget and edited.per_person_budget == previous.per_person_budget
    assert {item["field"] for item in requirement_delta(previous, edited)[0]} == {"party_size"}


async def test_clear_is_explicit_separate_and_persists_across_roundtrip():
    previous = original()
    text = "取消实际路程上限；总预算也不限制了；其余不变"
    proposal = RequirementOutput(clear_route_distance=True, clear_budget=True,
                                field_evidence={"clear_route_distance": "取消实际路程上限", "clear_budget": "总预算也不限制了"})
    patch = await RequirementAgent(SimpleNamespace(structured=AsyncMock(return_value=proposal))).run(text, [], previous)
    restored = TripSpec.model_validate_json(patch.to_trip_spec(text, previous).model_dump_json())
    assert restored.max_distance_km is None and restored.budget is None
    assert restored.search_radius_km == previous.search_radius_km and restored.per_person_budget == previous.per_person_budget
    followup = "总共留180分钟；其他不变"
    output = RequirementOutput(duration_minutes=180, field_evidence={"duration_minutes": "总共留180分钟"})
    patch = await RequirementAgent(SimpleNamespace(structured=AsyncMock(return_value=output))).run(followup, [], restored)
    final = patch.to_trip_spec(followup, restored)
    assert final.duration_minutes == 180 and final.budget is None and final.max_distance_km is None
    assert final.location == previous.location and final.must_visit_place_ids == previous.must_visit_place_ids
    assert final.selected_offer == previous.selected_offer


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


@pytest.mark.parametrize("values", [
    {"budget": 300, "clear_budget": True},
    {"party_size": 4, "party_size_unknown": True},
    {"time_window_start": "18:30", "time_window_start_unknown": True},
    {"search_radius_km": .5, "clear_search_radius": True},
    {"route_distance_km": 2, "clear_route_distance": True},
    {"location_name": "新起点", "location_reference": "current_origin"},
    {"required_activities": ["餐厅"], "remove_activities": ["餐厅"]},
])
def test_conflicting_structured_operations_are_model_errors(values):
    with pytest.raises(ValidationError):
        RequirementOutput(**values)


async def test_requirement_token_budget_is_not_a_provider_outage():
    previous = original()
    model = SimpleNamespace(
        structured=AsyncMock(side_effect=lambda schema, fallback, **kwargs: fallback),
        last_error="model_token_budget",
    )
    with pytest.raises(RequirementNotUsable, match="model_token_budget"):
        await RequirementAgent(model).run("人数改为4人", [], previous)


async def test_first_turn_keeps_a_known_client_origin_instead_of_asking():
    origin = Location(name="重庆", latitude=29.56, longitude=106.57)
    proposal = RequirementOutput(
        party_size=2, clarification_needed=True,
        clarification_fields=["location"], clarification_question="请提供起点",
    )
    world = SimpleNamespace(
        strict_location=True,
        requirement_origin=AsyncMock(return_value=("重庆", origin, {"source": "manual", "name": "重庆"})),
    )
    deps = GraphDeps(
        model=SimpleNamespace(structured=AsyncMock(return_value=proposal)),
        world=world, tools=SimpleNamespace(schemas=lambda: [], execute=AsyncMock()),
        planner=None, memory=None, runs=None, action_provider=None,
    )
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
    state = initial_state(run_id="origin", user_id="fixture", input_text="2人吃饭")
    result = await nodes["requirements"].ainvoke(state)
    assert result["trip_spec"].location == origin
    assert result["clarification"] is None
    world.requirement_origin.assert_awaited()


async def test_unavailable_model_preserves_state_and_does_not_invent_a_user_question():
    previous = original()
    serialized = previous.model_dump_json()
    model = ModelAdapter(Settings(runtime_profile="sandbox", _env_file=None))
    try:
        with pytest.raises(ModelProviderUnavailable):
            await RequirementAgent(model).run("人数改为4人，开始改为25点", [], previous)
        assert previous.model_dump_json() == serialized
        assert model.call_count == 0
    finally:
        await model.close()
