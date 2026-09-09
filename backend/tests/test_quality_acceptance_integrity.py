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
