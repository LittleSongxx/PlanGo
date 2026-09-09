"""Offline protocol fixtures; no actor, reviewer model, UI or human events run."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_quality_human_import import case as fixture_case
from test_quality_human_import import reseal_fixture
from test_quality_human_import import review as fixture_review

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/quality_ai_import.py"
sys.path.insert(0, str(SCRIPT.parent))
import quality_ai_import as ai  # noqa: E402

sys.path.pop(0)


def bundle():
    cases = [fixture_case(f"TEST-AI-{index:02d}", extra_bad_fact=index == 1, failed=index == 1) for index in range(1, 31)]
    return ai.human.seal_bundle({"schema_version": 1,
        "dataset": {"name": "offline AI importer protocol fixture", "scope": "controlled_acceptance", "planned_case_ids": [row["case_id"] for row in cases]},
        "product": {"revision": "a" * 40, "files": {"unit-test.py": "b" * 64}, "model_config": {"model": "offline-fixture"}},
        "cases": cases, "collection": {"attempts": [{"case_id": row["case_id"], "trial_id": row["packet"]["trial_id"],
                                                     "valid_attempt": True, "outcome": row["packet"]["attempt_outcome"]} for row in cases]}})


def reviews(value):
    header = {"schema_version": 1, "origin": "independent_ai", "reviewer_type": "AI", "human_reviewed": False,
              "reviewer": "offline-independent-context-fixture", "dataset_sha": value["dataset_sha"], "product_sha": value["product_sha"]}
    gold = {**header, "cases": [{"case_id": row["case_id"], "gold_sha": row["gold_sha"], "decision": "approve",
                                 "reason": "offline schema example", **ai.human.required_acknowledgements(row, "gold")} for row in value["cases"]]}
    output = {**header, "collection_sha": value["collection_sha"],
              "cases": [{"case_id": row["case_id"], "packet_sha": row["packet_sha"], "review": fixture_review(row)} for row in value["cases"]]}
    return gold, output


class IndependentAIImportTests(unittest.TestCase):
    def test_gold_gate_requires_all_ids_reason_hashes_and_ai_provenance(self):
        value = bundle()
        gold, _ = reviews(value)
        self.assertEqual(ai.validate_gold(value, gold), [])
        for field, replacement in (("human_reviewed", True), ("origin", "human_ui"), ("product_sha", "0" * 64)):
            with self.subTest(field=field):
                changed = copy.deepcopy(gold)
                changed[field] = replacement
                self.assertTrue(ai.validate_gold(value, changed))
        for field, replacement in (("decision", "reject"), ("reason", ""), ("reviewed_source_ids", []), ("gold_sha", "0" * 64)):
            with self.subTest(field=field):
                changed = copy.deepcopy(gold)
                changed["cases"][0][field] = replacement
                self.assertTrue(ai.validate_gold(value, changed))
        changed = copy.deepcopy(gold)
        changed["cases"].pop()
        self.assertTrue(any("missing_case" in issue for issue in ai.validate_gold(value, changed)))
        value["collection"]["attempts"] = []
        value = ai.human.seal_bundle(value)
        self.assertEqual(ai.validate_gold(value, gold), [], "gold gate must work before the actor runs")

    def test_percentages_use_ai_labels_without_human_signatures_and_pending_stays_pending(self):
        value = bundle()
        gold, output = reviews(value)
        result = ai.import_reviews(value, gold, output)
        self.assertEqual(result["report_kind"], "independent_ai_reviewed_controlled")
        self.assertAlmostEqual(result["scores"]["tsr_percent"], 100 * 29 / 30)
        self.assertAlmostEqual(result["scores"]["groundedness_percent"], 100 * 29.5 / 30)
        self.assertEqual(result["annotations"]["annotations_origin"], "ai_assisted")
        self.assertFalse(result["scores"]["human_verified"])
        self.assertFalse(result["scores"]["gold_human_verified"])
        self.assertFalse(result["release"]["human_calibrated"])
        self.assertEqual(len(result["release"]["review_receipts"]), 30)
        changed = copy.deepcopy(output)
        changed["cases"].pop()
        pending = ai.import_reviews(value, gold, changed)
        self.assertFalse(pending["final_percentages_available"])
        self.assertEqual(pending["counts"]["planned"], 30)
        self.assertEqual(pending["counts"]["annotated"], 29)
        for error in ("uncertain", "quote", "packet_hash", "rejected"):
            with self.subTest(error=error):
                changed = copy.deepcopy(output)
                row = changed["cases"][1]
                if error == "uncertain":
                    row["review"]["checks"]["R1"]["verdict"] = "uncertain"
                elif error == "quote":
                    row["review"]["claims"][0]["evidence"][0]["quote"] = "not in the source"
                elif error == "packet_hash":
                    row["packet_sha"] = "0" * 64
                else:
                    row["decision"] = "reject"
                self.assertFalse(ai.import_reviews(value, gold, changed)["final_percentages_available"])

    def test_existing_temporal_gate_is_reused_and_cli_refuses_overwrite(self):
        value = bundle()
        packet = value["cases"][1]["packet"]
        packet["outputs"][0]["checkpoint"]["captured_at"] = "2026-09-10T02:00:00+00:00"
        packet["independent_state"]["checkpoints"] = {"later": {"captured_at": "2026-09-10T03:00:00+00:00", "price": 47}}
        value = reseal_fixture(value)
        gold, output = reviews(value)
        output["cases"][1]["review"]["claims"][0]["evidence"] = [{"ref_id": "independent_state", "pointer": "/checkpoints/later/price", "quote": "47"}]
        result = ai.import_reviews(value, gold, output)
        self.assertFalse(result["final_percentages_available"])
        self.assertTrue(any("state_evidence_from_future" in issue for issue in result["issues"]))
        with tempfile.TemporaryDirectory(prefix="plango-ai-review-offline-fixture-") as temporary:
            directory = Path(temporary)
            paths = {name: directory / (name + ".json") for name in ("bundle", "gold-review", "output-review", "output")}
            for name, content in (("bundle", value), ("gold-review", gold), ("output-review", output)):
                paths[name].write_text(json.dumps(content))
            command = [sys.executable, str(SCRIPT), *[item for name, path in paths.items() for item in ("--" + name, str(path))]]
            subprocess.run(command, capture_output=True, check=True)
            before = paths["output"].read_bytes()
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.assertEqual(paths["output"].read_bytes(), before)
            self.assertEqual(paths["output"].stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
