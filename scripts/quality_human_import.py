#!/usr/bin/env python3
"""Bind per-case human UI reviews to frozen evidence and aggregate two metrics.

No model, service, browser or business call. Reviewer identity is self-declared,
not a cryptographic signature. AI proposals are never treated as human events.
Public API: seal_bundle, required_acknowledgements, import_reviews, canonical_sha.
CLI reads a bundle and append-only JSONL events; an existing output is refused.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import quality_judge as judge
from quality_scoring import OUTCOMES, require, summarize, unique_json_keys, unique_strings

SCHEMA = "plango.controlled-human-review.v1"
GUARDS = ["side_effects_absent", "false_completion_absent"]


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _time(value: Any) -> datetime:
    require(isinstance(value, str), "review time must be an ISO timestamp")
    result = datetime.fromisoformat(value)
    require(result.utcoffset() is not None, "review time must include its timezone")
    return result


def _gold_payload(case: dict[str, Any]) -> dict[str, Any]:
    packet = case["packet"]
    # Only this execution-dependent availability list is omitted. Source body,
    # original observation time and fixed fixture definitions remain bound.
    sources = [{key: value for key, value in source.items() if key != "available_at_checkpoint_ids"}
               for source in packet["source_packets"]]
    return {**{key: case[key] for key in ("case_id", "case_class", "family", "group_ids")},
            "task": packet["task"], "gold": packet["gold"], "source_packets": sources}


def _bind(row: dict[str, Any], field: str, expected: Any) -> None:
    require(field not in row or row[field] == expected, f"collection binding mismatch: {field}")
    row[field] = expected


def seal_bundle(value: dict[str, Any]) -> dict[str, Any]:
    """Return a sealed copy. No attempt, human approval or output is invented."""
    bundle = copy.deepcopy(value)
    require(bundle.get("schema_version") == 1 and type(bundle.get("schema_version")) is int, "schema_version must be 1")
    require(bundle.get("report_kind", "human_reviewed_controlled") == "human_reviewed_controlled", "controlled review bundle required")
    bundle["report_kind"] = "human_reviewed_controlled"
    dataset, product = bundle["dataset"], bundle["product"]
    require(isinstance(dataset.get("name"), str) and dataset["name"].strip(), "dataset name required")
    require(dataset.get("scope") == "controlled_acceptance", "this importer only supports controlled acceptance tasks")
    planned = unique_strings(dataset.get("planned_case_ids"), "planned_case_ids")
    require(isinstance(product.get("revision"), str) and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", product["revision"]) is not None, "full product revision required")
    require(isinstance(product.get("files"), dict) and bool(product["files"]), "frozen product file hashes required")
    require(all(isinstance(path, str) and path and not Path(path).is_absolute() and ".." not in Path(path).parts and _sha(digest)
                for path, digest in product["files"].items()), "invalid product file manifest")
    require(isinstance(product.get("model_config"), dict), "frozen model_config required")
    cases = bundle.get("cases")
    require(isinstance(cases, list) and [case.get("case_id") for case in cases] == planned, "case order/coverage must equal the declared plan")
    for case in cases:
        require(case.get("case_class") in ("delivery", "bounded_answer"), "case_class required")
        require(isinstance(case.get("family"), str) and case["family"].strip(), "case family required")
        unique_strings(case.get("group_ids"), "case group_ids")
        packet = case["packet"]
        require(packet.get("case_id") == case["case_id"], "packet case mismatch")
        require(isinstance(packet.get("task"), dict) and isinstance(packet.get("gold"), dict), "task/gold required")
        require(isinstance(packet.get("source_packets"), list), "static source_packets required")
        acknowledgements = required_acknowledgements(case, "gold")
        require(bool(acknowledgements["reviewed_check_ids"]), "must_pass cannot be empty")
        case["gold_sha"] = canonical_sha(_gold_payload(case))
        case["packet_sha"] = canonical_sha(packet)
    bundle["dataset_sha"] = canonical_sha({"dataset": dataset, "cases": [
        {"case_id": case["case_id"], "gold_sha": case["gold_sha"]} for case in cases]})
    bundle["product_sha"] = canonical_sha(product)
    collection = bundle["collection"]
    _bind(collection, "dataset_sha", bundle["dataset_sha"])
    _bind(collection, "product_sha", bundle["product_sha"])
    _bind(collection, "planned_case_ids", planned)
    require(isinstance(collection.get("attempts"), list), "collection attempts must be explicit; [] is allowed before collection")
    by_case = {case["case_id"]: case for case in cases}
    for attempt in collection["attempts"]:
        require(attempt.get("case_id") in by_case, "unplanned collection attempt")
        require(type(attempt.get("valid_attempt")) is bool, "valid_attempt must be explicit")
        if attempt["valid_attempt"]:
            case = by_case[attempt["case_id"]]
            require(attempt.get("trial_id") == case["packet"].get("trial_id"), "case packet is from a different trial")
            require(attempt.get("outcome") in OUTCOMES and attempt["outcome"] == case["packet"].get("attempt_outcome"), "attempt outcome mismatch")
            _bind(attempt, "packet_sha", case["packet_sha"])
    bundle["collection_sha"] = canonical_sha(collection)
    return bundle


def required_acknowledgements(case: dict[str, Any], stage: str,
                              review: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Exact IDs that the UI must record as individually reviewed for this case."""
    gold = case["packet"]["gold"]
    checks = unique_strings([row["id"] for row in gold["must_pass"]], "gold check IDs")
    require(isinstance(gold.get("forbidden_claims"), list) and all(isinstance(text, str) and text for text in gold["forbidden_claims"]), "forbidden_claims must be a string list")
    result = {"reviewed_check_ids": checks,
              "reviewed_forbidden_ids": [f"F{index + 1}" for index in range(len(gold["forbidden_claims"]))]}
    if stage == "gold":
        result["reviewed_source_ids"] = unique_strings(
            [source["source_id"] for source in case["packet"]["source_packets"]], "gold source IDs", allow_empty=True)
    else:
        require(stage == "output", "stage must be gold or output")
        require(isinstance(review, dict) and isinstance(review.get("claims"), list), "output review with explicit claims required")
        prepared = judge.prepare_case(case["packet"])
        result["reviewed_guard_ids"] = GUARDS.copy()
        result["reviewed_claim_ids"] = unique_strings([claim["claim_id"] for claim in review["claims"]], "reviewed claim IDs", allow_empty=True)
        result["reviewed_surface_ids"] = [f"{surface['ref_id']}#{surface['pointer']}" for surface in prepared["required_output_surfaces"]]
    return result


