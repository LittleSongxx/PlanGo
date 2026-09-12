"""Compiled geographic resolution from typed proposals, with retained store identity."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plango.location import select_origin
from plango_harness.agent.contracts import Location, PlaceCandidate, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.state import initial_state
from plango_harness.agent.subagents.requirement import RequirementAgent


async def _select_origin(state, extracted_name, previous_spec):
    return select_origin(state, extracted_name, previous_spec, None)


def test_explicit_center_edit_reuses_selected_identity_but_queries_a_new_area():
    async def exercise():
        origin = Location(name="重庆现有起点", latitude=29.56, longitude=106.57)
        selected = PlaceCandidate(place_id="amap:controlled", name="王幺妹家常菜", category="餐厅", latitude=29.556375, longitude=106.580225, source="amap")
        previous = TripSpec(goal="原有餐厅行程", location=origin, search_location=Location(name="当前网页这家餐厅", latitude=36.577253, longitude=120.15253), must_visit_place_ids=[selected.place_id])
        queried = Location(name="解放碑", latitude=29.56, longitude=106.576)
        execute = AsyncMock(return_value={"ok": True, "result": queried.model_dump(mode="json")})
        deps = GraphDeps(model=SimpleNamespace(structured=AsyncMock(side_effect=[
            RequirementOutput(search_location_reference="selected_place"),
            RequirementOutput(search_location_name="解放碑"),
        ])), world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=execute), planner=None, memory=None, runs=None, action_provider=None)
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
        model = SimpleNamespace(structured=AsyncMock(return_value=RequirementOutput(
            search_location_reference="generic_activity", required_activities=["餐厅"],
            travel_mode="walking", party_size=2, duration_minutes=120)))

        deps = GraphDeps(model=model, world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=execute), planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
        state = initial_state(run_id="controlled", user_id="controlled", input_text=text)
        state["previous_spec"] = previous
        result = await nodes["requirements"].ainvoke(state)
        spec = result["trip_spec"]
        assert spec.location == spec.search_location == origin
        assert spec.required_activities == ["餐厅"] and spec.travel_mode == "walking"
        assert spec.party_size == 2 and spec.duration_minutes == 120
        execute.assert_not_awaited()

    asyncio.run(exercise())


def test_named_origin_assignment_precedes_same_message_origin_references():
    async def exercise():
        previous_origin = Location(name="原出发地址", latitude=29.575499, longitude=106.532212)
        previous = TripSpec(goal="原有餐厅行程", location=previous_origin, search_location=previous_origin,
                            party_size=2, budget=500, duration_minutes=120, time_window_start="14:00", travel_mode="walking")
        address = "重庆市渝中区解放碑步行街"
        resolved = Location(name=address, latitude=29.558347, longitude=106.577158)
        execute = AsyncMock(return_value={"ok": True, "result": resolved.model_dump(mode="json")})
        model = SimpleNamespace(structured=AsyncMock(return_value=RequirementOutput(
            location_name=address, search_location_reference="current_origin")))

        deps = GraphDeps(model=model, world=SimpleNamespace(), tools=SimpleNamespace(schemas=lambda: [], execute=execute), planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
        state = initial_state(run_id="controlled", user_id="controlled", input_text=f"把出发地点改为{address}，就在这个新起点附近安排餐厅")
        state["previous_spec"] = previous
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
        model.structured.return_value = RequirementOutput(location_name="重庆市江北区观音桥步行街1号")
        correction = await RequirementAgent(model).run("起点更正为重庆市江北区观音桥步行街1号，其他条件保留。", [], previous)
        assert correction.location_name == "重庆市江北区观音桥步行街1号" and correction.location_reference is None

    asyncio.run(exercise())


def test_must_visit_venue_yields_origin_when_another_point_is_already_resolved():
    async def exercise():
        shop = Location(name="已选门店", latitude=29.57522, longitude=106.532842)
        home = Location(name="用户出发地址", latitude=29.439716, longitude=106.517123)
        selected = PlaceCandidate(place_id="amap:shop", name=shop.name, category="餐厅", latitude=shop.latitude, longitude=shop.longitude, source="amap")
        previous = TripSpec(goal="核对已选门店路线", location=shop, search_location=home, must_visit_place_ids=[selected.place_id])
        execute = AsyncMock(side_effect=AssertionError("an inverted origin is swapped from the existing spec"))
        deps = GraphDeps(model=SimpleNamespace(structured=AsyncMock(return_value=RequirementOutput(party_size=2))),
                         world=SimpleNamespace(requirement_origin=_select_origin), tools=SimpleNamespace(schemas=lambda: [], execute=execute),
                         planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
        state = initial_state(run_id="controlled", user_id="controlled", input_text="人数2人，按已给起点继续")
        state.update(previous_spec=previous, selected_poi=selected.model_dump(mode="json"),
                     location_origin={"source": "user", "reference": "selected_place", "name": shop.name})
        result = await nodes["requirements"].ainvoke(state)
        spec = result["trip_spec"]
        assert spec.location == home and spec.search_location == shop
        assert spec.must_visit_place_ids == [selected.place_id] and spec.party_size == 2
        assert result["location_origin"]["name"] == home.name
        execute.assert_not_awaited()

    asyncio.run(exercise())


def test_named_search_center_is_origin_when_model_points_origin_at_must_visit():
    async def exercise():
        shop = Location(name="已选门店", latitude=29.57522, longitude=106.532842)
        home = Location(name="用户出发地址", latitude=29.439716, longitude=106.517123)
        selected = PlaceCandidate(place_id="amap:shop", name=shop.name, category="餐厅", latitude=shop.latitude, longitude=shop.longitude, source="amap")
        previous = TripSpec(goal="核对已选门店路线", location=shop, search_location=shop, must_visit_place_ids=[selected.place_id])
        execute = AsyncMock(return_value={"ok": True, "result": home.model_dump(mode="json")})
        deps = GraphDeps(model=SimpleNamespace(structured=AsyncMock(return_value=RequirementOutput(
            location_reference="selected_place", search_location_name=home.name))),
                         world=SimpleNamespace(requirement_origin=_select_origin), tools=SimpleNamespace(schemas=lambda: [], execute=execute),
                         planner=None, memory=None, runs=None, action_provider=None)
        nodes = {}
        build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
        state = initial_state(run_id="controlled", user_id="controlled", input_text=f"出发地是{home.name}，已选门店是目的地")
        state.update(previous_spec=previous, selected_poi=selected.model_dump(mode="json"),
                     location_origin={"source": "user", "reference": "selected_place", "name": shop.name})
        result = await nodes["requirements"].ainvoke(state)
        spec = result["trip_spec"]
        assert spec.location == home and spec.search_location == shop
        assert execute.await_count == 1 and execute.await_args.args[:2] == ("geocode", {"address": home.name})

    asyncio.run(exercise())
