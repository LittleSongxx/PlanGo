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
import quality_human_import as human  # noqa: E402
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


def test_transport_receipt_requires_a_timestamp_no_later_than_the_output():
    packet = {"outputs": [{"checkpoint": {"id": "pending", "captured_at": "2026-09-09T12:00:00Z"}}],
              "transport_checks": {"desktop": {"finished_at": "2026-09-09T12:01:00Z",
                                                "acceptance": {"at": "2026-09-09T11:59:59Z", "accepted": True}}}}
    claim = {"claim_id": "receipt", "label": "supported", "output": {"ref_id": "output:pending"},
             "evidence": [{"ref_id": "transport_checks", "pointer": "/desktop/acceptance/accepted"}]}
    review = {"claims": [claim]}
    assert human._future_evidence_issues(review, packet) == []
    packet["transport_checks"]["desktop"]["acceptance"]["at"] = "2026-09-09T12:00:01Z"
    assert human._future_evidence_issues(review, packet) == ["transport_evidence_from_future:receipt"]
    packet["transport_checks"]["desktop"].pop("finished_at")
    packet["transport_checks"]["desktop"]["acceptance"].pop("at")
    assert human._future_evidence_issues(review, packet) == ["transport_evidence_time_missing:receipt"]
