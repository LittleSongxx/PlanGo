"""Isolated persisted conversation history; no graph, browser, or model calls."""

import asyncio

import pytest
from plango_harness.agent.contracts import RunPhase
from plango_harness.persistence.database import Database, agent_run
from plango_harness.persistence.runs import InputAcceptance, RunRepository
from sqlalchemy import update


async def test_boundary_replies_survive_restart_without_changing_model_messages(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/replies.sqlite"
    database = Database(url)
    runs = RunRepository(database)
    try:
        await runs.create_with_event("conversation", "fixture", "安排晚餐")
        original = (await runs.get("conversation"))["state_json"]
        messages = [{"type": "HumanMessage", "content": "安排晚餐", "id": "original"}]
        state = {**original, "messages": messages, "phase": "REQUIREMENTS_READY", "interrupt_id": "question:1",
                 "clarification": {"question": "几个人用餐？"}, "coordinator_reason": "internal route JSON"}
        interrupted = {"event_type": "GRAPH_INTERRUPTED", "payload": {"interrupts": [{"type": "clarification", "question": "几个人用餐？"}]}}
        await runs.save_state_and_events(state, events=[interrupted])
        # Duplicate persistence and a resume through an intermediate state are both harmless.
        await asyncio.gather(*(runs.save_state_and_events(state, events=[]) for _ in range(3)))
        await runs.save_state({**state, "phase": "RESEARCHING"})
        await runs.save_state_and_events(state, events=[interrupted])
        finished = {**state, "phase": "SUCCEEDED", "reason": "草案已保存，优惠规则仍待核验。",
                    "execution_outcome": {"summary": "outcome fallback must not replace the final reason"}}
        await runs.save_state(finished)
        await runs.save_state(finished)
        # Identical replies in a separately accepted user turn must both survive.
        await runs.save_state({**finished, "turn_id": 2})
        before = await runs.events("conversation")
        assert [event.payload for event in before if event.event_type == "ASSISTANT_MESSAGE"] == [
            {"content": "几个人用餐？", "turn_id": 1},
            {"content": "草案已保存，优惠规则仍待核验。", "turn_id": 1},
            {"content": "草案已保存，优惠规则仍待核验。", "turn_id": 2},
        ]
        assert (await runs.get("conversation"))["state_json"]["messages"] == messages
    finally:
        await database.close()
    database = Database(url)
    runs = RunRepository(database)
    try:
        row = await runs.get("conversation")
        await runs.save_state(row["state_json"])
        assert await runs.events("conversation") == before
        assert row["state_json"]["messages"] == messages
    finally:
        await database.close()


async def test_only_manual_browser_gates_and_approval_are_conversation_replies(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/gates.sqlite")
    runs = RunRepository(database)
    try:
        await runs.create_with_event("gates", "fixture", "读取网页")
        state = (await runs.get("gates"))["state_json"]
        wait = {"type": "browser", "id": "browser:1", "command_id": "1", "operation": "extract", "message": "等待桌面浏览器结果。"}
        state.update(phase="REQUIREMENTS_READY", interrupt_id="browser:1", browser_wait=wait,
                     reason="上轮答复", clarification={"question": "旧问题"})
        await runs.save_state_and_events(state, events=[{"event_type": "GRAPH_INTERRUPTED", "payload": {"interrupts": [wait]}}])
        assert not [event for event in await runs.events("gates") if event.event_type == "ASSISTANT_MESSAGE"]
        manual = {**wait, "error_kind": "login_required", "message": "请完成登录后继续。"}
        await runs.save_state({**state, "browser_wait": manual})
        # The interrupt question takes precedence over any stale reason or tool data.
        review = {"type": "draft_review", "id": "draft:1", "question": "可保存草案，或准备页面供人工核对。"}
        state.update(browser_wait=None, interrupt_id="draft:1", clarification={"kind": "draft_review"})
        await runs.save_state_and_events(state, events=[{"event_type": "GRAPH_INTERRUPTED", "payload": {"interrupts": [review]}}])
        state.update(phase="WAITING_APPROVAL", interrupt_id="approval:1", reason="等待批准具体浏览器操作")
        await runs.save_state(state)
        await runs.mark_terminal("gates", RunPhase.CANCELLED, "浏览器操作未获批准。")
        await runs.mark_terminal("gates", RunPhase.CANCELLED, "浏览器操作未获批准。")
        assert [event.payload["content"] for event in await runs.events("gates") if event.event_type == "ASSISTANT_MESSAGE"] == [
            "请完成登录后继续。", "可保存草案，或准备页面供人工核对。", "等待批准具体浏览器操作", "浏览器操作未获批准。",
        ]
    finally:
        await database.close()


async def test_reply_and_projection_rollback_together_and_acceptance_sequence_is_unchanged(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/transaction.sqlite")
    runs = RunRepository(database)
    try:
        await runs.create_with_event("atomic", "fixture", "原任务")
        row = await runs.get("atomic")
        terminal = {**row["state_json"], "phase": "FAILED", "reason": "超过本次运行时间上限"}
        with pytest.raises(RuntimeError, match="stale"):
            await runs.save_state_and_events(terminal, expected_version=0, events=[],
                                            acceptance=InputAcceptance("rejected", "hash"))
        assert await runs.get("atomic") == row
        assert await runs.accepted_input("rejected") is None
        assert len(await runs.events("atomic")) == 1
        created, _ = await runs.save_state_and_events(terminal, expected_version=row["version"],
            events=[{"event_type": "RUN_FAILED", "payload": {"error": "TimeoutError"}}],
            acceptance=InputAcceptance("accepted", "hash"))
        assert [event.event_type for event in created] == ["RUN_FAILED", "ASSISTANT_MESSAGE"]
        assert (await runs.accepted_input("accepted"))["event_seq"] == created[0].seq == 2
        assert created[1].payload == {"content": "超过本次运行时间上限", "turn_id": 1}
        await runs.mark_terminal("atomic", RunPhase.FAILED, "超过本次运行时间上限")
        assert len(await runs.events("atomic")) == 3
    finally:
        await database.close()


async def test_saving_legacy_state_does_not_backfill_or_invent_replies(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/legacy.sqlite")
    runs = RunRepository(database)
    try:
        await runs.create_with_event("legacy", "fixture", "历史任务")
        state = {**(await runs.get("legacy"))["state_json"], "phase": "SUCCEEDED", "reason": "旧答复"}
        async with database.session() as session:
            async with session.begin():
                await session.execute(update(agent_run).where(agent_run.c.run_id == "legacy").values(state_json=state, phase="SUCCEEDED"))
        await runs.save_state(state)
        assert len(await runs.events("legacy")) == 1
        await runs.save_state({**state, "phase": "RESEARCHING", "reason": {"raw": "tool JSON"}, "coordinator_reason": "internal routing"})
        await runs.mark_terminal("legacy", RunPhase.FAILED)
        assert len(await runs.events("legacy")) == 1
        await runs.save_state({**state, "turn_id": 2, "reason": "", "execution_outcome": {"summary": "已保留读取结果，规则待补充。"}})
        assert (await runs.events("legacy"))[-1].payload == {"content": "已保留读取结果，规则待补充。", "turn_id": 2}
    finally:
        await database.close()
