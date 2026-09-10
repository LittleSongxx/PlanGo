"""Memory protocol checks with synthetic DOM/model inputs; no external business or model calls."""

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango.graph import BrowserDecision
from plango.runtime import browser_memory_proposal
from plango_harness.agent.contracts import (
    ActionResult,
    ActionStatus,
    MemoryProposal,
    RunPhase,
    TripSpec,
)
from plango_harness.agent.decisions import ReflectionOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import initial_state
from plango_harness.memory.repository import MemoryRepository
from plango_harness.persistence.database import Database, procedural_rule
from sqlalchemy import select
from task_fixtures import browser_actor
from test_browser_harness import TOKEN, create_app, settings, wait_for
from test_browser_navigation import browser_driver


def wait_profile(client, user, predicate):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        profile = client.get("/api/v1/memory/profile", params={"user_id": user}).json()
        if predicate(profile):
            return profile
        time.sleep(.02)
    raise AssertionError(profile)


def read_page(client, user="alice"):
    run_id = client.post("/api/v1/runs", json={"user_id": user, "input_text": "读取当前网页菜单", "browser_session_id": "fixture-desktop"}).json()["run_id"]
    command, respond, _ = browser_driver(client, run_id)
    respond(command("extract"))
    done = wait_for(client, run_id, lambda value: value["phase"] in {"SUCCEEDED", "FAILED", "PARTIAL_FAILED"})
    assert done["phase"] == "SUCCEEDED", done
    return run_id


def test_feedback_preferences_replay_delete_and_restart_use_server_memory(tmp_path):
    config = settings(tmp_path)
    app = create_app(config, token=TOKEN)
    seen = []

    async def model(schema, *, fallback, **kwargs):
        if schema is BrowserDecision:
            seen.append(json.loads(kwargs["user"])["memory_context"])
            return BrowserDecision(operation="finish")
        return fallback

    app.state.runtime.model.structured = browser_actor(model)
    auth = {"Authorization": "Bearer " + TOKEN}
    with TestClient(app, headers=auth) as client:
        assert client.post("/api/v1/memory/preferences", json={"user_id": "alice", "text": "香菜", "polarity": "dislike"}).status_code == 200
        assert client.post("/api/v1/memory/preferences", json={"user_id": "bob", "text": "甜食", "polarity": "like"}).status_code == 200
        run_id = read_page(client)
        assert any(item.get("value", {}).get("text") == "香菜" for item in seen[-1])
        assert all(item.get("value", {}).get("text") != "甜食" for item in seen[-1])
        profile = wait_profile(client, "alice", lambda value: any(item.get("scope") == "task_answer" for item in value["summaries"]))
        assert len(profile["preferences"]) == 1
        assert profile["preferences"][0]["explicit"] is True and profile["preferences"][0]["source"].startswith("user:")
        original_source = profile["preferences"][0]["source"]
        with sqlite3.connect(tmp_path / "runs.sqlite") as db:
            db.execute("UPDATE user_fact SET source=? WHERE id=?", ("legacy-run:terminal:1", profile["preferences"][0]["id"]))
        historical = client.get("/api/v1/memory/profile?user_id=alice").json()["preferences"][0]
        assert historical["explicit"] is False and historical["source"] == "legacy-run:terminal:1"
        with sqlite3.connect(tmp_path / "runs.sqlite") as db:
            db.execute("UPDATE user_fact SET source=? WHERE id=?", (original_source, profile["preferences"][0]["id"]))
        body = {"feedback_id": "feedback-stable", "turn_id": 1, "rating": "helpful", "text": "这次查询很有帮助"}
        response = client.post(f"/api/v1/runs/{run_id}/feedback", json=body)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["replayed"] is False
        replay = client.post(f"/api/v1/runs/{run_id}/feedback", json=body).json()
        assert replay["replayed"] is True and replay["feedback"] == first["feedback"]
        assert client.post(f"/api/v1/runs/{run_id}/feedback", json={**body, "rating": "unhelpful"}).status_code == 409
        assert client.post(f"/api/v1/runs/{run_id}/feedback", json={**body, "feedback_id": "stale", "turn_id": 2}).status_code == 409
        assert client.get(f"/api/v1/runs/{run_id}").json()["feedback"] == [first["feedback"]]
        assert client.get(f"/api/v1/runs/{run_id}/feedback").json()["feedback"] == [first["feedback"]]
        profile = client.get("/api/v1/memory/profile?user_id=alice").json()
        assert len(profile["preferences"]) == 1, "rating must not infer a new preference"
        feedback_episode = next(item for item in profile["summaries"] if item.get("scope") == "user_feedback")
        assert client.delete("/api/v1/memory/episodes/" + feedback_episode["id"], params={"user_id": "alice"}).status_code == 200
        # A lost acknowledgement/profile fetch leaves the same stale UI card; DELETE can be retried.
        deleted = client.delete("/api/v1/memory/episodes/" + feedback_episode["id"], params={"user_id": "alice"})
        assert deleted.status_code == 200 and all(item["id"] != feedback_episode["id"] for item in deleted.json()["summaries"])
        assert client.delete("/api/v1/memory/episodes/" + feedback_episode["id"], params={"user_id": "bob"}).status_code == 200
        assert client.delete("/api/v1/memory/episodes/nonexistent", params={"user_id": "alice"}).status_code == 200
        assert client.get(f"/api/v1/runs/{run_id}/feedback").json()["feedback"] == []
        assert client.post(f"/api/v1/runs/{run_id}/feedback", json=body).status_code == 409
        assert client.delete("/api/v1/memory/preferences", params={"user_id": "alice", "text": "香菜"}).status_code == 200
        read_page(client)
        assert all(item.get("value", {}).get("text") != "香菜" for item in seen[-1])
        assert client.delete("/api/v1/memory/profile?user_id=alice").json()["summaries"] == []
    with TestClient(create_app(config, token=TOKEN), headers=auth) as client:
        time.sleep(.15)  # Startup reconciliation must not resurrect erased source receipts.
        profile = client.get("/api/v1/memory/profile?user_id=alice").json()
        assert profile["preferences"] == [] and profile["summaries"] == []
        assert client.get(f"/api/v1/runs/{run_id}/feedback").json()["feedback"] == []
        assert client.get("/api/v1/memory/profile?user_id=bob").json()["preferences"]
        read_page(client)
        wait_profile(client, "alice", lambda value: len(value["summaries"]) == 1)
        with sqlite3.connect(tmp_path / "runs.sqlite") as db:
            db.execute("UPDATE trip_episode SET valid_until='2000-01-01 00:00:00' WHERE user_id='alice'")
            db.execute("UPDATE memory_document SET valid_until='2000-01-01 00:00:00' WHERE user_id='alice'")
        assert client.get("/api/v1/memory/profile?user_id=alice").json()["summaries"] == []


