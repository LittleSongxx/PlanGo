"""Isolated API projection and sanitized diagnostics; no worker/model/browser runs."""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango_harness.agent.contracts import OfferReference
from pydantic import ValidationError
from test_browser_harness import TOKEN
from test_input_acceptance import app_without_worker


def test_offer_projection_error_is_readable_and_preserves_selection_without_logging_input(tmp_path, caplog):
    with pytest.raises(ValidationError) as invalid:
        OfferReference(command_id="source", artifact_id="page:source", offer_index=0, offer_hash="PRIVATE_INPUT", place_id="place")
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    runtime._offer_comparison = AsyncMock(side_effect=invalid.value)
    selected = {"command_id": "source", "artifact_id": "page:source", "offer_index": 0, "offer_hash": "a" * 64, "place_id": "place"}
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid = client.post("/api/v1/runs", json={"input_text": "核对已选优惠", "browser_session_id": "fixture"}).json()["run_id"]
        async def retain_selection():
            row = await runtime.runs.get(rid)
            state = {**row["state_json"], "structured_requirement_edit": {"turn_id": 1, "offer_selection": selected}}
            await runtime.runs.save_state_and_events(state, expected_version=row["version"], events=[])
        client.portal.call(retain_selection)
        result = client.get(f"/api/v1/runs/{rid}").json()
    assert result["selected_offer"] == selected
    assert result["offer_comparison"] is None
    assert result["offer_comparison_error"] == "暂时无法读取优惠信息，请重新打开来源页面核对。"
    assert "PRIVATE_INPUT" not in caplog.text and "input_value" not in caplog.text
    assert "ValidationError" in caplog.text and "offer_hash" in caplog.text and "string_pattern_mismatch" in caplog.text
