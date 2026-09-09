#!/usr/bin/env python3
"""Review a frozen known-dev collection. Default prepares packets, never calls a model.

--run makes serial, one-shot AI judge calls with bounded admission and usage
accounting. Gold and AI labels remain pending human review; no formal score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import quality_judge as judge
from quality_scoring import require, summarize, unique_json_keys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "eval/quality-v1"
MAX_BATCH_TOKENS = 48000
MAX_CASE_TOKENS = 12000
MAX_JUDGE_CALLS = 12
INITIAL_STAGES = {"initial", "initial-reviewable-draft"}
READ_OPERATIONS = {"extract", "snapshot", "read_page", "extract_tables", "current", "scroll", "navigate", "open_tab"}


def read(path: Path) -> Any:
    return json.loads(path.read_bytes(), object_pairs_hook=unique_json_keys)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _within(path: Path, directory: Path) -> Path:
    path = path.resolve()
    require(path.is_relative_to(directory.resolve()), "artifact path escapes the owned collection")
    return path


def _artifact(path: str, directory: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate if path.startswith("output/") else directory / candidate
    return _within(candidate, directory)


def _equal(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def check_oracle(result: dict[str, Any], oracle: dict[str, Any]) -> list[dict[str, Any]]:
    """Only registered equals/same_as on independently queried checkpoint fields."""
    checks = []
    for rule in oracle["checks"]:
        row = {"check_id": rule["check_id"], "verdict": "pending", "assertions": []}
        if rule.get("method") != "structured":
            row["reason"] = "semantic condition requires independent annotation"
            checks.append(row)
            continue
        for assertion in rule["assertions"]:
            item = {"path": assertion["path"], "op": assertion["op"], "verdict": "pending"}
            try:
                paths = [assertion["path"]] + ([assertion["reference_path"]] if assertion["op"] == "same_as" else [])
                for pointer in paths:
                    stage = pointer.split("/")[2]
                    state = result["checkpoints"][stage].get("independent_state")
                    require(isinstance(state, dict) and state.get("source") == "independent_SQLite_query", "missing independent SQL evidence")
                actual = judge._pointer(result, assertion["path"])
                if assertion["op"] == "same_as":
                    expected = judge._pointer(result, assertion["reference_path"])
                else:
                    require(assertion["op"] == "equals" and "expected" in assertion, "unsupported oracle operation")
                    expected = assertion["expected"]
                item.update(verdict="pass" if _equal(actual, expected) else "fail", actual=actual, expected=expected)
            except (KeyError, ValueError, TypeError, IndexError):
                item["reason"] = "missing_path_or_independent_capture"
            row["assertions"].append(item)
        verdicts = [item["verdict"] for item in row["assertions"]]
        row["verdict"] = "fail" if "fail" in verdicts else "pass" if verdicts and all(value == "pass" for value in verdicts) else "pending"
        checks.append(row)
    return checks


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line, object_pairs_hook=unique_json_keys) for line in path.read_text().splitlines() if line.strip()]


def make_packet(case: dict[str, Any], gold: dict[str, Any], sources: dict[str, Any],
                oracle: dict[str, Any], result: dict[str, Any], directory: Path,
                actor_manifest: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    outputs, snapshots, hashes = [], {}, {}
    baseline_ids, issues, capture_times = set(), [], {}
    checkpoints = result.get("checkpoints", {})
    complete = "final" in checkpoints
    for stage, capture in checkpoints.items():
        source_path = capture.get("source_snapshot") or capture.get("snapshot_path")
        if source_path:
            path = _artifact(source_path, directory)
            hashes[str(path.relative_to(directory))] = file_hash(path)
            snapshots[stage] = read(path)
        baseline = stage in INITIAL_STAGES or capture.get("scope") == "pre_task_context"
        if baseline:
            if capture.get("visible_path"):
                path = _artifact(capture["visible_path"], directory)
                hashes[str(path.relative_to(directory))] = file_hash(path)
                for message in read(path).get("messages", []):
                    if isinstance(message.get("id"), str) and message["id"]:
                        baseline_ids.add(message["id"])
                    else:
                        issues.append(f"baseline_message_id_missing:{stage}")
            else:
                issues.append(f"baseline_visible_unavailable:{stage}")
            continue
        if capture.get("scope") == "actual_owned_backend_restart_desktop_closed":
            continue
        if not capture.get("visible_path"):
            complete = False
            continue
        path = _artifact(capture["visible_path"], directory)
        hashes[str(path.relative_to(directory))] = file_hash(path)
        visible = read(path)
        outputs.append(visible)
        capture_times[visible["checkpoint"]["id"]] = capture.get("captured_at")
    excluded_by_checkpoint = {}
    for visible in outputs:
        excluded_by_checkpoint[visible["checkpoint"]["id"]] = [
            message["id"] for message in visible.get("messages", []) if message.get("id") in baseline_ids]
        visible["messages"] = [message for message in visible.get("messages", []) if message.get("id") not in baseline_ids]
    required = {"DEV-05": {"t1"}, "DEV-06": {"t1", "t2"}, "DEV-09": {"before_restart", "after_restart"}}
    complete = complete and required.get(case["case_id"], set()).issubset(checkpoints)
    sources_for_case = []
    imported = directory / "imported-state.json"
    has_import = imported.is_file()
    if has_import:
        hashes[imported.name] = file_hash(imported)
    import_available = set()
    if case["case_id"] in {"DEV-05", "DEV-06", "DEV-09", "DEV-10"}:
        fixture_issue = "import_record_unavailable"
        try:
            require(has_import, fixture_issue)
            imported_record = read(imported)
            require(imported_record.get("status") == "imported_unsaved_review", "import_not_completed")
            fixture_issue = "path_or_hash_unverified"
            fixture_path = DATA / "runtime-fixtures.dev.json"
            fixture_ref = str(fixture_path.relative_to(ROOT))
            require(imported_record.get("fixture_path") == fixture_ref, "unexpected_fixture_path")
            expected = (actor_manifest or {}).get("files", {}).get(fixture_ref)
            actual = file_hash(fixture_path)
            require(expected is not None and actual == expected == imported_record.get("fixture_sha256"), "fixture_hash_mismatch")
            fixture = read(fixture_path)
            require(case["case_id"] in fixture["case_ids"], "fixture_case_not_declared")
            content = {key: fixture[key] for key in ("origin", "place", "route", "supply", "weather")}
            fixture_issue = "import_clock_unverified"
            imported_at = datetime.fromisoformat(imported_record["imported_at"])
            observed_at = imported_record["clock"]["fixture_observed_at"]
            require(imported_at.utcoffset() is not None and datetime.fromisoformat(observed_at).utcoffset() is not None, "timezone_required")
            for checkpoint_id, captured_at in capture_times.items():
                try:
                    captured = datetime.fromisoformat(captured_at)
                    require(captured.utcoffset() is not None, "timezone_required")
                    if imported_at <= captured:
                        import_available.add(checkpoint_id)
                except (TypeError, ValueError):
                    issues.append(f"runtime_fixture_capture_time_unverified:{checkpoint_id}")
            hashes["fixture:" + fixture_ref] = actual
            sources_for_case.append({
                "source_id": fixture["source_id"], "source_kind": "explicit_synthetic", "content": content,
                "sha256": hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "observed_at": observed_at, "available_at_checkpoint_ids": sorted(import_available),
                "freshness_policy": {"scope": "fixed synthetic world used by the actual imported runtime; not live merchant facts",
                                     "expires_at": imported_record["clock"].get("fixture_expires_at")},
                "provenance": {"fixture_path": fixture_ref, "fixture_sha256": actual,
                               "import_record_sha256": hashes[imported.name], "actor_manifest_fixture_sha256": expected,
                               "imported_at": imported_record["imported_at"]},
            })
        except (OSError, KeyError, TypeError, ValueError):
            issues.append("runtime_fixture_" + fixture_issue)
    for source in sources["packets"]:
        if case["case_id"] not in source["case_ids"]:
            continue
        available = []
        for output in outputs:
            checkpoint_id = output["checkpoint"]["id"]
            for stage, envelope in snapshots.items():
                observed_id = envelope.get("checkpoint", {}).get("id", stage)
                if checkpoint_id != observed_id:
                    continue
                snapshot = envelope.get("snapshot", envelope)
                observation = snapshot.get("state", {}).get("browser_observation") or {}
                packet_id = observation.get("fields", {}).get("evaluation_source", {}).get("packet_id")
                if packet_id == source["packet_id"] or checkpoint_id in import_available:
                    available.append(checkpoint_id)
        sources_for_case.append({"source_id": source["packet_id"], "content": source["payload"],
                                 "observed_at": source.get("observed_at"), "source_kind": source.get("kind"),
                                 "sha256": source["payload_sha256"], "available_at_checkpoint_ids": sorted(set(available)),
                                 "freshness_policy": "historical/controlled scope and original timestamp; not current live merchant facts"})
    operations = []
    for row in _jsonl(directory / "observations.jsonl"):
        command, receipt = row.get("command", {}), row.get("result", {})
        operations.append({"command_id": command.get("command_id"), "operation": command.get("operation"),
                           "approved_action_id": command.get("approved_action_id"), "ok": receipt.get("ok"),
                           "error_kind": receipt.get("error_kind")})
    desktop = {}
    for name in ("desktop-record.json", "desktop-result.json"):
        path = directory / name
        if path.is_file():
            hashes[name] = file_hash(path)
            raw = read(path)
            desktop = {key: raw[key] for key in ("transport_counts", "driver_actions", "restored_comparison", "recovery", "acceptance", "cleanup", "desktop_restarted") if key in raw}
            break
    for name in ("observations.jsonl", "egress.jsonl", "controlled-world.jsonl"):
        path = directory / name
        if path.is_file():
            hashes[name] = file_hash(path)
    stop = result.get("stop_reason")
    outcome = {"completed": "completed", "script_finished": "completed", "timeout": "timeout",
               "external_blocked": "external_blocked", "budget_exhausted": "budget_exhausted"}.get(stop, "product_failure")
    packet = {
        "case_id": case["case_id"], "trial_id": f"{case['case_id']}:{result.get('trial_id', 'first')}",
        "task": case, "gold": gold, "source_packets": sources_for_case, "outputs": outputs,
        "preparation_issues": issues,
        "baseline_message_exclusion": {"excluded_baseline_message_ids": sorted(baseline_ids),
                                       "excluded_by_checkpoint": excluded_by_checkpoint,
                                       "policy": "Exclude pre_task_context message IDs from later history only; current cards stay complete. Original visible files and hashes are unchanged."},
        "all_delivery_checkpoints_captured": complete, "attempt_outcome": outcome,
        "independent_state": {"checkpoints": {stage: {"independent_state": capture.get("independent_state"),
                                                      "captured_at": capture.get("captured_at")} for stage, capture in checkpoints.items()},
                              "structured_oracle": check_oracle(result, oracle)},
        "transport_checks": {"browser_commands": operations, "desktop": desktop,
                             "recorded_browser_write_attempts": sum(row["operation"] not in READ_OPERATIONS or bool(row["approved_action_id"]) for row in operations),
                             "recorded_browser_writes_executed": sum(row["ok"] is True and row["operation"] not in READ_OPERATIONS for row in operations),
                             "egress": _jsonl(directory / "egress.jsonl"), "controlled_world": _jsonl(directory / "controlled-world.jsonl"),
                             "coverage": "Only runner-recorded browser commands, desktop transport and provider egress; not an unrestricted real-world audit"},
    }
    return packet, hashes


def review_collection(collection_path: Path, output: Path, *, run: bool = False, provider=None,
                      judge_cases: set[str] | None = None) -> dict[str, Any]:
    directory = collection_path.resolve().parent
    collection, manifest = read(collection_path), read(directory / "manifest.json")
    frozen = {key: value for key, value in manifest.items() if key not in {"source_sha", "git_revision", "offline_wiring_not_quality"}}
    require(hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == manifest["source_sha"], "actor manifest digest mismatch")
    require(collection["planned"] == manifest["case_ids"], "planned cases differ from frozen manifest")
    require(judge_cases is None or judge_cases.issubset(collection["planned"]), "unplanned judge case selection")
    require(not run or not manifest.get("offline_wiring_not_quality"), "offline wiring cannot become a model-quality score")
    files = {name: DATA / name for name in ("tasks.dev.json", "gold.dev.json", "source-packets.dev.json", "oracle-checks.dev.json")}
    data = {name: read(path) for name, path in files.items()}
    hashes = {name: file_hash(path) for name, path in files.items()}
    oracle = data["oracle-checks.dev.json"]
    for key, name in (("gold_sha256", "gold.dev.json"), ("tasks_sha256", "tasks.dev.json"), ("source_packets_sha256", "source-packets.dev.json")):
        require(oracle[key] == hashes[name], f"oracle hash mismatch: {name}")
    require(data["source-packets.dev.json"]["tasks_sha256"] == hashes["tasks.dev.json"], "source packet task hash mismatch")
    for source in data["source-packets.dev.json"]["packets"]:
        payload_hash = hashlib.sha256(json.dumps(source["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        require(payload_hash == source["payload_sha256"], "source payload hash mismatch")
    freeze_origin = {}
    for name, path in files.items():
        expected = manifest["files"].get(str(path.relative_to(ROOT)))
        require(expected is not None or name in {"gold.dev.json", "oracle-checks.dev.json"}, f"missing actor input hash: {name}")
        require(expected is None or expected == hashes[name], f"frozen data changed: {name}")
        freeze_origin[name] = "actor_manifest" if expected else "review_time_only"
    tasks = {row["case_id"]: row for row in data["tasks.dev.json"]}
    gold = {row["case_id"]: row for row in data["gold.dev.json"]}
    oracles = {row["case_id"]: row for row in oracle["cases"]}
    results = {row["case_id"]: row for row in collection["cases"]}
    require(len(results) == len(collection["cases"]), "duplicate case results cannot be selected")
    require(set(results).issubset(collection["planned"]), "unplanned case result")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    review_manifest = {"actor_manifest_sha256": file_hash(directory / "manifest.json"),
                       "collection_sha256": file_hash(collection_path), "actor_source_sha": manifest["source_sha"],
                       "data_hashes": hashes, "freeze_origin": freeze_origin,
                       "review_code_hashes": {name: file_hash(ROOT / "scripts" / name) for name in ("quality_review.py", "quality_judge.py", "quality_scoring.py")},
                       "limits": {"judge_calls": MAX_JUDGE_CALLS, "judge_reported_tokens": MAX_BATCH_TOKENS, "per_case_tokens": MAX_CASE_TOKENS},
                       "human_verified": False, "gold_human_verified": False}
    judge._write(output / "review-manifest.json", review_manifest)
    annotations = {"schema_version": 1, "dataset_version": "quality-v1:" + hashes["tasks.dev.json"],
                   "system_revision": manifest.get("git_revision", "uncommitted") + ":" + manifest["source_sha"],
                   "environment_level": "known_dev_mixed_collection", "annotations_origin": "ai_assisted",
                   "human_verified": False, "gold_human_verified": False, "planned_cases": [], "attempts": []}
    calls = tokens = 0
    stop = None
    case_rows = []
    for case_id in collection["planned"]:
        task, case_gold = tasks[case_id], gold[case_id]
        annotations["planned_cases"].append({"case_id": case_id, "case_class": task["case_class"], "family": task["family"],
            "group_ids": task["group_ids"], "must_pass": [row["id"] for row in case_gold["must_pass"]],
            "forbidden": [f"F{i + 1}" for i in range(len(case_gold["forbidden_claims"]))]})
        result = results.get(case_id)
        if result is None or result.get("stop_reason") == "not_run_after_batch_stop":
            case_rows.append({"case_id": case_id, "status": "not_run"})
            continue
        case_dir = _within(directory / case_id, directory)
        result_path = next((path for path in (case_dir / "collection.json", case_dir / "result.json") if path.is_file()), None)
        if result_path:
            require(read(result_path) == result, "root and case result disagree")
        target = output / case_id
        target.mkdir(mode=0o700)
        packet, artifact_hashes = make_packet(task, case_gold, data["source-packets.dev.json"], oracles[case_id], result, case_dir, manifest)
        if result_path:
            artifact_hashes[result_path.name] = file_hash(result_path)
        judge._write(target / "artifact-hashes.json", artifact_hashes)
        judge._write(target / "packet.json", packet)
        prepared = judge.prepare_case(packet)
        prepared["preparation_issues"].extend(packet["preparation_issues"])
        judge._write(target / "prepared.json", prepared)
        pending = {"case_id": case_id, "trial_id": packet["trial_id"], "valid_attempt": True, "replacement_for": None,
                   "invalid_reason": None, "outcome": packet["attempt_outcome"], "annotation_complete": False, "claims": []}
        if result.get("valid_attempt") is False:
            require(result.get("stop_reason") not in {"timeout", "external_blocked", "budget_exhausted",
                    "unscripted_clarification", "unexpected_write_proposal", "desktop_driver_failed"},
                    "product/external failure cannot be relabeled evaluator-invalid")
            pending.update(valid_attempt=False, outcome="evaluation_invalid", invalid_reason=result.get("invalid_reason"))
        status = {"case_id": case_id, "status": "prepared", "environment_level": result.get("environment_level"),
                  "preparation_issues": prepared["preparation_issues"]}
        blinded = {key: value for key, value in prepared.items() if key != "attempt_outcome"}
        estimate = len(judge.SYSTEM_PROMPT) + len(json.dumps(blinded, ensure_ascii=False)) + 256
        cap = max(0, min(4096, MAX_CASE_TOKENS - estimate, MAX_BATCH_TOKENS - tokens - estimate))
        status.update(estimated_input_tokens=estimate, available_output_tokens=cap,
                      token_estimate_scope="project admission heuristic, not the provider tokenizer")
        if run and pending["valid_attempt"] and not stop and (judge_cases is None or case_id in judge_cases):
            if calls >= MAX_JUDGE_CALLS or cap < 256:
                status["status"] = "pending_budget_admission"
                if calls >= MAX_JUDGE_CALLS or MAX_BATCH_TOKENS - tokens - estimate < 256:
                    stop = "judge_budget_admission"
            else:
                summary = judge.run_case(prepared, target / "judge", provider=provider, max_output_tokens=cap)
                calls += summary["requests_attempted"]
                if summary.get("usage_missing"):
                    stop = "judge_usage_missing_or_request_failed"
                else:
                    actual = summary["usage"]["total_tokens"]
                    tokens += actual
                    if actual > estimate + cap or tokens >= MAX_BATCH_TOKENS:
                        stop = "judge_token_limit"
                    elif summary.get("error_class"):
                        stop = "judge_response_invalid"
                review_path = target / "judge/review.json"
                if review_path.is_file():
                    parsed = read(review_path)
                    pending = parsed["scoring_attempt"]
                    for check in packet["independent_state"]["structured_oracle"]:
                        if check["verdict"] == "fail":
                            pending["checks"][check["check_id"]] = False
                        elif check["verdict"] == "pending" and next(row for row in oracles[case_id]["checks"] if row["check_id"] == check["check_id"])["method"] == "structured":
                            pending["annotation_complete"] = False
                    judge._write(target / "merged-annotation.json", pending)
                status.update(status=summary["status"], usage=summary["usage"], usage_missing=summary["usage_missing"])
        if stop:
            status["batch_stop"] = stop
        annotations["attempts"].append(pending)
        case_rows.append(status)
    judge._write(output / "annotations.json", annotations)
    scores = summarize(annotations)
    scores["annotations_sha256"] = file_hash(output / "annotations.json")
    scores["review_manifest_sha256"] = file_hash(output / "review-manifest.json")
    scores["offline_wiring_not_quality"] = bool(manifest.get("offline_wiring_not_quality"))
    judge._write(output / "scores.json", scores)
    summary = {"report_kind": "provisional_dev", "mode": "run" if run else "prepare_only", "judge_calls": calls,
               "judge_reported_tokens": tokens, "stop": stop, "planned": len(collection["planned"]),
               "annotated": scores["annotated"], "complete": scores["complete"], "human_verified": False,
               "gold_human_verified": False, "cases": case_rows}
    judge._write(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New private review directory")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--cases", help="Comma-separated judge subset; all planned cases remain in accounting")
    args = parser.parse_args()
    try:
        os.umask(0o077)
        print(json.dumps(review_collection(args.collection, args.output, run=args.run,
                                          judge_cases=set(args.cases.split(",")) if args.cases else None), ensure_ascii=False))
    except Exception as error:
        parser.exit(2, f"quality_review: {type(error).__name__}; no retry or output overwrite\n")


if __name__ == "__main__":
    main()
