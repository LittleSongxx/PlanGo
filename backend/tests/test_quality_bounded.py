"""Offline bounded-release wiring; no model, browser, API or service starts."""
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import quality_acceptance as acceptance  # noqa: E402
import quality_ai_import as ai  # noqa: E402
import quality_runner as runner  # noqa: E402
from test_quality_ai_import import bundle, reviews  # noqa: E402
from test_quality_human_import import reseal_fixture  # noqa: E402


def protocol(ids):
    return {"version": ai.BOUNDED_PROTOCOL, "name": "offline-six-regression-v1", "evaluation_kind": "regression",
            "planned_case_ids": ids, "primary_source_groups": 6,
            "case_authoring": "Offline test only", "execution_scope": "Existing controlled drivers",
            "limits": {"calls": 80, "case_calls": 12, "reported_tokens_stop": 120000},
            "budget_ledger": "output/shared-stage/model-budget.jsonl"}


def small_bundle():
    value = bundle()
    value["cases"] = value["cases"][:6]
    ids = [case["case_id"] for case in value["cases"]]
    value["dataset"].update(planned_case_ids=ids, protocol=protocol(ids))
    value["collection"]["attempts"] = value["collection"]["attempts"][:6]
    return reseal_fixture(value)


def test_six_case_protocol_reuses_scores_and_requires_every_review():
    value = small_bundle()
    gold, output = reviews(value)
    result = ai.import_reviews(value, gold, output)
    assert result["status"] == "complete" and result["scores"]["planned"] == 6
    assert result["scores"]["tsr_percent"] == pytest.approx(100 * 5 / 6)
    assert "6 controlled regression" in result["release"]["scope"]
    output["cases"].pop()
    assert ai.import_reviews(value, gold, output)["final_percentages_available"] is False
    gold["cases"].pop()
    assert any("missing_case" in error for error in ai.validate_gold(value, gold))
    for change in ({"version": "unknown"}, {"planned_case_ids": ["different"]}, {"limits": {"calls": 81}}):
        changed = copy.deepcopy(value)
        changed["dataset"]["protocol"].update(change)
        assert ai._bundle_issues(reseal_fixture(changed))
    value["dataset"].pop("protocol")
    assert ai._bundle_issues(reseal_fixture(value)), "An unversioned six-case plan must not masquerade as the old release"


