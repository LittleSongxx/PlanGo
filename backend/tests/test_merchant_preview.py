"""Controlled app-only merchant preview; no live login or business transaction."""

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import BrowserDecision
from plango.outcomes import browser_manual_error
from plango.world import PageData
from test_browser_harness import TOKEN, fixture, settings, wait_for


@pytest.mark.parametrize("elements", [[], [{"idx": 0, "tag": "a", "text": "点击下载", "name": "", "href": "https://www.dianping.com/app/download?utm_source=dp_pc_index"}]])
def test_app_only_menu_preview_preserves_partial_fields_without_a_vision_loop(tmp_path, elements):
    assert browser_manual_error({"url": "https://verify.meituan.com/v2/app/general_page", "fields": {"dom": {"manual_gate": None}}}) == "captcha_required"
    app = create_app(settings(tmp_path), token=TOKEN)
    place = "受控餐厅\n地址：重庆市受控路1号"
    offer = "精选双人餐\n周一至周日\n随时退\n98元"
    text = place + "\n团购套餐\n" + offer + "\n推荐菜\n清蒸鱼\n菜单(9)\n去大众点评App查看菜单详情"
    calls = []

    async def extract(schema, *, fallback, **kwargs):
        calls.append(schema.__name__)
        assert schema is not BrowserDecision, "An explicit app-only detail gate needs no model or Vision retry"
        if schema is PageData:
            return PageData.model_validate({"places": [{"name": "受控餐厅", "address": "重庆市受控路1号", "quote": place}],
                                           "menu": [{"name": "清蒸鱼", "quote": "清蒸鱼"}],
                                           "offers": [{"name": "精选双人餐", "price": 98, "people": 2, "conditions": ["周一至周日", "随时退"], "quote": offer}]})
        return fallback

    app.state.runtime.model.structured = extract
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取当前页面的门店地址、菜单和套餐使用条件，不下单。", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        wait_for(client, rid, lambda value: bool(value["state"].get("browser_wait")))
        command = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        response = {**fixture(command), "text": text, "title": "受控餐厅", "tables": [], "elements": elements,
                    "fields": {"dom": {"canvas_count": 1, "manual_gate": None, "forms": []}}}
        assert client.post(f"/api/v1/browser/commands/{command['command_id']}/result", json=response).status_code == 200
        result = wait_for(client, rid, lambda value: value["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert result["phase"] == "PARTIAL_FAILED", result
        outcome = result["state"]["execution_outcome"]
        assert outcome["status"] == "needs_evidence"
        assert set(outcome["data"]["observed_fields"]) == {"merchant", "address", "recommended_dishes", "offers"}
        assert outcome["data"]["missing_fields"] == ["menu", "offer_conditions"]
        assert "App" in result["state"]["reason"]
        assert not result["state"].get("action_results")
        assert result["state"].get("browser_vision_turn") is None
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []
        assert calls == ["PageData"]
