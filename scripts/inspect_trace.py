#!/usr/bin/env python3
"""Offline numeric summaries and frozen evidence checks; no services or model calls."""

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "eval/plango-next/regression-cases.json"


def summarize(snapshot):
    state = snapshot["state"]
    calls = state.get("model_calls", [])
    trace = state.get("trace", [])
    budget = state.get("turn_budget") or {}
    tokens = state.get("model_token_count", 0)
    tools = state.get("tool_call_count", 0)
    discovery = [item.get("payload", {}) for item in trace if item.get("event") == "discovery_complete"]
    by_kind = {}
    for item in calls:
        row = by_kind.setdefault(item.get("schema", item.get("kind", "unknown")), {"calls": 0, "reported_tokens": 0, "missing_usage": 0, "latency_ms": 0, "failures": 0})
        row["calls"] += 1
        row["failures"] += item.get("status") != "success"
        row["latency_ms"] = round(row["latency_ms"] + item.get("latency_ms", 0), 2)
        if isinstance(item.get("total_tokens"), int):
            row["reported_tokens"] += item["total_tokens"]
        else:
            row["missing_usage"] += 1
    timed = [item for item in trace if isinstance(item.get("ts"), (int, float))]
    origin = min((item["ts"] for item in timed), default=0)
    return {
        "identity": {"run_id": snapshot.get("run_id", state.get("run_id")), "turn_id": state.get("turn_id"),
                     "plan_version": state.get("plan_version"), "phase": snapshot.get("phase", state.get("phase"))},
        "cumulative": {
            "tokens": tokens, "tools": tools,
            "model_calls": state.get("model_call_count", 0),
            "fallbacks": state.get("model_fallback_count", 0),
            "model_latency_ms": state.get("model_total_latency_ms", 0),
        },
        "current_budget": {
            "token_baseline": budget.get("model_baseline", 0),
            "tool_baseline": budget.get("tool_baseline", 0),
            "tokens_since_baseline": tokens - budget.get("model_baseline", 0),
            "tools_since_baseline": tools - budget.get("tool_baseline", 0),
        },
        "model_records": {
            "count": len(calls),
            "success": sum(item.get("status") == "success" for item in calls),
            "fallback": sum(item.get("status") == "fallback" for item in calls),
            "token_budget_fallback": sum(item.get("error") == "model_token_budget" for item in calls),
            "tokens": sum(item.get("total_tokens", 0) for item in calls),
            "largest_input_tokens": max((item.get("input_tokens", 0) for item in calls), default=0),
            "by_kind": by_kind,
            "records_may_be_truncated": state.get("model_call_count", 0) > len(calls),
        },
        "timeline": {"scope": "Recorded stage wall-clock offsets; includes gaps, not per-tool or end-to-end latency",
                     "truncated": len(timed) > 80,
                     "events": [{"event": item.get("event"), "agent_id": item.get("agent_id"),
                                 "offset_seconds": round(item["ts"] - origin, 3)} for item in timed[-80:]]},
        "trace": {
            "records": len(trace),
            "clarifications": sum(item.get("event") == "clarification_requested" for item in trace),
            "cycle_flags": sum("semantic_loop_blocked" in item.get("payload", {}).get("override_reason", "") for item in trace),
            "browser_observations": sum(item.get("event") == "browser_observed" for item in trace),
            "discovery_passes": len(discovery),
            "identity_attempts_recorded": sum(item.get("identity_refresh_attempts", 0) for item in discovery),
            "identity_successes_recorded": sum(item.get("refreshed_identities", 0) for item in discovery),
            "discovery_passes_missing_refresh_counts": sum("identity_refresh_attempts" not in item for item in discovery),
        },
    }


def pointer(document, path):
    for part in path.strip("/").split("/") if path else []:
        part = part.replace("~1", "/").replace("~0", "~")
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def check_bundle():
    bundle = json.loads(BUNDLE.read_text())
    checked = 0
    cache = {}
    for case in bundle["cases"]:
        for source in case["sources"]:
            path = (ROOT / source).resolve()
            assert path.is_relative_to(ROOT / "eval") and path.is_file(), f"missing evidence: {case['id']}"
        for check in case["recorded_checks"]:
            source = check["source"]
            assert source in case["sources"], f"undeclared evidence: {case['id']}"
            if source not in cache:
                cache[source] = json.loads((ROOT / source).read_text())
            actual = pointer(cache[source], check["pointer"])
            assert actual == check["equals"], f"evidence changed: {case['id']} {check['pointer']}"
            checked += 1
    statuses = Counter(case["recorded_status"] for case in bundle["cases"])
    return {"frozen_evidence_checks": checked, "cases_by_recorded_status": dict(statuses), "live_tests_run": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true", help="Check the small frozen bundle, not current service behavior")
    args = parser.parse_args()
    if not args.snapshots and not args.check:
        parser.error("provide snapshot JSON paths or --check")
    result = {"snapshots": [summarize(json.loads(path.read_text())) for path in args.snapshots]}
    if args.check:
        result["bundle"] = check_bundle()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