def test_explicit_dataset_freeze_manifest_fixtures_and_nonreplacement(tmp_path, monkeypatch):
    original_data = acceptance.DATA
    tasks = runner.read(original_data / "tasks.json")
    tasks = [tasks[index] for index in (0, 4, 10, 16, 17, 29)]
    ids, sids = [t["case_id"] for t in tasks], {t["source_ids"][0] for t in tasks}
    dataset, work, freeze = tmp_path / "eval/new-six", tmp_path / "output/new-session", tmp_path / "eval/separate-freeze.json"
    dataset.mkdir(parents=True)
    for name, rows in (("tasks", tasks), ("sources", [s for s in runner.read(original_data / "sources.json") if s["source_id"] in sids]),
                       ("gold", [g for g in runner.read(original_data / "gold.json") if g["case_id"] in ids])):
        runner.write(dataset / (name + ".json"), rows)
    declared = protocol(ids)
    declared["primary_source_groups"] = len(sids)
    runner.write(dataset / "protocol.json", declared)
    source = tmp_path / "product.py"
    source.write_text("frozen")
    runner.write(freeze, {"files": {"product.py": acceptance.file_sha(source)}, "model_config": {}, "product_commit": "a" * 40})
    monkeypatch.setattr(acceptance, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(acceptance, "model_config", lambda: {})
    # Manifest adapters are real filenames, represented by local test-only bytes.
    for name in ("quality_acceptance.py", "quality_runner.py", "quality_state_cases.py", "quality_desktop_cases.cjs",
                 "export_quality_output.ts", "quality_ai_import.py", "quality_human_import.py", "quality_judge.py", "quality_scoring.py"):
        path = tmp_path / "scripts" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("offline-adapter")
    acceptance.prepare(work, dataset=dataset, freeze=freeze)
    with pytest.raises(AssertionError, match="Never reuse"):
        acceptance.prepare(work, dataset=dataset, freeze=freeze)
    prepared = runner.read(work / "gold-bundle.json")
    assert len(prepared["cases"]) == 6
    gold = {"schema_version": 1, "origin": "independent_ai", "reviewer_type": "AI", "human_reviewed": False,
            "reviewer": "offline-context", "dataset_sha": prepared["dataset_sha"], "product_sha": prepared["product_sha"],
            "cases": [{"case_id": row["case_id"], "gold_sha": row["gold_sha"], "decision": "approve", "reason": "Offline test",
                       **ai.human.required_acknowledgements(row, "gold")} for row in prepared["cases"]]}
    review = work / "gold-review.json"
    runner.write(review, gold)
    calls = []
    def collect(task, packets, settings, directory, control, sha, case_ids, **kwargs):
        assert "gold" not in task and "gold_facts" not in json.dumps(packets)
        assert task in tasks and len(packets["packets"]) == len(sids)
        assert runner.digest(kwargs["manifest_fn"]()) == sha
        if kwargs["fixture"]:
            assert kwargs["fixture_provenance"]["path"] == "eval/new-six/runtime-fixtures.json"
        result = {"trial_id": "first", "checkpoints": {}, "stop_reason": "completed", "reported_tokens": 0}
        runner.write(directory / "collection.json", result)
        calls.append(task["case_id"])
        return result
    monkeypatch.setattr(runner, "collect_case", collect)
    monkeypatch.setattr(runner, "project_settings", lambda directory: None)
    acceptance.run(work, ids, gold_review=review, dataset=dataset, freeze=freeze)
    assert calls == ids
    with pytest.raises(AssertionError, match="Never replace"):
        acceptance.run(work, [ids[0]], gold_review=review)
    with pytest.raises(AssertionError, match="dataset path changed"):
        acceptance.run(work, [ids[0]], gold_review=review, dataset=tmp_path / "eval/other")
    target = work / "outputs.json"
    acceptance.bundle_outputs(work, target, dataset=dataset, freeze=freeze)
    assert runner.read(target)["dataset_sha"] == prepared["dataset_sha"]
    with pytest.raises(FileExistsError):
        acceptance.bundle_outputs(work, target)
    manifest_path = next(work.glob("batch-*/manifest.json"))
    manifest = runner.read(manifest_path)
    manifest_path.write_text(json.dumps({**manifest, "product_sha": "0" * 64}))
    with pytest.raises(AssertionError, match="Mixed product/dataset"):
        acceptance.bundle_outputs(work, work / "mixed-output.json")
    manifest_path.write_text(json.dumps(manifest))
    source.write_text("changed current product")
    with pytest.raises(ValueError, match="Frozen product changed"):
        acceptance.product(freeze)
    source.write_text("frozen")
    (dataset / "gold.json").write_text("[]")
    with pytest.raises(AssertionError, match="Reviewed input changed"):
        acceptance.session_inputs(work)


def test_shared_budget_survives_sessions_and_rejects_limit_change_or_unfinished_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    ledger = tmp_path / "output/budget.jsonl"
    limits = {"calls": 2, "case_calls": 1, "reported_tokens_stop": 20}
    async def invoke(awaitable, *, timeout):
        return await awaitable
    async def response():
        return {"raw": SimpleNamespace(usage_metadata={"total_tokens": 7})}
    for index in range(2):
        with runner.shared_budget(ledger, limits) as control:
            assert len(control["calls"]) == index and control["reported_tokens"] == 7 * index
            runtime = SimpleNamespace(model=SimpleNamespace(_invoke=invoke))
            runner.install_budget(runtime, f"OFFLINE-{index}", control, runner.digest({}), [], tmp_path / f"call-{index}.jsonl", manifest_fn=lambda: {})
            asyncio.run(runtime.model._invoke(response(), timeout=None))
    with pytest.raises(ValueError, match="Shared stage stopped"):
        with runner.shared_budget(ledger, limits):
            pass
    with pytest.raises(ValueError, match="limits changed"):
        with runner.shared_budget(ledger, {**limits, "calls": 3}):
            pass
    interrupted = tmp_path / "output/interrupted.jsonl"
    with runner.shared_budget(interrupted, limits):
        runner.append(interrupted, {"event": "started", "call_id": "uncompleted"})
    with pytest.raises(ValueError, match="unfinished model call"):
        with runner.shared_budget(interrupted, limits):
            pass


def test_freeze_is_exclusive_and_records_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "ROOT", tmp_path)
    monkeypatch.setattr(acceptance, "product_files", lambda: {"product.py": "b" * 64})
    monkeypatch.setattr(acceptance, "model_config", lambda: {"model": "offline-only"})
    monkeypatch.setattr(acceptance.subprocess, "check_output", lambda *args, **kwargs: "a" * 40)
    for name in ("out/main/index.js", "out/renderer/index.html"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    path = tmp_path / "eval/new-freeze.json"
    acceptance.freeze_product(path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        acceptance.freeze_product(path)
    assert path.read_bytes() == before
    assert runner.read(path)["schema_version"] == 2


def test_output_packaging_keeps_original_events_receipt_time_and_explicit_fixture(tmp_path):
    original = copy.deepcopy(runner.read(acceptance.DATA / "review/bundle.json")["cases"][16])
    sid = original["packet"]["task"]["source_ids"][0]
    fixture = acceptance.default_fixture(sid)
    fixture_path = tmp_path / "different-fixtures.json"
    runner.write(fixture_path, {sid: fixture})
    source = next(s for s in original["packet"]["source_packets"] if s["source_id"] == "ENV-" + sid)
    source["content"] = fixture
    now = "2026-09-09T13:00:00Z"
    event = {"event_seq": 7, "event_type": "INPUT_ACCEPTED", "created_at": now, "payload": {"request_id": "actual-original"}}
    envelope = {"snapshot": {"state": {}}, "events": [event]}
    runner.write(tmp_path / "snapshot.json", envelope)
    runner.write(tmp_path / "visible.json", {"messages": [], "cards": [], "checkpoint": {"id": "final"}})
    runner.write(tmp_path / "imported-state.json", {"fixture_value_sha256": ai.human.canonical_sha(fixture),
                 "fixture_sha256": acceptance.file_sha(fixture_path), "imported_at": "2026-09-09T12:59:00Z"})
    receipt = {"at": now, "accepted": True, "request_id": "actual-original"}
    runner.write(tmp_path / "desktop-result.json", {"acceptance": receipt, "message_responses": [{"at": now, "status": 202}]})
    runner.write(tmp_path / "collection.json", {"trial_id": "first", "stop_reason": "completed", "checkpoints": {
        "final": {"snapshot_path": str(tmp_path / "snapshot.json"), "visible_path": str(tmp_path / "visible.json"),
                  "captured_at": now, "desktop_evidence": {"id": "final"}, "independent_state": {"same_run": True}}}})
    result = acceptance.output_case(original, tmp_path, fixtures_path=fixture_path)
    desktop = result["packet"]["transport_checks"]["desktop"]
    assert desktop["acceptance"] == receipt
    assert desktop["message_responses"] == [{"at": now, "status": 202}]
    assert desktop["checkpoint_events"]["final"] == {"captured_at": now, "events": [event]}
    assert result["packet"]["outputs"][0]["checkpoint"]["captured_at"] == now
    fixture_path.write_text("{}")
    with pytest.raises(AssertionError):
        acceptance.output_case(original, tmp_path, fixtures_path=fixture_path)
