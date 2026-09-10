"""Structured sparse edits, temporal provenance, and real subgraph boundaries."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from plango_harness.agent.contracts import Evidence, Location, PartyMember, PlaceCandidate, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.requirements import requirement_delta
from plango_harness.agent.state import initial_state, planning_reset
from plango_harness.domain.planning import visit_payload
from plango_harness.settings import Settings


def test_sparse_edits_keep_date_origin_and_locked_provenance_until_diff():
    spec = RequirementOutput(visit_date=date(2026, 9, 11), time_window_start_unknown=True).to_trip_spec("明天去吃饭")
    edited = RequirementOutput(budget=500).to_trip_spec("预算改为500元", spec)
    patch, refresh = requirement_delta(spec, edited)
    assert edited.visit_date == spec.visit_date and edited.time_window_start is None
    assert edited.location == spec.location
    assert {item["field"] for item in patch} == {"budget"}
    assert not any(refresh.values())
    _, party_refresh = requirement_delta(edited, edited.model_copy(update={"party_size": 4}))
    assert party_refresh == dict(discovery=False, weather=False, supply=True, routes=True)
    original = {"selected_plan": {"stops": [{"place_id": "chosen", "locked": True}]}, "trip_spec": edited, "evidence": ["original"], "execution_outcome": {"status": "satisfied"}}
    reset = planning_reset(original)
    assert reset["previous_plan"] == original["selected_plan"]
    assert reset["evidence"] == original["evidence"]
    assert reset["execution_outcome"] is None
    today_source = datetime(2026, 9, 10, 2, tzinfo=timezone.utc)
    assert visit_payload({"open_minute": 600, "close_minute": 1200}, edited, today_source) == {}
    scoped = {"visit_date": "2026-09-11", "timezone": "Asia/Shanghai", "open_minute": 600}
    assert visit_payload(scoped, edited, today_source) == scoped


def test_route_edit_preserves_search_radius_and_origin():
    origin = Location(name="观音桥步行街8号大融城LG层055号", latitude=29.57, longitude=106.57)
    previous = TripSpec(goal="已确认草案", location=origin, search_radius_km=.5)
    output = RequirementOutput(travel_mode="walking", route_distance_km=2)
    edited = output.to_trip_spec("改用步行，路程不超过2公里", previous)
    assert edited.location == previous.location and edited.search_radius_km == .5
    assert edited.travel_mode == "walking" and edited.max_distance_km == 2
    _, changed = requirement_delta(previous, edited)
    assert changed["routes"] and changed["supply"]
    assert TripSpec(goal="旧checkpoint").travel_mode == "driving"


def test_partial_role_changes_preserve_other_roles_and_member_details():
    previous = TripSpec(goal="原有同行人", party_size=4, party_counts={"用户": 1, "成人": 2, "孩子": 1},
                        party=[PartyMember(role="用户"), PartyMember(role="成人"), PartyMember(role="孩子", age=8)],
                        budget=400, per_person_budget=100)
    output = RequirementOutput(party_size=3, party_counts={"成人": 1}, clear_budget=True,
                               clear_per_person_budget=True, search_location_reference="selected_place")
    spec = output.to_trip_spec("本轮明确提案", previous)
    assert spec.party_size == sum(spec.party_counts.values()) == 3
    assert spec.party_counts == {"用户": 1, "成人": 1, "孩子": 1}
    assert next(member for member in spec.party if member.role == "孩子").age == 8
    assert spec.budget is None and spec.per_person_budget is None
    assert output.search_location_name is None and not output.clarification_needed
    exited = RequirementOutput(party_size=2, party_counts={"孩子": 0}).to_trip_spec("孩子退出", spec)
    assert exited.party_counts["孩子"] == 0 and all(member.role != "孩子" for member in exited.party)


def test_explicit_total_headcount_does_not_change_independent_fields():
    previous = TripSpec(goal="原用餐草案", party_size=2, budget=250, per_person_budget=125,
                        visit_date=date(2026, 9, 11), must_visit_place_ids=["chosen"])
    for output in (RequirementOutput(party_size=3),
                   RequirementOutput(party_size=3, clear_budget=True, clear_per_person_budget=True)):
        edited = output.to_trip_spec("本轮明确提案", previous)
        assert edited.party_size == 3 and not output.clarification_needed
        assert edited.visit_date == previous.visit_date and edited.location == previous.location
        assert edited.must_visit_place_ids == previous.must_visit_place_ids
        assert edited.budget == (None if output.clear_budget else previous.budget)
        assert edited.per_person_budget == (None if output.clear_per_person_budget else previous.per_person_budget)
    unknown = RequirementOutput(party_size_unknown=True).to_trip_spec("人数改为待定", previous)
    assert unknown.party_size is None and unknown.budget == previous.budget


def test_date_edit_and_explicit_order_clear_are_independent():
    previous = TripSpec(goal="原用餐草案", party_size=2, budget=250, visit_date=date(2026, 9, 11),
                        required_activities=["展览", "餐厅"], activity_order=["展览", "餐厅"],
                        search_radius_km=.5, max_distance_km=2,
                        location=Location(name="观音桥步行街", latitude=29.57, longitude=106.57))
    dated = RequirementOutput(visit_date=date(2026, 9, 12), clear_route_distance=True).to_trip_spec("明确日期和距离修改", previous)
    assert dated.visit_date == date(2026, 9, 12) and dated.max_distance_km is None
    assert dated.location == previous.location and dated.search_radius_km == .5
    assert dated.activity_order == previous.activity_order
    unordered = RequirementOutput(activity_order=[]).to_trip_spec("取消活动顺序", dated)
    assert unordered.activity_order == [] and unordered.required_activities == dated.required_activities
    restored = TripSpec.model_validate_json(unordered.model_dump_json())
    assert RequirementOutput().to_trip_spec("保留当前安排", restored) == restored


def test_discovery_reuses_candidates_and_advocate_receives_original_evidence():
    async def exercise():
        now = datetime.now(timezone.utc)
        place = PlaceCandidate(place_id="amap:chosen", name="受控样本店", category="餐厅", latitude=29.56, longitude=106.57, evidence_ids=["fixture:chosen"])
        fact = Evidence(evidence_id="fixture:chosen", source="amap", source_ref="https://fixture.invalid/poi", claim="受控样本", observed_at=now, expires_at=now + timedelta(minutes=5), payload={"place_id": place.place_id})
        calls = []

        async def execute(name, args, ctx):
            calls.append((name, ctx.trip_spec.location))
            assert name == "get_weather", "A context-only edit must not repeat place search."
            return {"ok": True, "result": {"weather": {"fixture": True}, "evidence": fact.model_dump(mode="json")}}

        tools = SimpleNamespace(schemas=lambda: [], execute=execute)
        model = ModelAdapter(Settings(runtime_profile="sandbox", _env_file=None))
        deps = GraphDeps(model=model, tools=tools, world=SimpleNamespace(), planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}

        def capture(graph):
            nodes.update({name: graph.nodes[name].runnable for name in ("discovery", "advocate_worker")})

        build_graph(deps, extension=capture)
        origin = Location(name="真实起点", latitude=29.5, longitude=106.5)
        destination = Location(name="目标商圈", latitude=place.latitude, longitude=place.longitude)
        spec = TripSpec(goal="样本行程", location=origin, search_location=destination, must_visit_place_ids=[place.place_id], party_size=2)
        state = initial_state(run_id="fixture", user_id="fixture", input_text=spec.goal)
        state.update(trip_spec=spec, requirement_refresh={"discovery": False, "weather": True}, place_candidates=[place], evidence=[fact], selected_poi={**place.model_dump(mode="json"), "evidence": fact.model_dump(mode="json")})
        result = await nodes["discovery"].ainvoke(state)
        assert calls == [("get_weather", destination)]
        assert spec.location == origin
        assert result["place_candidates"][0].place_id == place.place_id
        assert result["evidence"][0].observed_at == fact.observed_at
        report = await nodes["advocate_worker"].ainvoke({**state, **result, "advocate_role": "预算"})
        assert report["advocate_reports"][0].evidence_ids == [fact.evidence_id]

    asyncio.run(exercise())


def test_single_policy_preserves_browser_role_and_skill_prefix():
    async def exercise():
        systems = []

        class FixtureModel:
            structured = False

            def with_structured_output(self, *args, **kwargs):
                self.structured = True
                return self

            def bind_tools(self, *args, **kwargs):
                self.structured = False
                return self

            def bind(self, **kwargs):
                return self

            async def ainvoke(self, messages):
                systems.append(messages[0]["content"])
                return RequirementOutput(budget=300) if self.structured else SimpleNamespace(tool_calls=[])

        adapter = ModelAdapter(Settings(runtime_profile="sandbox", agent_mode="single", _env_file=None), FixtureModel())
        adapter.system_prefix = "技能元数据仅供参考。\n"
        system = "你是 Browser Agent。遵守页面审批和事实边界。"
        await adapter.structured(RequirementOutput, system=system, user="受控样本", fallback=RequirementOutput())
        await adapter.tool_calls(system=system, user="受控样本", tools=[])
        assert systems == [adapter.system_prefix + system] * 2

    asyncio.run(exercise())
