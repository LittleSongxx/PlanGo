"""Compiled requirement-node regression for the retained wrong-region UI state."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plango_harness.agent.contracts import Location, PlaceCandidate, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import initial_state
from plango_harness.agent.subagents.requirement import RequirementAgent
from plango_harness.settings import Settings


def test_explicit_center_edit_reuses_selected_identity_but_queries_a_new_area():
    async def exercise():
        origin = Location(name="重庆现有起点", latitude=29.56, longitude=106.57)
        selected = PlaceCandidate(place_id="amap:controlled", name="王幺妹家常菜", category="餐厅", latitude=29.556375, longitude=106.580225, source="amap")
        previous = TripSpec(goal="原有餐厅行程", location=origin, search_location=Location(name="当前网页这家餐厅", latitude=36.577253, longitude=120.15253), must_visit_place_ids=[selected.place_id])
        queried = Location(name="解放碑", latitude=29.56, longitude=106.576)
        execute = AsyncMock(return_value={"ok": True, "result": queried.model_dump(mode="json")})
        deps = GraphDeps(model=ModelAdapter(Settings(runtime_profile="sandbox", _env_file=None)), world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=execute), planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
        state = initial_state(run_id="controlled", user_id="controlled", input_text="搜索中心改为「王幺妹家常菜」")
        state.update(previous_spec=previous, selected_poi=selected.model_dump(mode="json"))
        result = await nodes["requirements"].ainvoke(state)
        spec = result["trip_spec"]
        assert spec.location == origin
        assert (spec.search_location.latitude, spec.search_location.longitude) == (selected.latitude, selected.longitude)
        assert spec.must_visit_place_ids == [selected.place_id]
        assert result["requirement_refresh"]["discovery"]
        execute.assert_not_awaited()
        result = await nodes["requirements"].ainvoke({**state, "input_text": "搜索中心改为解放碑"})
        assert result["trip_spec"].location == origin and result["trip_spec"].search_location == queried
        assert execute.await_count == 1 and execute.await_args.args[:2] == ("geocode", {"address": "解放碑"})

    asyncio.run(exercise())


def test_generic_activity_replaces_a_polluted_region_without_querying_the_category():
    async def exercise():
        text = "今天14:00从重庆市江北区观音桥步行街出发，2位成人，总预算300元，步行2公里以内，安排一个2小时行程，只去一家餐厅吃饭，不加其他地点。请提供真实地点的待核验草案，不需要预约或取号。"
        origin = Location(name="重庆市江北区观音桥步行街", latitude=29.575499, longitude=106.532212)
        previous = TripSpec(goal=text, location=origin, search_location=Location(name="一家餐厅", latitude=34.25, longitude=108.94))
        execute = AsyncMock(return_value={"ok": True, "result": origin.model_dump(mode="json")})
        model = ModelAdapter(Settings(runtime_profile="sandbox", _env_file=None))

        async def confused(schema, *, fallback, **kwargs):
            return RequirementOutput(location_name="一家餐厅", search_location_name="一家餐厅")

        model.structured = confused
        deps = GraphDeps(model=model, world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=execute), planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
        state = initial_state(run_id="controlled", user_id="controlled", input_text=text)
        state["previous_spec"] = previous
        rejected = await nodes["requirements"].ainvoke(state)
        assert rejected["trip_spec"].model_dump(exclude={"goal"}) == previous.model_dump(exclude={"goal"})
        assert rejected["clarification"]["fields"] == ["context"]
        execute.assert_not_awaited()
        # The sandbox fallback still handles the previously supported wording.
        model.structured = AsyncMock(side_effect=lambda schema, *, fallback, **kwargs: fallback)
        result = await nodes["requirements"].ainvoke(state)
        spec = result["trip_spec"]
        assert spec.location == spec.search_location == origin
        assert spec.required_activities == ["餐厅"] and spec.travel_mode == "walking"
        assert spec.party_size == 2 and spec.duration_minutes == 120
        assert execute.await_count == 1 and execute.await_args.args[1]["address"] == origin.name

    asyncio.run(exercise())


def test_named_origin_assignment_precedes_same_message_origin_references():
    async def exercise():
        previous_origin = Location(name="原出发地址", latitude=29.575499, longitude=106.532212)
        previous = TripSpec(goal="原有餐厅行程", location=previous_origin, search_location=previous_origin,
                            party_size=2, budget=500, duration_minutes=120, time_window_start="14:00", travel_mode="walking")
        address = "重庆市渝中区解放碑步行街"
        resolved = Location(name=address, latitude=29.558347, longitude=106.577158)
        execute = AsyncMock(return_value={"ok": True, "result": resolved.model_dump(mode="json")})
        model = ModelAdapter(Settings(runtime_profile="sandbox", _env_file=None))

        async def confused(schema, *, fallback, **kwargs):
            return RequirementOutput(location_name="这个新起点")

        model.structured = confused
        deps = GraphDeps(model=model, world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=execute), planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
        state = initial_state(run_id="controlled", user_id="controlled", input_text=f"把出发地点改为{address}，就在这个新起点附近安排餐厅")
        state["previous_spec"] = previous
        rejected = await nodes["requirements"].ainvoke(state)
        assert rejected["trip_spec"].model_dump(exclude={"goal"}) == previous.model_dump(exclude={"goal"})
        assert rejected["clarification"]["fields"] == ["context"]
        execute.assert_not_awaited()
        model.structured = AsyncMock(side_effect=lambda schema, *, fallback, **kwargs: fallback)
        for reference in ("这个新起点", "该新起点", "此新起点"):
            text = f"把出发地点改为{address}，就在{reference}附近安排餐厅，其他要求不变。"
            state = initial_state(run_id="controlled", user_id="controlled", input_text=text)
            state["previous_spec"] = previous
            execute.reset_mock()
            result = await nodes["requirements"].ainvoke(state)
            spec = result["trip_spec"]
            assert spec.location == spec.search_location == resolved
            assert (spec.party_size, spec.budget, spec.duration_minutes, spec.time_window_start, spec.travel_mode) == (2, 500, 120, "14:00", "walking")
            assert execute.await_count == 1 and execute.await_args.args[1] == {"address": address}
        direct = RequirementAgent._fallback(f"把出发地点改为{address}，从这个新起点出发", [], previous)
        assert direct.location_name == address and direct.location_reference is None
        correction = await RequirementAgent(model).run("起点更正为重庆市江北区观音桥步行街1号，2026年9月10日18:30，活动总时长120分钟，仍为3人和总预算250元，保留选定门店与优惠。", [], previous)
        assert correction.location_name == "重庆市江北区观音桥步行街1号" and correction.location_reference is None

    asyncio.run(exercise())
