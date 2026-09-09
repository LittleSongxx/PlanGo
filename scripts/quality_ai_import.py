#!/usr/bin/env python3
"""Aggregate a separate AI context's controlled-30 reviews, never human signatures.

Gold is the actor admission gate. Complete, hash-bound output reviews are the
score gate. No model/provider/browser/service is invoked by this module.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import quality_human_import as human
import quality_judge as judge
from quality_scoring import summarize, unique_json_keys

SCOPE = "30 controlled acceptance cases; independent-context AI review; not human-calibrated, unseen-website generalization, an independent model family, or a third-party benchmark"


def _bundle_issues(bundle: dict[str, Any]) -> list[str]:
    try:
        sealed = human.seal_bundle(bundle)
        if len(sealed["cases"]) != 30:
            return ["bundle:expected_30_planned_cases"]
        if any(bundle.get(key) != sealed[key] for key in ("dataset_sha", "product_sha", "collection_sha")):
            return ["bundle:hash_mismatch"]
        if any(old.get("gold_sha") != new["gold_sha"] or old.get("packet_sha") != new["packet_sha"]
               for old, new in zip(bundle["cases"], sealed["cases"])):
            return ["bundle:case_hash_mismatch"]
        summarize(human._annotations(bundle))  # Existing first-attempt/invalid-chain checks.
    except (KeyError, TypeError, ValueError):
        return ["bundle:invalid_or_changed"]
    return []


def _header_issues(bundle: dict[str, Any], review: Any, stage: str) -> list[str]:
    if not isinstance(review, dict):
        return [stage + ":review_missing"]
    issues = []
    required = {"schema_version": 1, "origin": "independent_ai", "reviewer_type": "AI", "human_reviewed": False,
                "dataset_sha": bundle["dataset_sha"], "product_sha": bundle["product_sha"]}
    if stage == "output":
        required["collection_sha"] = bundle["collection_sha"]
    for key, expected in required.items():
        if type(review.get(key)) is not type(expected) or review[key] != expected:
            issues.append(f"{stage}:invalid_{key}")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        issues.append(stage + ":reviewer_missing")
    if review.get("decision", "approve") != "approve":
        issues.append(stage + ":review_rejected")
    if review.get("human_verified") not in (None, False) or review.get("gold_human_verified") not in (None, False):
        issues.append(stage + ":inconsistent_human_claim")
    return issues


def _case_rows(bundle: dict[str, Any], review: dict[str, Any], stage: str) -> tuple[dict[str, Any], list[str]]:
    rows = review.get("cases")
    if not isinstance(rows, list):
        return {}, [stage + ":cases_missing"]
    actual, issues = {}, []
    planned = set(bundle["dataset"]["planned_case_ids"])
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            issues.append(stage + ":malformed_case")
            continue
        case_id = row["case_id"]
        if case_id not in planned:
            issues.append(f"{stage}:unplanned_case:{case_id}")
        elif case_id in actual:
            issues.append(f"{stage}:duplicate_case:{case_id}")
        else:
            actual[case_id] = row
    issues.extend(f"{stage}:missing_case:{case_id}" for case_id in sorted(planned - actual.keys()))
    return actual, issues


def validate_gold(bundle: dict[str, Any], review: dict[str, Any]) -> list[str]:
    """Empty issues means all 30 gold/source cases were approved by the AI reviewer."""
    issues = _bundle_issues(bundle)
    if issues:
        return issues
    issues = _header_issues(bundle, review, "gold")
    if not isinstance(review, dict):
        return issues
    rows, coverage = _case_rows(bundle, review, "gold")
    issues.extend(coverage)
    for case in bundle["cases"]:
        case_id = case["case_id"]
        row = rows.get(case_id)
        if row is None:
            continue
        if row.get("gold_sha") != case["gold_sha"]:
            issues.append(f"gold:hash_mismatch:{case_id}")
        if row.get("decision") != "approve":
            issues.append(f"gold:not_approved:{case_id}")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            issues.append(f"gold:reason_missing:{case_id}")
        try:
            complete = human._acknowledged(row, human.required_acknowledgements(case, "gold"))
        except (KeyError, TypeError, ValueError):
            complete = False
        if not complete:
            issues.append(f"gold:items_not_all_reviewed:{case_id}")
    return list(dict.fromkeys(issues))


def import_reviews(bundle: dict[str, Any], gold_review: dict[str, Any],
                   output_review: dict[str, Any]) -> dict[str, Any]:
    """Reuse the math and evidence gates directly; do not manufacture human events."""
    issues = _bundle_issues(bundle)
    if issues:
        return {"status": "pending", "final_percentages_available": False, "issues": issues,
                "report_kind": "independent_ai_review_pending", "reviewer_type": "AI",
                "human_verified": False, "gold_human_verified": False}
    annotations = human._annotations(bundle)
    annotations.update(annotations_origin="ai_assisted", environment_level="controlled_acceptance_independent_ai_review",
                       human_verified=False, gold_human_verified=False)
    gold_issues = validate_gold(bundle, gold_review)
    issues.extend(gold_issues)
    header = _header_issues(bundle, output_review, "output")
    issues.extend(header)
    rows, coverage = _case_rows(bundle, output_review, "output") if isinstance(output_review, dict) else ({}, [])
    issues.extend(coverage)
    if not gold_issues and not header and gold_review["reviewer"] != output_review["reviewer"]:
        header.append("output:reviewer_differs_from_gold")
        issues.append(header[-1])
    receipts = []
    if not gold_issues and not header:
        gold_rows = {row["case_id"]: row for row in gold_review["cases"]}
        for case in bundle["cases"]:
            case_id = case["case_id"]
            row = rows.get(case_id)
            if row is None:
                continue
            if row.get("packet_sha") != case["packet_sha"]:
                issues.append(f"output:packet_hash_mismatch:{case_id}")
                continue
            if row.get("decision", "approve") != "approve":
                issues.append(f"output:review_rejected:{case_id}")
                continue
            main = next((item for item in annotations["attempts"] if item["case_id"] == case_id and item["valid_attempt"]), None)
            if main is None:
                issues.append(f"output:valid_attempt_missing:{case_id}")
                continue
            try:
                prepared = judge.prepare_case(case["packet"])
                prepared["preparation_issues"].extend(case["packet"].get("preparation_issues", []))
                parsed = judge.parse_review(row.get("review"), prepared)
                problems = parsed["issues"] or human._future_evidence_issues(row["review"], case["packet"])
            except (KeyError, TypeError, ValueError, AttributeError):
                issues.append(f"output:invalid_annotation:{case_id}")
                continue
            if problems:
                issues.extend(f"output:{case_id}:{problem}" for problem in problems)
                continue
            preserved = {key: copy.deepcopy(main.get(key)) for key in ("replacement_for", "invalid_reason")}
            main.update(parsed["scoring_attempt"], **preserved)
            receipts.append({"case_id": case_id, "trial_id": main["trial_id"], "gold_sha": case["gold_sha"], "packet_sha": case["packet_sha"],
                             "gold_case_review_sha": human.canonical_sha(gold_rows[case_id]),
                             "output_case_review_sha": human.canonical_sha(row)})
    scores = summarize(annotations)
    complete = not issues and scores["complete"]
    release = {"schema": "plango.independent-ai-controlled-review.v1", "scope": SCOPE,
               "dataset_sha": bundle["dataset_sha"], "product_sha": bundle["product_sha"], "collection_sha": bundle["collection_sha"],
               "bundle_sha": human.canonical_sha(bundle), "gold_review_sha": human.canonical_sha(gold_review),
               "output_review_sha": human.canonical_sha(output_review), "annotations_sha": human.canonical_sha(annotations),
               "review_receipts": receipts, "reviewer_type": "AI", "reviewer": gold_review.get("reviewer") if isinstance(gold_review, dict) else None,
               "human_verified": False, "gold_human_verified": False, "human_calibrated": False,
               "context_provenance": "Reviewer context isolation is established by the orchestration record, not inferred from hashes or model agreement"}
    if complete:
        scores.update(report_kind="independent_ai_reviewed_controlled", reviewer_type="AI", scope=SCOPE, release=release,
                      tsr_percent=100 * scores["tsr"],
                      groundedness_percent=100 * scores["groundedness_macro"] if scores["groundedness_macro"] is not None else None)
        return {"status": "complete", "report_kind": "independent_ai_reviewed_controlled", "reviewer_type": "AI",
                "final_percentages_available": True, "human_verified": False, "gold_human_verified": False,
                "scores": scores, "annotations": annotations, "release": release, "issues": []}
    return {"status": "pending", "report_kind": "independent_ai_review_pending", "reviewer_type": "AI",
            "final_percentages_available": False, "human_verified": False, "gold_human_verified": False,
            "tsr_percent": None, "groundedness_percent": None, "issues": list(dict.fromkeys(issues)),
            "counts": {key: scores[key] for key in ("planned", "attempted", "valid", "annotated", "pending_annotation")},
            "annotations": annotations, "release": release}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "gold-review", "output-review", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        paths = {"bundle": args.bundle, "gold_review": args.gold_review, "output_review": args.output_review}
        raw = {key: path.read_bytes() for key, path in paths.items()}
        values = {key: json.loads(value, object_pairs_hook=unique_json_keys) for key, value in raw.items()}
        result = import_reviews(**values)
        result["input_files_sha256"] = {key: hashlib.sha256(value).hexdigest() for key, value in raw.items()}
        judge._write(args.output, result)
        print(json.dumps({"status": result["status"], "report_kind": result["report_kind"], "reviewer_type": "AI",
                          "final_percentages_available": result["final_percentages_available"], "issues": len(result["issues"])}))
    except Exception as error:
        parser.exit(2, f"quality_ai_import: {type(error).__name__}; existing output was not overwritten\n")


if __name__ == "__main__":
    main()
