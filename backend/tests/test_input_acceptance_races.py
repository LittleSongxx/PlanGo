"""Deterministic interleavings at the acceptance transaction; isolated offline state only."""

import pytest
from fastapi.testclient import TestClient
from plango.browser import commands
from test_browser_harness import TOKEN
from test_input_acceptance import app_without_worker


@pytest.mark.parametrize("interleaving", ["pending_command", "lease", "unknown", "cancel"])
def test_replan_cannot_overwrite_a_boundary_changed_before_acceptance(tmp_path, interleaving):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "读取网页", "browser_session_id": "fixture"}).json()["run_id"]

        async def exercise():
            row = await runtime.runs.get(rid)
            state = {**row["state_json"], "phase": "REQUIREMENTS_READY", "browser_wait": {"command_id": "read-command"}, "interrupt_id": "fixture-pause"}
            async with runtime.database.session() as session:
                async with session.begin():
                    await session.execute(commands.insert().values(command_id="read-command", run_id=rid, browser_session_id="fixture", payload={"operation": "extract"}, created_at=1.0))
            await runtime.runs.save_state_and_events(state, expected_version=row["version"], events=[])
            original_save = runtime.runs.save_state_and_events
            boundary = {}

            async def interleave(*args, **kwargs):
                runtime.runs.save_state_and_events = original_save
                if interleaving == "pending_command":
                    accepted = await runtime.accept_message(rid, "继续", request_id="accepted-resume")
                    assert accepted["accepted"]
                elif interleaving == "lease":
                    assert await runtime.runs.claim(rid, "fixture-worker")
                elif interleaving == "unknown":
                    await runtime.runs.record_action(action_id="fixture-unknown", run_id=rid, plan_id="fixture-plan", plan_version=1,
                                                     tool_name="click", idempotency_key="fixture-write", request_hash="fixture-hash", arguments={}, status="UNKNOWN")
                else:
                    await runtime.runs.request_cancel(rid)
                boundary["row"] = await runtime.runs.get(rid)
                boundary["binding"] = await runtime.bridge.binding(rid)
                boundary["actions"] = await runtime.runs.actions(rid)
                return await original_save(*args, **kwargs)

            runtime.runs.save_state_and_events = interleave
            try:
                with pytest.raises((ValueError, RuntimeError)):
                    await runtime.accept_message(rid, "人数改3人", request_id="racing-edit", location_context={"city": "北京", "source": "manual"})
            finally:
                runtime.runs.save_state_and_events = original_save
            assert await runtime.runs.accepted_input("racing-edit") is None
            assert await runtime.runs.get(rid) == boundary["row"]
            assert await runtime.bridge.binding(rid) == boundary["binding"]
            assert await runtime.runs.actions(rid) == boundary["actions"]
            if interleaving == "pending_command":
                assert boundary["row"]["pending_command"]
                assert (await runtime.accept_message(rid, "继续", request_id="accepted-resume"))["replayed"]
                assert await runtime.runs.get(rid) == boundary["row"], "Replaying an accepted request must preserve its queued command"

        client.portal.call(exercise)
