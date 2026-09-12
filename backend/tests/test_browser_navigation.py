"""Synthetic protocol integration: native navigation is distinct from unknown click handlers."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import BrowserDecision
from task_fixtures import browser_actor
from test_browser_harness import TOKEN, fixture, settings, wait_for

TARGET = "https://fixture.invalid/reservation-form"


def browser_driver(client, run_id):
    handled = set()

    def command(operation):
        current = wait_for(client, run_id, lambda v: (v["state"].get("browser_wait") or {}).get("command_id") not in handled | {None})
        value = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
        assert value["operation"] == operation, current
        handled.add(value["command_id"])
        return value

    def respond(value, **extra):
        payload = {**fixture(value), "snapshot_id": "snapshot-" + value["command_id"], **extra}
        result = client.post("/api/v1/browser/commands/" + value["command_id"] + "/result", json=payload)
        assert result.status_code == 200, result.text
        return payload

    def approve():
        paused = wait_for(client, run_id, lambda v: v["phase"] == "WAITING_APPROVAL")
        result = client.post("/api/v1/runs/" + run_id + "/resume", json={"decision": "approve", "interrupt_id": paused["interrupt_id"]})
        assert result.status_code == 202, result.text

    return command, respond, approve


def test_native_anchor_then_input_continues_and_keeps_both_durable_actions(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    decisions = iter([BrowserDecision(operation="click", idx=0), BrowserDecision(operation="type", idx=0, text="3"), BrowserDecision(operation="finish")])

    async def next_decision(schema, *, fallback, **kwargs):
        return next(decisions)

    app.state.runtime.model.structured = browser_actor(next_decision)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "打开网页菜单，填入人数3并读取报价", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command, respond, approve = browser_driver(client, run_id)
        respond(command("extract"), elements=[{"idx": 0, "tag": "a", "role": "link", "name": "菜单", "text": "菜单", "href": TARGET}])
        approve()
        navigation = command("click")
        acknowledgement = respond(navigation, ok=True, outcome="executed", url=TARGET, interaction_kind="navigation")
        respond(command("snapshot"), url=TARGET, elements=[{"idx": 0, "tag": "input", "role": "", "name": "人数", "text": ""}])
        approve()
        respond(command("type"), ok=True, outcome="executed", url=TARGET)
        respond(command("snapshot"), url=TARGET, text="人数3，当前报价50元，未提交预约")
        done = wait_for(client, run_id, lambda v: v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == "SUCCEEDED", done
        assert done["state"]["action_results"][0]["result"]["scope"] == "browser_interaction"
        with sqlite3.connect(tmp_path / "runs.sqlite") as database:
            rows = database.execute("SELECT status,result_json FROM agent_action WHERE run_id=?", (run_id,)).fetchall()
        assert len(rows) == 2
        assert all(status == "SUCCEEDED" and json.loads(result)["scope"] == "browser_interaction" for status, result in rows)
        replay = client.post("/api/v1/browser/commands/" + navigation["command_id"] + "/result", json=acknowledgement)
        assert replay.json()["replayed"]
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []


def test_stale_type_continues_reading_instead_of_closing_as_submit(tmp_path):
    """A type that never applied is not an unknown business receipt."""
    app = create_app(settings(tmp_path), token=TOKEN)
    decisions = iter([
        BrowserDecision(operation="type", idx=0, text="受控词"),
        BrowserDecision(operation="type", idx=0, text="受控词"),
        BrowserDecision(operation="finish"),
    ])

    async def next_decision(schema, *, fallback, **kwargs):
        return next(decisions)

    app.state.runtime.model.structured = browser_actor(next_decision)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "在当前页输入受控词并读取结果", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command, respond, approve = browser_driver(client, run_id)
        respond(command("extract"), elements=[{"idx": 0, "tag": "input", "role": "", "name": "关键词", "text": "", "input_type": "text"}])
        approve()
        typed = command("type")
        respond(typed, ok=False, outcome="blocked", error_kind="stale_snapshot")
        refreshed = command("extract")
        respond(refreshed, elements=[{"idx": 0, "tag": "input", "role": "", "name": "关键词", "text": "", "input_type": "text"}])
        paused = wait_for(client, run_id, lambda v: v["phase"] in {"WAITING_APPROVAL", "SUCCEEDED", "PARTIAL_FAILED", "FAILED", "RESEARCHING"})
        assert paused["phase"] != "PARTIAL_FAILED", paused
        assert paused["phase"] in {"WAITING_APPROVAL", "RESEARCHING", "REQUIREMENTS_READY"}
        assert (paused["state"].get("reason") or "").startswith("页面操作未执行") or paused["phase"] == "WAITING_APPROVAL"


@pytest.mark.parametrize("tag,url,outcome", [("button", TARGET, "executed"), ("a", TARGET + "?redirected", "executed"), ("a", TARGET, "unknown")])
def test_unverified_or_unknown_click_cannot_gain_navigation_privileges(tmp_path, tag, url, outcome):
    app = create_app(settings(tmp_path), token=TOKEN)
    decisions = iter([BrowserDecision(operation="click", idx=0), BrowserDecision(operation="finish")])

    async def next_decision(schema, *, fallback, **kwargs):
        return next(decisions)

    app.state.runtime.model.structured = browser_actor(next_decision)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "打开网页入口并读取", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command, respond, approve = browser_driver(client, run_id)
        respond(command("extract"), elements=[{"idx": 0, "tag": tag, "role": "button", "name": "下一步", "text": "下一步", "href": TARGET}])
        approve()
        click = command("click")
        acknowledgement = respond(click, ok=outcome == "executed", outcome=outcome, url=url, interaction_kind="navigation")
        respond(command("snapshot"), url=url, text="页面显示成功，实际业务身份未核验")
        done = wait_for(client, run_id, lambda v: v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == "SUCCEEDED", done
        assert done["state"]["action_results"][0]["status"] != "UNKNOWN"
        with sqlite3.connect(tmp_path / "runs.sqlite") as database:
            rows = database.execute("SELECT tool_name,status,result_json FROM agent_action WHERE run_id=?", (run_id,)).fetchall()
        clicks = [row for row in rows if row[0] == "click"]
        assert clicks and all(status == "FAILED" and json.loads(result).get("scope") != "browser_interaction" for _, status, result in clicks)
        assert client.post("/api/v1/browser/commands/" + click["command_id"] + "/result", json=acknowledgement).json()["replayed"]
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []
        follow = client.post("/api/v1/runs/" + run_id + "/messages", json={"text": "继续读取当前页"})
        assert follow.status_code == 202, follow.text


def test_unreadable_receipt_snapshot_retries_extract_instead_of_locking(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    decisions = iter([BrowserDecision(operation="click", idx=0), BrowserDecision(operation="finish")])

    async def next_decision(schema, *, fallback, **kwargs):
        return next(decisions)

    app.state.runtime.model.structured = browser_actor(next_decision)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "打开网页入口并读取", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command, respond, approve = browser_driver(client, run_id)
        respond(command("extract"), elements=[{"idx": 0, "tag": "button", "role": "button", "name": "搜索", "text": "搜索"}])
        approve()
        click = command("click")
        respond(click, ok=True, outcome="executed", url=TARGET, interaction_kind="navigation")
        respond(command("snapshot"), ok=False, outcome="failed", error_kind="browser_not_visible", text="")
        respond(command("extract"), url=TARGET, text="搜索结果列表，尚未提交业务")
        done = wait_for(client, run_id, lambda v: v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == "SUCCEEDED", done
        assert done["state"]["action_results"][0]["status"] != "UNKNOWN"
        with sqlite3.connect(tmp_path / "runs.sqlite") as database:
            rows = database.execute("SELECT status,result_json FROM agent_action WHERE run_id=?", (run_id,)).fetchall()
        assert rows and all(status == "FAILED" for status, _ in rows)
        assert client.post("/api/v1/runs/" + run_id + "/messages", json={"text": "继续读取当前页"}).status_code == 202
