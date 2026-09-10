"""Controlled restart/current-page binding regression; no live merchant or browser."""


import pytest
from fastapi.testclient import TestClient
from plango.task import DeliveryDecision, TaskDecision
from test_browser_harness import TOKEN, create_app, fixture, settings, wait_for


@pytest.mark.parametrize("target", ["当前页面", "这个页面", "本页", "此网页"])
def test_new_explicit_current_page_read_after_restart_does_not_reuse_previous_tab_or_scope(tmp_path, target):
    config = settings(tmp_path)
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(create_app(config, token=TOKEN), headers=headers) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取当前网页菜单和套餐使用条件", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        wait_for(client, rid, lambda value: bool(value["state"].get("browser_wait")))
        command = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        assert client.post(f"/api/v1/browser/commands/{command['command_id']}/result", json=fixture(command)).status_code == 200
        old = wait_for(client, rid, lambda value: value["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert old["state"]["browser_observation"]["tab_id"] == "fixture-tab"
    app = create_app(config, token=TOKEN)
    place = "雾岚餐厅\n地址：重庆市青竹路8号"
    offer = "双人套餐\n售价98元"

    async def extract(schema, *, fallback, **kwargs):
        import json
        assert schema in {TaskDecision, DeliveryDecision}
        if not json.loads(kwargs["user"])["browser_steps"]:
            return TaskDecision(operation="read")
        return TaskDecision(operation="answer", answer="已读取当前页面的门店、地址和优惠预览。")

    app.state.runtime.model.structured = extract
    with TestClient(app, headers=headers) as client:
        text = f"只读取{target}可见的门店名称、地址和优惠预览，保留售价并列出缺失的规则；不进入App、不购买、不下单。"
        response = client.post(f"/api/v1/runs/{rid}/messages", json={"text": text, "request_id": "new-current-page"})
        assert response.status_code == 202, response.text
        wait_for(client, rid, lambda value: (value["state"].get("browser_wait") or {}).get("command_id") not in {None, command["command_id"]})
        fresh = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        assert fresh["operation"] == "extract" and fresh.get("tab_id") is None
        assert fresh.get("expected_snapshot_id") is None and fresh.get("approved_action_id") is None
        result = {**fixture(fresh), "tab_id": "new-current-tab", "title": "雾岚餐厅", "text": place + "\n团购套餐\n" + offer, "tables": []}
        assert client.post(f"/api/v1/browser/commands/{fresh['command_id']}/result", json=result).status_code == 200
        done = wait_for(client, rid, lambda value: value["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == "SUCCEEDED", done
        assert done["state"]["browser_observation"]["tab_id"] == "new-current-tab"
        assert done["state"]["execution_outcome"]["kind"] == "task_answer"
        assert done["state"]["execution_outcome"]["data"]["business_completed"] is False
        assert done["state"]["execution_outcome"]["summary"].startswith("已读取当前页面")
        assert place in done["state"]["browser_artifacts"][0]["data"]["text"]
        assert not done["state"].get("action_results")
        events = client.get(f"/api/v1/runs/{rid}/events").json()["events"]
        assert any(event["event_type"] == "RUN_CREATED" and "菜单" in event["payload"]["input_text"] for event in events)
