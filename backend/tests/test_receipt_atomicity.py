"""API-level fault injection with a real local database, supplementing desktop E2E recovery."""
import copy
import time
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango.browser import BrowserScreenshot, commands
from plango.graph import BrowserDecision, VisualReading
from plango_harness.persistence.database import run_event
from sqlalchemy import insert, select
from task_fixtures import browser_actor
from test_browser_harness import TOKEN, create_app, fixture, settings, wait_for
from test_browser_navigation import browser_driver
from test_browser_vision import binding, capture_metadata


def test_receipt_and_event_rollback_together_then_replay_once(tmp_path):
    app = create_app(settings(str(tmp_path)), token=TOKEN)
    runtime = app.state.runtime
    original = runtime.runs.append_event_in_transaction
    fail = True

    async def append(session, **kwargs):
        if fail and kwargs['event_type'] == 'BROWSER_OBSERVATION':
            raise RuntimeError('controlled interruption before audit append')
        return await original(session, **kwargs)

    runtime.runs.append_event_in_transaction = append
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        run_id = client.post('/api/v1/runs', json={'input_text': '读取当前网页', 'browser_session_id': 'fixture-desktop'}).json()['run_id']
        wait_for(client, run_id, lambda row: bool(row['state'].get('browser_wait')))
        command = client.get('/api/v1/browser/commands?browser_session_id=fixture-desktop').json()['commands'][0]
        url = '/api/v1/browser/commands/' + command['command_id'] + '/result'
        body = fixture(command)
        with pytest.raises(RuntimeError, match='controlled interruption'):
            client.post(url, json=body)
        assert client.portal.call(runtime.bridge.get, command['command_id'])['result'] is None
        assert not any(e['event_type'] == 'BROWSER_OBSERVATION' for e in client.get(f'/api/v1/runs/{run_id}/events').json()['events'])
        fail = False
        assert client.post(url, json=body).json()['accepted']
        assert client.post(url, json=body).json()['replayed']
        assert client.post(url, json={**body, 'text': 'different result'}).status_code == 409

        async def count():
            async with runtime.database.session() as session:
                return len((await session.execute(select(run_event.c.seq).where(run_event.c.run_id == run_id,
                    run_event.c.event_type == 'BROWSER_OBSERVATION',
                    run_event.c.payload_json['command_id'].as_string() == command['command_id']))).all())
        assert client.portal.call(count) == 1


def test_saved_screenshot_receipt_replays_after_31_seconds(tmp_path, monkeypatch):
    app = create_app(settings(str(tmp_path)), token=TOKEN)
    runtime = app.state.runtime
    runtime.resume_browser = AsyncMock()
    before, body, command = binding()
    body.update(browser_session_id="fixture", screenshot=capture_metadata())

    async def setup():
        await runtime.runs.create_with_event("screenshot-replay", "desktop", "controlled screenshot receipt")
        await runtime.bridge.bind("screenshot-replay", "fixture")
        async with runtime.database.session() as session:
            async with session.begin():
                await session.execute(insert(commands).values(command_id=command["command_id"], run_id="screenshot-replay", browser_session_id="fixture", payload={**command, "_expected_page": before}, created_at=time.time()))

    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        client.portal.call(setup)
        url = "/api/v1/browser/commands/" + command["command_id"] + "/result"
        assert client.post(url, json=body).status_code == 200
        original = BrowserScreenshot.check_binding

        def aged(self, *args, **kwargs):
            return original(self, *args, **kwargs, now=datetime.fromisoformat(body["screenshot"]["captured_at"]).timestamp() + 31)

        monkeypatch.setattr(BrowserScreenshot, "check_binding", aged)
        response = client.post(url, json=body)
        assert response.status_code == 200 and response.json()["replayed"], response.text
        assert client.post(url, json={**body, "text": "changed immutable receipt"}).status_code == 409


def test_late_first_capture_is_acknowledged_but_graph_does_not_use_or_recapture_it(tmp_path, monkeypatch):
    config = settings(str(tmp_path)).model_copy(update={"browser_vision_enabled": True, "openai_api_key": "replaced-local-model"})
    app = create_app(config, token=TOKEN)
    called = []

    async def model(schema, *, fallback, **kwargs):
        called.append(schema)
        return BrowserDecision(operation="snapshot", vision_reason="canvas") if schema is BrowserDecision else fallback

    app.state.runtime.model.structured = browser_actor(model)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "读取当前网页内容", "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command, respond, _ = browser_driver(client, run_id)
        respond(command("extract"), snapshot_id="dom-snapshot", page_version="document:1", text="", tables=[], elements=[], fields={"dom": {"canvas_count": 1}})
        capture = command("screenshot")
        body = {**fixture(capture), "snapshot_id": "dom-snapshot", "page_version": "document:1", "screenshot": capture_metadata(), "text": "", "tables": []}
        original = BrowserScreenshot.check_binding
        received_at = datetime.fromisoformat(body["screenshot"]["captured_at"]).timestamp() + 31

        def late(self, *args, **kwargs):
            return original(self, *args, **kwargs, now=received_at)

        monkeypatch.setattr(BrowserScreenshot, "check_binding", late)
        url = "/api/v1/browser/commands/" + capture["command_id"] + "/result"
        for field, value in (("snapshot_id", "wrong-snapshot"), ("page_version", "wrong-page"),
                             ("captured_at", datetime.fromtimestamp(received_at + 5).astimezone().isoformat()),
                             ("captured_at", datetime.fromtimestamp(received_at - 100).astimezone().isoformat())):
            bad = copy.deepcopy(body)
            bad["screenshot"][field] = value
            assert client.post(url, json=bad).status_code == 409
        bad = copy.deepcopy(body)
        bad["screenshot"]["data_url"] = "data:image/png;base64,AAAA"
        assert client.post(url, json=bad).status_code == 422
        response = client.post(url, json=body)
        assert response.status_code == 200, response.text
        assert client.post(url, json=body).json()["replayed"]
        done = wait_for(client, run_id, lambda v: bool(v.get("outcome")))
        assert done["phase"] == "PARTIAL_FAILED" and "截图已过期" in done["state"]["reason"]
        assert VisualReading not in called
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []
        rows = client.portal.call(app.state.runtime.bridge.observations, run_id)
        assert sum(row["payload"]["operation"] == "screenshot" for row in rows) == 1
        saved = next(row["result"] for row in rows if row["command_id"] == capture["command_id"])
        assert saved["screenshot"] == BrowserScreenshot.model_validate(body["screenshot"]).model_dump(mode="json", exclude_none=True)
