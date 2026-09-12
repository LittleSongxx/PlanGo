"""Trustworthy runner: actor isolation and snapshot projection. No holdout scoring."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.trustworthy.actor import load_actor_dataset  # noqa: E402
from scripts.trustworthy.project import (  # noqa: E402
    delivery_from,
    infrastructure_reason,
    project_attempt,
)
from scripts.trustworthy.runner import (  # noqa: E402
    IsolatedRunner,
    isolated_settings,
    trip_spec_from_initial,
)
from scripts.trustworthy.schema import world_pack  # noqa: E402

HOLDOUT = ROOT / "eval" / "trustworthy-v1" / "holdout"


def test_actor_loader_does_not_open_oracles(monkeypatch):
    opened: list[str] = []
    original = Path.read_text

    def wrapped(self, *args, **kwargs):
        opened.append(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", wrapped)
    actor = load_actor_dataset(HOLDOUT)
    assert actor["oracles_opened"] is False
    assert "oracles.json" not in opened
    assert "ho-calc-001" in actor["tasks"]
    assert actor["tasks"]["ho-calc-001"]["world_id"] in actor["worlds"]


def test_project_attempt_maps_snapshot_contract():
    world = {
        "world_id": "w",
        "documents": [{"doc_id": "d", "title": "t", "text": "三人同行合计 186 元。", "observed_at": "2026-09-11T09:00:00+08:00"}],
    }
    snapshot = {
        "run_id": "run-1",
        "phase": "SUCCEEDED",
        "state": {
            "reason": "ignored",
            "trip_spec": {"party_size": 4, "goal": "改人数"},
            "previous_spec": {"party_size": 2, "goal": "改人数"},
            "execution_outcome": {
                "kind": "task_answer",
                "status": "satisfied",
                "summary": "三人同行合计 186 元。",
                "data": {
                    "business_completed": False,
                    "calculations": [{"ok": True, "scope": "arithmetic_only", "value": "186"}],
                },
            },
            "browser_observation": {"text": "三人同行合计 186 元。"},
        },
    }
    attempt = project_attempt(
        task={"task_id": "ho-calc-001"},
        world=world,
        snapshot=snapshot,
        trial_id="t1",
        prior_trip_spec={"party_size": 2},
        valid_attempt=True,
    )
    assert attempt["valid_attempt"] is True
    assert attempt["outcome"] == "completed"
    assert attempt["delivery"]["text"] == "三人同行合计 186 元。"
    assert attempt["delivery"]["answer_number"] == 186
    assert attempt["end_state"]["trip_spec"]["party_size"] == 4
    assert attempt["end_state"]["previous_spec"]["party_size"] == 2
    assert attempt["end_state"]["prior_trip_spec"]["party_size"] == 2
    assert attempt["end_state"]["execution_outcome"]["data"]["business_completed"] is False
    assert "186" in attempt["observation_pack"]["text"]


def test_empty_delivery_has_no_answer_number():
    delivery = delivery_from({"state": {"execution_outcome": {"summary": "", "data": {}}}})
    assert delivery == {"text": ""}


def test_trip_spec_fills_schema_required_coordinates_only():
    spec = trip_spec_from_initial({"location": {"name": "石梁茶室"}, "party_size": 3}, "关掉再打开")
    assert spec["location"]["name"] == "石梁茶室"
    assert spec["party_size"] == 3
    assert "latitude" in spec["location"]


def test_isolated_runner_with_stub_model_does_not_open_oracles(tmp_path, monkeypatch):
    from plango.task import DeliveryDecision, TaskDecision

    actor = load_actor_dataset(HOLDOUT)
    task = actor["tasks"]["ho-calc-001"]
    world = actor["worlds"][task["world_id"]]
    opened: list[str] = []
    original = Path.read_text

    def wrapped(self, *args, **kwargs):
        opened.append(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", wrapped)

    async def choose(schema, *, fallback, **kwargs):
        if schema in {TaskDecision, DeliveryDecision}:
            return TaskDecision(operation="answer", answer=world_pack(world)["text"].split("。")[-2] + "。")
        return fallback

    runner = IsolatedRunner(tmp_path / "work", live=False, timeout=90, structured=choose)
    attempt = runner.run_task(task, world)
    assert "oracles.json" not in opened
    assert attempt["task_id"] == "ho-calc-001"
    assert attempt["observation_pack"]["text"]
    assert attempt["valid_attempt"], attempt.get("invalid_reason")
    assert attempt["delivery"]["text"]
    assert (attempt["end_state"].get("execution_outcome") or {}).get("data", {}).get("business_completed") is not True


def test_eval_settings_do_not_apply_external_cutoffs(tmp_path):
    settings = isolated_settings(tmp_path / "eval-limits", live=False)
    assert settings.openai_timeout_seconds >= 3600
    assert settings.max_run_seconds >= 3600
    assert settings.openai_max_retries >= 5


def test_provider_timeout_snapshot_is_not_a_scored_failure():
    snapshot = {
        "run_id": "run-timeout",
        "phase": "FAILED",
        "state": {"reason": "模型请求超时，本轮未取得可用回复。", "trip_spec": {}, "execution_outcome": {}},
    }
    reason = infrastructure_reason(snapshot)
    assert reason == "模型请求超时"
    attempt = project_attempt(
        task={"task_id": "ho-spar-020"},
        world={"world_id": "w", "documents": [{"doc_id": "d", "title": "t", "text": "页文", "observed_at": "2026-09-11T09:00:00+08:00"}]},
        snapshot=snapshot,
        trial_id="t-timeout",
        valid_attempt=False,
        invalid_reason=reason,
    )
    assert attempt["valid_attempt"] is False
    assert attempt["invalid_reason"] == "模型请求超时"


def test_provider_timeout_is_invalid_and_retries_until_usable(tmp_path):
    import json

    from plango.task import Calculation, DeliveryDecision, TaskDecision
    from plango_harness.agent.model_adapter import ModelProviderUnavailable

    actor = load_actor_dataset(HOLDOUT)
    task = actor["tasks"]["ho-calc-001"]
    world = actor["worlds"][task["world_id"]]
    calls = {"n": 0}

    async def choose(schema, *, fallback, **kwargs):
        if schema in {TaskDecision, DeliveryDecision}:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ModelProviderUnavailable("timeout")
            payload = json.loads(kwargs.get("user") or "{}")
            if any(isinstance(row, dict) and row.get("scope") == "arithmetic_only" and row.get("ok") for row in payload.get("tool_results") or []):
                return TaskDecision(operation="answer", answer="步行加上摆渡全程 22 分钟。")
            return TaskDecision(
                operation="calculate",
                calculations=[Calculation(id="total", operation="sum", operands=["14", "8"])],
            )
        return fallback

    runner = IsolatedRunner(tmp_path / "timeout-retry", live=False, timeout=90, structured=choose)
    attempt = runner.run_task(task, world)
    assert calls["n"] >= 2
    assert attempt["valid_attempt"], attempt.get("invalid_reason")
    assert "22" in attempt["delivery"]["text"]


def test_exhausted_provider_timeout_stays_invalid(tmp_path):
    from plango.task import DeliveryDecision, TaskDecision
    from plango_harness.agent.model_adapter import ModelProviderUnavailable

    actor = load_actor_dataset(HOLDOUT)
    task = actor["tasks"]["ho-calc-001"]
    world = actor["worlds"][task["world_id"]]

    async def choose(schema, *, fallback, **kwargs):
        if schema in {TaskDecision, DeliveryDecision}:
            raise ModelProviderUnavailable("timeout")
        return fallback

    runner = IsolatedRunner(tmp_path / "timeout-invalid", live=False, timeout=90, structured=choose)
    attempt = runner.run_task(task, world)
    assert attempt["valid_attempt"] is False
    assert "超时" in (attempt.get("invalid_reason") or "")


def test_persist_restart_keeps_seeded_spec(tmp_path):
    from plango.task import DeliveryDecision, TaskDecision

    actor = load_actor_dataset(HOLDOUT)
    task = next(row for row in actor["tasks"].values() if row["layer"] == "persist" and "party_size" in (row.get("initial_trip_spec") or {}))
    world = actor["worlds"][task["world_id"]]

    async def choose(schema, *, fallback, **kwargs):
        if schema in {TaskDecision, DeliveryDecision}:
            return TaskDecision(operation="answer", answer="字段仍在。")
        return fallback

    runner = IsolatedRunner(tmp_path / "persist", live=False, timeout=90, structured=choose)
    attempt = runner.run_task(task, world)
    assert attempt["valid_attempt"], attempt.get("invalid_reason")
    assert attempt["end_state"]["prior_trip_spec"].get("party_size") == task["initial_trip_spec"]["party_size"]
    assert attempt["end_state"]["trip_spec"].get("party_size") == task["initial_trip_spec"]["party_size"]


def test_poll_shape_operation_is_read_at_top_level():
    """The poll endpoint flattens the command payload; a nested-only read served every
    real browser request as blocked, which no v1/v2 task happened to exercise."""
    from scripts.trustworthy.runner import READ_OPS, command_operation, frozen_observation

    flat = {"command_id": "c1", "browser_session_id": "s", "operation": "snapshot", "arguments": {}}
    assert command_operation(flat) == "snapshot"
    assert command_operation({"command_id": "c1", "payload": {"operation": "extract"}}) == "extract"
    assert command_operation({"command_id": "c1"}) is None
    world = {
        "world_id": "w",
        "documents": [{"doc_id": "d", "title": "t", "text": "页文", "observed_at": "2026-09-12T00:00:00+08:00"}],
    }
    observed = frozen_observation(flat, "s", world, blocked=command_operation(flat) not in READ_OPS)
    assert observed["ok"] is True and observed["text"] == "页文"
    blocked = frozen_observation(flat, "s", world, blocked=True)
    assert blocked["ok"] is False and blocked["outcome"] == "blocked"
