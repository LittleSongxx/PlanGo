"""Synthetic source responses exercise real requirement/coordinator/discovery boundaries."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from plango.browser import run_context
from plango.settings import DesktopSettings
from plango.world import BrowserWorld
from plango_harness.agent.contracts import Evidence, Location, PlaceCandidate, TripSpec
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import initial_state
from plango_harness.tools.registry import ToolRegistry


@pytest.mark.parametrize("detail_ok", [True, False])
async def test_date_edit_refreshes_identity_and_forecast_before_advocacy_without_search(detail_ok):
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    tomorrow = today + timedelta(days=1)
    origin = Location(name="重庆", latitude=29.56, longitude=106.57, city_code="500103")
    previous = TripSpec(goal="两人餐厅行程", location=origin, party_size=2, visit_date=today, time_window_start="14:00")
    old = Evidence(evidence_id="amap-poi:old", source="amap", source_ref="https://fixture.invalid/poi", observed_at=now - timedelta(minutes=20), expires_at=now - timedelta(minutes=15), payload={"place_id": "amap:fixture", "name": "受控餐厅"})
    place = PlaceCandidate(place_id="amap:fixture", name="受控餐厅", address="受控地址", category="餐厅", latitude=29.56, longitude=106.57, average_price=80, price_known=True, evidence_ids=[old.evidence_id], source="amap")
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/v5/place/detail":
            assert request.url.params["id"] == "fixture"
            return httpx.Response(200, json={"status": "1", "pois": [{"id": "fixture", "name": "受控餐厅", "address": "受控地址", "type": "餐饮服务", "location": "106.57,29.56", "business": {"cost": "85", "opentime_today": "09:00-22:00"}}] if detail_ok else []})
        assert request.url.path == "/v3/weather/weatherInfo", "Cached venue list must not be searched again."
        assert request.url.params["extensions"] == "all"
        return httpx.Response(200, json={"status": "1", "forecasts": [{"reporttime": now.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"), "casts": [{"date": tomorrow.isoformat(), "dayweather": "多云", "nightweather": "阴", "daytemp": "26"}]}]})

    settings = DesktopSettings(amap_webservice_key="mock-transport-only")
    model = ModelAdapter(settings)
    world = BrowserWorld(settings, SimpleNamespace(binding=AsyncMock(return_value={"location_context": {"city": "重庆", "source": "manual", "latitude": 29.56, "longitude": 106.57}})), model)
    await world.amap.client.aclose()
    world.amap.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    deps = GraphDeps(model=model, world=world, tools=ToolRegistry(), planner=None, memory=None, runs=None, action_provider=None, max_tool_calls=4)
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update({name: graph.nodes[name].runnable for name in ("requirements", "supervisor", "discovery")}))
    state = initial_state(run_id="date-fixture", user_id="fixture", input_text="改成明天14:00，其他要求不变")
    state.update(previous_spec=previous, place_candidates=[place], evidence=[old], weather={"text": "旧天气"}, requirement_reference_at=now.isoformat(), turn_id=2, location_origin={"source": "user"})
    token = run_context.set({"run_id": state["run_id"], "places": {}})
    try:
        state.update(await nodes["requirements"].ainvoke(state))
        assert state["requirement_refresh"]["discovery"] is False
        assert state["requirement_refresh"]["weather"] is True
        decision = await nodes["supervisor"].ainvoke(state)
        assert decision["next_action"] == "discover", "Missing Advocate reports must not override required context refresh."
        state.update(decision)
        state.update(await nodes["discovery"].ainvoke(state))
        assert requests == ["/v5/place/detail", "/v3/weather/weatherInfo"]
        assert state["tool_call_count"] == 2
        assert state["weather"]["visit_date"] == tomorrow.isoformat()
        candidate = state["place_candidates"][0]
        assert candidate.place_id == place.place_id and state["trip_spec"].location == origin
        assert old.expires_at == now - timedelta(minutes=15)
        assert all(item.evidence_id != old.evidence_id for item in state["evidence"])
        if detail_ok:
            assert candidate.price_known and candidate.average_price == 85
            proof = next(item for item in state["evidence"] if item.evidence_id in candidate.evidence_ids)
            assert proof.observed_at > old.expires_at and proof.payload["name"] == candidate.name
        else:
            assert not candidate.price_known and candidate.evidence_ids == []
        assert (await world.get_place(candidate.place_id)).evidence_ids == candidate.evidence_ids
        assert (await nodes["supervisor"].ainvoke(state))["next_action"] == "advocate", "A completed refresh attempt must not loop."
    finally:
        run_context.reset(token)
        await world.close()
