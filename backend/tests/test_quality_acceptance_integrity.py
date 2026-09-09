"""Offline evaluator contracts; no new acceptance case or model is executed."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import quality_acceptance as acceptance  # noqa: E402
import quality_human_import as human  # noqa: E402
import quality_runner as runner  # noqa: E402
import quality_state_cases as state_cases  # noqa: E402


def test_invalid_generated_source_is_rejected_before_initial_state_is_written(tmp_path):
    case = next(c for c in json.loads((state_cases.ROOT / "eval/quality-v1/tasks.dev.json").read_text()) if c["case_id"] == "DEV-05")
    runtime = SimpleNamespace(_started=True, graph=object(), runs=SimpleNamespace(create_with_event=AsyncMock()),
        settings=SimpleNamespace(database_url=f"sqlite+aiosqlite:///{tmp_path / 'runs.sqlite'}", is_sqlite=True,
                                 data_dir=tmp_path, checkpoint_path=tmp_path / "checkpoint.sqlite"))
    with patch.object(state_cases.uuid, "uuid4", return_value=SimpleNamespace(hex="bad:generated-id")):
        with pytest.raises(ValidationError):
            asyncio.run(state_cases.seed(runtime, case, tmp_path))
    runtime.runs.create_with_event.assert_not_awaited()
    assert not (tmp_path / "imported-state.json").exists()


@pytest.mark.parametrize("timestamp", ["at", "observed_at"])
def test_transport_receipt_requires_a_timestamp_no_later_than_the_output(timestamp):
    packet = {"outputs": [{"checkpoint": {"id": "pending", "captured_at": "2026-09-09T12:00:00Z"}}],
              "transport_checks": {"desktop": {"finished_at": "2026-09-09T12:01:00Z",
                                                "acceptance": {timestamp: "2026-09-09T11:59:59Z", "accepted": True}}}}
    claim = {"claim_id": "receipt", "label": "supported", "output": {"ref_id": "output:pending"},
             "evidence": [{"ref_id": "transport_checks", "pointer": "/desktop/acceptance/accepted"}]}
    review = {"claims": [claim]}
    assert human._future_evidence_issues(review, packet) == []
    packet["transport_checks"]["desktop"]["acceptance"][timestamp] = "2026-09-09T12:00:01Z"
    assert human._future_evidence_issues(review, packet) == ["transport_evidence_from_future:receipt"]
    packet["transport_checks"]["desktop"].pop("finished_at")
    packet["transport_checks"]["desktop"]["acceptance"].pop(timestamp)
    assert human._future_evidence_issues(review, packet) == ["transport_evidence_time_missing:receipt"]


def test_transport_invalidation_binds_original_attempt_and_preserves_registry(tmp_path, monkeypatch):
    work = tmp_path / "output/session"
    directory = work / "batch/CASE-01"
    directory.mkdir(parents=True)
    monkeypatch.setattr(acceptance, "ROOT", tmp_path)
    runner.write(directory / "snapshot.json", {"original": True})
    runner.write(directory / "collection.json", {"trial_id": "first", "stop_reason": "script_finished", "checkpoints": {
        "after_restart": {"scope": "actual_owned_backend_restart_desktop_closed"}}})
    row = {"trial_id": "CASE-01:first", "case_id": "CASE-01", "directory": "output/session/batch/CASE-01"}
    registry = work / "attempt-registry.jsonl"
    registry.write_text(json.dumps(row) + "\n")
    capture = {"artifact_hashes": {"snapshot.json": acceptance.file_sha(directory / "snapshot.json")},
               "case_collection_sha256": acceptance.file_sha(directory / "collection.json")}
    original = {"dataset_sha": "dataset", "product_sha": "product", "collection_sha": "collection", "cases": [{
        "case_id": "CASE-01", "gold_sha": "gold", "packet_sha": "packet", "packet": {"trial_id": row["trial_id"],
        "task": {"environment": {"driver": "save_restart"}}, "capture_audit": capture}}]}
    runner.write(work / "output-bundle.json", original)
    runner.write(work / "gold-bundle.json", original)
    audit = {"origin": "independent_ai", "reviewer_type": "AI", "human_reviewed": False,
        "decision": "invalidate_original_attempt_as_runner_error", "invalid_reason": {"category": "runner_error"},
        "original_bundle_sha": human.canonical_sha(original), "original_collection_sha": "collection",
        "dataset_sha": "dataset", "product_sha": "product", "case_id": "CASE-01", "impact_scope": {"driver": "save_restart", "case_ids": ["CASE-01"]},
        "original_packet_sha": "packet", "original_gold_sha": "gold", "original_capture_audit_sha": human.canonical_sha(capture),
        "original_trial_id": row["trial_id"], "original_raw_trial_id": "first", "original_case_collection_sha256": capture["case_collection_sha256"],
        "replacement_policy": {"allowed": True, "maximum_replacement_attempts": 1, "replacement_for": row["trial_id"]}}
    runner.write(work / "transport-adjudication.json", audit)
    before = registry.read_bytes()
    acceptance.adjudicate_transport(work)
    assert set(acceptance.invalid_attempts(work)) == {"CASE-01:first"}
    assert registry.read_bytes() == before
    with pytest.raises(FileExistsError):
        acceptance.adjudicate_transport(work)
    (directory / "snapshot.json").write_text("changed")
    with pytest.raises(AssertionError, match="Original attempt artifact changed"):
        acceptance.invalid_attempts(work)
