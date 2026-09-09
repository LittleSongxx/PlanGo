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
    assert "预算" in spec.goal and _advocate_roles(spec) == ["体验"]
    assert _advocate_roles(spec.model_copy(update={"budget": 250})) == ["预算", "体验"]
    assert _advocate_roles(spec.model_copy(update={"per_person_budget": 100})) == ["预算", "体验"]
    assert _advocate_roles(spec.model_copy(update={"soft_preferences": ["便宜优先"]})) == ["预算", "体验"]
    assert _advocate_roles(spec.model_copy(update={"party_counts": {"成人": 2, "儿童": 1}})) == ["家庭", "健康", "体验"]
    assert _advocate_roles(spec.model_copy(update={"party_counts": {"成人": 2, "儿童": 0}})) == ["体验"]

    inspect = runpy.run_path(str(root / "scripts/inspect_trace.py"))
    summary = inspect["summarize"](snapshot)
    assert summary["cumulative"]["tokens"] == 117705
    assert summary["current_budget"]["tokens_since_baseline"] == 25760
    assert summary["trace"]["identity_attempts_recorded"] == 20
    assert summary["trace"]["discovery_passes_missing_refresh_counts"] == 3
    snapshot["state"]["input_text"] = "PRIVATE_PROMPT_SENTINEL"
    assert "PRIVATE_PROMPT_SENTINEL" not in json.dumps(inspect["summarize"](snapshot))
    assert inspect["pointer"]({"a/b": [{"~": None}]}, "/a~1b/0/~0") is None
