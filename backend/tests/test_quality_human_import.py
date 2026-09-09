"""Protocol fixtures only: human-event examples stay in memory/temporary directories."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/quality_human_import.py"
sys.path.insert(0, str(SCRIPT.parent))
import quality_human_import as human  # noqa: E402

sys.path.pop(0)


def case(case_id, *, extra_bad_fact=False, failed=False):
    content = "券售价47元。" + ("免费领取。" if extra_bad_fact else "")
    return {"case_id": case_id, "case_class": "delivery", "family": "offline_protocol_fixture", "group_ids": [case_id],
            "packet": {"case_id": case_id, "trial_id": case_id + ":first", "task": {"agent_input": {"user_turns": [{"message": "核对售价"}]}},
                       "gold": {"must_pass": [{"id": "R1", "description": "正确交付售价"}], "forbidden_claims": ["已预约"]},
                       "source_packets": [{"source_id": "RAW", "content": {"text": "券售价47元"}, "observed_at": "2026-09-10T00:00:00+00:00",
                                           "available_at_checkpoint_ids": ["final"]}],
                       "outputs": [{"schema": "plango.quality-output.v1", "checkpoint": {"id": "final"},
                                    "coverage": {"history_coverage_partial": False}, "messages": [{"id": "answer", "content": content}], "cards": []}],
                       "independent_state": {"stored": True}, "transport_checks": {"business_writes": 0},
                       "all_delivery_checkpoints_captured": True, "attempt_outcome": "product_failure" if failed else "completed"}}


def bundle():
    cases = [case("TEST-A", extra_bad_fact=True), case("TEST-B", failed=True)]
    return human.seal_bundle({"schema_version": 1, "dataset": {"name": "offline importer unit-test fixture", "scope": "controlled_acceptance",
                                                              "planned_case_ids": [row["case_id"] for row in cases]},
                              "product": {"revision": "a" * 40, "files": {"test-only.py": "b" * 64}, "model_config": {"model": "offline-fixture"}},
                              "cases": cases, "collection": {"attempts": [{"case_id": row["case_id"], "trial_id": row["packet"]["trial_id"],
                                                                          "valid_attempt": True, "outcome": row["packet"]["attempt_outcome"]} for row in cases]}})


def review(row):
    source = {"ref_id": "source:RAW", "pointer": "/text", "quote": "券售价47元"}
    output = {"ref_id": "output:final", "pointer": "/messages/0/content", "quote": "券售价47元"}
    check = {"verdict": "pass", "reason": "offline annotation fixture", "evidence": [source, output]}
    first = {"claim_id": "C1", "canonical_claim": "券售价47元", "entity_id": "TEST-OFFER", "temporal_scope": "fixture historical scope",
             "label": "supported", "reason": "offline annotation fixture", "output": output, "evidence": [source],
             "support_checks": {name: "pass" for name in human.judge.SUPPORT_CHECKS}}
    claims = [first]
    if "免费" in row["packet"]["outputs"][0]["messages"][0]["content"]:
        claims.append({**copy.deepcopy(first), "claim_id": "C2", "canonical_claim": "可以免费领取", "label": "unsupported",
                       "output": {**output, "quote": "免费领取"}, "evidence": [], "reason": "fixture source does not support free redemption"})
    return {"checks": {"R1": {**check, "verdict": "fail" if row["packet"]["attempt_outcome"] != "completed" else "pass"}},
            "forbidden_checks": {"F1": copy.deepcopy(check)}, "false_completion_absent": copy.deepcopy(check),
            "side_effects_absent": {"verdict": "pass", "reason": "no recorded writes", "evidence": [
                {"ref_id": "transport_checks", "pointer": "/business_writes", "quote": "0"}]},
            "coverage_complete": True, "coverage_notes": "offline fixture",
            "claims": claims, "surface_coverage": [{"ref_id": "output:final", "pointer": "/messages/0/content", "claim_ids": [claim["claim_id"] for claim in claims]}]}


def events_for(value):
    result = []
    for row in value["cases"]:
        base = {"origin": "human_ui", "reviewer": "offline-test-reviewer", "case_id": row["case_id"], "decision": "approve",
                "dataset_sha": value["dataset_sha"], "product_sha": value["product_sha"]}
        result.append({**base, "event_id": row["case_id"] + ":gold", "stage": "gold", "reviewed_at": "2026-09-10T01:00:00+00:00",
                       "gold_sha": row["gold_sha"], **human.required_acknowledgements(row, "gold")})
        labels = review(row)
        result.append({**base, "event_id": row["case_id"] + ":output", "stage": "output", "reviewed_at": "2026-09-10T02:00:00+00:00",
                       "collection_sha": value["collection_sha"], "packet_sha": row["packet_sha"], "review": labels,
                       **human.required_acknowledgements(row, "output", labels)})
    return result


def reseal_fixture(value):
    """Build a new temporary fixture version, never migrate actual human records."""
    value = copy.deepcopy(value)
    for field in ("dataset_sha", "product_sha", "planned_case_ids"):
        value["collection"].pop(field, None)
    for attempt in value["collection"]["attempts"]:
        attempt.pop("packet_sha", None)
    return human.seal_bundle(value)


class HumanImportTests(unittest.TestCase):
    def test_later_state_capture_cannot_support_earlier_output_and_missing_time_is_pending(self):
        original = bundle()
        packet = original["cases"][0]["packet"]
        packet["outputs"][0]["checkpoint"]["captured_at"] = "2026-09-10T02:00:00+00:00"
        packet["independent_state"]["checkpoints"] = {"t2": {"captured_at": "2026-09-10T02:01:00+00:00", "price": 47}}
        for scenario, expected in (("future", "state_evidence_from_future:C1"),
                                   ("missing_state", "state_evidence_time_missing:C1"),
                                   ("missing_output", "state_evidence_time_missing:C1"),
                                   ("broad_pointer", "state_evidence_requires_specific_checkpoint:C1"),
                                   ("same_time", None)):
            with self.subTest(scenario=scenario):
                value = copy.deepcopy(original)
                target = value["cases"][0]["packet"]
                if scenario == "missing_state":
                    target["independent_state"]["checkpoints"]["t2"].pop("captured_at")
                elif scenario == "missing_output":
                    target["outputs"][0]["checkpoint"].pop("captured_at")
                elif scenario == "same_time":
                    target["independent_state"]["checkpoints"]["t2"]["captured_at"] = target["outputs"][0]["checkpoint"]["captured_at"]
                value = reseal_fixture(value)
                events = events_for(value)
                pointer = "/checkpoints" if scenario == "broad_pointer" else "/checkpoints/t2/price"
                events[1]["review"]["claims"][0]["evidence"] = [{"ref_id": "independent_state", "pointer": pointer, "quote": "47"}]
                result = human.import_reviews(value, events)
                if expected:
                    self.assertFalse(result["final_percentages_available"])
                    self.assertIn(expected, result["pending"][0]["reasons"])
                else:
                    self.assertTrue(result["final_percentages_available"])

    def test_future_user_turn_is_blocked_with_the_correct_imported_turn_offset(self):
        for driver, before_turn, after_turn in (("read", 1, 2), ("edit", 2, 3), ("message_recovery", 2, 3)):
            for active_turn in (before_turn, after_turn, None):
                with self.subTest(driver=driver, active_turn=active_turn):
                    value = bundle()
                    packet = value["cases"][0]["packet"]
                    packet["task"]["environment"] = {"driver": driver}
                    packet["task"]["agent_input"]["user_turns"] = [{"message": "核对信息"}, {"message": "券售价47元"}]
                    packet["outputs"][0]["checkpoint"]["turn_id"] = active_turn
                    packet["outputs"][0]["messages"][0]["content"] = "用户提供的" + packet["outputs"][0]["messages"][0]["content"]
                    value = reseal_fixture(value)
                    events = events_for(value)
                    claim = events[1]["review"]["claims"][0]
                    claim.update(canonical_claim="用户提供的券售价为47元", entity_id="test-user")
                    claim["output"]["quote"] = "用户提供的券售价47元"
                    claim["evidence"] = [{"ref_id": "task", "pointer": "/agent_input/user_turns/1/message", "quote": "券售价47元"}]
                    result = human.import_reviews(value, events)
                    if active_turn == after_turn:
                        self.assertTrue(result["final_percentages_available"])
                    else:
                        reason = "task_evidence_turn_missing:C1" if active_turn is None else "task_evidence_from_future:C1"
                        self.assertFalse(result["final_percentages_available"])
                        self.assertIn(reason, result["pending"][0]["reasons"])

    def test_all_case_records_required_before_percentages_are_released(self):
        value = bundle()
        events = events_for(value)
        self.assertFalse(human.import_reviews(value, events[:-1])["final_percentages_available"])
        value["cases"][1]["proposed_annotation"] = review(value["cases"][1])
        self.assertFalse(human.import_reviews(value, events[:-1])["final_percentages_available"])
        result = human.import_reviews(value, events)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["scores"]["tsr_percent"], 50)
        self.assertEqual(result["scores"]["groundedness_percent"], 75)
        self.assertEqual(result["scores"]["groundedness_tasks"], 2)
        self.assertEqual(result["scores"]["factual_claims"], 3)
        self.assertTrue(result["scores"]["human_verified"])
        self.assertEqual(len(result["release"]["review_receipts"]), 2)
        self.assertEqual(result["release"]["reviewer_mode"], "single_human")

    def test_gold_can_be_signed_before_outputs_and_keeps_its_static_source_binding(self):
        original = bundle()
        before = copy.deepcopy(original)
        before["collection"]["attempts"] = []
        for row in before["cases"]:
            row["packet"].update(outputs=[], independent_state={}, transport_checks={}, all_delivery_checkpoints_captured=False)
            row["packet"]["source_packets"][0]["available_at_checkpoint_ids"] = []
        before = human.seal_bundle(before)
        self.assertEqual(before["dataset_sha"], original["dataset_sha"])
        self.assertEqual(before["cases"][0]["gold_sha"], original["cases"][0]["gold_sha"])
        self.assertNotEqual(before["cases"][0]["packet_sha"], original["cases"][0]["packet_sha"])
        gold_events = [event for event in events_for(original) if event["stage"] == "gold"]
        result = human.import_reviews(before, gold_events)
        self.assertEqual(result["release"]["gold_reviewed_cases"], 2)
        self.assertFalse(result["final_percentages_available"])
        self.assertEqual(result["counts"]["valid"], 0)
        changed = copy.deepcopy(original)
        changed["cases"][0]["packet"]["source_packets"][0]["content"]["text"] = "changed raw source"
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            human.import_reviews(changed, events_for(original))
        changed = copy.deepcopy(original)
        changed["product"]["model_config"]["model"] = "different-model"
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            human.import_reviews(changed, events_for(original))

    def test_global_ai_or_partial_approvals_never_substitute_for_item_reviews(self):
        value = bundle()
        self.assertFalse(human.import_reviews(value, [])["final_percentages_available"])
        for modification in ({"origin": "ai_assisted"}, {"fixture": True}, {"case_id": "all"}):
            events = events_for(value)
            events[0].update(modification)
            with self.assertRaises(ValueError):
                human.import_reviews(value, events)
        events = events_for(value)
        events[1]["reviewed_guard_ids"] = []
        result = human.import_reviews(value, events)
        self.assertFalse(result["final_percentages_available"])
        self.assertIn("output_items_not_all_reviewed", result["pending"][0]["reasons"])
        events = events_for(value)
        events[1]["packet_sha"] = "0" * 64
        self.assertIn("output_review_hash_mismatch", human.import_reviews(value, events)["pending"][0]["reasons"])
        events = events_for(value)
        events[1]["reviewed_at"] = "2026-09-09T23:00:00+00:00"
        self.assertIn("gold_must_be_reviewed_before_output", human.import_reviews(value, events)["pending"][0]["reasons"])

    def test_unknown_checks_missing_surface_or_unsupported_quote_stay_pending(self):
        value = bundle()
        for change in ("uncertain", "surface", "quote", "duplicate"):
            with self.subTest(change=change):
                events = events_for(value)
                labels = events[1]["review"]
                if change == "uncertain":
                    labels["checks"]["R1"]["verdict"] = "uncertain"
                elif change == "surface":
                    labels["surface_coverage"] = []
                elif change == "quote":
                    labels["claims"][0]["evidence"][0]["quote"] = "invented source words"
                else:
                    labels["claims"][1].update(label="duplicate", duplicate_of="C1")
                result = human.import_reviews(value, events)
                self.assertFalse(result["final_percentages_available"])

    def test_cli_uses_only_temporary_fixture_records_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="plango-human-import-offline-fixture-") as temporary:
            directory = Path(temporary)
            value = bundle()
            bundle_path, events_path, result_path = directory / "bundle.json", directory / "events.jsonl", directory / "result.json"
            bundle_path.write_text(json.dumps(value))
            events_path.write_text("\n".join(json.dumps(event) for event in events_for(value)) + "\n")
            command = [sys.executable, str(SCRIPT), "--bundle", str(bundle_path), "--events", str(events_path), "--output", str(result_path)]
            first = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(first.stdout)["final_percentages_available"])
            before = result_path.read_bytes()
            self.assertEqual(result_path.stat().st_mode & 0o777, 0o600)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(result_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
