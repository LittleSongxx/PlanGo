"""Offline UI boundary tests. Only TEST storage rows; no human review is simulated."""
import io
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

_paths = list(sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
try:
    import quality_human_ui as ui
finally:
    sys.path[:] = _paths


def store_fixture(tmp_path, stage="gold"):
    case_id = "TEST-ONLY"
    bundle = ui.seal_bundle({"schema_version": 1, "dataset": {"name": "TEST ONLY", "scope": "controlled_acceptance", "planned_case_ids": [case_id]},
        "product": {"revision": "a" * 40, "files": {"test-only.txt": "b" * 64}, "model_config": {}},
        "collection": {"attempts": []}, "cases": [{"case_id": case_id, "case_class": "bounded_answer", "family": "test_only", "group_ids": ["test_only"],
            "packet": {"case_id": case_id, "task": {"agent_input": {"user_turns": [{"message": "TEST INPUT"}]}, "scope": "TEST ONLY"},
                "gold": {"must_pass": [{"id": "R1", "description": "TEST CONDITION"}], "forbidden_claims": ["TEST FORBIDDEN"]},
                "source_packets": [{"source_id": "TEST-SOURCE", "source_kind": "synthetic", "content": {"text": "<img src=x onerror=alert('TEST')>"}}]}}]})
    bundle_path = tmp_path / "test-bundle.json"
    bundle_path.write_text(json.dumps(bundle))
    return ui.ReviewStore(bundle_path, tmp_path / "unused-human-events.jsonl", stage)


def test_http_auth_requires_token_host_and_write_origin():
    origin, token = "http://127.0.0.1:45678", "TEST-SESSION"
    headers = {"Host": "127.0.0.1:45678", "Authorization": "Bearer " + token, "Origin": origin}
    assert ui.authorized(headers, token, origin, write=True)
    assert not ui.authorized({**headers, "Origin": "https://external.invalid"}, token, origin, write=True)
    assert not ui.authorized({key: value for key, value in headers.items() if key != "Origin"}, token, origin, write=True)
    assert not ui.authorized({**headers, "Authorization": "Bearer wrong"}, token, origin)
    assert not ui.authorized({**headers, "Host": "rebound.invalid:45678"}, token, origin)


def test_unconfirmed_empty_identity_and_stale_hashes_never_append_events(tmp_path):
    store = store_fixture(tmp_path)
    case = store.case_view("TEST-ONLY")
    payload = {"event_id": str(uuid.uuid4()), "case_id": "TEST-ONLY", "stage": "gold", "reviewer": "", "decision": "approve", "confirmed_all": False,
               **case["bindings"], **case["acknowledgements"]}
    for change in [{}, {"reviewer": "TEST"}, {"reviewer": "TEST", "confirmed_all": True, "gold_sha": "0" * 64},
                   {"reviewer": "TEST", "confirmed_all": True, "packet_sha": "0" * 64},
                   {"reviewer": "TEST", "confirmed_all": True, "reviewed_source_ids": []},
                   {"reviewer": "TEST", "confirmed_all": True, "decision": "reject", "notes": ""}]:
        with pytest.raises(ValueError):
            store.submit({**payload, **change})
    assert not store.events_path.exists()
    changed = ui.read_json(store.bundle_path)
    changed["cases"][0]["packet"]["task"]["scope"] = "changed test material"
    store.bundle_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="版本已变化"):
        store.case_view("TEST-ONLY")


def test_missing_ai_annotation_cannot_be_confirmed_and_sources_are_text_only(tmp_path):
    store = store_fixture(tmp_path, "output")
    case = store.case_view("TEST-ONLY")
    assert case["proposal_ready"] is False
    with pytest.raises(ValueError, match="等待AI初标"):
        store.submit({"event_id": str(uuid.uuid4()), "case_id": "TEST-ONLY", "stage": "output", "reviewer": "TEST", "decision": "approve", "confirmed_all": True,
                      "review": {"claims": [], "coverage_complete": True}, **case["bindings"]})
    assert not store.events_path.exists()
    assert "innerHTML" not in ui.JS and "textContent" in ui.JS
    assert "window.open" not in ui.JS and "eval(" not in ui.JS
    assert "<img src=x" in case["source_packets"][0]["content"]["text"], "Data remains unexecuted plain text"


def test_append_only_storage_flushes_without_creating_human_review_rows(tmp_path, monkeypatch):
    path = tmp_path / "TEST-storage-only.jsonl"
    calls = []
    original_fsync = os.fsync
    monkeypatch.setattr(ui.os, "fsync", lambda fd: (calls.append(fd), original_fsync(fd)))
    with path.open("a+", encoding="utf-8") as stream:
        ui.append_event(stream, {"test_only": True, "kind": "storage_fixture", "n": 1})
        before = path.read_bytes()
        ui.append_event(stream, {"test_only": True, "kind": "storage_fixture", "n": 2})
        assert [row["n"] for row in ui.read_events(stream)] == [1, 2]
    assert path.read_bytes().startswith(before) and len(calls) == 2
    assert "human_ui" not in path.read_text()
    with pytest.raises(ValueError, match="末行不完整"):
        ui.read_events(io.StringIO('{"test_only":true}'))