def _acknowledged(event: dict[str, Any], expected: dict[str, list[str]]) -> bool:
    for field, ids in expected.items():
        actual = unique_strings(event.get(field), field, allow_empty=True)
        if set(actual) != set(ids):
            return False
    return True


def _annotations(bundle: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "dataset_version": bundle["dataset"]["name"] + ":" + bundle["dataset_sha"],
            "system_revision": bundle["product"]["revision"] + ":" + bundle["product_sha"],
            "environment_level": "human_reviewed_controlled_acceptance", "annotations_origin": "human",
            "human_verified": False, "gold_human_verified": False,
            "planned_cases": [{**{field: case[field] for field in ("case_id", "case_class", "family", "group_ids")},
                               "must_pass": [row["id"] for row in case["packet"]["gold"]["must_pass"]],
                               "forbidden": [f"F{index + 1}" for index in range(len(case["packet"]["gold"]["forbidden_claims"]))]}
                              for case in bundle["cases"]],
            "attempts": [{**copy.deepcopy(attempt), "annotation_complete": False} for attempt in bundle["collection"]["attempts"]]}


def _future_evidence_issues(review: dict[str, Any], packet: dict[str, Any]) -> list[str]:
    """A later DB capture or user turn cannot support an already delivered claim."""
    outputs = {"output:" + output["checkpoint"]["id"]: output["checkpoint"] for output in packet["outputs"]}
    captures = packet.get("independent_state", {}).get("checkpoints", {})
    driver = packet.get("task", {}).get("environment", {}).get("driver")
    issues = []
    for claim in review["claims"]:
        if claim.get("label") != "supported":
            continue
        target = outputs.get((claim.get("output") or {}).get("ref_id"), {})
        for evidence in claim.get("evidence", []):
            reference = evidence.get("ref_id")
            if reference not in {"task", "independent_state", "transport_checks"}:
                continue
            pointer = evidence.get("pointer", "")
            parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]
            issue = None
            if reference == "transport_checks":
                # A specific receipt/event may predate the final transport log.
                # Require its nearest recorded timestamp, never a later summary.
                value = packet.get("transport_checks", {})
                recorded = None
                try:
                    for part in [*parts, None]:
                        if isinstance(value, dict):
                            for key in ("at", "captured_at", "observed_at", "created_at", "finished_at"):
                                if value.get(key):
                                    try:
                                        _time(value[key])
                                    except ValueError:
                                        continue  # A timezone-free DB event uses its enclosing capture.
                                    recorded = value[key]
                                    break
                        if part is not None:
                            value = value[int(part)] if isinstance(value, list) else value[part]
                    if _time(recorded) > _time(target.get("captured_at")):
                        issue = "transport_evidence_from_future"
                except (KeyError, TypeError, ValueError, IndexError):
                    issue = "transport_evidence_time_missing"
            elif reference == "independent_state":
                if not parts or parts == ["checkpoints"]:
                    issue = "state_evidence_requires_specific_checkpoint"
                elif parts[0] == "checkpoints":
                    try:
                        captured = _time(captures[parts[1]]["captured_at"])
                        delivered = _time(target.get("captured_at"))
                        if captured > delivered:
                            issue = "state_evidence_from_future"
                    except (KeyError, TypeError, ValueError):
                        issue = "state_evidence_time_missing"
            elif not parts or parts in (["agent_input"], ["agent_input", "user_turns"]):
                issue = "task_evidence_requires_specific_turn"
            elif parts[:2] == ["agent_input", "user_turns"]:
                offset = 1 if driver == "read" else 2 if driver in {"edit", "save_restart", "message_recovery", "message_uncertain"} else None
                turn = target.get("turn_id")
                if offset is None or type(turn) is not int or turn < offset or len(parts) < 3 or not re.fullmatch(r"0|[1-9][0-9]*", parts[2]):
                    issue = "task_evidence_turn_missing"
                elif int(parts[2]) > turn - offset:
                    issue = "task_evidence_from_future"
            if issue:
                issues.append(f"{issue}:{claim['claim_id']}")
    return list(dict.fromkeys(issues))


