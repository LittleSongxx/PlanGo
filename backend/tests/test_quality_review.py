"""Small offline integration checks for frozen-data review preparation."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import quality_review as review  # noqa: E402

sys.path.pop(0)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False))


class QualityReviewTests(unittest.TestCase):
    def test_baseline_ids_are_removed_from_later_history_without_changing_cards_or_files(self):
        with tempfile.TemporaryDirectory(prefix="quality-baseline-test-", dir=ROOT / "output") as temporary:
            directory = Path(temporary)
            baseline = {"checkpoint": {"id": "baseline"}, "messages": [{"id": "seed-reply", "content": "Imported draft"}], "cards": []}
            final = {"checkpoint": {"id": "final"}, "messages": [
                {"id": "seed-reply", "content": "Imported draft"}, {"id": "new-reply", "content": "Updated draft"}],
                "cards": [{"views": [{"rendered_text": "Current selected plan remains delivered"}]}]}
            write(directory / "baseline.visible.json", baseline)
            write(directory / "final.visible.json", final)
            before = {path.name: path.read_bytes() for path in directory.glob("*.json")}
            # Baseline appears last in this dictionary: exclusion must inspect all checkpoints.
            result = {"stop_reason": "completed", "checkpoints": {
                "final": {"visible_path": "final.visible.json"},
                "desktop-initial-reviewable-draft": {"scope": "pre_task_context", "visible_path": "baseline.visible.json"},
            }}
            packet, hashes = review.make_packet({"case_id": "A"}, {}, {"packets": []}, {"checks": []}, result, directory)
            self.assertEqual([message["id"] for message in packet["outputs"][0]["messages"]], ["new-reply"])
            self.assertEqual(packet["outputs"][0]["cards"], final["cards"])
            self.assertEqual(packet["baseline_message_exclusion"]["excluded_baseline_message_ids"], ["seed-reply"])
            self.assertEqual(packet["baseline_message_exclusion"]["excluded_by_checkpoint"], {"final": ["seed-reply"]})
            for name, raw in before.items():
                self.assertEqual((directory / name).read_bytes(), raw)
                self.assertEqual(hashes[name], hashlib.sha256(raw).hexdigest())

    def test_runtime_fixture_requires_import_and_both_hashes_and_uses_actual_capture_time(self):
        with tempfile.TemporaryDirectory(prefix="quality-runtime-source-test-", dir=ROOT / "output") as temporary:
            directory = Path(temporary)
            data = directory / "data"
            data.mkdir()
            fixture = {"case_ids": ["DEV-05"], "source_id": "SYN-WORLD", "origin": {"name": "受控起点"},
                       "place": {"price_known": False}, "route": {"distance_km": 0.2, "walking_min": 3, "cost_per_person": 0},
                       "supply": {"source": "unknown"}, "weather": {"status": "unknown"}, "initial_draft": {"not_a_new_answer": True}}
            fixture_path = data / "runtime-fixtures.dev.json"
            write(fixture_path, fixture)
            fixture_ref, fixture_hash = str(fixture_path.relative_to(ROOT)), review.file_hash(fixture_path)
            imported = {"status": "imported_unsaved_review", "fixture_path": fixture_ref, "fixture_sha256": fixture_hash,
                        "imported_at": "2026-09-09T12:00:00+00:00", "clock": {"fixture_observed_at": "2026-09-09T12:00:00+08:00"}}
            write(directory / "imported-state.json", imported)
            result = {"stop_reason": "completed", "checkpoints": {}}
            for stage, captured in (("t1", "2026-09-09T11:59:00+00:00"), ("final", "2026-09-09T12:01:00+00:00")):
                write(directory / f"{stage}.visible.json", {"checkpoint": {"id": stage}, "messages": [], "cards": []})
                result["checkpoints"][stage] = {"visible_path": f"{stage}.visible.json", "captured_at": captured}
            manifest = {"files": {fixture_ref: fixture_hash}}
            with patch.object(review, "DATA", data):
                packet, _ = review.make_packet({"case_id": "DEV-05"}, {}, {"packets": []}, {"checks": []}, result, directory, manifest)
                source = packet["source_packets"][0]
                self.assertEqual(source["source_kind"], "explicit_synthetic")
                self.assertEqual(source["content"]["route"], fixture["route"])
                self.assertNotIn("initial_draft", source["content"])
                self.assertEqual(source["observed_at"], imported["clock"]["fixture_observed_at"])
                self.assertEqual(source["available_at_checkpoint_ids"], ["final"])
                self.assertEqual(packet["preparation_issues"], [])
                for missing in ("manifest_hash", "import_hash", "file_hash", "import_record"):
                    with self.subTest(missing=missing):
                        write(fixture_path, fixture)
                        write(directory / "imported-state.json", imported)
                        changed_manifest = {"files": dict(manifest["files"])}
                        if missing == "manifest_hash":
                            changed_manifest["files"][fixture_ref] = "different"
                        elif missing == "import_hash":
                            write(directory / "imported-state.json", {**imported, "fixture_sha256": "different"})
                        elif missing == "file_hash":
                            write(fixture_path, {**fixture, "route": {"distance_km": 999}})
                        else:
                            (directory / "imported-state.json").unlink()
                        failed, _ = review.make_packet({"case_id": "DEV-05"}, {}, {"packets": []}, {"checks": []}, result, directory, changed_manifest)
                        self.assertEqual(failed["source_packets"], [])
                        self.assertTrue(any(issue.startswith("runtime_fixture_") for issue in failed["preparation_issues"]))

    def test_independent_oracle_distinguishes_missing_null_bool_and_same_as(self):
        result = {"checkpoints": {"initial": {"independent_state": {"source": "independent_SQLite_query", "spec": {"budget": None}}}}}
        oracle = {"checks": [{"check_id": "R1", "method": "structured", "assertions": [
            {"path": "/checkpoints/initial/independent_state/spec/budget", "op": "equals", "expected": None}]}]}
        self.assertEqual(review.check_oracle(result, oracle)[0]["verdict"], "pass")
        self.assertTrue(review._equal(2, 2.0))
        self.assertFalse(review._equal(True, 1))
        result["checkpoints"]["initial"]["independent_state"]["spec"].pop("budget")
        self.assertEqual(review.check_oracle(result, oracle)[0]["verdict"], "pending")
        result["checkpoints"]["initial"]["independent_state"] = None
        self.assertEqual(review.check_oracle(result, oracle)[0]["verdict"], "pending")

    def test_prepare_links_frozen_materials_and_preserves_all_planned_cases(self):
        with tempfile.TemporaryDirectory(prefix="quality-review-test-", dir=ROOT / "output") as temporary:
            work = Path(temporary)
            data, run, case_dir = work / "data", work / "run", work / "run/A"
            data.mkdir()
            case_dir.mkdir(parents=True)
            task = {"case_id": "A", "case_class": "delivery", "family": "reading", "group_ids": ["source:test"],
                    "agent_input": {"user_turns": [{"message": "读取价格"}]}}
            gold = {"case_id": "A", "must_pass": [{"id": "R1", "description": "price correct"}], "forbidden_claims": ["已预约"]}
            write(data / "tasks.dev.json", [task, {**task, "case_id": "B"}])
            write(data / "gold.dev.json", [gold, {**gold, "case_id": "B"}])
            payload = {"text": "售价47元"}
            source = {"packet_id": "RAW", "case_ids": ["A"], "payload": payload,
                      "payload_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
            write(data / "source-packets.dev.json", {"tasks_sha256": review.file_hash(data / "tasks.dev.json"), "packets": [source]})
            oracle = {"gold_sha256": review.file_hash(data / "gold.dev.json"), "tasks_sha256": review.file_hash(data / "tasks.dev.json"),
                      "source_packets_sha256": review.file_hash(data / "source-packets.dev.json"),
                      "cases": [{"case_id": name, "checks": [{"check_id": "R1", "method": "manual"}]} for name in ("A", "B")]}
            write(data / "oracle-checks.dev.json", oracle)
            visible = {"schema": "plango.quality-output.v1", "checkpoint": {"id": "final"},
                       "coverage": {"history_coverage_partial": False}, "messages": [{"content": "售价47元"}], "cards": []}
            write(case_dir / "final.visible.json", visible)
            write(case_dir / "final.snapshot.json", {"checkpoint": {"id": "final"}, "snapshot": {"state": {
                "browser_observation": {"fields": {"evaluation_source": {"packet_id": "RAW"}}}}}})
            result = {"case_id": "A", "trial_id": "first", "stop_reason": "completed", "environment_level": "offline_annotation_test",
                      "checkpoints": {"final": {"snapshot_path": "final.snapshot.json", "visible_path": "final.visible.json",
                                                  "independent_state": {"source": "independent_SQLite_query", "counts": {"agent_action": 0}}}}}
            write(case_dir / "collection.json", result)
            write(run / "collection.json", {"planned": ["A", "B"], "cases": [result, {"case_id": "B", "stop_reason": "not_run_after_batch_stop"}]})
            manifest = {"case_ids": ["A", "B"], "files": {str(path.relative_to(ROOT)): review.file_hash(path) for path in data.glob("*.json")}}
            manifest["source_sha"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            manifest["offline_wiring_not_quality"] = True
            write(run / "manifest.json", manifest)
            with patch.object(review, "DATA", data):
                summary = review.review_collection(run / "collection.json", work / "prepared")
                self.assertEqual(summary["judge_calls"], 0)
                scores = review.read(work / "prepared/scores.json")
                self.assertEqual((scores["planned"], scores["attempted"], scores["valid"]), (2, 1, 1))
                self.assertFalse(scores["complete"])
                self.assertIsNone(scores["tsr"])
                packet = review.read(work / "prepared/A/packet.json")
                self.assertEqual(packet["source_packets"][0]["available_at_checkpoint_ids"], ["final"])
                self.assertEqual(packet["trial_id"], "A:first")
                self.assertGreater(summary["cases"][0]["estimated_input_tokens"], 0)
                with self.assertRaises(FileExistsError):
                    review.review_collection(run / "collection.json", work / "prepared")
                with self.assertRaisesRegex(ValueError, "offline wiring"):
                    review.review_collection(run / "collection.json", work / "not-live", run=True)
                write(data / "gold.dev.json", [])
                with self.assertRaisesRegex(ValueError, "oracle hash mismatch"):
                    review.review_collection(run / "collection.json", work / "changed")


if __name__ == "__main__":
    unittest.main()
