#!/usr/bin/env python3
"""Prepare or execute a frozen, bounded live-model pilot with synthetic DOM observations.

Preflight (no credentials/network): .venv/bin/python scripts/check_live_task_quality.py
Run only after the owner freezes sources: add --run --source-sha <preflight SHA>.
The real model sees ordinary runtime prompts; this runner never replaces model
outputs, never approves writes and never loads a live business webpage.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import logging
import math
import sys
import tempfile
import time
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "vendor/planora/backend")]
FIXTURE = ROOT / "eval/fixtures/task_quality_pilot_v1.json"
MAX_REQUESTS = 16
MAX_CASE_REQUESTS = 4
TERMINAL = {"SUCCEEDED", "PARTIAL_FAILED", "FAILED", "INFEASIBLE", "CANCELLED"}

from check_live_services import ProbeStop, account_or_quota, safe_error  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from yoyu.app import create_app  # noqa: E402
from yoyu.settings import DesktopSettings  # noqa: E402


def source_manifest():
    paths = [
        *sorted((ROOT / "backend/yoyu").rglob("*.py")),
        *sorted((ROOT / "vendor/planora/backend/planora").rglob("*.py")),
        Path(__file__).resolve(),
        ROOT / "scripts/check_live_services.py",
        FIXTURE,
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
    ]
    result = {}
    for path in paths:
        try:
            result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            result[str(path.relative_to(ROOT))] = "unreadable:" + type(error).__name__
    return result


def digest(manifest):
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def finite(value):
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        else None
    )


def usage_of(response):
    raw = (
        response.get("raw")
        if isinstance(response, dict) and response.get("raw") is not None
        else response
    )
    usage = (
        getattr(raw, "usage_metadata", None)
        or (getattr(raw, "response_metadata", {}) or {}).get("token_usage")
        or {}
    )
    return (
        {
            k: int(v)
            for k, v in usage.items()
            if k
            in {
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
            }
            and finite(v) is not None
            and v >= 0
        }
        if isinstance(usage, dict)
        else {}
    )


def install_budget(adapter, control, case_id, expected_manifest):
    original = adapter._invoke
    case_control = {"calls": 0, "stop": None, "turn": 0}

    async def invoke(awaitable, *, timeout):
        kind = (
            "source_drift"
            if source_manifest() != expected_manifest
            else "batch_request_limit"
            if len(control["calls"]) >= MAX_REQUESTS
            else "case_request_limit"
            if case_control["calls"] >= MAX_CASE_REQUESTS
            else None
        )
        if kind:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            case_control["stop"] = kind
            if kind != "case_request_limit":
                control["stop"] = kind
            if kind == "source_drift":
                control["source_drift"] = True
            raise ProbeStop(kind, "PilotLimit")
        record = {
            "case_id": case_id,
            "turn": case_control["turn"],
            "request_index": len(control["calls"]) + 1,
        }
        control["calls"].append(record)
        case_control["calls"] += 1
        started = time.perf_counter()
        try:
            result = await original(awaitable, timeout=timeout)
            record.update(status="response", usage=usage_of(result))
            return result
        except Exception as error:
            detail = safe_error(error)
            record.update(status="error", **detail)
            if account_or_quota(error):
                control["stop"] = case_control["stop"] = "account_or_quota"
                raise ProbeStop(
                    "account_or_quota", detail["error_class"], detail.get("http_status")
                ) from None
            raise
        finally:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)

    adapter._invoke = invoke
    return case_control


def safe_snapshot_facts(snapshot, expected):
    state = snapshot.get("state") or {}
    artifacts = state.get("browser_artifacts") or []
    comparisons = [a for a in artifacts if a.get("type") == "price_comparison"]
    comparison = comparisons[-1].get("data", {}) if comparisons else None
    names = set(
        expected.get("prices")
        or expected.get("named_merchants")
        or [expected.get("unknown_merchant"), expected.get("known_merchant")]
    )
    places = []
    for artifact in artifacts:
        for place in (artifact.get("data") or {}).get("places", []):
            places.append(
                {
                    "name": place.get("name")
                    if place.get("name") in names
                    else "[unexpected_entity]",
                    "price": finite(place.get("average_price")),
                    "per_person": place.get("price_unit") in {"人均", "每人", "per_person"},
                }
            )
    facts = {
        "phase": snapshot.get("phase"),
        "turn_id": finite(state.get("turn_id")),
        "observed_place_prices": places,
        "comparison": None,
        "browser_wait": bool(state.get("browser_wait")),
        "action_results_count": len(state.get("action_results") or []),
    }
    if comparison:
        facts["comparison"] = {
            "party_size": finite(comparison.get("party_size")),
            "total_budget": finite(comparison.get("total_budget")),
            "recommendation": comparison.get("recommendation")
            if comparison.get("recommendation") in names
            else "[unexpected_entity]",
            "savings": finite(comparison.get("savings")),
            "entries": [
                {
                    "name": e.get("name") if e.get("name") in names else "[unexpected_entity]",
                    "unit_price": finite(e.get("unit_price")),
                    "total": finite(e.get("total")),
                    "within_budget": e.get("within_budget")
                    if isinstance(e.get("within_budget"), bool)
                    else None,
                    "fixture_source": urlsplit(str(e.get("source_url") or "")).hostname
                    == "fixture.invalid",
                    "evidence_present": bool(e.get("evidence_id")),
                }
                for e in comparison.get("entries", [])
            ],
        }
    return facts


def evaluate_turn(snapshot, turn, delivered, prior_commands, driver_status):
    expected, dom = turn["expected"], turn["dom"]
    state = snapshot.get("state") or {}
    artifacts = state.get("browser_artifacts") or []
    pages = [a for a in artifacts if a.get("type") == "browser_page"]
    source_retained = any(
        a.get("url") == dom["url"] and (a.get("data") or {}).get("text") == dom["text"]
        for a in pages
    )
    checks = {
        "fixture_source_retained": source_retained,
        "no_write_actions": not state.get("action_results"),
        "not_runtime_failure": snapshot.get("phase") not in {"FAILED", "CANCELLED", "INFEASIBLE"}
        and driver_status in {"terminal", "needs_more_evidence"},
        "no_budget_or_deadline_fallback": state.get("model_last_error")
        not in {"model_token_budget", "run_deadline_exhausted"}
        and not any(
            x in str(state.get("reason") or "")
            for x in (
                "超出本轮上下文预算",
                "超过本次运行的模型 token 上限",
                "达到浏览器步骤预算",
                "超过本次运行时间上限",
            )
        ),
    }
    if expected["kind"] == "comparison":
        records = [a.get("data") or {} for a in artifacts if a.get("type") == "price_comparison"]
        comparison = records[-1] if records else {}
        entries = {e.get("name"): e for e in comparison.get("entries", [])}
        party, cap, prices = expected["party_size"], expected["total_budget"], expected["prices"]
        checks.update(
            task_completed=snapshot.get("phase") == "SUCCEEDED",
            comparison_present=bool(comparison),
            merchant_set_correct=set(entries) == set(prices)
            and len(comparison.get("entries", [])) == len(prices),
            party_preserved=comparison.get("party_size") == party,
            budget_correct=comparison.get("total_budget") == cap,
            recommendation_correct=comparison.get("recommendation") == expected["recommendation"],
            difference_correct=comparison.get("savings")
            == (max(prices.values()) - min(prices.values())) * party,
        )
        checks["prices_totals_and_budget_flags_correct"] = all(
            e.get("unit_price") == unit
            and e.get("total") == unit * party
            and e.get("within_budget") is (unit * party <= cap)
            for name, unit in prices.items()
            for e in [entries.get(name, {})]
        )
        checks["source_and_quotes_grounded"] = bool(entries) and all(
            e.get("source_url") == dom["url"]
            and bool(e.get("evidence_id"))
            and e.get("quote")
            and e["quote"] in dom["text"]
            and name in e["quote"]
            for name, e in entries.items()
        )
        if expected.get("fresh_observation_required"):
            checks["fresh_command_after_edit"] = bool(delivered) and not set(delivered) & set(
                prior_commands
            )
            checks["no_previous_price_cards"] = all(
                (a.get("data") or {}).get("text") == dom["text"] for a in pages
            )
    else:
        checks["no_false_task_success"] = snapshot.get("phase") != "SUCCEEDED"
        comparison_rows = [
            a.get("data") or {} for a in artifacts if a.get("type") == "price_comparison"
        ]
        checks["no_unsupported_recommendation"] = not any(
            a.get("recommendation") for a in comparison_rows
        )
        comparison_entries = [e for a in comparison_rows for e in a.get("entries", [])]
        places = [p for a in artifacts for p in (a.get("data") or {}).get("places", [])]
        if expected["kind"] == "unknown_target":
            checks["unknown_target_not_given_neighbor_price"] = all(
                p.get("average_price") is None
                for p in places
                if p.get("name") == expected["unknown_merchant"]
            )
            checks["unknown_not_priced_in_answer"] = all(
                e.get("unit_price") is None and e.get("total") is None
                for e in comparison_entries
                if e.get("name") == expected["unknown_merchant"]
            )
            checks["known_neighbor_extracted"] = any(
                p.get("name") == expected["known_merchant"]
                and p.get("average_price") == expected["known_price"]
                for p in places
            )
        else:
            checks["no_invented_merchant_price"] = not any(
                p.get("average_price") is not None for p in places
            ) and not any(
                e.get("unit_price") is not None or e.get("total") is not None
                for e in comparison_entries
            )
    return checks


def drive_turn(client, run_id, case_id, turn_index, turn, case_control, prior_commands):
    started = time.perf_counter()
    sent = {}
    unsupported = set()
    status = "timeout"
    snapshot = {}
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get("/api/v1/runs/" + run_id)
        if response.status_code != 200:
            status = "driver_error"
            break
        snapshot = response.json()
        if case_control["stop"]:
            status = "stopped"
            break
        if snapshot.get("phase") in TERMINAL:
            status = "terminal"
            break
        if snapshot.get("phase") == "WAITING_APPROVAL":
            status = "unexpected_write"
            break
        pending = (
            client.get("/api/v1/browser/commands", params={"browser_session_id": "pilot-fixture"})
            .json()
            .get("commands", [])
        )
        for command in pending:
            if command.get("run_id") != run_id or command["command_id"] in sent:
                continue
            operation = command["operation"]
            if operation in {"click", "type"} or command.get("approved_action_id"):
                status = "unexpected_write"
                break
            url = (command.get("arguments") or {}).get("url")
            allowed = (
                operation
                in {"extract", "extract_tables", "snapshot", "read_page", "current", "scroll"}
                or operation in {"navigate", "open_tab"}
                and url == turn["dom"]["url"]
            )
            base = {"command_id": command["command_id"], "browser_session_id": "pilot-fixture"}
            if allowed and len(sent) < 8:
                observation = {
                    **base,
                    **turn["dom"],
                    "ok": True,
                    "outcome": "observed",
                    "tab_id": f"fixture-{case_id}",
                    "snapshot_id": f"fixture-{case_id}-turn{turn_index}-observation{len(sent)}",
                    "elements": [],
                }
            else:
                observation = {
                    **base,
                    "ok": False,
                    "outcome": "blocked",
                    "error_kind": "fixture_page_unavailable",
                }
                unsupported.add(command["command_id"])
            posted = client.post(
                "/api/v1/browser/commands/" + command["command_id"] + "/result", json=observation
            )
            if posted.status_code != 200:
                status = "driver_error"
                break
            sent[command["command_id"]] = observation
        if status in {"driver_error", "unexpected_write"}:
            break
        wait = (snapshot.get("state") or {}).get("browser_wait") or {}
        if wait.get("command_id") in unsupported:
            status = "needs_more_evidence"
            break
        if snapshot.get("interrupt_id") and not wait and not pending:
            status = "unexpected_clarification"
            break
        time.sleep(0.05)
    observed = [
        cid
        for cid, value in sent.items()
        if value.get("ok") is True and value.get("outcome") == "observed"
    ]
    checks = evaluate_turn(snapshot, turn, observed, prior_commands, status)
    return {
        "index": turn_index,
        "status": status,
        "passed": all(checks.values()),
        "checks": checks,
        "facts": safe_snapshot_facts(snapshot, turn["expected"]),
        "model_requests_so_far": case_control["calls"],
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "delivered_observations": len(observed),
        "blocked_command_results": len(unsupported),
    }, observed


def run_case(case, settings_values, control, manifest, result):
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="yoyu-live-quality-") as directory:
        settings = DesktopSettings.model_validate(
            {
                **settings_values,
                "database_url": f"sqlite+aiosqlite:///{directory}/runs.sqlite",
                "data_dir": Path(directory),
                "checkpoint_path": Path(directory) / "checkpoints.sqlite",
                "amap_webservice_key": "",
                "embedding_api_key": "",
                "openai_max_retries": 0,
                "openai_timeout_seconds": 15,
                "max_run_seconds": 90,
                "max_model_tokens": 12000,
                "max_tool_calls": 12,
            }
        )
        # TestClient headers carry the local control token; it is neither a provider key nor persisted in reports.
        token = uuid.uuid4().hex
        app = create_app(settings, token=token)
        case_control = install_budget(app.state.runtime.model, control, case["id"], manifest)
        with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
            run_id = None
            previous_commands = []
            for index, turn in enumerate(case["turns"], start=1):
                case_control["turn"] = index
                if case_control["stop"] or control["stop"]:
                    result["turns"].append(
                        {"index": index, "status": "skipped_after_limit", "passed": False}
                    )
                    continue
                body = (
                    {
                        "input_text": turn["message"],
                        "browser_session_id": "pilot-fixture",
                        "enabled_skills": [],
                        "location_context": {"city": "上海", "source": "config"},
                    }
                    if run_id is None
                    else {
                        "text": turn["message"],
                        "location_context": {"city": "上海", "source": "config"},
                    }
                )
                response = client.post(
                    "/api/v1/runs" if run_id is None else "/api/v1/runs/" + run_id + "/messages",
                    json=body,
                )
                if response.status_code != 202:
                    result["turns"].append(
                        {
                            "index": index,
                            "status": "request_rejected",
                            "http_status": response.status_code,
                            "passed": False,
                        }
                    )
                    continue
                run_id = response.json().get("run_id") or run_id
                turn_result, delivered = drive_turn(
                    client, run_id, case["id"], index, turn, case_control, previous_commands
                )
                turn_result["model_requests"] = sum(
                    c["case_id"] == case["id"] and c["turn"] == index for c in control["calls"]
                )
                turn_calls = [
                    c for c in control["calls"] if c["case_id"] == case["id"] and c["turn"] == index
                ]
                turn_result["checks"]["real_model_responded"] = any(
                    c.get("status") == "response" for c in turn_calls
                )
                turn_result["passed"] = all(turn_result["checks"].values())
                result["turns"].append(turn_result)
                previous_commands += delivered
                if turn_result["status"] not in {"terminal", "needs_more_evidence"}:
                    break
            for missed in range(len(result["turns"]) + 1, len(case["turns"]) + 1):
                result["turns"].append(
                    {"index": missed, "status": "skipped_after_incomplete_turn", "passed": False}
                )
            if run_id:
                client.post("/api/v1/runs/" + run_id + "/cancel")
    result.update(
        passed=len(result["turns"]) == len(case["turns"])
        and all(t["passed"] for t in result["turns"]),
        model_requests=case_control["calls"],
        stop_reason=case_control["stop"],
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--source-sha")
    args = parser.parse_args()
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(fixture["cases"]) == 4 and len({c["id"] for c in fixture["cases"]}) == 4
    manifest = source_manifest()
    source_sha = digest(manifest)
    if not args.run:
        print(
            json.dumps(
                {
                    "ready": True,
                    "source_sha": source_sha,
                    "fixture_sha": manifest[str(FIXTURE.relative_to(ROOT))],
                    "cases": [c["id"] for c in fixture["cases"]],
                    "max_model_requests": MAX_REQUESTS,
                    "max_requests_per_case": MAX_CASE_REQUESTS,
                    "network_calls_made": 0,
                }
            )
        )
        return 0
    if args.source_sha != source_sha:
        print(json.dumps({"status": "source_freeze_mismatch", "network_calls_made": 0}))
        return 2
    values = dotenv_values(ROOT / ".env")
    settings_values = {
        field: values[env]
        for field, env in {
            "openai_api_key": "OPENAI_API_KEY",
            "openai_base_url": "OPENAI_BASE_URL",
            "openai_model": "OPENAI_MODEL",
        }.items()
        if values.get(env)
    }
    if not settings_values.get("openai_api_key"):
        print(json.dumps({"status": "missing_model_key", "network_calls_made": 0}))
        return 2
    created = datetime.now(timezone.utc)
    control = {"calls": [], "stop": None, "source_drift": False}
    cases = []
    with warnings.catch_warnings(record=True) as captured:
        for case in fixture["cases"]:
            if control["stop"] or source_manifest() != manifest:
                control["stop"] = control["stop"] or "source_drift"
                if control["stop"] == "source_drift":
                    control["source_drift"] = True
                cases.append(
                    {
                        "id": case["id"],
                        "kind": case["kind"],
                        "passed": False,
                        "status": "skipped",
                        "stop_reason": control["stop"],
                        "turns": [],
                    }
                )
                continue
            result = {"id": case["id"], "kind": case["kind"], "passed": False, "turns": []}
            cases.append(result)
            try:
                run_case(case, settings_values, control, manifest, result)
            except Exception as error:
                result.update(passed=False, status="driver_exception", **safe_error(error))
            result["model_requests"] = sum(c["case_id"] == case["id"] for c in control["calls"])
            for missing in range(len(result["turns"]) + 1, len(case["turns"]) + 1):
                result["turns"].append(
                    {"index": missing, "status": "skipped_after_driver_exception", "passed": False}
                )
    after = source_manifest()
    stable = after == manifest and not control["source_drift"]
    report = {
        "created_at": created.isoformat(),
        "environment": fixture["environment"],
        "real_model": True,
        "real_browser": False,
        "live_amap_requests": 0,
        "business_side_effects": 0,
        "model": settings_values.get(
            "openai_model", DesktopSettings.model_fields["openai_model"].default
        ),
        "limits": {
            "batch_requests": MAX_REQUESTS,
            "case_requests": MAX_CASE_REQUESTS,
            "case_model_tokens": 12000,
            "sdk_retries": 0,
        },
        "denominator": 4,
        "cases_meeting_expected_outcome": sum(c["passed"] for c in cases),
        "cases": cases,
        "model_requests": len(control["calls"]),
        "reported_total_tokens": sum(
            c.get("usage", {}).get(
                "total_tokens",
                c.get("usage", {}).get("input_tokens", c.get("usage", {}).get("prompt_tokens", 0))
                + c.get("usage", {}).get(
                    "output_tokens", c.get("usage", {}).get("completion_tokens", 0)
                ),
            )
            for c in control["calls"]
        ),
        "usage_complete": bool(control["calls"])
        and all(c.get("status") == "response" and bool(c.get("usage")) for c in control["calls"]),
        "positive_outcomes": {
            "passed": sum(c["passed"] for c in cases if c["kind"].startswith("positive")),
            "denominator": 2,
        },
        "negative_guards": {
            "passed": sum(c["passed"] for c in cases if c["kind"].startswith("negative")),
            "denominator": 2,
        },
        "batch_complete": control["stop"] is None
        and all(
            len(c.get("turns", [])) == len(fixture["cases"][i]["turns"])
            and all(t.get("status") in {"terminal", "needs_more_evidence"} for t in c["turns"])
            for i, c in enumerate(cases)
        ),
        "calls": control["calls"],
        "stop_reason": control["stop"],
        "source_before": manifest,
        "source_after": after,
        "source_stable": stable,
        "valid_batch": stable,
        "source_sha": source_sha,
        "fixture_sha": manifest[str(FIXTURE.relative_to(ROOT))],
        "warning_classes": sorted({type(w.message).__name__ for w in captured}),
    }
    destination = (
        ROOT
        / "eval"
        / f"live_task_quality_{created.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}.json"
    )
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "report": str(destination),
                "cases_meeting_expected_outcome": report["cases_meeting_expected_outcome"],
                "denominator": 4,
                "model_requests": report["model_requests"],
                "source_stable": stable,
                "stop_reason": control["stop"],
            }
        )
    )
    return 0 if stable and all(c["passed"] for c in cases) else 1


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    raise SystemExit(main())