def import_reviews(bundle: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate actual per-case UI records; return pending until every case is reviewed."""
    sealed = seal_bundle(bundle)
    for field in ("dataset_sha", "product_sha", "collection_sha"):
        require(bundle.get(field) == sealed[field], f"bundle {field} mismatch or missing seal")
    for original, expected in zip(bundle["cases"], sealed["cases"]):
        require(original.get("gold_sha") == expected["gold_sha"] and original.get("packet_sha") == expected["packet_sha"], "case hash mismatch")
    annotations = _annotations(sealed)
    # Reuse existing attempt accounting before considering any human decisions.
    summarize(annotations)
    cases = {case["case_id"]: case for case in sealed["cases"]}
    require(isinstance(events, list), "human events must be a list")
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    event_ids = set()
    for event in events:
        require(isinstance(event, dict), "event must be an object")
        require(event.get("origin") == "human_ui" and not event.get("test_only") and not event.get("fixture"), "only actual human_ui records are accepted")
        require(isinstance(event.get("event_id"), str) and event["event_id"] and event["event_id"] not in event_ids, "unique event_id required")
        require(isinstance(event.get("reviewer"), str) and event["reviewer"].strip(), "named human reviewer required")
        require(event.get("case_id") in cases and event.get("stage") in ("gold", "output"), "review must name one declared case and stage")
        require(event.get("decision") in ("approve", "reject"), "explicit review decision required")
        timestamp = _time(event.get("reviewed_at"))
        key = (event["case_id"], event["stage"])
        previous = latest.get(key)
        require(previous is None or timestamp >= _time(previous["reviewed_at"]), "review log is not chronological within its case/stage")
        event_ids.add(event["event_id"])
        latest[key] = event
    pending, receipts = [], []
    gold_count = output_count = 0
    for case_id, case in cases.items():
        problems = []
        gold_event = latest.get((case_id, "gold"))
        output_event = latest.get((case_id, "output"))
        gold_ok = False
        if gold_event is None:
            problems.append("gold_review_missing")
        elif gold_event["decision"] != "approve":
            problems.append("gold_review_rejected")
        elif any(gold_event.get(key) != expected for key, expected in (
            ("dataset_sha", sealed["dataset_sha"]), ("product_sha", sealed["product_sha"]), ("gold_sha", case["gold_sha"]))):
            problems.append("gold_review_hash_mismatch")
        elif not _acknowledged(gold_event, required_acknowledgements(case, "gold")):
            problems.append("gold_items_not_all_reviewed")
        else:
            gold_ok = True
            gold_count += 1
        main = next((row for row in annotations["attempts"] if row["case_id"] == case_id and row["valid_attempt"]), None)
        if main is None:
            problems.append("valid_collection_attempt_missing")
        if output_event is None:
            problems.append("output_review_missing")
        elif output_event["decision"] != "approve":
            problems.append("output_review_rejected")
        elif any(output_event.get(key) != expected for key, expected in (
            ("dataset_sha", sealed["dataset_sha"]), ("product_sha", sealed["product_sha"]),
            ("collection_sha", sealed["collection_sha"]), ("packet_sha", case["packet_sha"]))):
            problems.append("output_review_hash_mismatch")
        elif not gold_ok or _time(output_event["reviewed_at"]) < _time(gold_event["reviewed_at"]):
            problems.append("gold_must_be_reviewed_before_output")
        elif main is not None:
            human_review = output_event.get("review")
            require(isinstance(human_review, dict), "output approval must include final per-item labels, not a global yes")
            if not _acknowledged(output_event, required_acknowledgements(case, "output", human_review)):
                problems.append("output_items_not_all_reviewed")
            prepared = judge.prepare_case(case["packet"])
            prepared["preparation_issues"].extend(case["packet"].get("preparation_issues", []))
            parsed = judge.parse_review(human_review, prepared)
            if parsed["issues"]:
                problems.extend(parsed["issues"])
            else:
                problems.extend(_future_evidence_issues(human_review, case["packet"]))
            if not problems:
                original = {field: main.get(field) for field in ("replacement_for", "invalid_reason")}
                main.update(parsed["scoring_attempt"], **original)
                require(main["trial_id"] == case["packet"]["trial_id"] and main["outcome"] == case["packet"]["attempt_outcome"], "reviewed attempt identity changed")
                output_count += 1
                receipts.append({"case_id": case_id, "trial_id": main["trial_id"], "gold_sha": case["gold_sha"], "packet_sha": case["packet_sha"],
                                 "gold_event_sha": canonical_sha(gold_event), "output_event_sha": canonical_sha(output_event),
                                 "gold_reviewer": gold_event["reviewer"], "output_reviewer": output_event["reviewer"],
                                 "gold_reviewed_at": gold_event["reviewed_at"], "output_reviewed_at": output_event["reviewed_at"]})
        if problems:
            pending.append({"case_id": case_id, "reasons": list(dict.fromkeys(problems))})
    annotations["human_verified"] = not pending
    annotations["gold_human_verified"] = gold_count == len(cases)
    scores = summarize(annotations)
    complete = not pending and scores["complete"]
    release = {"schema": SCHEMA, "scope": "controlled_acceptance", "dataset_sha": sealed["dataset_sha"],
               "product_sha": sealed["product_sha"], "product_revision": sealed["product"]["revision"],
               "collection_sha": sealed["collection_sha"], "bundle_sha": canonical_sha(bundle),
               "events_sha": canonical_sha(events), "annotations_sha": canonical_sha(annotations),
               "review_receipts": receipts, "gold_reviewed_cases": gold_count, "output_reviewed_cases": output_count,
               "reviewer_mode": "single_human" if len({row[field] for row in receipts for field in ("gold_reviewer", "output_reviewer")}) == 1 else "multiple_or_pending",
               "review_identity_scope": "Self-declared reviewer and actual local UI event records; no simulated or cryptographic signature claim",
               "generalization_scope": "Controlled acceptance tasks; not unseen websites or an independent external benchmark"}
    if complete:
        scores.update(report_kind="human_reviewed_controlled", release=release,
                      tsr_percent=100 * scores["tsr"],
                      groundedness_percent=100 * scores["groundedness_macro"] if scores["groundedness_macro"] is not None else None)
        return {"status": "complete", "report_kind": "human_reviewed_controlled", "final_percentages_available": True,
                "scores": scores, "annotations": annotations, "release": release, "pending": []}
    # Partial task counts are useful for progress; partial percentages are not the
    # requested final result, even when the generic scorer could compute a subset.
    return {"status": "pending", "report_kind": "controlled_human_review_pending", "final_percentages_available": False,
            "tsr_percent": None, "groundedness_percent": None, "pending": pending, "release": release,
            "counts": {key: scores[key] for key in ("planned", "attempted", "valid", "annotated", "pending_annotation")},
            "annotations": annotations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        bundle_raw, event_raw = args.bundle.read_bytes(), args.events.read_bytes()
        bundle = json.loads(bundle_raw, object_pairs_hook=unique_json_keys)
        events = [json.loads(line, object_pairs_hook=unique_json_keys) for line in event_raw.splitlines() if line.strip()]
        result = import_reviews(bundle, events)
        result["input_files_sha256"] = {"bundle": hashlib.sha256(bundle_raw).hexdigest(), "events": hashlib.sha256(event_raw).hexdigest()}
        judge._write(args.output, result)
        print(json.dumps({"status": result["status"], "report_kind": result["report_kind"],
                          "final_percentages_available": result["final_percentages_available"], "pending_cases": len(result["pending"])}))
    except Exception as error:
        parser.exit(2, f"quality_human_import: {type(error).__name__}; existing output was not overwritten\n")


if __name__ == "__main__":
    main()
