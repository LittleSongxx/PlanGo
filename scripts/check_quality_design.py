#!/usr/bin/env python3
"""Validate draft evaluation materials only. No model, browser, service, or scoring run."""

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "eval/quality-v1"


def read(name):
    return json.loads((DIRECTORY / name).read_text())


def main():
    tasks, gold = read("tasks.dev.json"), read("gold.dev.json")
    plan, registry = read("split-plan.json"), read("sources.dev.json")
    ids = [task["case_id"] for task in tasks]
    assert len(ids) == len(set(ids)) == plan["dev_cases_expected"] == 12
    assert set(ids) == {item["case_id"] for item in gold}
    assert len(gold) == len(tasks)
    tasks_by_id = {task["case_id"]: task for task in tasks}
    sources = {item["source_id"]: item for item in registry["sources"]}
    assert len(sources) == len(registry["sources"])
    families = Counter(task["family"] for task in tasks)
    assert families == Counter({name: row["dev"] for name, row in plan["family_quotas"].items()})
    assert sum(row["holdout"] for row in plan["family_quotas"].values()) == plan["holdout_cases_planned"]
    for row in plan["family_quotas"].values():
        assert row["delivery"] + row["bounded_answer"] == row["holdout"]
    for task in tasks:
        assert task["status"] == "draft_pending_human_review"
        assert task["case_class"] in {"delivery", "bounded_answer"}
        assert task["scope"] and task["readiness_blockers"] and task["group_ids"]
        assert len(task["group_ids"]) == len(set(task["group_ids"]))
        if task["as_of"] is not None:
            assert datetime.fromisoformat(task["as_of"]).utcoffset() is not None
        assert set(task["agent_input"]) == {"user_turns"}, "Do not expose environment/oracle instructions as model input"
        assert task["agent_input"]["user_turns"] or task["environment"].get("driver_actions")
        assert set(task["source_ids"]) <= sources.keys()
        assert task["environment"]["source_ids"] == task["source_ids"]
        for turn in task["agent_input"]["user_turns"]:
            assert isinstance(turn["message"], str) and turn["message"].strip()
    for row in gold:
        assert row["status"] == "draft_pending_human_review" and row["annotation_status"] == "ai_authored_draft"
        assert row["human_reviewers"] == [] and row["contamination_note"]
        checks = row["must_pass"]
        assert checks and len({item["id"] for item in checks}) == len(checks)
        assert all(item["description"] and item["check_at"] and item["oracle_source"] for item in checks)
        assert row["forbidden_claims"] and row["accepted_variations"]
        assert len({fact["id"] for fact in row["gold_facts"]}) == len(row["gold_facts"])
        for fact in row["gold_facts"]:
            assert fact["source_id"] in tasks_by_id[row["case_id"]]["source_ids"] and fact["proposition"] and fact["locator"]
    verified, missing = [], []
    for source in sources.values():
        if "private_raw_path" not in source:
            continue
        path = (ROOT / source["private_raw_path"]).resolve()
        assert path.is_relative_to(ROOT / "output"), "Private sources must be inside this repository's output"
        if not path.is_file():
            missing.append(source["source_id"])
            continue
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["whole_file_sha256"], f"Source changed: {source['source_id']}"
        value = json.loads(raw)
        for part in source["json_pointer"].strip("/").split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(encoded).hexdigest() == source["extracted_value_sha256"]
        verified.append(source["source_id"])
    assert plan["holdout_cases_present"] == 0 and plan["live_sentinels_run"] == 0 and plan["evaluation_ready"] is False
    print(json.dumps({"design_valid": True, "dev_case_drafts": len(tasks), "gold_drafts": len(gold),
                      "families": dict(families), "verified_raw_source_hashes": verified, "private_sources_missing": missing,
                      "human_reviewed_gold": 0, "held_out_cases": 0, "evaluation_ready": False,
                      "model_evaluations_run": 0, "quality_scores_produced": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
