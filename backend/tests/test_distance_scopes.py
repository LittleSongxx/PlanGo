"""Separate search and road-distance limits; controlled contracts only."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from plango.browser import run_context
from plango.settings import DesktopSettings
from plango.world import BrowserWorld
from plango_harness.agent.contracts import Location, PlanCandidate, PlanStop, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.requirements import requirement_delta
from plango_harness.agent.subagents.requirement import RequirementAgent
from plango_harness.domain.planning import verify_plan


def test_legacy_limits_and_independent_set_clear_are_preserved():
    legacy = TripSpec.model_validate({"goal": "旧范围", "max_distance_km": 3})
    assert legacy.search_radius_km == 3
    route = RequirementOutput(route_distance_km=5).to_trip_spec("改路程", legacy)
    assert (route.search_radius_km, route.max_distance_km) == (3, 5)
    search = RequirementOutput(search_radius_km=.5).to_trip_spec("改搜索", route)
    assert (search.search_radius_km, search.max_distance_km) == (.5, 5)
    assert requirement_delta(route, search)[1]["discovery"]
    reset = RequirementOutput(clear_search_radius=True).to_trip_spec("取消搜索限制", search)
    assert reset.search_radius_km is None and reset.max_distance_km == 5
    reset = RequirementOutput(clear_route_distance=True).to_trip_spec("取消路程上限", search)
    assert reset.search_radius_km == .5 and reset.max_distance_km is None
    old_edit = RequirementOutput(max_distance_km=2).to_trip_spec("旧客户端修改", search)
    assert old_edit.search_radius_km == old_edit.max_distance_km == 2
    assert TripSpec(goal="旧大范围", max_distance_km=100).search_radius_km == 50


async def test_separate_limits_reach_search_and_actual_route_verification():
    spec = TripSpec(goal="半径小于道路绕行距离", location=Location(name="重庆", latitude=29.56, longitude=106.57), search_radius_km=.5, max_distance_km=2)
    world = BrowserWorld(DesktopSettings(amap_webservice_key="controlled-no-http"), None, None)
    world.amap._get = AsyncMock(return_value={"status": "1", "pois": []})
    token = run_context.set({"world_source": "amap"})
    try:
        world.bind_run_state({"trip_spec": spec})
        await world.search_places("餐厅", spec.location)
        assert world.amap._get.call_args.args[1]["radius"] == 500
        plan = PlanCandidate(plan_id="controlled", stops=[PlanStop(place_id="controlled", name="受控地点", category="餐厅",
            start_minute=600, end_minute=660, distance_km=1.2, distance_kind="route")])
        result = await verify_plan(spec, plan, None)
        assert not any(item.name.startswith("distance:") for item in result.hard_violations)
        result = await verify_plan(spec.model_copy(update={"max_distance_km": 1}), plan, None)
        assert any(item.name.startswith("distance:") for item in result.hard_violations)
    finally:
        run_context.reset(token)
        await world.close()


async def test_explicit_model_scopes_use_existing_sparse_contract():
    model = SimpleNamespace(structured=AsyncMock(side_effect=[
        RequirementOutput(search_radius_km=3, route_distance_km=5),
        RequirementOutput(clear_route_distance=True),
    ]))
    agent = RequirementAgent(model)
    base = TripSpec(goal="受控已确认需求", time_window_start="18:30", max_distance_km=2)
    edit = await agent.run("搜索半径三公里，单段路程上限五公里，其他不变", [], base)
    spec = edit.to_trip_spec("受控修改", base)
    assert spec.search_radius_km == 3 and spec.max_distance_km == 5
    edit = await agent.run("取消单段路程上限，其他不变", [], spec)
    changed = edit.to_trip_spec("受控取消", spec)
    assert changed.search_radius_km == 3 and changed.max_distance_km is None
    assert changed.time_window_start == base.time_window_start
    assert model.structured.await_count == 2
