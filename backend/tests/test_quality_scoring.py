"""Independent, offline checks for evaluation accounting, not Agent quality scores."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/quality_scoring.py"
SPEC = importlib.util.spec_from_file_location("independent_quality_scoring", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SCORING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORING)


def claim(claim_id="C1", label="supported", proposition="券售价47元"):
    return {
        "claim_id": claim_id, "label": label, "canonical_claim": proposition,
        "entity_id": "offer-1", "temporal_scope": "historical-snapshot-1",
        "evidence_refs": ["raw.json#/offers/0/sale_price"], "reason": "offline annotation fixture",
    }


def document():
    return {
        "schema_version": 1, "dataset_version": "offline-tests-v1", "system_revision": "fixture",
        "environment_level": "offline_annotation_accounting", "annotations_origin": "ai_assisted",
        "human_verified": False, "gold_human_verified": False,
        "planned_cases": [{
            "case_id": "A", "case_class": "delivery", "family": "reading", "group_ids": ["source:1"],
            "must_pass": ["R1", "R2"], "forbidden": ["F1"],
        }],
        "attempts": [{
            "case_id": "A", "trial_id": "A-1", "valid_attempt": True,
            "replacement_for": None, "invalid_reason": None, "outcome": "completed",
            "checks": {"R1": True, "R2": True}, "forbidden_checks": {"F1": True},
            "side_effects_absent": True, "false_completion_absent": True,
            "annotation_complete": True, "claims": [claim()],
        }],
    }


def add_case(data, case_id="B", attempted=True):
    case = deepcopy(data["planned_cases"][0])
    case.update(case_id=case_id, case_class="bounded_answer")
    data["planned_cases"].append(case)
    if attempted:
        attempt = deepcopy(data["attempts"][0])
        attempt.update(case_id=case_id, trial_id=f"{case_id}-1")
        data["attempts"].append(attempt)


class QualityScoringTests(unittest.TestCase):
    def test_all_requirements_and_boundaries_must_pass_not_product_phase(self):
        data = document()
        report = SCORING.summarize(data)
        self.assertEqual(report["tsr"], 1)
        self.assertEqual(report["report_kind"], "provisional_dev")
        self.assertFalse(report["human_verified"])
        for field, check_id in [("checks", "R2"), ("forbidden_checks", "F1")]:
            with self.subTest(field=field):
                changed = deepcopy(data)
                changed["attempts"][0][field][check_id] = False
                changed["attempts"][0]["run"] = {"phase": "SUCCEEDED"}
                self.assertEqual(SCORING.summarize(changed)["tsr"], 0)
        for field in ("side_effects_absent", "false_completion_absent"):
            with self.subTest(field=field):
                changed = deepcopy(data)
                changed["attempts"][0][field] = False
                self.assertEqual(SCORING.summarize(changed)["tsr"], 0)
        data["attempts"][0]["checks"].pop("R2")
        with self.assertRaisesRegex(ValueError, "predeclared"):
            SCORING.summarize(data)

    def test_macro_average_includes_failed_tasks_and_keeps_contradictions(self):
        data = document()
        data["attempts"][0]["claims"] = [claim(f"C{i}", proposition=f"事实{i}") for i in range(9)]
        add_case(data)
        data["attempts"][1].update(outcome="external_blocked", claims=[claim("B1", "contradicted", "券售价50元")])
        report = SCORING.summarize(data)
        self.assertEqual((report["tsr"], report["groundedness_macro"]), (0.5, 0.5))
        self.assertEqual((report["supported_claims"], report["factual_claims"]), (9, 10))
        self.assertEqual(report["by_class"]["bounded_answer"]["failures"], 1)
        self.assertEqual(report["shared_source_groups"], ["source:1"])
        self.assertAlmostEqual(report["tsr_wilson_95"][0], 0.0945312057)
        self.assertAlmostEqual(report["tsr_wilson_95"][1], 0.9054687943)
        scored = SCORING.score_claims([claim(), claim("C2", "contradicted", "券售价50元")])
        self.assertEqual((scored["factual_claims"], scored["groundedness"]), (2, 0.5))

    def test_duplicate_requires_explicit_matching_proposition_entity_and_scope(self):
        original = claim()
        duplicate = claim("C2", "duplicate")
        duplicate["duplicate_of"] = "C1"
        scored = SCORING.score_claims([original, duplicate])
        self.assertEqual((scored["factual_claims"], scored["labels"]["duplicate"]), (1, 1))
        # Equal canonical text is not silently deduplicated without the explicit label.
        self.assertEqual(SCORING.score_claims([original, claim("C3")])["factual_claims"], 2)
        for field in ("canonical_claim", "entity_id", "temporal_scope"):
            with self.subTest(field=field):
                changed = deepcopy(duplicate)
                changed[field] = "different assertion"
                with self.assertRaisesRegex(ValueError, "cannot be deduplicated"):
                    SCORING.score_claims([original, changed])
        duplicate["duplicate_of"] = "missing"
        with self.assertRaisesRegex(ValueError, "earlier claim"):
            SCORING.score_claims([original, duplicate])

    def test_empty_facts_are_na_and_non_support_labels_remain_in_denominator(self):
        data = document()
        data["attempts"][0]["claims"] = [{"claim_id": "greeting", "label": "non_factual", "reason": "greeting"}]
        report = SCORING.summarize(data)
        self.assertIsNone(report["groundedness_macro"])
        self.assertEqual(report["groundedness_na_tasks"], 1)
        self.assertEqual(report["cases"][0]["task_success"], 1)
        data["attempts"][0]["claims"] = [claim(str(i), label) for i, label in enumerate(sorted(SCORING.FACT_LABELS))]
        report = SCORING.summarize(data)
        self.assertEqual((report["factual_claims"], report["groundedness_macro"]), (5, 0.2))
        data["attempts"][0]["claims"] = [claim()]
        data["attempts"][0]["claims"][0]["evidence_refs"] = []
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            SCORING.summarize(data)

    def test_invalid_replacement_is_explicit_and_cannot_retry_valid_failures(self):
        data = document()
        first = data["attempts"][0]
        replacement = deepcopy(first)
        first.update(valid_attempt=False, outcome="evaluation_invalid", invalid_reason={
            "category": "fixture_missing", "detail": "source file absent", "adjudicator": "test-reviewer",
        })
        report = SCORING.summarize(data)
        self.assertEqual((report["planned"], report["attempted"], report["valid"]), (1, 1, 0))
        self.assertEqual(report["unresolved_invalid_cases"], ["A"])
        self.assertEqual(report["completeness"], "incomplete")
        replacement.update(trial_id="A-2", replacement_for="A-1")
        data["attempts"].append(replacement)
        report = SCORING.summarize(data)
        self.assertEqual((report["valid"], report["attempt_records"], report["tsr"]), (1, 2, 1))
        self.assertEqual(report["cases"][0]["trial_ids"], ["A-1", "A-2"])
        self.assertEqual(len(report["invalid_attempts"]), 1)
        replacement["replacement_for"] = None
        with self.assertRaisesRegex(ValueError, "replacement_for"):
            SCORING.summarize(data)
        data = document()
        data["attempts"][0]["outcome"] = "timeout"
        self.assertEqual(SCORING.summarize(data)["tsr"], 0)
        data["attempts"].append({**deepcopy(data["attempts"][0]), "trial_id": "A-2", "replacement_for": "A-1"})
        with self.assertRaisesRegex(ValueError, "valid main attempt"):
            SCORING.summarize(data)

    def test_product_and_external_failures_cannot_be_declared_invalid(self):
        for outcome in ("timeout", "external_blocked", "budget_exhausted", "product_failure"):
            with self.subTest(outcome=outcome):
                data = document()
                data["attempts"][0].update(outcome=outcome)
                report = SCORING.summarize(data)
                self.assertEqual((report["valid"], report["failures"]), (1, 1))
                data["attempts"][0].update(valid_attempt=False, invalid_reason={
                    "category": "runner_error", "detail": "cannot hide a product failure", "adjudicator": "test",
                })
                with self.assertRaisesRegex(ValueError, "failures are valid"):
                    SCORING.summarize(data)

    def test_unrun_and_unannotated_cases_do_not_become_silent_successes(self):
        data = document()
        add_case(data, attempted=False)
        report = SCORING.summarize(data)
        self.assertEqual((report["planned"], report["attempted"], report["valid"]), (2, 1, 1))
        self.assertFalse(report["complete"])
        self.assertEqual(report["not_run_cases"], ["B"])
        data["attempts"][0]["annotation_complete"] = False
        report = SCORING.summarize(data)
        self.assertEqual(report["pending_annotation"], 1)
        self.assertIsNone(report["tsr"])
        self.assertIsNone(report["groundedness_macro"])
        self.assertIsNone(report["tsr_wilson_95"])

    def test_formal_score_and_forged_boolean_are_rejected(self):
        data = document()
        data["report_kind"] = "formal_score"
        with self.assertRaisesRegex(ValueError, "provisional_dev"):
            SCORING.summarize(data)
        data = document()
        data["attempts"][0]["checks"]["R1"] = 1
        with self.assertRaisesRegex(ValueError, "explicit boolean"):
            SCORING.summarize(data)

    def test_cli_does_not_overwrite_and_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory(prefix="plango-quality-scoring-") as temporary:
            source = Path(temporary) / "annotations.json"
            output = Path(temporary) / "report.json"
            source.write_text(json.dumps(document()))
            command = [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)]
            subprocess.run(command, check=True, capture_output=True)
            original = output.read_bytes()
            report = json.loads(original)
            self.assertEqual(len(report["annotations_sha256"]), 64)
            repeat = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(repeat.returncode, 2)
            self.assertEqual(output.read_bytes(), original)
            source.write_text('{"schema_version": 1, "schema_version": 1}')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Duplicate JSON key", result.stderr)


if __name__ == "__main__":
    unittest.main()
