"""Controlled HTTP routes and prices only; these fixtures are not merchant acceptance."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from plango.browser import run_context
from plango.settings import DesktopSettings
from plango.world import BrowserWorld
from plango_harness.agent.contracts import (
    Evidence,
    Location,
    PlaceCandidate,
    PlanCandidate,
    PlanStop,
    TripSpec,
)
from plango_harness.domain.planning import PlanEngine, verify_plan
from plango_harness.providers.world import AmapWorldProvider, Supply

ORIGIN = Location(name="受控起点", latitude=29.55, longitude=106.55, city_code="500103")
PLACES = [PlaceCandidate(place_id=f"amap:controlled-{index}", name=f"受控站点{index}", category="展览",
    latitude=29.56 + index * .01, longitude=106.57, source="amap", average_price=50,
    evidence_ids=[f"controlled-poi:{index}"], open_minute=0, close_minute=1440) for index in range(2)]


def transit_path(cost="4.5"):
    def walk(distance, duration, polyline):
        return {"distance": str(distance), "duration": str(duration),
                "steps": [{"instruction": "受控步行衔接", "polyline": polyline}]}

    def bus(name, distance, duration, polyline):
        return {"name": name, "distance": str(distance), "duration": str(duration),
                "departure_stop": {"name": name + "起站"}, "arrival_stop": {"name": name + "终站"},
                "polyline": polyline}

    return {"cost": cost, "duration": "1200", "walking_distance": "700", "segments": [
        {"walking": walk(200, 180, "106.550,29.550;106.551,29.551"),
         "bus": {"buslines": [bus("受控1路", 1000, 300, "106.551,29.551;106.560,29.560"), bus("备选不应累加", 99999, 300, "")]}},
        {"walking": walk(300, 240, "106.560,29.560;106.562,29.562"),
         "bus": {"buslines": [bus("受控2路", 2000, 300, "106.562,29.562;106.570,29.570")]}},
        {"walking": walk(200, 180, "106.570,29.570;106.571,29.571"), "bus": {"buslines": []}},
    ]}


async def controlled_provider(*, path=None, cross_city=False, city_code="023", unavailable=False):
    calls = []

    def response(request):
        calls.append(request)
        if request.url.path.endswith("geocode/regeo"):
            code = "010" if cross_city and request.url.params["location"] != "106.550000,29.550000" else city_code
            return httpx.Response(200, json={"status": "1", "regeocode": {"addressComponent": {"citycode": code, "city": "受控城市"}}})
        if unavailable:
            return httpx.Response(200, json={"status": "0", "infocode": "10003"})
        if request.url.path.endswith("transit/integrated"):
            return httpx.Response(200, json={"status": "1", "route": {
                "distance": "999999",  # The provider's point-to-point walking distance is not transit distance.
                "transits": [path if path is not None else transit_path()],
            }})
        return httpx.Response(200, json={"status": "1", "route": {"paths": [{"distance": "1200", "duration": "420",
            "polyline": "106.550000,29.550000;106.560000,29.560000"}]}})

    provider = AmapWorldProvider(DesktopSettings(amap_webservice_key="controlled-http-only"))
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    return provider, calls


async def enrich(provider, *, mode="transit", party_size=3, budget=400, two_stops=False):
    now = datetime.now(timezone.utc)
    places = PLACES if two_stops else PLACES[:1]
    facts = [Evidence(evidence_id=p.evidence_ids[0], source="amap", source_ref="controlled-http-only",
        observed_at=now, expires_at=now + timedelta(minutes=10), payload={"place_id": p.place_id}) for p in places]

    async def get_place(pid):
        return next(p for p in places if p.place_id == pid)

    async def get_supply(pid, at_minute):
        return Supply(pid, True, True, None, 0, "amap", now, now + timedelta(minutes=10))

    world = SimpleNamespace(get_place=get_place, get_supply=get_supply, estimate_route=provider.estimate_route)
    planner = PlanEngine(world)
    spec = TripSpec(goal="受控路线费用核验", location=ORIGIN, time_window_start="14:00", travel_mode=mode,
                    party_size=party_size, budget=budget)
    plan = PlanCandidate(plan_id="controlled-transit", stops=[PlanStop(place_id=p.place_id, name=p.name,
        category=p.category, start_minute=840, end_minute=870, evidence_ids=p.evidence_ids) for p in places])
    result = await planner.enrich(spec, plan, evidence=facts)
    checked = await verify_plan(spec, result, None, evidence=[*facts, *planner.last_evidence])
    return result, checked, planner, spec, facts


async def test_real_adapter_contract_keeps_transfer_walks_citycode_and_departure():
    provider, calls = await controlled_provider()
    try:
        result, checked, planner, spec, facts = await enrich(provider, two_stops=True)
        assert result.total_cost == 327 and checked.hard_constraints_pass
        assert all(stop.estimated_cost == 163.5 and stop.transport_cost == 13.5 for stop in result.stops)
        routes = [request for request in calls if "/direction/" in request.url.path]
        assert [request.url.params["time"] for request in routes] == ["14:00", "14:50"]
        assert routes[0].url.params["city"] == routes[0].url.params["cityd"] == "023"
        assert routes[1].url.params["origin"] == "106.570000,29.560000"
        assert len([request for request in calls if "regeo" in request.url.path]) == 3  # Shared middle point is cached.
        evidence = next(e for e in planner.last_evidence if "segments" in e.payload)
        assert evidence.payload["distance_km"] == 3.7 and evidence.payload["walking_distance_m"] == 700
        assert evidence.payload["transfers"] == 1 and len(evidence.payload["segments"]) == 5
        assert "受控1路" in result.stops[0].transport_summary and "受控2路" in result.stops[0].transport_summary
        assert "步行200米至受控1路起站" in result.stops[0].transport_summary
        assert "转乘受控2路" in result.stops[0].transport_summary
        assert "标准票价估算 ¥4.5/人" in result.stops[0].transport_summary
        assert "备选不应累加" not in result.stops[0].transport_summary
        assert evidence.payload["paths"][0]["path"][0] == [106.55, 29.55]
        assert evidence.payload["paths"][1]["mode"] == "transit" and evidence.payload["paths"][1]["name"] == "受控1路"
        repeated = await planner.enrich(spec, result, evidence=facts)
        assert repeated.total_cost == 327 and len(calls) == 5  # No duplicate fare after enrichment/cache reuse.
        route, _ = await provider.estimate_route(ORIGIN, PLACES[0], mode="transit", visit_date=date(2026, 9, 10), at_minute=1080)
        assert calls[-1].url.params["date"] == "2026-09-10" and calls[-1].url.params["time"] == "18:00"
        assert route["requested_visit_date"] == "2026-09-10"
    finally:
        await provider.close()


async def test_nested_empty_provider_containers_are_not_railway_or_missing_walks():
    path = transit_path()
    for segment in path["segments"]:
        segment.update(railway={"via_stops": [], "alters": [], "spaces": []}, taxi=[])
    path["segments"][0]["walking"] = {"steps": []}
    path["walking_distance"] = "500"
    provider, _ = await controlled_provider(path=path)
    try:
        route, proof = await provider.estimate_route(ORIGIN, PLACES[0], mode="transit")
        assert route["distance_km"] == 3.5 and route["transit_min"] == 20
        assert route["walking_distance_m"] == 500 and proof.confidence > 0
    finally:
        await provider.close()


@pytest.mark.parametrize("fare", [None, [], "", "nan", "-1", False, True])
async def test_missing_fare_preserves_route_but_budget_is_unknown(fare):
    provider, _ = await controlled_provider(path=transit_path(fare))
    try:
        result, checked, _, _, _ = await enrich(provider)
        assert result.stops[0].travel_min == 20 and result.stops[0].distance_kind == "route"
        assert result.stops[0].transport_cost is None and result.total_cost == 150
        assert checked.hard_constraints_pass and not checked.evidence_complete
        assert any(check.name.startswith("transport_cost:") for check in checked.unknown_evidence)
    finally:
        await provider.close()


@pytest.mark.parametrize("mode,cost,total", [("driving", None, 150), ("walking", 0, 150), ("transit", 13.5, 163.5)])
async def test_fee_scope_and_budget_include_transport_once(mode, cost, total):
    provider, _ = await controlled_provider()
    try:
        result, checked, _, _, _ = await enrich(provider, mode=mode, budget=155)
        assert result.total_cost == total and result.stops[0].transport_cost == cost
        assert any(c.name == "budget" for c in checked.hard_violations) is (mode == "transit")
        if mode == "driving":
            assert "油费、停车费及过路费未估" in result.stops[0].transport_summary
            assert any(c.name.startswith("transport_cost:") for c in checked.unknown_evidence)
            route, _ = await provider.estimate_route(ORIGIN, PLACES[0], mode="driving")
            assert route["paths"][0]["path"] == [[106.55, 29.55], [106.56, 29.56]]
        elif mode == "walking":
            assert checked.executable
            route, _ = await provider.estimate_route(ORIGIN, PLACES[0], mode="walking")
            assert route["paths"][0]["path"] == [[106.55, 29.55], [106.56, 29.56]]
    finally:
        await provider.close()


@pytest.mark.parametrize("kind", ["cross_city", "adcode", "railway", "missing_segments", "quota"])
async def test_unsupported_or_missing_transit_never_becomes_zero_time_or_free(kind):
    path = transit_path()
    if kind == "railway":
        path["segments"][0]["railway"] = {"name": "未支持火车段"}
    if kind == "missing_segments":
        path["segments"] = []
    provider, calls = await controlled_provider(path=path, cross_city=kind == "cross_city",
        city_code="500103" if kind == "adcode" else "023", unavailable=kind == "quota")
    try:
        route, evidence = await provider.estimate_route(ORIGIN, PLACES[0], mode="transit")
        assert evidence.confidence == 0 and route["distance_kind"] == "straight_line_lower_bound"
        assert route["transit_min"] is None and route["cost_per_person"] is None
        assert route["distance_km"] > 0 and route["origin"] == [ORIGIN.latitude, ORIGIN.longitude]
        if kind in {"cross_city", "adcode"}:
            assert not any("/direction/" in request.url.path for request in calls)
    finally:
        await provider.close()


async def test_browser_world_uses_persisted_mode_date_and_explicit_leg_time():
    world = BrowserWorld(DesktopSettings(amap_webservice_key="controlled-http-only"), SimpleNamespace(), SimpleNamespace())
    await world.amap.close()
    world.amap, calls = await controlled_provider()
    token = run_context.set({"places": {}})
    try:
        spec = TripSpec(goal="受控公交行程", location=ORIGIN, travel_mode="transit", visit_date=date(2026, 9, 10))
        world.bind_run_state({"trip_spec": spec})
        route, _ = await world.estimate_route(ORIGIN, PLACES[0], at_minute=900)
        assert route["transit_min"] == 20
        assert calls[-1].url.params["time"] == "15:00" and calls[-1].url.params["date"] == "2026-09-10"
    finally:
        run_context.reset(token)
        await world.close()
