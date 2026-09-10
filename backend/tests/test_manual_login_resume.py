"""Controlled protocol samples; the real QR login failure is retained under eval/plango-next."""

from fastapi.testclient import TestClient
from plango.app import create_app
from plango.outcomes import read_outcome
from plango.task import BrowserDecision, DeliveryDecision, TaskDecision
from test_browser_harness import TOKEN, fixture, settings, wait_for


def test_qr_login_pauses_before_page_models_and_resumes_original_run_after_restart(tmp_path):
    config = settings(tmp_path)
    app = create_app(config, token=TOKEN)

    calls = []
    async def classify_only(schema, **kwargs):
        calls.append(schema)
        assert schema in {TaskDecision, DeliveryDecision}, "A login page must not reach extraction or Vision"
        return TaskDecision(operation="read", browser=BrowserDecision(operation="extract"))

    app.state.runtime.model.structured = classify_only
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(app, headers=headers) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取当前网页菜单", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        wait_for(client, rid, lambda v: bool(v["state"].get("browser_wait")))
        assert calls == [TaskDecision], "Only the user's text is classified before page observation"
        old = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        login = {**fixture(old), "url": "https://account.dianping.com/pclogin", "title": "大众点评网", "text": "扫描二维码登录",
                 "tables": [], "fields": {"dom": {"manual_gate": "login", "forms": []}}}
        assert client.post(f"/api/v1/browser/commands/{old['command_id']}/result", json=login).status_code == 200
        paused = wait_for(client, rid, lambda v: (v["state"].get("browser_wait") or {}).get("error_kind") == "authentication_required")
        assert paused["phase"] == "REQUIREMENTS_READY"
        assert all(not item.get("data", {}).get("menu") for item in paused["state"].get("browser_artifacts", []))
        assert paused["state"].get("browser_steps", 0) == 0
        assert not paused["state"].get("action_results")
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []
        assert calls == [TaskDecision], "Login observation must pause without further model calls"

    restored_app = create_app(config, token=TOKEN)
    async def resumed_model(schema, **kwargs):
        assert schema in {TaskDecision, DeliveryDecision}, "Resume must preserve the already classified user turn"
        return TaskDecision(operation="answer", answer="已读取当前页面，登录状态与菜单内容待人工确认。")
    restored_app.state.runtime.model.structured = resumed_model
    with TestClient(restored_app, headers=headers) as client:
        resumed = client.post(f"/api/v1/runs/{rid}/resume", json={"decision": "resume", "interrupt_id": paused["interrupt_id"]})
        assert resumed.status_code == 202, resumed.text
        wait_for(client, rid, lambda v: (v["state"].get("browser_wait") or {}).get("command_id") not in {None, old["command_id"]})
        fresh = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        assert fresh["command_id"] != old["command_id"] and fresh["operation"] == "extract"
        assert client.post(f"/api/v1/browser/commands/{fresh['command_id']}/result", json=fixture(fresh)).status_code == 200
        done = wait_for(client, rid, lambda v: v["phase"] in {"SUCCEEDED", "FAILED", "PARTIAL_FAILED"})
        assert done["phase"] == "SUCCEEDED", done
        assert done["run_id"] == rid and not done["state"].get("action_results")
        assert done["state"]["execution_outcome"]["data"]["business_completed"] is False
        assert client.post(f"/api/v1/browser/commands/{old['command_id']}/result", json=login).json()["replayed"]
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []

    # Even a persisted old artifact cannot upgrade a login screen to a completed read.
    assert read_outcome({"execution_goal": {"kind": "page_read", "request": "读取网页"}, "browser_observation": login}).status == "needs_evidence"
