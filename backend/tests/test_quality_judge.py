"""Offline checks: judge evidence validation and one-call accounting, no live provider."""

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/quality_judge.py"
sys.path.insert(0, str(SCRIPT.parent))
import quality_judge as judge  # noqa: E402

sys.path.pop(0)


def packet():
    return {
        "case_id": "DEV-OFFLINE", "trial_id": "offline-1", "attempt_outcome": "completed",
        "task": {"agent_input": {"user_turns": [{"message": "只读券售价"}]}, "scope": "read", "model": "ACTOR_IDENTITY"},
        "gold": {"must_pass": [{"id": "R1", "description": "正确读出售价"}], "forbidden_claims": ["已预约"]},
        "source_packets": [{"source_id": "RAW", "content": {"text": "代金券售价47元"}, "available_at_checkpoint_ids": ["read"]}],
        "outputs": [{"schema": "plango.quality-output.v1", "checkpoint": {"id": "read", "phase": "SUCCEEDED", "outcome": "SUCCEEDED"},
                     "coverage": {"history_coverage_partial": False}, "messages": [{"content": "券售价47元"}],
                     "cards": [{"views": [{"rendered_text": "售价47元"}], "structure": {"model": "ACTOR_IDENTITY"}}]}],
        "all_delivery_checkpoints_captured": True,
        "independent_state": {"record_count": 1, "model_calls": [{"reasoning": "HIDDEN_REASONING"}]},
        "transport_checks": {"business_writes": 0, "headers": {"Authorization": "PRIVATE_AUTH"}},
    }


def review():
    output = {"ref_id": "output:read", "pointer": "/messages/0/content", "quote": "券售价47元"}
    source = {"ref_id": "source:RAW", "pointer": "/text", "quote": "代金券售价47元"}
    result = {"verdict": "pass", "reason": "test annotation only", "evidence": [source, output]}
    return {
        "checks": {"R1": deepcopy(result)}, "forbidden_checks": {"F1": deepcopy(result)},
        "side_effects_absent": {"verdict": "pass", "reason": "observed no writes", "evidence": [
            {"ref_id": "transport_checks", "pointer": "/business_writes", "quote": "0"}]},
        "false_completion_absent": deepcopy(result), "coverage_complete": True, "coverage_notes": "test",
        "surface_coverage": [
            {"ref_id": "output:read", "pointer": "/messages/0/content", "claim_ids": ["C1"]},
            {"ref_id": "output:read", "pointer": "/cards/0/views/0/rendered_text", "claim_ids": ["C1"]},
        ],
        "claims": [{"claim_id": "C1", "canonical_claim": "券售价47元", "entity_id": "RAW:offer", "temporal_scope": "historical",
                    "label": "supported", "reason": "test annotation only", "output": output, "evidence": [source],
                    "support_checks": {key: "pass" for key in judge.SUPPORT_CHECKS}}],
    }


