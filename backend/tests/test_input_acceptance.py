"""Controlled HTTP loss/retry fixtures; isolated SQLite, no model or merchant calls."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.browser import bindings, commands
from plango_harness.persistence.database import run_event
from plango_harness.persistence.runs import InputAcceptance
from sqlalchemy import update
from test_browser_harness import TOKEN, settings
from test_image_reuse import IMAGE
from test_selected_poi_refresh import canonical_place


def app_without_worker(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    app.state.runtime.embedded_worker = False
    return app


def finish_turn(client, run_id):
    async def finish():
        runtime = client.app.state.runtime
        row = await runtime.runs.get(run_id)
        state = {**row["state_json"], "phase": "SUCCEEDED", "pending_message": None,
                 "outcome": "SUCCEEDED", "model_token_count": 120, "tool_call_count": 2}
        await runtime.runs.save_state_and_events(state, expected_version=row["version"], clear_pending_command=True, events=[])
    client.portal.call(finish)


def test_create_response_loss_retry_keeps_one_run(tmp_path):
    app = app_without_worker(tmp_path)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        body = {"request_id": "create-loss", "input_text": "读取网页", "browser_session_id": "fixture"}
        accepted = client.post("/api/v1/runs", json=body).json()  # Simulate discarded response.
        retry = client.post("/api/v1/runs", json=body).json()
        assert retry["run_id"] == accepted["run_id"]
        assert accepted["replayed"] is False and retry["replayed"] is True
        assert client.get("/api/v1/requests/create-loss").json() == {key: accepted[key] for key in ("accepted", "request_id", "request_fingerprint", "run_id", "event_seq")}
        assert len(client.get("/api/v1/runs").json()["runs"]) == 1
        assert client.get("/api/v1/requests/not-sent").status_code == 404
        assert client.get("/api/v1/requests/create-loss", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post("/api/v1/runs", json={**body, "input_text": "别的内容"}).status_code == 409
    with TestClient(app_without_worker(tmp_path), headers={"Authorization": "Bearer " + TOKEN}) as client:
        assert client.post("/api/v1/runs", json=body).json() == retry
        independent = client.post("/api/v1/runs", json={**body, "request_id": "separate-turn"}).json()
        assert independent["run_id"] != accepted["run_id"]


def test_message_response_loss_retry_after_completion_keeps_turn_and_budget(tmp_path):
    app = app_without_worker(tmp_path)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取网页", "browser_session_id": "fixture"}).json()["run_id"]
        finish_turn(client, rid)
        body = {"request_id": "message-loss", "text": "人数改为3人"}
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).status_code == 202
        finish_turn(client, rid)
        before = client.portal.call(app.state.runtime.runs.get, rid)
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).status_code == 202
        after = client.portal.call(app.state.runtime.runs.get, rid)
        assert after == before
        assert client.post(f"/api/v1/runs/{rid}/messages", json={**body, "text": "冲突"}).status_code == 409
        independent = client.post(f"/api/v1/runs/{rid}/messages", json={**body, "request_id": "message-next-turn"})
        assert independent.status_code == 202, independent.text
        current = client.portal.call(app.state.runtime.runs.get, rid)
        assert current["state_json"]["turn_id"] == before["state_json"]["turn_id"] + 1
        assert current["state_json"]["turn_budget"]["id"] != before["state_json"]["turn_budget"]["id"]
        assert current["state_json"]["model_token_count"] == before["state_json"]["model_token_count"]
        assert client.post(f"/api/v1/runs/{rid}/messages", json={**body, "request_id": "while-busy"}).status_code == 409
        assert client.get("/api/v1/requests/while-busy").status_code == 404


def test_lost_conflict_response_lookup_identifies_the_original_payload(tmp_path):
    app = app_without_worker(tmp_path)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        original = {"request_id": "fingerprint-create", "request_fingerprint": "a" * 64,
                    "input_text": "原始内容", "browser_session_id": "fixture"}
        first = client.post("/api/v1/runs", json=original).json()
        assert first["request_fingerprint"] == original["request_fingerprint"]
        conflict = {**original, "input_text": "不同内容", "request_fingerprint": "b" * 64}
        assert client.post("/api/v1/runs", json=conflict).status_code == 409  # Discard this conflict response.
        receipt = client.get("/api/v1/requests/fingerprint-create").json()
        assert receipt["run_id"] == first["run_id"]
        assert receipt["request_fingerprint"] == original["request_fingerprint"] != conflict["request_fingerprint"]
        assert client.post("/api/v1/runs", json={**original, "request_fingerprint": "c" * 64}).status_code == 409
        assert client.post("/api/v1/runs", json={**original, "request_fingerprint": "invalid"}).status_code == 422
        rid = first["run_id"]
        finish_turn(client, rid)
        message = {"request_id": "fingerprint-message", "request_fingerprint": "d" * 64, "text": "3人"}
        assert client.post(f"/api/v1/runs/{rid}/messages", json=message).json()["request_fingerprint"] == message["request_fingerprint"]
        assert client.post(f"/api/v1/runs/{rid}/messages", json={**message, "text": "2人", "request_fingerprint": "e" * 64}).status_code == 409
        assert client.get("/api/v1/requests/fingerprint-message").json()["request_fingerprint"] == message["request_fingerprint"]
    with TestClient(app_without_worker(tmp_path), headers={"Authorization": "Bearer " + TOKEN}) as client:
        assert client.get("/api/v1/requests/fingerprint-create").json() == receipt
        assert client.post("/api/v1/runs", json=original).json()["replayed"] is True


def test_concurrent_creation_accepts_once_and_queue_failure_keeps_receipt(tmp_path):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        runtime.queue.enqueue = AsyncMock(side_effect=RuntimeError("synthetic queue unavailable"))
        body = {"request_id": "concurrent", "input_text": "读取网页", "browser_session_id": "fixture"}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: client.post("/api/v1/runs", json=body), range(4)))
        assert all(response.status_code == 202 for response in results), [response.text for response in results]
        accepted = [response.json() for response in results]
        assert len({item["run_id"] for item in accepted}) == 1
        assert sum(not item["replayed"] for item in accepted) == 1
        assert runtime.queue.enqueue.await_count == 1
        rid = accepted[0]["run_id"]
        assert rid in client.portal.call(runtime.runs.pending_work)


def test_image_and_selected_store_retry_never_overwrites_later_context(tmp_path):
    app = create_app(settings(tmp_path).model_copy(update={"openai_api_key": "synthetic-no-model-called"}), token=TOKEN)
    runtime = app.state.runtime
    runtime.embedded_worker = False
    runtime.world_service.provider.amap.get_place = AsyncMock(return_value=canonical_place())
    body = {"request_id": "store-image", "input_text": "读取图片并选店", "browser_session_id": "fixture", "image": IMAGE,
            "location_context": {"city": "重庆", "source": "manual"},
            "selected_poi": {"poi_id": "fixture", "name": "雾岚餐厅", "longitude": 106.57, "latitude": 29.56}}
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json=body).json()["run_id"]
        original = client.portal.call(runtime.runs.get, rid)
        finish_turn(client, rid)
        message = {"request_id": "image-message", "text": "读取新图片", "image": IMAGE,
                   "location_context": {"city": "成都", "source": "manual"}}
        assert client.post(f"/api/v1/runs/{rid}/messages", json=message).status_code == 202
        finish_turn(client, rid)
        async def later_context():
            async with runtime.database.session() as session:
                async with session.begin():
                    await session.execute(update(bindings).where(bindings.c.run_id == rid).values(input_image=None, location_context={"city": "北京", "source": "manual"}))
        client.portal.call(later_context)
        before = client.portal.call(runtime.bridge.binding, rid)
        runtime.settings.openai_api_key = ""  # A later configuration cannot undo accepted input.
        assert client.post("/api/v1/runs", json=body).json()["replayed"]
        assert client.post(f"/api/v1/runs/{rid}/messages", json=message).json()["replayed"]
        assert client.portal.call(runtime.bridge.binding, rid) == before
        assert client.portal.call(runtime.runs.get, rid)["state_json"]["selected_poi"] == original["state_json"]["selected_poi"]
        runtime.world_service.provider.amap.get_place.assert_awaited_once()
        assert client.post("/api/v1/runs", json={**body, "image": None}).status_code == 409
        assert client.post(f"/api/v1/runs/{rid}/messages", json={**message, "location_context": {"city": "上海", "source": "manual"}}).status_code == 409


def test_transaction_failure_rolls_back_receipt_binding_run_event_and_budget(tmp_path):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取网页", "browser_session_id": "fixture"}).json()["run_id"]
        finish_turn(client, rid)
        before = client.portal.call(runtime.runs.get, rid)
        binding = client.portal.call(runtime.bridge.binding, rid)
        async def reject():
            # Duplicate audit primary key fails after receipt and binding statements.
            acceptance = InputAcceptance("rolled-back", "fixture-hash", [
                update(bindings).where(bindings.c.run_id == rid).values(input_image="not committed"),
                run_event.insert().values(run_id=rid, seq=1, event_type="fixture", phase="CREATED", payload_json={}, created_at=before["created_at"]),
            ])
            with pytest.raises(Exception, match="UNIQUE constraint failed"):
                await runtime.replan(rid, "人数改3人", acceptance=acceptance)
        client.portal.call(reject)
        assert client.portal.call(runtime.runs.get, rid) == before
        assert client.portal.call(runtime.bridge.binding, rid) == binding
        assert client.get("/api/v1/requests/rolled-back").status_code == 404


@pytest.mark.parametrize("browser_wait", [False, True])
def test_paused_input_replay_keeps_command_and_browser_generation(tmp_path, browser_wait):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取网页", "browser_session_id": "fixture"}).json()["run_id"]
        async def pause():
            row = await runtime.runs.get(rid)
            state = {**row["state_json"], "phase": "REQUIREMENTS_READY", "clarification": {"question": "请确认"}, "interrupt_id": "fixture-pause"}
            if browser_wait:
                state["browser_wait"] = {"command_id": "read-command"}
                async with runtime.database.session() as session:
                    async with session.begin():
                        await session.execute(commands.insert().values(command_id="read-command", run_id=rid, browser_session_id="fixture", payload={"operation": "extract"}, created_at=1.0))
            await runtime.runs.save_state_and_events(state, expected_version=row["version"], events=[])
        client.portal.call(pause)
        body = {"request_id": "paused-input", "text": "继续" if browser_wait else "3人"}
        first = client.post(f"/api/v1/runs/{rid}/messages", json=body)
        assert first.status_code == 202, first.text
        row = client.portal.call(runtime.runs.get, rid)
        binding = client.portal.call(runtime.bridge.binding, rid)
        assert row["pending_command"]
        assert binding["generation"] == int(browser_wait)
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).json()["replayed"]
        assert client.portal.call(runtime.runs.get, rid) == row
        assert client.portal.call(runtime.bridge.binding, rid) == binding
        assert client.post(f"/api/v1/runs/{rid}/messages", json={**body, "request_id": "new-while-pending"}).status_code == 409
        assert client.get("/api/v1/requests/new-while-pending").status_code == 404
        finish_turn(client, rid)
        finished = client.portal.call(runtime.runs.get, rid)
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).json()["replayed"]
        assert client.portal.call(runtime.runs.get, rid) == finished
        assert client.portal.call(runtime.bridge.binding, rid) == binding


def test_unknown_action_and_accepted_input_are_immutable_on_message_retry(tmp_path):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取网页", "browser_session_id": "fixture"}).json()["run_id"]
        finish_turn(client, rid)
        body = {"request_id": "before-unknown", "text": "3人"}
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).status_code == 202
        async def unknown():
            row = await runtime.runs.get(rid)
            state = {**row["state_json"], "phase": "PARTIAL_FAILED", "action_results": [{"action_id": "unknown-write", "status": "UNKNOWN"}]}
            await runtime.runs.save_state_and_events(state, expected_version=row["version"], events=[])
        client.portal.call(unknown)
        before = client.portal.call(runtime.runs.get, rid)
        binding = client.portal.call(runtime.bridge.binding, rid)
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).json()["replayed"]
        assert client.post(f"/api/v1/runs/{rid}/messages", json={"request_id": "after-unknown", "text": "继续", "location_context": {"city": "北京", "source": "manual"}}).status_code == 409
        assert client.portal.call(runtime.runs.get, rid) == before
        assert client.portal.call(runtime.bridge.binding, rid) == binding
        assert client.get("/api/v1/requests/after-unknown").status_code == 404


def test_acceptance_upgrade_preserves_existing_database_rows(tmp_path):
    config = Config("alembic.ini")
    database = tmp_path / "legacy.sqlite"
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database}"
    command.upgrade(config, "0012_plango_rename")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO plango_browser_binding (run_id,browser_session_id,generation) VALUES ('old-run','kept-session',7)")
        connection.execute("INSERT INTO plango_browser_command (command_id,run_id,browser_session_id,payload,result,created_at) VALUES ('old-command','old-run','kept-session','{}','{\"status\":\"UNKNOWN\"}',1)")
        before = {table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in ("plango_browser_binding", "plango_browser_command")}
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert all(connection.execute(f"SELECT * FROM {table}").fetchall() == rows for table, rows in before.items())
        assert connection.execute("SELECT count(*) FROM input_acceptance").fetchone() == (0,)
