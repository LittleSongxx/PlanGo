"""Controlled HTTP transport fixtures; never real merchant facts or model calls."""

import httpx
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.settings import DesktopSettings
from plango_harness.agent.contracts import Location
from plango_harness.providers.world import AmapWorldProvider

POI = {"id": "fixture-poi", "name": "受控餐厅", "location": "106.577,29.558", "type": "餐饮服务;中餐厅",
       "cityname": "重庆市", "address": "受控地址", "photos": [{"url": "https://fixture.invalid/photo.png"}],
       "business": {"cost": "80", "rating": "4.7", "opentime_today": "09:00-22:00"}}


def transport(calls):
    def response(request):
        calls.append((request.url.path, {k: v for k, v in request.url.params.items() if k != "key"}))
        if request.url.path.endswith("/geocode/geo"):
            return httpx.Response(200, json={"status": "1", "geocodes": [{"location": "106.577,29.558", "formatted_address": "重庆市", "province": "重庆市", "city": [], "district": "渝中区", "level": "市"}]})
        if request.url.path.endswith("/geocode/regeo"):
            return httpx.Response(200, json={"status": "1", "regeocode": {"formatted_address": "重庆市渝中区受控地址", "addressComponent": {"province": "重庆市", "city": [], "district": "渝中区"}}})
        return httpx.Response(200, json={"status": "1", "pois": [POI]})
    return httpx.MockTransport(response)


async def test_shared_poi_source_scope_cache_metadata_and_planner_hours():
    provider = AmapWorldProvider(DesktopSettings(amap_webservice_key="controlled-fixture-only"))
    await provider.client.aclose()
    calls = []
    provider.client = httpx.AsyncClient(transport=transport(calls))
    try:
        city = await provider.search_pois("餐厅", city="重庆")
        assert city["scope"] == "city" and calls[-1][0] == "/v5/place/text"
        assert calls[-1][1]["region"] == "重庆" and calls[-1][1]["city_limit"] == "true"
        again = await provider.search_pois("餐厅", city="重庆")
        assert again["cache_hit"] and len(calls) == 1
        assert again["observed_at"] == city["observed_at"] and again["expires_at"] == city["expires_at"]
        assert again["pois"][0]["photos"] == POI["photos"]
        around = await provider.search_pois("餐厅", longitude=106.577, latitude=29.558, radius_m=3000)
        assert around["scope"] == "around" and calls[-1][0] == "/v5/place/around"
        assert calls[-1][1]["location"] == "106.577000,29.558000" and calls[-1][1]["radius"] == "3000"
        await provider.search_pois("餐厅", longitude=106.53, latitude=29.57, radius_m=3000)
        assert len(calls) == 3
        await provider.search_pois("餐厅", city="重庆", refresh=True)
        assert len(calls) == 4
        places, evidence = await provider.search_places("餐厅", Location(name="重庆", longitude=106.577, latitude=29.558))
        assert places[0].name == "受控餐厅" and places[0].open_minute == 540 and places[0].close_minute == 1320
        assert places[0].price_known and places[0].average_price == 80
        assert all("/v5/place/around" in e.source_ref for e in evidence)
        _, cached_evidence = await provider.search_places("餐厅", Location(name="重庆", longitude=106.577, latitude=29.558))
        assert cached_evidence[-1].observed_at == evidence[-1].observed_at
        assert cached_evidence[-1].expires_at == evidence[-1].expires_at
    finally:
        await provider.close()


def test_geo_routes_keep_scope_provenance_and_never_create_browser_jobs(tmp_path):
    config = DesktopSettings(amap_webservice_key="controlled-fixture-only", data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/runs.sqlite", checkpoint_path=tmp_path / "checkpoints.sqlite")
    app = create_app(config, token="geo-test-only")
    calls = []
    provider = app.state.runtime.world_service.provider.amap
    # Replace only the network transport; all request building and response parsing are real.
    provider.client = httpx.AsyncClient(transport=transport(calls))
    with TestClient(app, headers={"Authorization": "Bearer geo-test-only"}) as client:
        body = {"query": "餐厅", "location_context": {"city": "重庆", "source": "config"}}
        assert client.post('/api/v1/geo/search', json=body, headers={"Authorization": ""}).status_code == 401
        data = client.post('/api/v1/geo/search', json=body).json()
        assert data['scope'] == 'city' and data['pois'][0]['photos'] == POI['photos']
        body['location_context'].update(longitude=106.577, latitude=29.558, granularity='city')
        assert client.post('/api/v1/geo/search', json=body).json()['scope'] == 'city'
        body['location_context'].update(source='manual', detail_source='address', granularity='address')
        assert client.post('/api/v1/geo/search', json=body).json()['scope'] == 'around'
        invalid = {**body, 'location_context': {**body['location_context'], 'coordinate_system': 'WGS84'}}
        assert client.post('/api/v1/geo/search', json=invalid).status_code == 422
        assert client.post('/api/v1/geo/search', json={**body, 'limit': 26}).status_code == 422
        city = client.post('/api/v1/geo/geocode', json={'address': '重庆', 'city': '重庆'}).json()['location']
        assert city['granularity'] == 'city' and city['city'] == '重庆市'
        address = client.post('/api/v1/geo/reverse', json={'longitude': 106.577, 'latitude': 29.558}).json()['location']
        assert address['district'] == '渝中区' and 'accuracy' not in address
        assert client.get('/api/v1/runs').json()['runs'] == []