class QualityJudgeTests(unittest.TestCase):
    def test_only_exact_public_preview_parameters_survive_url_redaction(self):
        url = "https://www.szuo.com/en/niccolo-chongqing-tealounge/reserve/landing?pax=2&start_date=2026-09-11&start_time=15%3A00"
        self.assertEqual(judge._clean(url), url)
        for unsafe in (url + "&api_key=PRIVATE", url + "&pax=3", url.replace("www.szuo", "other.szuo"),
                       url.replace("https://", "https://user:password@"), url.replace("2026-09-11", "2026-02-30")):
            cleaned = judge._clean(unsafe)
            self.assertNotIn("?", cleaned)
            self.assertNotIn("password", cleaned)
            self.assertNotIn("PRIVATE", cleaned)

    def test_blind_packet_keeps_all_visible_output_and_excludes_private_actor_context(self):
        prepared = judge.prepare_case(packet())
        encoded = json.dumps(prepared, ensure_ascii=False)
        for excluded in ("ACTOR_IDENTITY", "HIDDEN_REASONING", "PRIVATE_AUTH", "SUCCEEDED"):
            self.assertNotIn(excluded, encoded)
        self.assertIn("券售价47元", encoded)
        self.assertIn("售价47元", encoded)
        self.assertEqual(prepared["report_kind"], "provisional_dev")
        self.assertFalse(prepared["human_verified"])
        parsed = judge.parse_review(review(), prepared)
        self.assertTrue(parsed["scoring_attempt"]["annotation_complete"])
        self.assertFalse(parsed["gold_human_verified"])

    def test_valid_location_is_not_enough_without_six_support_conditions(self):
        prepared = judge.prepare_case(packet())
        changed = review()
        changed["claims"][0]["support_checks"]["meaning_units"] = "uncertain"
        parsed = judge.parse_review(changed, prepared)
        self.assertFalse(parsed["scoring_attempt"]["annotation_complete"])
        self.assertIn("support_conditions_unresolved:claim:0", parsed["issues"])
        changed = review()
        changed["surface_coverage"].pop()
        self.assertIn("surface_coverage_incomplete", judge.parse_review(changed, prepared)["issues"])
        changed = review()
        changed["claims"][0]["evidence"][0]["quote"] = "券售价50元"
        self.assertIn("invalid_claim_evidence:claim:0", judge.parse_review(changed, prepared)["issues"])
        changed["claims"][0]["evidence"][0]["ref_id"] = "source:POST_HOC"
        self.assertIn("invalid_claim_evidence:claim:0", judge.parse_review(changed, prepared)["issues"])
        changed["claims"][0]["evidence"] = [changed["claims"][0]["output"]]
        self.assertIn("invalid_claim_evidence:claim:0", judge.parse_review(changed, prepared)["issues"])

    def test_missing_history_chronology_or_uncertain_checks_stay_pending(self):
        data = packet()
        data["all_delivery_checkpoints_captured"] = False
        data["outputs"][0]["coverage"]["history_coverage_partial"] = True
        data["source_packets"][0]["available_at_checkpoint_ids"] = []
        changed = review()
        changed["checks"]["R1"]["verdict"] = "uncertain"
        parsed = judge.parse_review(changed, judge.prepare_case(data))
        self.assertFalse(parsed["scoring_attempt"]["annotation_complete"])
        for issue in ("delivery_checkpoint_coverage_not_confirmed", "history_coverage_partial:read",
                      "uncertain:checks:R1", "invalid_claim_evidence:claim:0"):
            self.assertIn(issue, parsed["issues"])

    def test_empty_claims_and_contradictions_preserve_scoring_semantics(self):
        prepared = judge.prepare_case(packet())
        changed = review()
        changed["claims"] = []
        for row in changed["surface_coverage"]:
            row.update(claim_ids=[], non_factual_reason="offline fixture for no facts accounting")
        parsed = judge.parse_review(changed, prepared)
        self.assertEqual(judge.score_claims(parsed["scoring_attempt"]["claims"])["groundedness"], None)
        changed = review()
        second = deepcopy(changed["claims"][0])
        second.update(claim_id="C2", canonical_claim="券售价50元", label="contradicted")
        changed["claims"].append(second)
        self.assertEqual(len(judge.parse_review(changed, prepared)["scoring_attempt"]["claims"]), 2)
        second.update(label="duplicate", duplicate_of="C1")
        self.assertIn("claim_schema_or_duplicate_invalid", judge.parse_review(changed, prepared)["issues"])

    def test_provider_called_once_raw_usage_retained_and_exceptions_not_leaked(self):
        prepared = judge.prepare_case(packet())
        calls = []

        def provider(messages, cap):
            calls.append(cap)
            self.assertNotIn('"attempt_outcome"', messages[-1]["content"])
            return {"choices": [{"message": {"content": json.dumps(review())}}],
                    "usage": {"prompt_tokens": 110, "completion_tokens": 220, "total_tokens": 330}}

        with tempfile.TemporaryDirectory(prefix="plango-judge-offline-") as temporary:
            destination = Path(temporary) / "success"
            summary = judge.run_case(prepared, destination, provider=provider, max_output_tokens=800)
            self.assertEqual(calls, [800])
            self.assertEqual(summary["usage"]["total_tokens"], 330)
            self.assertTrue((destination / "raw-response.json").is_file())
            self.assertEqual((destination / "raw-response.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                judge.run_case(prepared, destination, provider=provider)
            self.assertEqual(len(calls), 1)

            def failed_provider(messages, cap):
                calls.append(cap)
                raise TimeoutError("PRIVATE_KEY in header https://user:password@example.org?token=PRIVATE")

            failed = judge.run_case(prepared, Path(temporary) / "failure", provider=failed_provider)
            self.assertEqual(len(calls), 2)
            self.assertTrue(failed["usage_missing"])
            self.assertFalse(failed["annotation_complete"])
            self.assertNotIn("PRIVATE", json.dumps(failed))
            with self.assertRaises(ValueError):
                judge.run_case(prepared, Path(temporary) / "overbudget", provider=provider, max_output_tokens=12001)
            self.assertEqual(len(calls), 2)

    def test_cli_prepare_does_not_call_provider_or_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="plango-judge-prepare-") as temporary:
            source, output = Path(temporary) / "packet.json", Path(temporary) / "prepared"
            source.write_text(json.dumps(packet()))
            command = [sys.executable, str(SCRIPT), "--packet", str(source), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["requests_attempted"], 0)
            self.assertFalse((output / "raw-response.json").exists())
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
