"""Synthetic replicas of observed public preview structure; no live site/model calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.browser import run_context
from plango.settings import DesktopSettings
from plango.world import BrowserWorld, PageData, dianping_preview_data
from test_browser_harness import TOKEN, fixture, settings, wait_for

BODY = """返回
青竹餐厅(江北店)
川菜
11700条¥62/人
江畔路8号附5号6楼
代金券
50元代金券
随时退
过期自动退
¥
47
9.4折
买券
更多1张代金券
团购套餐
精选双人餐
周一至周日
随时退
过期自动退
¥
98
8.2折
￥120
抢购
招牌冷面鸡
周一至周日
随时退
过期自动退
¥
19.9
6.3折
￥32
抢购
推荐菜
查看更多
网友推荐(726)
115人推荐
57人推荐
清蒸鱼
鱼香肉丝
椒王馋嘴兔手工川北凉粉歌乐山辣子鸡包菜炒粉丝纸锅蟹黄豆花
去大众点评App查看全部726道推荐菜
菜单(9)
去大众点评App查看菜单详情
"""


def observation(**changes):
    return {"command_id": "preview-command", "url": "https://www.dianping.com/shop/Shop123",
            "title": "【青竹餐厅(江北店)】电话_地址_价格 - 大众点评网", "text": BODY, "tables": [], **changes}


def test_literal_preview_prices_entities_and_scope():
    data = dianping_preview_data(observation())
    assert data.places[0].name == "青竹餐厅(江北店)"
    assert (data.places[0].address, data.places[0].average_price, data.places[0].price_unit) == ("江畔路8号附5号6楼", 62, "人均")
    assert [item.price for item in data.offers] == [47, 98, 19.9]
    assert [item.original_price for item in data.offers] == [None, None, None]
    assert data.offers[1].people == 2
    assert data.offers[0].conditions == ["随时退", "过期自动退"]
    assert data.offers[1].conditions == ["周一至周日", "随时退", "过期自动退"]
    assert [item.name for item in data.menu] == ["清蒸鱼", "鱼香肉丝"]
    assert all(item.price is None for item in data.menu)
    assert all(item.quote in BODY and item.name in item.quote for item in [*data.places, *data.offers, *data.menu])
    assert "精选双人餐" not in data.offers[0].quote and "招牌冷面鸡" not in data.offers[1].quote
    assert data.places[0].reservable is None and data.places[0].open_now is None


@pytest.mark.parametrize("url", ["https://dianping.com/shop/Shop123", "https://m.dianping.com/shop/Shop123/"])
def test_exact_site_and_shop_path(url):
    assert dianping_preview_data(observation(url=url)) is not None


@pytest.mark.parametrize("changes", [
    {"url": "https://dianping.com.evil.example/shop/Shop123"},
    {"url": "https://fake-dianping.com/shop/Shop123"},
    {"url": "https://www.dianping.com/member/Shop123"},
    {"url": "https://www.dianping.com/shop/Shop123/other"},
    {"url": "https://user:password@www.dianping.com/shop/Shop123"},
    {"title": "【白鹭餐厅】电话_地址 - 大众点评网"},
    {"text": BODY.replace("青竹餐厅(江北店)", "白鹭餐厅")},
    {"fields": {"dom": {"manual_gate": "captcha"}}},
])
def test_other_sites_merchants_and_manual_gates_do_not_use_adapter(changes):
    assert dianping_preview_data(observation(**changes)) is None


@pytest.mark.parametrize("text", [
    BODY.replace("¥\n47", "47"),
    BODY.replace("¥\n47", "￥120"),
    BODY.replace("¥\n47", "¥\n47\n¥\n48"),
    BODY.replace("¥\n47", "原价\n¥\n120"),
    BODY.replace("¥\n47", "¥\n" + "9" * 400),
])
def test_face_value_unlabelled_or_ambiguous_numbers_are_not_prices(text):
    data = dianping_preview_data(observation(text=text))
    assert data.offers[0].name == "50元代金券" and data.offers[0].price is None
    assert data.offers[1].price == 98, "An adjacent offer cannot donate its price"


def test_missing_and_ambiguous_merchant_facts_remain_unknown():
    data = dianping_preview_data(observation(text=BODY.replace("11700条¥62/人", "11700条").replace("江畔路8号附5号6楼", "江畔路8号\n长江路10号")))
    assert data.places[0].address is None and data.places[0].average_price is None
    assert data.places[0].price_unit is None
    huge_average = dianping_preview_data(observation(text=BODY.replace("¥62/人", "¥" + "9" * 400 + "/人")))
    assert huge_average.places[0].average_price is None and huge_average.places[0].price_unit is None


def test_unterminated_offer_and_related_merchants_cannot_donate_facts():
    text = BODY.replace("¥\n47\n9.4折\n买券", "下一张100元代金券\n¥\n90\n买券")
    data = dianping_preview_data(observation(text=text))
    assert "50元代金券" not in [item.name for item in data.offers]
    assert [item.price for item in data.offers] == [98, 19.9]
    assert dianping_preview_data(observation(text=BODY.replace("代金券\n50元代金券", "附近推荐\n白鹭餐厅\n代金券\n50元代金券"))) is None
    later = dianping_preview_data(observation(text=BODY.replace("团购套餐", "附近推荐\n白鹭餐厅\n团购套餐")))
    assert [item.price for item in later.offers] == [47] and not later.menu


@pytest.mark.asyncio
async def test_adapter_reuses_command_cache_without_model_and_preserves_saved_results():
    bridge = SimpleNamespace(get=AsyncMock(return_value=None))
    model = SimpleNamespace(structured=AsyncMock(side_effect=AssertionError("preview needs no model")))
    world = BrowserWorld(DesktopSettings(_env_file=None, amap_webservice_key=""), bridge, model)
    token = run_context.set({})
    try:
        data = await world.extract(observation())
        assert (await world.extract(observation())) is data
        assert bridge.get.await_count == 1 and model.structured.await_count == 0
        bridge.get.return_value = {"payload": {"_processed": PageData().model_dump(), "_processed_version": 4}}
        saved = await world.extract(observation(command_id="previous-command"))
        assert saved == PageData(), "Historical processed results are not rewritten by a new adapter"
    finally:
        run_context.reset(token)
        await world.close()


def test_new_adapter_result_rebuilds_structured_artifact_from_durable_command(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    app.state.runtime.model.structured = AsyncMock(side_effect=AssertionError("preview needs no model"))
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "读取当前页面的门店地址、菜单和套餐使用条件，不下单。", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        wait_for(client, run_id, lambda run: bool(run["state"].get("browser_wait")))
        command = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        response = {**fixture(command), **observation(command_id=command["command_id"]), "elements": []}
        assert client.post(f"/api/v1/browser/commands/{command['command_id']}/result", json=response).status_code == 200
        done = wait_for(client, run_id, lambda run: run["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == "PARTIAL_FAILED"
        stored = client.portal.call(app.state.runtime.bridge.get, command["command_id"])
        assert stored["payload"]["_processed_version"] == 4
        assert [item["price"] for item in stored["payload"]["_processed"]["offers"]] == [47, 98, 19.9]
        assert app.state.runtime.model.structured.await_count == 0

    restored_app = create_app(settings(tmp_path), token=TOKEN)
    with TestClient(restored_app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        snapshot = restored_app.state.runtime.snapshot

        def without_cached_artifacts(row):
            value = snapshot(row)
            value["state"] = {**value["state"], "browser_artifacts": []}
            return value

        with patch.object(restored_app.state.runtime, "snapshot", side_effect=without_cached_artifacts):
            restored = client.get(f"/api/v1/runs/{run_id}").json()
        rebuilt = next(item for item in restored["state"]["browser_artifacts"] if item["artifact_id"] == "page:" + command["command_id"])
        assert rebuilt["data"]["offers"] == stored["payload"]["_processed"]["offers"]
        assert rebuilt["data"]["places"] == stored["payload"]["_processed"]["places"]
        assert rebuilt["data"]["menu"] == stored["payload"]["_processed"]["menu"]
        assert client.portal.call(restored_app.state.runtime.bridge.get, command["command_id"]) == stored, "Recovery cannot rewrite the saved command or its receipt"
