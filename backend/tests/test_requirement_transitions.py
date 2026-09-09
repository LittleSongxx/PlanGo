"""Synthetic regression checks for temporal provenance and real subgraph boundaries."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from plango_harness.agent.contracts import Evidence, Location, PartyMember, PlaceCandidate, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.requirements import requirement_delta, temporal_patch
from plango_harness.agent.state import initial_state, planning_reset
from plango_harness.agent.subagents.requirement import RequirementAgent, _bound_candidates
from plango_harness.domain.planning import visit_payload
from plango_harness.settings import Settings


def test_sparse_edits_keep_date_origin_and_locked_provenance_until_diff():
    anchor = "2026-09-09T16:30:00+00:00"  # Already September 10 in Chongqing.
    temporal = temporal_patch("明天，时间待定", None, anchor)
    assert temporal["visit_date"] == date(2026, 9, 11)
    spec = RequirementOutput(**temporal).to_trip_spec("明天去吃饭")
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


def test_walking_is_a_route_requirement_but_pedestrian_street_is_only_an_address():
    street = "我们2个人，今天下午14点从重庆市江北区观音桥步行街出发，玩3小时，总预算300元，只安排附近吃饭，不需要预约或取号。"
    fallback = RequirementAgent._fallback(street, [])
    assert fallback.travel_mode is None and fallback.budget == 300 and fallback.duration_minutes == 180
    assert not _bound_candidates("观音桥步行街8号大融城LG层055号")
    original = fallback.to_trip_spec(street)
    walk = RequirementAgent._fallback("改成步行2公里内", [], original)
    assert walk.travel_mode == "walking" and walk.max_distance_km == 2 and walk.search_location_name is None
    edited = walk.to_trip_spec("改成步行2公里内", original)
    _, changed = requirement_delta(original, edited)
    assert changed["routes"] and changed["supply"]
    assert TripSpec(goal="旧checkpoint").travel_mode == "driving"


def test_group_counts_no_budget_and_selected_venue_references_are_sparse():
    for text, expected in (("3位成人一起吃晚餐", 3), ("我和3个朋友一起吃晚餐", 4)):
        spec = RequirementAgent._fallback(text, []).to_trip_spec(text)
        assert spec.party_size == sum(spec.party_counts.values()) == expected
    previous = TripSpec(goal="3位成人一起吃晚餐", party_size=4, party_counts={"用户": 1, "成人": 3}, party=[PartyMember(role="用户"), PartyMember(role="成人")], budget=400, per_person_budget=100)
    text = "只去当前网页这家餐厅，没有第二个地点。总共3人包含我，不设预算。"
    output = RequirementAgent._fallback(text, [], previous)
    spec = output.to_trip_spec(text, previous)
    assert spec.party_size == sum(spec.party_counts.values()) == 3
    assert spec.budget is None and spec.per_person_budget is None
    assert output.search_location_name is None and not output.clarification_needed
    assert RequirementAgent._fallback("改到解放碑", [], spec).search_location_name == "解放碑"


def test_explicit_total_headcount_survives_model_stabilization_and_sparse_merge():
    previous = TripSpec(goal="原用餐草案", party_size=2, budget=250, per_person_budget=125,
                        visit_date=date(2026, 9, 11), must_visit_place_ids=["chosen"])
    for text in ("把人数改成3人，同时取消总预算和人均预算限制。日期、时间、已选门店、优惠和其他条件都保持。",
                 "人数改为3人，其他不变", "请将人数调整到三人，其他不变", "人数：3"):
        fallback = RequirementAgent._fallback(text, [], previous)
        for model_size in (None, 3, 9):
            output = RequirementAgent._stabilize_explicit_fields(
                RequirementOutput(party_size=model_size), fallback, text=text, previous_spec=previous)
            edited = output.to_trip_spec(text, previous)
            assert edited.party_size == 3 and not output.clarification_needed
            assert edited.visit_date == previous.visit_date and edited.location == previous.location
            assert edited.must_visit_place_ids == previous.must_visit_place_ids
            expected = None if "取消" in text else previous.budget
            assert edited.budget == expected
            assert edited.per_person_budget == (None if "取消" in text else previous.per_person_budget)
    for text in ("人数改为0人", "人数改为13人", "人数改为1.5人", "人数待定"):
        output = RequirementAgent._fallback(text, [], previous)
        assert output.party_size is None and output.party_size_unknown and output.clarification_needed
    for text in ("不要把人数改成3人", "不需要把人数改成3人", "不把人数改成3人", "不要把 人数改成3人"):
        assert RequirementAgent._fallback(text, [], previous).party_size is None
    group = previous.model_copy(update={"goal": "我和1个孩子一起出行", "party_counts": {"用户": 1, "孩子": 1},
                                        "party": [PartyMember(role="用户"), PartyMember(role="孩子")]})
    for text in ("孩子人数改为3人", "孩子 人数改为3人"):
        role = RequirementAgent._fallback(text, [], group)
        assert role.party_counts["孩子"] == 3 and role.party_size == 4 and not role.clarification_needed


def test_date_edits_do_not_become_geographic_searches():
    previous = TripSpec(goal="原用餐草案", party_size=2, budget=250, visit_date=date(2026, 9, 11),
                        search_radius_km=0.5, max_distance_km=2,
                        location=Location(name="观音桥步行街", latitude=29.57, longitude=106.57))
    for text in ("再把日期改成2026年9月12日，取消单段路程上限；搜索半径仍是0.5公里。",
                 "改成2026年9月12日", "日期改为下周日"):
        fallback = RequirementAgent._fallback(text, [], previous, reference_at="2026-09-09T12:00:00+08:00")
        output = RequirementAgent._stabilize_explicit_fields(
            RequirementOutput(location_name="模型猜测的地区", search_location_name="2026年9月12日"),
            fallback, text=text, previous_spec=previous)
        assert output.location_name is None and output.search_location_name is None
        assert output.to_trip_spec(text, previous).location == previous.location
    text = "再把日期改成2026年9月12日，取消单段路程上限；搜索半径仍是0.5公里。"
    fallback = RequirementAgent._fallback(text, [], previous)
    edited = RequirementAgent._stabilize_explicit_fields(RequirementOutput(), fallback, text=text, previous_spec=previous).to_trip_spec(text, previous)
    assert edited.visit_date == date(2026, 9, 12) and edited.max_distance_km is None and edited.search_radius_km == 0.5
    assert RequirementAgent._fallback("起点改为重庆观音桥步行街，日期保持", [], previous).location_name == "重庆观音桥步行街"
    for text in ("日期不变但起点改为观音桥", "日期保持且起点改为观音桥"):
        assert RequirementAgent._fallback(text, [], previous).location_name == "观音桥"
    assert RequirementAgent._fallback("改到九月艺术中心", [], previous).search_location_name == "九月艺术中心"


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
