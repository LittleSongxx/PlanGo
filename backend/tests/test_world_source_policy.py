"""Source-policy integration with synthetic Amap/DOM facts; no external requests."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from plango.app import create_app
from plango.browser import run_context
from plango.outcomes import TaskIntent
from plango.settings import DesktopSettings
from plango.world import BrowserWorld, PageData
from plango_harness.agent.contracts import Evidence, Location, PlaceCandidate, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from test_browser_harness import TOKEN, settings, wait_for


def fixture_place():
    place = PlaceCandidate(place_id="amap:fixture", name="受控餐厅", address="重庆受控路1号", category="餐厅", latitude=29.56, longitude=106.57, average_price=50, price_known=True, open_minute=0, close_minute=1440, source="amap", evidence_ids=["fixture:place"])
    now = datetime.now(timezone.utc)
    fact = Evidence(evidence_id="fixture:place", source="amap", source_ref="https://fixture.invalid/poi", claim="合成高德样本", payload=place.model_dump(mode="json"), observed_at=now, expires_at=now + timedelta(minutes=10))
    return place, fact


def test_amap_planning_from_empty_desktop_has_draft_without_any_browser_command(tmp_path):
    app = create_app(settings(tmp_path).model_copy(update={"amap_webservice_key": "only-replaced-fixture-methods"}), token=TOKEN)
    world = app.state.runtime.world_service.provider
    place, fact = fixture_place()
    world.amap.search_places = AsyncMock(return_value=([place], [fact]))
    world.amap.get_place = AsyncMock(return_value=place)
    world.amap.estimate_route = AsyncMock(return_value=({"distance_km": 1, "driving_min": 5, "source": "amap"}, fact))
    world.amap.get_weather = AsyncMock(return_value=({"text": "晴", "rain": False}, fact))
    world.amap._get = AsyncMock(side_effect=AssertionError("No fixture may reach a real HTTP provider."))
    world.page = AsyncMock(side_effect=AssertionError("Ordinary geo planning must not require a browser tab."))
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "1人，今天18:30吃饭，预算300元，安排一个餐厅行程", "browser_session_id": "empty-desktop", "location_context": {"city": "重庆", "latitude": 29.56, "longitude": 106.57, "source": "manual"}}).json()["run_id"]
        current = wait_for(client, run_id, lambda v: bool(v.get("interrupt_id")) or v["phase"] in {"FAILED", "INFEASIBLE"})
        assert current["state"].get("selected_plan"), current
        assert current["phase"] == "REQUIREMENTS_READY", current
        assert any(c["name"].startswith("supply:") for c in current["state"]["verifier"]["unknown_evidence"])
        assert client.get("/api/v1/browser/commands?browser_session_id=empty-desktop").json()["commands"] == []
        assert world.amap.search_places.await_count and world.amap.estimate_route.await_count
        world.page.assert_not_awaited()
        world.amap._get.assert_not_awaited()


def test_explicit_browser_source_is_scoped_and_region_change_rejects_old_page_facts():
    async def exercise():
        world = BrowserWorld(DesktopSettings(amap_webservice_key="stub-only"), SimpleNamespace(), SimpleNamespace())
        place, fact = fixture_place()
        old = TripSpec(goal="按当前网页规划", location=Location(name="重庆", latitude=29.56, longitude=106.57))
        new = old.model_copy(update={"location": Location(name="北京", latitude=39.9, longitude=116.4)})
        page = {"command_id": "fixture-page", "snapshot_id": "fixture-page", "url": "https://fixture.invalid/shop", "observed_at": fact.observed_at.isoformat(), "fields": {"places": [place.model_dump(mode="json")], "routes": {place.place_id: {"distance_km": 1, "driving_min": 1}}}}
        world.page = AsyncMock(return_value=page)
        world.extract = AsyncMock(return_value=PageData())
        world.amap.search_places = AsyncMock(return_value=([], []))
        world.amap.estimate_route = AsyncMock(return_value=({"source": "amap", "driving_min": None}, fact))
        token = run_context.set({"places": {}})
        try:
            world.bind_run_state({"input_text": "预算改为500元", "trip_spec": old, "previous_spec": old})
            assert run_context.get()["world_source"] == "browser"
            places, _ = await world.search_places("餐厅", old.location)
            assert [p.place_id for p in places] == [place.place_id]
            world.bind_run_state({"input_text": "改到北京", "trip_spec": new, "previous_spec": old})
            places, facts = await world.search_places("餐厅", new.location)
            assert places == [] and facts == []
            assert run_context.get()["places"] == {}
            route, _ = await world.estimate_route(new.location, place)
            assert route["source"] == "amap"  # Unbound old-page route was not accepted.
            world.bind_run_state({"input_text": "不用网页，改用高德规划", "trip_spec": new, "previous_spec": old})
            assert run_context.get()["world_source"] == "amap"
        finally:
            run_context.reset(token)
        token = run_context.set({"places": {}})
        try:
            world.bind_run_state({"input_text": "普通行程规划", "trip_spec": new.model_copy(update={"goal": "普通行程规划"})})
            assert run_context.get()["world_source"] == "amap", "Previous run's page preference must not leak."
        finally:
            run_context.reset(token)
            await world.close()

    asyncio.run(exercise())


def test_selected_poi_center_template_never_geocodes_merchant_as_origin(tmp_path):
    """Regression from the real UI failure; all provider/model responses below are synthetic."""
    app = create_app(settings(tmp_path).model_copy(update={"amap_webservice_key": "only-replaced-fixture-methods"}), token=TOKEN)
    world = app.state.runtime.world_service.provider
    place, _ = fixture_place()
    place = place.model_copy(update={"name": "寿司郎(大融城店)"})
    world.amap.get_place = AsyncMock(return_value=place)
    world.amap.geocode = AsyncMock(side_effect=AssertionError("Canonical selected POI must not become a global origin geocode."))

    async def confused_model(schema, *, fallback, **kwargs):
        return TaskIntent(kind="planning", requirements=RequirementOutput(location_name=place.name)) if schema is TaskIntent else fallback

    app.state.runtime.model.structured = confused_model
    text = "就以「寿司郎(大融城店)」（观音桥步行街8号大融城LG层055号）为中心，帮我排一套附近的周末方案"
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        created = client.post("/api/v1/runs", json={"input_text": text, "browser_session_id": "empty-desktop", "location_context": {"city": "重庆", "latitude": 29.575499, "longitude": 106.532212, "source": "manual"}, "selected_poi": {"poi_id": "fixture", "name": place.name, "source": "amap", "latitude": place.latitude, "longitude": place.longitude}})
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        current = wait_for(client, run_id, lambda v: bool(v.get("interrupt_id")))
        spec = current["state"]["trip_spec"]
        assert (spec["location"]["latitude"], spec["location"]["longitude"]) == (29.575499, 106.532212)
        assert spec["search_location"]["latitude"] == place.latitude
        assert spec["must_visit_place_ids"] == [place.place_id]
        assert current["state"]["clarification"]["fields"] == ["context"], current
        world.amap.geocode.assert_not_awaited()

        app.state.runtime.model.structured = AsyncMock(side_effect=lambda schema, *, fallback, **kwargs: fallback)
        reply = f"今天2026-09-0918:30到19:30，3位成人一起吃晚餐，不设预算，只按当前网页里的「{place.name}」安排一个餐厅行程，不增加其他地点。行程确认后核对预约表单，不要提交。"
        assert client.post(f"/api/v1/runs/{run_id}/messages", json={"text": reply}).status_code == 202
        reading = wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")) or v["phase"] == "FAILED")
        assert reading["phase"] != "FAILED", reading
        assert reading["state"]["browser_task_context"]["mode"] == "planning"
        assert reading["state"]["browser_task_context"]["latest"] == reply
        assert reading["state"]["trip_spec"]["party_size"] == 3
        assert reading["state"]["trip_spec"]["budget"] is None
        assert sum(reading["state"]["trip_spec"]["party_counts"].values()) == 3
        assert reading["state"]["trip_spec"]["location"] == spec["location"]
        world.amap.geocode.assert_not_awaited()



def test_clarification_reenters_shared_intent_entry_once(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "周末安排一个行程，预算待定", "browser_session_id": "empty-desktop", "location_context": {"city": "重庆", "latitude": 29.56, "longitude": 106.57, "source": "manual"}}).json()["run_id"]
        first = wait_for(client, run_id, lambda v: bool(v.get("interrupt_id")))
        text = "不要规划，先读取当前网页菜单"
        assert client.post(f"/api/v1/runs/{run_id}/messages", json={"text": text}).status_code == 202
        reading = wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
        assert reading["state"]["turn_id"] == first["state"]["turn_id"] + 1
        assert reading["state"]["browser_task_context"]["mode"] == "browser"
        assert reading["state"]["browser_task_context"]["read_kind"] == "menu_read"
        assert reading["state"]["browser_task_context"]["latest"] == text
        assert reading["state"]["clarification"] is None, "Old planning questions cannot block the new browser task's approvals"


def test_current_origin_reference_reuses_known_point_or_clarifies_without_geocoding(tmp_path):
    app = create_app(settings(tmp_path).model_copy(update={"amap_webservice_key": "never-called-fixture"}), token=TOKEN)
    geocode = AsyncMock(side_effect=AssertionError("An existing-origin reference is not a geographic search."))
    app.state.runtime.world_service.provider.amap.geocode = geocode
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        for point in ({"latitude": 29.575499, "longitude": 106.532212}, {}):
            text = "从目前出发地址出发，周末安排一个行程，预算待定"
            rid = client.post("/api/v1/runs", json={"input_text": text, "browser_session_id": "origin-fixture", "location_context": {"city": "重庆", "source": "manual", **point}}).json()["run_id"]
            paused = wait_for(client, rid, lambda v: bool(v.get("interrupt_id")))
            if point:
                spec = paused["state"]["trip_spec"]
                assert (spec["location"]["latitude"], spec["location"]["longitude"]) == (point["latitude"], point["longitude"])
            else:
                assert paused["state"].get("trip_spec") is None
                assert "起点" in paused["state"]["clarification"]["question"]
        geocode.assert_not_awaited()
