"""Frozen real-run failures and a controlled same-turn cycle, without live calls."""

import json
import runpy
from copy import deepcopy
from pathlib import Path

from plango_harness.agent.contracts import TripSpec
from plango_harness.agent.graph import _advocate_roles, _semantic_cycle


def test_user_edits_do_not_reuse_historical_cycles_or_role_requests():
    root = Path(__file__).resolve().parents[2]
    snapshot = json.loads((root / "eval/plango-live-preparation/four-person-ready-to-review.json").read_text())
    state = snapshot["state"]
    trace = state["trace"]
    original = deepcopy(trace)
    last_requirement = max(i for i, item in enumerate(trace) if item["event"] == "requirements_ready")
    assert not _semantic_cycle(trace[:last_requirement + 1]), "new people edit must not inherit prior discover/advocate cycles"
    assert not _semantic_cycle(trace)
    def decision(action):
        return {"event": "supervisor_decision", "payload": {"effective_action": action}}
    cycle = [decision(action) for action in ("discover", "advocate", "discover", "advocate")]
    assert _semantic_cycle(trace + cycle), "a real same-pass cycle must still stop"
    for event in ("requirements_ready", "replan_requested", "clarification_received"):
        assert not _semantic_cycle(cycle + [{"event": event}] + cycle[:2])
    assert trace == original, "inspection must preserve historical failures"

    spec = TripSpec.model_validate(state["trip_spec"])
    assert spec.budget is None and spec.per_person_budget is None
    # The goal says 不设预算; a phrase scan used to read that as a budget concern and
    # start an advocate for it. Roles now come from who is actually attending.
    assert "预算" in spec.goal and _advocate_roles(spec) == []
    assert _advocate_roles(spec.model_copy(update={"budget": 250})) == []
    assert _advocate_roles(spec.model_copy(update={"soft_preferences": ["便宜优先"]})) == []
    assert _advocate_roles(spec.model_copy(update={"party_counts": {"成人": 2, "儿童": 1}})) == ["成人", "儿童"]
    assert _advocate_roles(spec.model_copy(update={"party_counts": {"成人": 2, "儿童": 0}})) == []
    # An unfamiliar role is represented like any other, with no vocabulary to match.
    assert _advocate_roles(spec.model_copy(update={"party_counts": {"同事": 2, "宠物": 1}})) == ["同事", "宠物"]

    inspect = runpy.run_path(str(root / "scripts/inspect_trace.py"))
    summary = inspect["summarize"](snapshot)
    assert summary["cumulative"]["tokens"] == 117705
    assert summary["current_budget"]["tokens_since_baseline"] == 25760
    assert summary["trace"]["identity_attempts_recorded"] == 20
    assert summary["trace"]["discovery_passes_missing_refresh_counts"] == 3
    snapshot["state"]["input_text"] = "PRIVATE_PROMPT_SENTINEL"
    assert "PRIVATE_PROMPT_SENTINEL" not in json.dumps(inspect["summarize"](snapshot))
    assert inspect["pointer"]({"a/b": [{"~": None}]}, "/a~1b/0/~0") is None


def test_diagnostic_cost_breakdown_preserves_missing_usage_and_does_not_invent_tool_latency():
    root = Path(__file__).resolve().parents[2]
    inspect = runpy.run_path(str(root / "scripts/inspect_trace.py"))
    summary = inspect["summarize"]({"run_id": "controlled", "state": {
        "turn_id": 2, "model_call_count": 3, "model_calls": [
            {"kind": "TaskIntent", "status": "success", "total_tokens": 120, "latency_ms": 12.5},
            {"kind": "TaskIntent", "status": "error", "latency_ms": 30}],
        "trace": [{"event": "requirements_ready", "ts": 100}, {"event": "approval_requested", "ts": 105}],
    }})
    assert summary["model_records"]["by_kind"]["TaskIntent"] == {
        "calls": 2, "reported_tokens": 120, "missing_usage": 1, "latency_ms": 42.5, "failures": 1}
    assert summary["model_records"]["records_may_be_truncated"]
    assert [row["offset_seconds"] for row in summary["timeline"]["events"]] == [0, 5]
    assert "not per-tool or end-to-end latency" in summary["timeline"]["scope"]
