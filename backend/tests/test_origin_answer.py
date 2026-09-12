from types import SimpleNamespace
from unittest.mock import AsyncMock

from plango.browser import run_context
from plango.location import LocationContext, select_origin
from plango.settings import DesktopSettings
from plango.world import BrowserWorld


def city_context():
    return LocationContext(city="重庆", source="config")


def test_location_interrupt_keeps_user_sentence_when_extract_is_only_the_search_city():
    state = {
        "clarification": {"question": "未能取得重庆的真实起点坐标，请提供定位，或配置高德 Key 并明确出发地点。"},
        "messages": [{"type": "human", "content": "从重庆观音桥地铁站出发，按这个起点核对路线。"}],
        "input_text": "读取店铺\n从重庆观音桥地铁站出发，按这个起点核对路线。",
    }
    name, location, meta = select_origin(state, "重庆", None, city_context())
    assert location is None
    assert name.startswith("从重庆观音桥地铁站出发")
    assert meta["source"] == "user"


def test_selected_destination_is_not_kept_as_origin_when_user_names_another_place():
    from plango_harness.agent.contracts import Location, TripSpec

    shop = Location(name="已选门店", latitude=29.57522, longitude=106.532842)
    previous = TripSpec(goal="核对已选门店路线", location=shop, search_location=shop, must_visit_place_ids=["amap:shop"])
    state = {
        "selected_poi": {"name": shop.name, "place_id": "amap:shop"},
        "location_origin": {"source": "user", "reference": "selected_place", "name": shop.name},
        "messages": [{"type": "human", "content": "出发地点是用户出发地址"}],
        "input_text": "出发地点是用户出发地址",
    }
    name, location, meta = select_origin(state, "用户出发地址", previous, city_context())
    assert name == "用户出发地址"
    assert location is None
    assert meta == {"source": "user", "name": "用户出发地址"}


def test_initial_city_extract_is_not_replaced_by_the_whole_task_sentence():
    state = {
        "input_text": "我们2人，预算400元，在重庆安排半天看展再吃饭",
        "messages": [{"type": "human", "content": "我们2人，预算400元，在重庆安排半天看展再吃饭"}],
    }
    name, location, meta = select_origin(state, "重庆", None, city_context())
    assert name == "重庆"
    assert location is None
    assert meta["source"] == "user"


async def test_geocode_without_key_does_not_extract():
    world = BrowserWorld(DesktopSettings(amap_webservice_key=""), SimpleNamespace(), SimpleNamespace())
    world.page = AsyncMock(side_effect=AssertionError("no-key geocode must not extract"))
    token = run_context.set({"run_id": "origin-fixture"})
    try:
        assert await world.geocode("渡口码头") is None
        world.page.assert_not_awaited()
    finally:
        run_context.reset(token)
        await world.close()
