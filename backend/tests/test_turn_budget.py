"""Synthetic model usage and real compiled checkpoints verify user-turn budget boundaries."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from plango.app import create_app
from plango.graph import BrowserDecision
from plango.settings import DesktopSettings
from plango_harness.agent.contracts import Location, RunPhase
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import PlanGoState, _budget_checkpoint
from plango_harness.runtime import PlanGoRuntime
from plango_harness.settings import Settings
from test_browser_harness import TOKEN, settings, wait_for


def test_browser_model_admission_uses_remaining_turn_budget_not_cumulative_total():
    async def exercise():
        decision = BrowserDecision(operation="snapshot", rationale="读取同一页面的当前状态")
        invoke = AsyncMock(return_value={"parsed": decision, "raw": SimpleNamespace(usage_metadata={"total_tokens": 321})})
        provider = SimpleNamespace(with_structured_output=lambda *args, **kwargs: SimpleNamespace(ainvoke=invoke))
        adapter = ModelAdapter(DesktopSettings(_env_file=None, max_model_tokens=12000), model=provider)
        adapter.reset_run(12152, call_count=6)
        adapter.set_run_budget(None, token_baseline=8809)
        assert adapter.token_limit == 20809 and adapter._remaining_tokens() == 6657
        fallback = BrowserDecision(rationale="范围尚未完成")
        result = await adapter.structured(BrowserDecision, system="读取网页", user="读取商家菜单", fallback=fallback)
        assert result == decision and invoke.await_count == 1
        assert adapter.total_tokens == 12473 and adapter.call_count == 7
        assert adapter.token_baseline == 8809 and adapter.last_error is None
        # An overlarge next prompt can still be rejected before billing despite an unspent turn cap.
        blocked = await adapter.structured(BrowserDecision, system="读取网页", user="页面原文" * 2000, fallback=fallback)
        assert blocked is fallback and invoke.await_count == 1
        assert adapter.last_error == "model_token_budget" and adapter._remaining_tokens() > 0
        assert adapter.total_tokens == 12473 and adapter.call_count == 7

    asyncio.run(exercise())


def test_new_user_edits_receive_budget_without_resetting_cumulative_usage(tmp_path):
    config = settings(tmp_path).model_copy(update={"max_model_tokens": 2000, "max_tool_calls": 1, "amap_webservice_key": "replaced-geocoder-only"})
    app = create_app(config, token=TOKEN)
    adapter = app.state.runtime.model
    world = app.state.runtime.world_service.provider
    world.amap.geocode = AsyncMock(side_effect=lambda address, **kwargs: Location(name=address, latitude=30.0, longitude=110.0))
    world.amap._get = AsyncMock(side_effect=AssertionError("No real network in budget fixtures."))

    async def model(schema, *, fallback, **kwargs):
        if schema is RequirementOutput:
            assert adapter.token_limit - adapter.total_tokens >= 1500
            adapter.total_tokens += 1500  # Explicit synthetic provider usage.
            adapter.call_count += 1
        return fallback

    adapter.structured = model
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "从北京出发，周末安排活动，预算待定", "browser_session_id": "budget-fixture", "location_context": {"city": "重庆", "source": "manual"}}).json()["run_id"]
        first = wait_for(client, rid, lambda v: bool(v.get("interrupt_id")))
        assert first["state"]["model_token_count"] == 1500
        assert first["state"]["tool_call_count"] == 1
        for number, text in enumerate(("起点：上海，预算改为300元，日期仍待定", "起点：深圳，预算改为500元，日期仍待定"), 2):
            response = client.post(f"/api/v1/runs/{rid}/messages", json={"text": text})
            assert response.status_code == 202, response.text
            current = wait_for(client, rid, lambda v: v["state"].get("model_token_count") == number * 1500 and bool(v.get("interrupt_id")))
            assert current["phase"] == "REQUIREMENTS_READY", current
            assert current["state"]["turn_budget"]["model_baseline"] == (number - 1) * 1500
            assert current["state"]["turn_budget"]["tool_baseline"] == number - 1
            assert current["state"]["tool_call_count"] == number
            assert adapter.token_limit == (number - 1) * 1500 + config.max_model_tokens
        assert current["state"]["model_call_count"] == 3
        world.amap._get.assert_not_awaited()


def test_budget_fanout_merge_never_adds_or_restores_an_old_grant():
    old = {"id": "user:2", "grant_seq": 2, "started_at": 100, "deadline_at": 400, "model_baseline": 1500, "tool_baseline": 1}
    new = {"id": "user:5", "grant_seq": 5, "started_at": 200, "deadline_at": 500, "model_baseline": 3000, "tool_baseline": 2}
    assert _budget_checkpoint(new, old) == _budget_checkpoint(old, new) == new
    resumed = {**new, "deadline_at": 600}
    assert _budget_checkpoint(_budget_checkpoint(new, resumed), new) == resumed
    assert _budget_checkpoint(resumed, {}) == resumed


async def runtime_with_gate(directory, kind):
    config = Settings(runtime_profile="sandbox", redis_url="local://", database_url=f"sqlite+aiosqlite:///{directory}/runs.sqlite", checkpoint_path=directory / "checkpoints.sqlite", data_dir=directory, max_run_seconds=30, _env_file=None)
    runtime = PlanGoRuntime(config, embedded_worker=False)
    await runtime.start()

    async def before(state):
        runtime.model.total_tokens += 100
        runtime.model.call_count += 1
        return {"tool_call_count": 1, "model_token_count": 100, "turn_count": 1}

    def gate(state):
        answer = interrupt({"type": kind, "id": f"{kind}:fixture", "question": "受控人工等待"})
        assert runtime.model._remaining_seconds() > 0
        return {"phase": RunPhase.SUCCEEDED, "outcome": "SUCCEEDED", "interrupt_id": None, "consumed_command_id": answer.get("_command_id")}

    graph = StateGraph(PlanGoState)
    graph.add_node("before", before)
    graph.add_node("gate", gate)
    graph.add_edge(START, "before")
    graph.add_edge("before", "gate")
    graph.add_edge("gate", END)
    runtime.graph = graph.compile(checkpointer=runtime._checkpointer)
    return runtime


def test_all_interrupts_preserve_remaining_time_and_crash_replay_does_not_refill(tmp_path):
    async def exercise():
        class ProcessExit(BaseException):
            pass

        for kind in ("clarification", "approval", "browser"):
            folder = tmp_path / kind
            folder.mkdir()
            runtime = await runtime_with_gate(folder, kind)
            try:
                rid = (await runtime.create_run("fixture", "受控任务"))["run_id"]
                paused = await runtime._run_graph(rid)
                row = await runtime.runs.get(rid)
                state = row["state_json"]
                assert state["budget_pause"]["remaining_seconds"] > 0
                state["turn_budget"]["deadline_at"] = time.time() - 3600
                state["budget_pause"]["remaining_seconds"] = 12
                await runtime.runs.save_state_and_events(state, expected_version=row["version"], events=[])
                row = await runtime.runs.get(rid)
                # Approval and automatic browser acknowledgement carry no new user text.
                payload = {"decision": "approve" if kind == "approval" else "resume", "text": ""}
                accepted = await runtime.runs.update_input_with_event(run_id=rid, text=None, phase=RunPhase(row["phase"]), event_type="BROWSER_RESUME_REQUESTED" if kind == "browser" else "RESUME_REQUESTED", command_payload=payload, expected_version=row["version"])
                duplicate = await runtime.runs.update_input_with_event(run_id=rid, text=None, phase=RunPhase(row["phase"]), event_type="RESUME_REQUESTED", command_payload=payload, expected_version=row["version"])
                assert duplicate.payload["command_id"] == accepted.payload["command_id"]
                invoke = runtime.graph.ainvoke

                async def crash(*args, **kwargs):
                    if kind == "approval":
                        await invoke(*args, **kwargs)  # Checkpoint completed, projection has not committed.
                    raise ProcessExit()

                runtime.graph.ainvoke = crash
                try:
                    await runtime._run_graph(rid, resume=payload, command_id=accepted.payload["command_id"])
                except ProcessExit:
                    pass
                row = await runtime.runs.get(rid)
                restored = row["state_json"]["turn_budget"]["deadline_at"]
                assert 0 < restored - time.time() <= 12
                assert row["state_json"]["budget_pause"] is None
                runtime.graph.ainvoke = invoke
                await runtime.close()
                runtime = await runtime_with_gate(folder, kind)
                done = await runtime._run_graph(rid, resume=payload, command_id=accepted.payload["command_id"])
                assert done["phase"] == "SUCCEEDED", done
                assert done["state"]["turn_budget"]["deadline_at"] == restored
                assert done["state"]["turn_budget"]["model_baseline"] == 0
                assert done["state"]["model_token_count"] == paused["state"]["model_token_count"] == 100
                assert done["state"]["tool_call_count"] == 1
            finally:
                await runtime.close()

    asyncio.run(exercise())
