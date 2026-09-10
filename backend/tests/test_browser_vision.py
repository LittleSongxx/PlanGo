"""Controlled PNG/protocol inputs only; no screenshot model calls or business writes."""

import base64
import copy
import json
import sqlite3
import struct
import time
import zlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.browser import BrowserScreenshot, run_context
from plango.graph import BrowserDecision, VisualReading, vision_blocked, vision_reason_supported
from plango.outcomes import current_visual_observation
from plango.task import TaskDecision
from test_browser_harness import TOKEN, fixture, settings, wait_for
from test_browser_navigation import browser_driver


def native_png(width=32, height=16):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress((b"\0" + b"\x80\x80\x80" * width) * height)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def capture_metadata():
    return {
        "screenshot_id": "screenshot-fixture", "snapshot_id": "dom-snapshot", "url": "https://fixture.invalid/menu",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "viewport": {"width": 32, "height": 16}, "image": {"width": 32, "height": 16},
        "dpr": 1, "zoom": 1, "clip": {"x": 0, "y": 0, "width": 32, "height": 16},
        "scroll": {"x": 0, "y": 0}, "page_version": "document:1", "data_url": native_png(),
    }


def binding():
    before = {"snapshot_id": "dom-snapshot", "url": "https://fixture.invalid/menu", "tab_id": "fixture-tab", "page_version": "document:1"}
    observation = {**before, "command_id": "capture-command", "ok": True, "outcome": "observed"}
    command = {"operation": "screenshot", "expected_snapshot_id": "dom-snapshot", "tab_id": "fixture-tab", "command_id": "capture-command"}
    return before, observation, command


@pytest.mark.parametrize("field,value", [
    ("captured_at", "2026-09-09T00:00:00"), ("dpr", float("nan")),
    ("viewport", {"width": "32", "height": 16}), ("clip", {"x": 1, "y": 0, "width": 32, "height": 16}),
    ("image", {"width": 100, "height": 50}), ("data_url", "https://other.invalid/image.png"),
    ("data_url", "data:image/png;base64,!invalid"), ("data_url", "data:image/png;base64," + "A" * 10_666_700),
])
def test_screenshot_rejects_invalid_geometry_payload_and_naive_time(field, value):
    payload = capture_metadata()
    payload[field] = value
    with pytest.raises(ValueError):
        BrowserScreenshot.model_validate(payload)


@pytest.mark.parametrize("changed", ["url", "tab_id", "snapshot_id", "page_version", "command_id", "past", "future", "before_command"])
def test_capture_is_bound_to_current_page_command_and_time(changed):
    payload = capture_metadata()
    before, observation, command = binding()
    created = 0.0
    if changed in observation:
        observation[changed] = "other-value"
    elif changed in {"past", "future"}:
        payload["captured_at"] = (datetime.now(timezone.utc) + timedelta(seconds=-31 if changed == "past" else 5)).isoformat()
    else:
        created = time.time() + 5
    with pytest.raises(ValueError):
        BrowserScreenshot.model_validate(payload).check_binding(observation, command, before, created_at=created)


def test_png_dimensions_and_zoom_aware_dpr_are_checked_without_double_scaling():
    payload = capture_metadata()
    payload.update(dpr=2, zoom=1.25, image={"width": 64, "height": 32})
    with pytest.raises(ValueError, match="image_dimensions_mismatch"):
        BrowserScreenshot.model_validate(payload)
    payload["data_url"] = native_png(64, 32)
    screenshot = BrowserScreenshot.model_validate(payload)
    before, observation, command = binding()
    screenshot.check_binding(observation, command, before)


def test_vision_policy_requires_dom_evidence_and_keeps_denial_unknown_login_boundaries():
    before, observation, _ = binding()
    state = {"phase": "RESEARCHING", "browser_steps": 1, "browser_observation": {**observation, "text": "", "elements": []}, "browser_task_context": {"kind": "extract"}}
    assert vision_blocked(state) is None
    assert vision_reason_supported(state, "no_semantic_target")
    for change in ({"approval_decision": "reject"}, {"action_proposal": {"expires_at": "2000-01-01T00:00:00Z"}},
                   {"action_results": [{"status": "UNKNOWN"}]}, {"browser_receipt_pending": True}, {"phase": "CANCELLED"}):
        assert vision_blocked({**state, **change})
    for kind in ("login_required", "captcha_required", "stale_snapshot", "approval_expired"):
        assert vision_blocked({**state, "browser_observation": {**observation, "error_kind": kind}})
    assert not vision_reason_supported({**state, "browser_steps": 0}, "no_semantic_target")
    assert not vision_reason_supported({**state, "browser_observation": {**observation, "text": "可读菜单"}}, "no_semantic_target")
    assert vision_reason_supported({**state, "browser_observation": {**observation, "fields": {"dom": {"canvas_count": 1}}}}, "canvas")
    assert vision_reason_supported({**state, "browser_observation": {**observation, "elements": [{"name": "同名"}, {"name": "同名"}]}}, "ambiguous_target")


