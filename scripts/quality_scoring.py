#!/usr/bin/env python3
"""Aggregate independent annotations; never call the product or infer its success.

Input: schema_version=1, dataset/system/environment metadata, annotation provenance,
planned_cases, and attempts. A planned case declares case_id, case_class, family,
group_ids, must_pass IDs and forbidden IDs. Each valid attempt supplies booleans
for those IDs, side_effects_absent, false_completion_absent, annotation_complete,
and atomic claims. True in forbidden_checks means the prohibition was respected.

Labels are human/AI annotations, not verified here by a semantic judge. The report
preserves that provenance. It cannot certify human review or unseen test material.
Use --input annotations.json --output new-report.json; existing files are refused.
"""

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

FACT_LABELS = {"supported", "contradicted", "unsupported", "conflicting", "unverifiable"}
OUTCOMES = {"completed", "product_failure", "timeout", "external_blocked", "budget_exhausted"}
INVALID_REASONS = {"runner_error", "fixture_missing", "gold_error"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def unique_strings(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    require(isinstance(value, list), f"{name} must be a list")
    require(all(nonempty(item) for item in value), f"{name} must contain nonempty strings")
    require(len(value) == len(set(value)), f"{name} contains duplicate IDs")
    require(allow_empty or bool(value), f"{name} cannot be empty")
    return value


def bool_field(row: dict[str, Any], name: str) -> bool:
    require(type(row.get(name)) is bool, f"{name} must be an explicit boolean")
    return row[name]


def wilson(successes: int, total: int) -> list[float] | None:
    """95% Wilson interval over task counts, never claim counts or repetitions."""
    if not total:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total**2)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def score_claims(claims: Any) -> dict[str, Any]:
    require(isinstance(claims, list), "claims must be an explicit list; no facts means []")
    seen: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for claim in claims:
        require(isinstance(claim, dict), "claim must be an object")
        claim_id = claim.get("claim_id")
        require(nonempty(claim_id) and claim_id not in seen, "claim_id must be nonempty and unique")
        label = claim.get("label")
        require(nonempty(label) and label in FACT_LABELS | {"non_factual", "duplicate"}, "Unknown claim label")
        require(nonempty(claim.get("reason")), f"{claim_id}: annotation/exclusion reason required")
        if label != "non_factual":
            for field in ("canonical_claim", "entity_id", "temporal_scope"):
                require(nonempty(claim.get(field)), f"{claim_id}: {field} required")
        if label == "supported":
            unique_strings(claim.get("evidence_refs"), f"{claim_id}.evidence_refs")
        if label == "duplicate":
            duplicate_of = claim.get("duplicate_of")
            require(nonempty(duplicate_of), f"{claim_id}: duplicate_of must name an earlier claim")
            original = seen.get(duplicate_of)
            require(original is not None, f"{claim_id}: duplicate_of must reference an earlier claim")
            require(original["label"] in FACT_LABELS, f"{claim_id}: duplicate must reference a factual original")
            for field in ("canonical_claim", "entity_id", "temporal_scope"):
                require(claim[field] == original[field], f"{claim_id}: different {field} cannot be deduplicated")
        else:
            require(claim.get("duplicate_of") is None, f"{claim_id}: duplicate_of requires duplicate label")
        seen[claim_id] = claim
        counts[label] += 1
    factual = sum(counts[label] for label in FACT_LABELS)
    return {
        "supported": counts["supported"],
        "factual_claims": factual,
        "groundedness": counts["supported"] / factual if factual else None,
        "labels": {label: counts[label] for label in sorted(FACT_LABELS | {"non_factual", "duplicate"})},
    }


def score_attempt(attempt: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    completed = bool_field(attempt, "annotation_complete")
    if not completed:
        return {"annotation_complete": False, "task_success": None, "claims": None}
    failed = []
    for field, expected in (("checks", case["must_pass"]), ("forbidden_checks", case["forbidden"])):
        actual = attempt.get(field)
        require(isinstance(actual, dict) and set(actual) == set(expected), f"{case['case_id']}: {field} must cover exactly the predeclared IDs")
        for check_id in expected:
            if not bool_field(actual, check_id):
                failed.append(f"{field}:{check_id}")
    for field in ("side_effects_absent", "false_completion_absent"):
        if not bool_field(attempt, field):
            failed.append(field)
    if attempt["outcome"] != "completed":
        failed.append(f"outcome:{attempt['outcome']}")
    return {
        "annotation_complete": True,
        "task_success": int(not failed),
        "failed_checks": failed,
        "claims": score_claims(attempt.get("claims")),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row["valid_attempt"]]
    annotated = [row for row in valid if row["annotation_complete"]]
    pending = len(valid) - len(annotated)
    successes = sum(row["task_success"] for row in annotated)
    with_facts = [row for row in annotated if row["claims"]["factual_claims"]]
    return {
        "planned": len(rows),
        "attempted": sum(bool(row["trial_ids"]) for row in rows),
        "valid": len(valid),
        "annotated": len(annotated),
        "pending_annotation": pending,
        "successes": successes,
        "failures": len(annotated) - successes,
        "tsr": successes / len(valid) if valid and not pending else None,
        "tsr_wilson_95": wilson(successes, len(valid)) if not pending else None,
        "groundedness_macro": sum(row["claims"]["groundedness"] for row in with_facts) / len(with_facts) if with_facts and not pending else None,
        "groundedness_tasks": len(with_facts),
        "groundedness_na_tasks": len(annotated) - len(with_facts),
        "supported_claims": sum(row["claims"]["supported"] for row in annotated),
        "factual_claims": sum(row["claims"]["factual_claims"] for row in annotated),
        "complete": bool(rows) and len(valid) == len(rows) and not pending,
    }


def summarize(document: dict[str, Any]) -> dict[str, Any]:
    require(isinstance(document, dict) and type(document.get("schema_version")) is int and document["schema_version"] == 1, "schema_version must be 1")
    for field in ("dataset_version", "system_revision", "environment_level"):
        require(nonempty(document.get(field)), f"{field} required")
    origin = document.get("annotations_origin")
    require(nonempty(origin) and origin in {"human", "ai_assisted", "automated"}, "annotations_origin must be human, ai_assisted, or automated")
    human = bool_field(document, "human_verified")
    gold_human = bool_field(document, "gold_human_verified")
    # Current materials are public development drafts, never an unseen/formal score.
    require(document.get("report_kind", "provisional_dev") == "provisional_dev", "This dev scorer only emits provisional_dev; formal_score requires a separately reviewed release protocol")
    planned = document.get("planned_cases")
    require(isinstance(planned, list) and bool(planned), "planned_cases must be a nonempty list")
    cases = {}
    groups: Counter[str] = Counter()
    for case in planned:
        require(isinstance(case, dict), "planned case must be an object")
        case_id = case.get("case_id")
        require(nonempty(case_id) and case_id not in cases, "planned case_id must be nonempty and unique")
        require(nonempty(case.get("case_class")) and case["case_class"] in {"delivery", "bounded_answer"}, f"{case_id}: unknown case_class")
        require(nonempty(case.get("family")), f"{case_id}: family required")
        groups.update(unique_strings(case.get("group_ids"), f"{case_id}.group_ids"))
        unique_strings(case.get("must_pass"), f"{case_id}.must_pass")
        unique_strings(case.get("forbidden"), f"{case_id}.forbidden", allow_empty=True)
        cases[case_id] = case
    attempts = document.get("attempts")
    require(isinstance(attempts, list), "attempts must be a list")
    by_trial: dict[str, dict[str, Any]] = {}
    by_case: dict[str, list[dict[str, Any]]] = {case_id: [] for case_id in cases}
    invalid = []
    for attempt in attempts:
        require(isinstance(attempt, dict), "attempt must be an object")
        case_id, trial_id = attempt.get("case_id"), attempt.get("trial_id")
        require(nonempty(case_id) and case_id in cases, "Unplanned case")
        require(nonempty(trial_id) and trial_id not in by_trial, "trial_id must be nonempty and unique")
        valid = bool_field(attempt, "valid_attempt")
        previous = by_case[case_id]
        if previous:
            prior = previous[-1]
            require(not prior["valid_attempt"], f"{case_id}: cannot replace a valid main attempt or select the best retry")
            require(attempt.get("replacement_for") == prior["trial_id"], f"{case_id}: replacement_for must reference the immediately preceding invalid attempt")
        else:
            require(attempt.get("replacement_for") is None, f"{case_id}: first attempt cannot replace another case")
        if valid:
            require(attempt.get("invalid_reason") is None, f"{trial_id}: valid attempt cannot have invalid_reason")
            require(nonempty(attempt.get("outcome")) and attempt["outcome"] in OUTCOMES, f"{trial_id}: valid attempt requires an explicit outcome")
        else:
            reason = attempt.get("invalid_reason")
            require(isinstance(reason, dict) and nonempty(reason.get("category")) and reason["category"] in INVALID_REASONS, f"{trial_id}: only evaluator defects may be invalid")
            require(nonempty(reason.get("detail")) and nonempty(reason.get("adjudicator")), f"{trial_id}: invalidation requires detail and adjudicator")
            require(attempt.get("outcome") is None or isinstance(attempt["outcome"], str), f"{trial_id}: outcome must be a string")
            require(attempt.get("outcome") not in {"timeout", "external_blocked", "budget_exhausted", "product_failure"}, f"{trial_id}: product/external failures are valid attempts")
            invalid.append({"case_id": case_id, "trial_id": trial_id, "reason": reason, "replacement_for": attempt.get("replacement_for")})
        by_trial[trial_id] = attempt
        previous.append(attempt)
    rows = []
    for case_id, case in cases.items():
        history = by_case[case_id]
        latest = history[-1] if history else None
        valid = latest is not None and latest["valid_attempt"]
        row = {
            "case_id": case_id, "case_class": case["case_class"], "family": case["family"],
            "group_ids": case["group_ids"], "trial_ids": [attempt["trial_id"] for attempt in history],
            "main_trial_id": latest["trial_id"] if valid else None, "valid_attempt": valid,
            "outcome": latest.get("outcome") if latest else "not_run",
            "annotation_complete": False, "task_success": None, "claims": None,
        }
        if valid:
            row.update(score_attempt(latest, case))
        rows.append(row)
    summary = aggregate(rows)
    unresolved = [row["case_id"] for row in rows if row["trial_ids"] and not row["valid_attempt"]]
    related = sorted(group_id for group_id, count in groups.items() if count > 1)
    report = {
        "schema_version": 1, "report_kind": "provisional_dev",
        **{field: document[field] for field in ("dataset_version", "system_revision", "environment_level", "annotations_origin")},
        "human_verified": human, "gold_human_verified": gold_human,
        "provenance_notice": "Annotation provenance is declared by the input; aggregation does not independently certify human review or factual entailment.",
        **summary,
        "attempt_records": len(attempts), "invalid_attempts": invalid,
        "unresolved_invalid_cases": unresolved,
        "not_run_cases": [row["case_id"] for row in rows if not row["trial_ids"]],
        "completeness": "complete" if summary["complete"] else "incomplete",
        "score_scope": "valid_primary_tasks; incomplete batches are partial results, not the planned-batch score",
        "confidence_interval_scope": "Wilson 95% uses valid primary task counts only; descriptive, not an independence guarantee. No claim-level CI or bootstrap is inferred.",
        "shared_source_groups": related,
        "source_correlation_notice": "Shared source/template groups make this task-level Wilson interval descriptive only." if related else "No shared registered groups detected; this does not prove independence or population representativeness.",
        "by_class": {value: aggregate([row for row in rows if row["case_class"] == value]) for value in ("delivery", "bounded_answer")},
        "by_family": {value: aggregate([row for row in rows if row["family"] == value]) for value in sorted({case["family"] for case in cases.values()})},
        "cases": rows,
    }
    return report


def unique_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        raw = args.input.read_bytes()
        result = summarize(json.loads(raw, object_pairs_hook=unique_json_keys))
        result["annotations_sha256"] = hashlib.sha256(raw).hexdigest()
        # Exclusive creation also refuses symlinks and input/output aliases.
        with args.output.open("x", encoding="utf-8") as destination:
            json.dump(result, destination, ensure_ascii=False, indent=2, allow_nan=False)
            destination.write("\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"quality_scoring: {exc}\n")


if __name__ == "__main__":
    main()
