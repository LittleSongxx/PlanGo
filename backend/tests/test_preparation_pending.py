"""Synthetic legacy durable commands test the real HTTP dispatch/receipt recovery boundary."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.browser import commands
from plango_harness.persistence.database import agent_action, agent_run
from sqlalchemy import insert, select, update
from test_browser_harness import TOKEN, settings


@pytest.mark.parametrize("status", ["RUNNING", "UNKNOWN"])
def test_legacy_preparation_submit_is_not_dispatched_or_rewritten_and_late_receipt_is_accepted(tmp_path, status):
    app = create_app(settings(tmp_path), token=TOKEN)
    app.state.runtime._enqueue_run = AsyncMock(return_value=True)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        text = "只准备预约表单，尚未提交"
        run_id = client.post("/api/v1/runs", json={"input_text": text, "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command_id, action_id = "legacy-submit", "legacy-action"
        payload = {"command_id": command_id, "run_id": run_id, "browser_session_id": "fixture-desktop", "operation": "click", "arguments": {"idx": 3},
                   "approved_action_id": action_id, "expected_snapshot_id": "legacy-snapshot", "tab_id": "legacy-tab", "_turn_id": 1,
                   "_turn_key": hashlib.sha256(text.encode()).hexdigest()}
        action_arguments = {"target": {"idx": 3, "tag": "button", "input_type": "submit", "text": "确认预约"}}
        ledger_result = {"status": "UNKNOWN", "resolution_required": True} if status == "UNKNOWN" else None

        async def legacy_rows():
            now = datetime.now(timezone.utc)
            async with app.state.runtime.database.session() as session:
                async with session.begin():
                    await session.execute(update(agent_run).where(agent_run.c.run_id == run_id).values(phase="REQUIREMENTS_READY", state_json={
                        "run_id": run_id, "turn_id": 1, "browser_task_context": {"kind": "prepare", "mode": "browser"},
                        "approval_decision": "approve", "action_proposal": {"actions": [{"action_id": action_id}]},
                        "browser_wait": {"command_id": command_id}, "interrupt_id": "browser:" + command_id,
                    }))
                    await session.execute(insert(agent_action).values(action_id=action_id, run_id=run_id, plan_id="legacy-plan", plan_version=1,
                        tool_name="click", idempotency_key=action_id, request_hash="legacy-hash", status=status, arguments_json=action_arguments,
                        result_json=ledger_result, created_at=now, updated_at=now))
                    await session.execute(insert(commands).values(command_id=command_id, run_id=run_id, browser_session_id="fixture-desktop", payload=payload, created_at=time.time()))

        client.portal.call(legacy_rows)

        def poll(_):
            response = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop")
            assert response.status_code == 200, response.text
            return response.json()["commands"]

        with ThreadPoolExecutor(max_workers=3) as executor:
            assert list(executor.map(poll, range(6))) == [[]] * 6
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
        blocked = [event for event in events if event["event_type"] == "BROWSER_COMMAND_BLOCKED"]
        assert len(blocked) == 1 and blocked[0]["payload"]["command_id"] == command_id
        assert blocked[0]["payload"]["reason"] == "准备范围不允许自动提交，请人工核对"

        async def persisted():
            async with app.state.runtime.database.session() as session:
                command = (await session.execute(select(commands.c.result, commands.c.payload).where(commands.c.command_id == command_id))).one()
                action = (await session.execute(select(agent_action.c.status, agent_action.c.result_json, agent_action.c.arguments_json).where(agent_action.c.action_id == action_id))).one()
            return command, action

        command, action = client.portal.call(persisted)
        assert command == (None, payload)
        assert action == (status, ledger_result, action_arguments)
        receipt = {"command_id": command_id, "browser_session_id": "fixture-desktop", "ok": True, "outcome": "executed", "url": "https://fixture.invalid/book", "tab_id": "legacy-tab"}
        accepted = client.post(f"/api/v1/browser/commands/{command_id}/result", json=receipt)
        assert accepted.status_code == 200 and accepted.json()["accepted"], accepted.text
        assert client.post(f"/api/v1/browser/commands/{command_id}/result", json=receipt).json()["replayed"] is True
        command, action = client.portal.call(persisted)
        assert command[0]["outcome"] == "executed"
        assert action == (status, ledger_result, action_arguments)
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
        assert len([event for event in events if event["event_type"] == "BROWSER_COMMAND_BLOCKED"]) == 1
        assert len([event for event in events if event["event_type"] == "BROWSER_OBSERVATION"]) == 1