async def test_commit_is_atomic_idempotent_and_erasure_blocks_late_projection(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/memory.sqlite", allow_fallback=False)
    await database.connect()
    memory = MemoryRepository(database)
    try:
        proposal = MemoryProposal(kind="rule", key="same", source_event_id="user:once", value={"predicate": "x", "action": "prefer"})
        commits = await asyncio.gather(memory.commit("alice", [proposal]), memory.commit("alice", [proposal]))
        assert sorted(map(len, commits)) == [0, 1]
        async with database.session() as session:
            assert (await session.execute(select(procedural_rule.c.hit_count))).scalar_one() == 1
        with pytest.raises(ValueError, match="payload_conflict"):
            await memory.commit("alice", [proposal.model_copy(update={"value": {"predicate": "x", "action": "avoid"}})])
        observed = datetime.now(timezone.utc)
        episode = MemoryProposal(kind="episode", key="browser_outcome", source_event_id="system:browser-outcome:run:1", value={"summary": "只读页面", "observed_at": observed.isoformat()}, valid_until=observed + timedelta(days=30))
        await memory.commit("alice", [episode])
        await memory.forget_user("alice")
        assert await memory.commit("alice", [episode]) == []
        assert await memory.retrieve("alice") == []
        event = (await memory.events_for_source("alice", episode.source_event_id))[0]
        assert event["payload_json"] == {"forgotten": True, "source_event_id": episode.source_event_id}
    finally:
        await database.close()


def test_unknown_actions_and_business_claims_are_not_automatic_browser_memories():
    row = {"run_id": "run", "user_id": "alice", "phase": "SUCCEEDED", "state_json": {"turn_id": 1, "action_results": [{"status": "UNKNOWN"}], "execution_outcome": {"status": "satisfied", "summary": "看似成功", "data": {"scope": "ready_to_review"}}}}
    assert browser_memory_proposal(row) is None
    row["state_json"]["action_results"] = []
    row["state_json"]["execution_outcome"]["data"]["scope"] = "business_receipt"
    assert browser_memory_proposal(row) is None


@pytest.mark.parametrize("case", ["rejected", "empty", "inferred_fact", "episode"])
async def test_actual_reflection_subgraph_preserves_turn_and_does_not_infer_personal_facts(tmp_path, case):
    model = ModelAdapter(settings(tmp_path))
    model.structured = AsyncMock(return_value=ReflectionOutput(remember=True, kind="fact" if case != "episode" else "episode", key="preference:少花钱" if case != "episode" else "trip_outcome", value={"summary": "synthetic result"}))
    memory = SimpleNamespace(events_for_source=AsyncMock(return_value=[]), commit=AsyncMock(side_effect=lambda user, items: [item.model_dump(mode="json") for item in items]))
    deps = GraphDeps(model=model, tools=SimpleNamespace(schemas=lambda: []), world=SimpleNamespace(), planner=None, memory=memory, runs=None, action_provider=None)
    nodes = {}

    def capture(graph):
        nodes["reflect"] = graph.nodes["reflect"].runnable

    build_graph(deps, extension=capture)
    state = initial_state(run_id="fixture", user_id="alice", input_text="synthetic goal")
    state.update(turn_id=4, trip_spec=TripSpec(goal="synthetic goal"), phase=RunPhase.CANCELLED if case == "rejected" else RunPhase.SUCCEEDED,
                 approval_decision="reject" if case == "rejected" else "approve", requirement_reference_at="2026-09-09T03:00:00+00:00",
                 action_results=[] if case in {"rejected", "empty"} else [ActionResult(action_id="synthetic", status=ActionStatus.SUCCEEDED)])
    result = await nodes["reflect"].ainvoke(state)
    assert result["reflection_done"]
    if case in {"rejected", "empty"}:
        model.structured.assert_not_awaited()
    if case != "episode":
        memory.commit.assert_not_awaited()
    else:
        proposal = memory.commit.await_args.args[1][0]
        assert proposal.source_event_id == "fixture:terminal:4"
        assert proposal.value["observed_at"] == state["requirement_reference_at"]