@pytest.mark.parametrize("enabled,unknown,reading_status", [(False, False, "observed"), (True, True, "observed"), (True, False, "observed"), (True, False, "manual"), (True, False, "unsupported"), (True, False, "repeat")])
def test_graph_vision_is_opt_in_once_readonly_and_checks_durable_unknown(tmp_path, enabled, unknown, reading_status):
    config = settings(tmp_path).model_copy(update={"browser_vision_enabled": enabled, "openai_api_key": "offline-fixture-replaced"})
    app = create_app(config, token=TOKEN)
    calls = []

    repeated_turns = set()
    unknown_recorded = False
    vision_errors = []

    async def model(schema, *, fallback, **kwargs):
        nonlocal unknown_recorded
        calls.append(schema)
        if schema is TaskDecision:
            context = json.loads(kwargs["user"])
            if context.get("browser_steps", 0) == 0:
                return TaskDecision(operation="read")
            errors = [item for item in context.get("tool_results", []) if item.get("tool") == "vision" and not item.get("ok")]
            if errors:
                vision_errors.extend(errors)
                return TaskDecision(operation="answer", answer="已保留当前只读资料；无法继续截图的部分仍待确认。",
                                    answer_status="partial" if not enabled or unknown else "complete")
            if unknown and not unknown_recorded:
                await app.state.runtime.ledger.reserve(
                    action_id="unknown-action", run_id=run_context.get()["run_id"], plan_id="fixture-plan", plan_version=1,
                    tool_name="click", idempotency_key="unknown-action", request_hash="f" * 64, arguments={}, status="UNKNOWN",
                )
                unknown_recorded = True
            if not context["vision_used_this_turn"]:
                return TaskDecision(operation="read", browser=BrowserDecision(operation="snapshot", vision_reason="canvas"))
            if reading_status == "repeat" and context["turn_id"] not in repeated_turns:
                repeated_turns.add(context["turn_id"])
                return TaskDecision(operation="read", browser=BrowserDecision(operation="snapshot", vision_reason="canvas"))
            return TaskDecision(operation="answer", answer="已读取当前页面，图像内容保留为只读观察。")
        if schema is VisualReading:
            assert kwargs["image"].startswith("data:image/png;base64,")
            return VisualReading(status="observed" if reading_status == "repeat" else reading_status, text="测试截图上的图形菜单，业务状态未核验")
        return fallback

    app.state.runtime.model.structured = model
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "读取当前网页内容", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command, respond, _ = browser_driver(client, run_id)
        respond(command("extract"), snapshot_id="dom-snapshot", page_version="document:1", text="", tables=[], elements=[], fields={"dom": {"canvas_count": 1}})
        if enabled and not unknown:
            capture = command("screenshot")
            assert capture["expected_snapshot_id"] == "dom-snapshot"
            payload = capture_metadata()
            raw = {**fixture(capture), "snapshot_id": "dom-snapshot", "page_version": "document:1", "screenshot": payload, "text": "", "tables": []}
            bad = copy.deepcopy(raw)
            bad["screenshot"]["snapshot_id"] = "another-page"
            assert client.post("/api/v1/browser/commands/" + capture["command_id"] + "/result", json=bad).status_code == 409
            response = client.post("/api/v1/browser/commands/" + capture["command_id"] + "/result", json=raw)
            assert response.status_code == 200, response.text
        done = wait_for(client, run_id, lambda v: v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == ("SUCCEEDED" if enabled and not unknown and reading_status in {"observed", "repeat"} else "PARTIAL_FAILED"), done
        visuals = [a for a in done["state"].get("browser_artifacts", []) if a["type"] == "browser_visual"]
        assert len(visuals) == int(enabled and not unknown and reading_status in {"observed", "repeat"})
        assert calls.count(VisualReading) == int(enabled and not unknown)
        if reading_status == "repeat":
            assert vision_errors, "The same owner receives the spent-capture error before answering."
        with sqlite3.connect(tmp_path / "runs.sqlite") as database:
            rows = database.execute("SELECT payload FROM plango_browser_command WHERE run_id=?", (run_id,)).fetchall()
            assert sum(json.loads(row[0])["operation"] == "screenshot" for row in rows) == int(enabled and not unknown)
            actions = database.execute("SELECT status FROM agent_action WHERE run_id=?", (run_id,)).fetchall()
            assert actions == ([("UNKNOWN",)] if unknown else [])
        if visuals:
            assert visuals[0]["data"]["scope"] == "visual_observation"
            assert "data_url" not in visuals[0]["data"]["screenshot"]
            assert client.post("/api/v1/browser/commands/" + capture["command_id"] + "/result", json=raw).json()["replayed"]
            assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []
        if enabled and not unknown and reading_status == "observed":
            # A user-authored next turn may capture again; the first turn itself captured once.
            response = client.post("/api/v1/runs/" + run_id + "/messages", json={"text": "继续读取当前网页内容"})
            assert response.status_code == 202, response.text
            respond(command("extract"), snapshot_id="dom-snapshot-2", page_version="document:2", text="", tables=[], elements=[])
            second = command("screenshot")
            assert second["command_id"] != capture["command_id"]
            second_meta = {**capture_metadata(), "screenshot_id": "second-capture", "snapshot_id": "dom-snapshot-2", "page_version": "document:2"}
            respond(second, snapshot_id="dom-snapshot-2", page_version="document:2", screenshot=second_meta, text="", tables=[])
            done = wait_for(client, run_id, lambda v: v["state"].get("turn_id") == 2 and v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
            assert done["phase"] == "SUCCEEDED", done
            assert calls.count(VisualReading) == 2
            with sqlite3.connect(tmp_path / "runs.sqlite") as database:
                captures = [json.loads(row[0]) for row in database.execute("SELECT payload FROM plango_browser_command WHERE run_id=?", (run_id,)) if json.loads(row[0])["operation"] == "screenshot"]
            assert [item["_turn_id"] for item in captures] == [1, 2]


@pytest.mark.parametrize("extra", [
    {"elements": [{"tag": "input", "input_type": "password", "name": "密码"}]},
    {"elements": [{"tag": "input", "input_type": "text", "name": "验证码"}]},
    {"title": "安全验证"}, {"text": "请完成安全验证后继续访问"},
    {"fields": {"dom": {"manual_gate": "captcha"}}},
])
def test_actual_login_or_challenge_dom_cannot_trigger_vision(extra):
    _, observation, _ = binding()
    assert vision_blocked({"phase": "RESEARCHING", "browser_observation": {**observation, **extra}})


def test_footer_login_link_does_not_block_menu_vision():
    _, observation, _ = binding()
    state = {"phase": "RESEARCHING", "browser_observation": {**observation, "title": "餐厅菜单", "text": "菜单信息。页脚：登录/注册", "elements": [{"tag": "a", "name": "登录", "href": "https://fixture.invalid/login"}]}}
    assert vision_blocked(state) is None


def test_rejected_browser_approval_does_not_enter_visual_fallback(tmp_path):
    config = settings(tmp_path).model_copy(update={"browser_vision_enabled": True, "openai_api_key": "offline-fixture-replaced"})
    app = create_app(config, token=TOKEN)
    calls = []

    async def model(schema, *, fallback, **kwargs):
        calls.append(schema)
        if schema is TaskDecision:
            if not json.loads(kwargs["user"])["browser_steps"]:
                return TaskDecision(operation="read")
            return TaskDecision(operation="read", browser=BrowserDecision(operation="click", idx=0))
        return fallback

    app.state.runtime.model.structured = model
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "打开网页入口", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command, respond, _ = browser_driver(client, run_id)
        respond(command("extract"), page_version="document:1", elements=[{"idx": 0, "tag": "button", "name": "入口"}], fields={"dom": {"canvas_count": 1}})
        paused = wait_for(client, run_id, lambda v: v["phase"] == "WAITING_APPROVAL")
        response = client.post("/api/v1/runs/" + run_id + "/resume", json={"decision": "reject", "interrupt_id": paused["interrupt_id"]})
        assert response.status_code == 202
        assert wait_for(client, run_id, lambda v: v["phase"] == "CANCELLED")
        assert VisualReading not in calls
        with sqlite3.connect(tmp_path / "runs.sqlite") as database:
            assert all(json.loads(row[0])["operation"] == "extract" for row in database.execute("SELECT payload FROM plango_browser_command WHERE run_id=?", (run_id,)))
            assert database.execute("SELECT count(*) FROM agent_action WHERE run_id=?", (run_id,)).fetchone()[0] == 0


def test_spent_vision_counter_alone_or_foreign_evidence_does_not_complete_a_read():
    before, observation, _ = binding()
    state = {"turn_id": 3, "browser_vision_turn": 3, "browser_observation": observation, "browser_artifacts": []}
    assert current_visual_observation(state) is None
    artifact = {"type": "browser_visual", "source": "browser", "turn_id": 3, "url": before["url"],
                "snapshot_id": before["snapshot_id"], "observed_at": datetime.now(timezone.utc).isoformat(),
                "data": {"scope": "visual_observation", "visual_text": "实际图像观测内容", "screenshot": capture_metadata()}}
    state["browser_artifacts"] = [artifact]
    assert current_visual_observation(state) is artifact
    for field, value in (("url", "https://other.invalid"), ("snapshot_id", "old-snapshot"), ("turn_id", 2), ("source", "unknown")):
        changed = {**artifact, field: value}
        assert current_visual_observation({**state, "browser_artifacts": [changed]}) is None
    for blocked in ({"action_results": [{"status": "UNKNOWN"}]}, {"approval_decision": "reject"}, {"action_proposal": {"expires_at": "2000-01-01T00:00:00Z"}}):
        assert vision_blocked({**state, **blocked}) is not None
