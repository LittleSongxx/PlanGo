"""Controlled HTTP source rows verify city scope and honest failed-route distances."""

import httpx
from plango.settings import DesktopSettings
from plango_harness.agent.contracts import (
    Location,
    PlaceCandidate,
    PlanCandidate,
    PlanStop,
    TripSpec,
)
from plango_harness.domain.planning import verify_plan
from plango_harness.providers.world import AmapWorldProvider


async def test_city_scoped_geocode_and_failed_route_use_the_actual_origin():
    calls = []

    def response(request):
        calls.append((request.url.path, request.url.params.get("address"), request.url.params.get("city")))
        if "/direction/" in request.url.path:
            return httpx.Response(200, json={"status": "1", "route": {"paths": []}})
        address = request.url.params["address"]
        city, point, code = ("重庆市", "106.57,29.56", "500103") if address == "解放碑" else ("北京市", "116.4,39.9", "110101") if address == "北京" else ("西安市", "108.94,34.25", "610100")
        return httpx.Response(200, json={"status": "1", "geocodes": [{"city": city, "province": city, "location": point, "adcode": code}]})

    world = AmapWorldProvider(DesktopSettings(amap_webservice_key="mock-transport-only"))
    await world.client.aclose()
    world.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    try:
        assert await world.geocode("一家餐厅", city="重庆") is None
        assert calls[0][2] == "重庆"
        assert (await world.geocode("解放碑", city="重庆")).city_code == "500103"
        assert (await world.geocode("北京", city="重庆")).city_code == "110101"
        origin = Location(name="重庆", latitude=29.575499, longitude=106.532212)
        place = PlaceCandidate(place_id="amap:fixture", name="受控异地餐厅", category="餐厅", latitude=34.25, longitude=108.94, distance_km=0.6, source="amap")
        route, evidence = await world.estimate_route(origin, place, mode="walking")
        assert route["distance_km"] > 500 and route["distance_kind"] == "straight_line_lower_bound"
        assert route["walking_min"] is None and evidence.confidence == 0
        stop = PlanStop(place_id=place.place_id, name=place.name, category=place.category, start_minute=840, end_minute=900, distance_km=route["distance_km"], distance_kind=route["distance_kind"], tags=["route_unknown", "supply_unknown"])
        spec = TripSpec(goal="步行2公里内吃饭", location=origin, travel_mode="walking", max_distance_km=2)
        plan = PlanCandidate(plan_id="fixture", stops=[stop])
        checked = await verify_plan(spec, plan, None)
        assert any(c.name.startswith("distance:") and "直线" in c.detail for c in checked.hard_violations)
        assert not checked.hard_constraints_pass
        near = plan.model_copy(update={"stops": [stop.model_copy(update={"distance_km": 0.6})]})
        checked = await verify_plan(spec, near, None)
        assert not checked.evidence_complete and not checked.executable
    finally:
        await world.close()
