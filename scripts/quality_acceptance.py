#!/usr/bin/env python3
"""Freeze, prepare and execute preregistered controlled acceptance releases.

Gold approval is required before run, with independent AI review allowed only
under the user's explicit delegation. The product/model freeze and attempt
registry are enforced while collecting; later evidence packaging reads archives.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import uuid
from collections import Counter
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import quality_human_import as human
import quality_judge as judge
import quality_runner as runner
from quality_ai_import import validate_protocol

ROOT = runner.ROOT
DATA = ROOT / "eval/quality-v2-30"
STATE_DRIVERS = {"edit", "save_restart", "message_recovery", "message_uncertain"}


def now():
    return datetime.now(timezone.utc).isoformat()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def events(work):
    path = Path(work) / "human-events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def invalid_attempts(work):
    result = {}
    for filename, review in (("invalidated-attempts.json", "fixture-adjudication.json"),
                             ("preinput-invalidations.json", "startup-adjudication.json"),
                             ("transport-invalidations.json", "transport-adjudication.json")):
        path = Path(work) / filename
        if path.exists():
            ledger = runner.read(path)
            assert ledger["adjudication_sha256"] == file_sha(Path(work) / review)
            if filename == "transport-invalidations.json":
                assert ledger["attempts"] == [transport_invalidation(Path(work))], "Transport adjudication binding changed"
            for row in ledger["attempts"]:
                assert row["trial_id"] not in result
                result[row["trial_id"]] = row
    return result


def transport_invalidation(work):
    """Validate the independent decision against the original sealed attempt."""
    work = work.resolve()
    audit = runner.read(work / "transport-adjudication.json")
    original = runner.read(work / "output-bundle.json")
    gold = runner.read(work / "gold-bundle.json")
    assert audit["origin"] == "independent_ai" and audit["reviewer_type"] == "AI" and audit["human_reviewed"] is False
    assert audit["decision"] == "invalidate_original_attempt_as_runner_error" and audit["invalid_reason"]["category"] == "runner_error"
    assert audit["original_bundle_sha"] == human.canonical_sha(original)
    assert audit["original_collection_sha"] == original["collection_sha"]
    assert all(audit[key] == original[key] == gold[key] for key in ("dataset_sha", "product_sha"))
    case = next(c for c in original["cases"] if c["case_id"] == audit["case_id"])
    packet = case["packet"]
    assert packet["task"]["environment"]["driver"] == audit["impact_scope"]["driver"] == "save_restart"
    assert audit["impact_scope"]["case_ids"] == [case["case_id"]]
    for key in ("packet_sha", "gold_sha"):
        assert case[key] == audit["original_" + key]
    assert next(c for c in gold["cases"] if c["case_id"] == case["case_id"])["gold_sha"] == case["gold_sha"]
    assert human.canonical_sha(packet["capture_audit"]) == audit["original_capture_audit_sha"]
    assert packet["trial_id"] == audit["original_trial_id"]
    policy = audit["replacement_policy"]
    assert policy["allowed"] is True and policy["maximum_replacement_attempts"] == 1 and policy["replacement_for"] == packet["trial_id"]
    rows = [json.loads(line) for line in (work / "attempt-registry.jsonl").read_text().splitlines()]
    matches = [row for row in rows if row["trial_id"] == packet["trial_id"]]
    assert len(matches) == 1 and matches[0]["case_id"] == case["case_id"]
    row = matches[0]
    directory = (ROOT / row["directory"]).resolve()
    assert directory.is_relative_to(work)
    capture = packet["capture_audit"]
    assert file_sha(directory / "collection.json") == audit["original_case_collection_sha256"] == capture["case_collection_sha256"]
    for name, sha in capture["artifact_hashes"].items():
        path = (directory / name).resolve()
        assert path.is_relative_to(directory) and file_sha(path) == sha, "Original attempt artifact changed"
    record = runner.read(directory / "collection.json")
    assert record["trial_id"] == audit["original_raw_trial_id"]
    assert record["checkpoints"]["after_restart"]["scope"] == "actual_owned_backend_restart_desktop_closed"
    assert "desktop-both-restarted" not in record["checkpoints"]
    return {**row, "invalid_reason": audit["invalid_reason"], "raw_stop_reason": record["stop_reason"],
            "case_collection_sha256": audit["original_case_collection_sha256"],
            "original_bundle_canonical_sha": audit["original_bundle_sha"], "original_bundle_file_sha256": file_sha(work / "output-bundle.json"),
            "original_capture_audit_sha": audit["original_capture_audit_sha"]}


def adjudicate_transport(work):
    work = Path(work).resolve()
    row = transport_invalidation(work)
    runner.write(work / "transport-invalidations.json", {"adjudication_sha256": file_sha(work / "transport-adjudication.json"),
                 "created_at": now(), "attempts": [row]})
    print(json.dumps({"transport_invalid_attempts": 1, "old_evidence_retained": True}))


def adjudicate_preinput(work):
    work = Path(work).resolve()
    bundle = runner.read(work / "gold-bundle.json")
    audit = runner.read(work / "startup-adjudication.json")
    assert audit["origin"] == "independent_ai" and audit["human_reviewed"] is False
    assert audit["dataset_sha"] == bundle["dataset_sha"] and audit["product_sha"] == bundle["product_sha"]
    assert audit["decision"] == "invalidate_preinput_attempt" and audit["reason_category"] == "runner_error"
    registry = [json.loads(x) for x in (work / "attempt-registry.jsonl").read_text().splitlines()]
    affected = set(audit["affected_trial_ids"])
    rows = []
    for row in registry:
        if row["trial_id"] not in affected:
            continue
        directory = (ROOT / row["directory"]).resolve()
        assert directory.is_relative_to(work)
        record = runner.read(directory / "collection.json")
        initial = record["checkpoints"]["final"]["independent_state"]
        assert record["model_invocations"] == 0 and initial["counts"]["input_acceptance"] == 0 and initial["turn_id"] == 1
        assert not (directory / "desktop-config.json").exists() and not (directory / "initial.snapshot.json").exists()
        assert runner.read(directory / "collector-error.json")["error_class"] == "ConnectTimeout"
        rows.append({**row, "invalid_reason": {"category": "runner_error", "adjudicator": audit["reviewer"],
            "detail": "Collector loopback connection failed before any declared input or desktop action; no actor/model call occurred."},
            "raw_stop_reason": record["stop_reason"], "case_collection_sha256": file_sha(directory / "collection.json")})
    assert {r["trial_id"] for r in rows} == affected
    runner.write(work / "preinput-invalidations.json", {"adjudication_sha256": file_sha(work / "startup-adjudication.json"),
                                                        "created_at": now(), "attempts": rows})
    print(json.dumps({"preinput_invalid_attempts": len(rows), "old_evidence_retained": True}))


def adjudicate_fixture(work):
    """Apply the independent uniform decision, without consulting task outcomes."""
    work = Path(work).resolve()
    bundle = runner.read(work / "gold-bundle.json")
    audit = runner.read(work / "fixture-adjudication.json")
    assert audit["origin"] == "independent_ai" and audit["reviewer_type"] == "AI" and audit["human_reviewed"] is False
    assert audit["dataset_sha"] == bundle["dataset_sha"] and audit["product_sha"] == bundle["product_sha"]
    assert audit["decision"] == "invalidate_fixture_attempts" and audit["reason_category"] == "runner_error"
    affected = {c["case_id"] for c in bundle["cases"] if c["packet"]["task"]["environment"]["driver"] in STATE_DRIVERS}
    assert set(audit["affected_case_ids"]) == affected
    rows = [json.loads(x) for x in (work / "attempt-registry.jsonl").read_text().splitlines()]
    invalid = []
    for row in rows:
        if row["case_id"] not in affected:
            continue
        directory = (ROOT / row["directory"]).resolve()
        assert directory.is_relative_to(work)
        initial = runner.read(directory / "initial.snapshot.json")
        ref = initial["snapshot"]["state"]["trip_spec"]["selected_offer"]
        assert ref["command_id"].startswith("imported-source:"), "Decision only applies to the declared invalid generator"
        invalid.append({**row, "invalid_reason": {"category": "runner_error", "adjudicator": audit["reviewer"],
            "detail": "Imported offer command ID violated its reference contract before actor input; all affected workflows invalidated uniformly."},
            "initial_snapshot_sha256": file_sha(directory / "initial.snapshot.json"),
            "raw_stop_reason": runner.read(directory / "collection.json")["stop_reason"]})
    runner.write(work / "invalidated-attempts.json", {"adjudication_sha256": file_sha(work / "fixture-adjudication.json"),
        "created_at": now(), "attempts": invalid, "unstarted_affected_cases": sorted(affected - {r["case_id"] for r in invalid})})
    print(json.dumps({"invalid_evaluator_attempts": len(invalid), "old_evidence_retained": True}))


def repository_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Evaluation path must stay in this repository")
    return path


def model_config():
    settings = runner.project_settings(ROOT / "output/quality-preflight-unstarted")
    actual = {field: getattr(settings, field) for field in ("agent_mode", "max_model_tokens", "max_tool_calls", "max_run_seconds")}
    actual.update(model=settings.openai_model, timeout_seconds=settings.openai_timeout_seconds,
                  max_retries=settings.openai_max_retries, vision_enabled=settings.browser_vision_enabled)
    return actual


def product_files():
    paths = [* (ROOT / "backend/plango").rglob("*.py"),
             * (ROOT / "vendor/plango_harness/backend/plango_harness").rglob("*.py")]
    for directory in ("src", "out", "skills"):
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file())
    paths.extend(ROOT / name for name in ("pyproject.toml", "uv.lock", "package.json", "package-lock.json", "electron.vite.config.ts", "tsconfig.json", "tsconfig.node.json", "tsconfig.web.json"))
    return {str(repository_path(path).relative_to(ROOT)): file_sha(path) for path in sorted(set(paths))}


def freeze_product(path):
    path = repository_path(path)
    if not (ROOT / "out/main/index.js").is_file() or not (ROOT / "out/renderer/index.html").is_file():
        raise ValueError("Build the product before freezing")
    frozen = {"schema_version": 2, "frozen_at": now(), "product_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "files": product_files(), "model_config": model_config(),
        "worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True)),
        "scope": "Frozen source/build/model for separately preregistered bounded controlled datasets",
        "rules": ["Freeze before independent case authoring; review all gold before execution.",
                  "Keep every valid failure and all attempts; never overwrite a previous freeze."]}
    path.parent.mkdir(parents=True, exist_ok=True)
    runner.write(path, frozen)
    print(json.dumps({"freeze": str(path.relative_to(ROOT)), "files": len(frozen["files"]), "model_calls": 0}))


def product(freeze=None):
    frozen = runner.read(repository_path(freeze or DATA / "product-freeze.json"))
    for path, digest in frozen["files"].items():
        if file_sha(repository_path(ROOT / path)) != digest:
            raise ValueError("Frozen product changed: " + path)
    if frozen.get("schema_version") == 2 and product_files() != frozen["files"]:
        raise ValueError("Frozen product file inventory changed")
    actual = model_config()
    if actual != frozen["model_config"]:
        raise ValueError("Frozen model configuration changed")
    return {"revision": frozen["product_commit"], "files": frozen["files"], "model_config": actual}


def default_fixture(source_id):
    """Declared common world, independent of any expected edit or answer."""
    return {"source_id": "ENV-" + source_id, "scope": "虚构评测坐标及单段步行路况，非真实高德/商家供给",
            "origin": {"name": "受控评测起点", "latitude": 29.56, "longitude": 106.57, "city_code": "023"},
            "place": {"latitude": 29.561, "longitude": 106.571, "category": "餐厅", "average_price": 0,
                      "price_known": False, "source": "simulated", "rating": 0, "open_minute": None, "close_minute": None},
            "route": {"mode": "walking", "distance_km": 0.2, "walking_min": 3, "cost_per_person": 0},
            "supply": {"open_now": None, "reservable": None, "seats_left": None, "estimated_wait_min": None, "source": "unknown"},
            "weather": {"status": "unknown", "reason": "受控资料未提供天气"},
            "initial_draft": {"arrival_offset_minutes": 3, "dwell_minutes": 80, "save_state": "not_saved",
                              "business_completed": False, "unknowns": ["完整餐费未知", "营业及供给未知", "已选优惠完整使用规则未核验"]}}


def dataset_metadata(dataset, tasks, sources):
    path = dataset / "protocol.json"
    if path.exists():
        protocol = runner.read(path)
        result = {"scope": "controlled_acceptance", "protocol": protocol,
                  **{key: protocol[key] for key in ("name", "planned_case_ids", "primary_source_groups", "case_authoring", "execution_scope")}}
        validate_protocol(result)
        assert protocol["planned_case_ids"] == [t["case_id"] for t in tasks], "Preregistered case order changed"
        assert protocol["primary_source_groups"] == len({s["group_id"] for s in sources})
        repository_path(ROOT / protocol["budget_ledger"])
        return result
    assert dataset == DATA.resolve(), "New datasets require protocol.json"
    assert len(tasks) == 30 and len(sources) == 15
    assert Counter(t["case_class"] for t in tasks) == {"delivery": 20, "bounded_answer": 10}
    assert Counter(t["family"] for t in tasks) == {key: 5 for key in ("reading", "offers", "edits", "routes", "recovery", "boundaries")}
    assert Counter(s for t in tasks for s in t["source_ids"]) == {s["source_id"]: 2 for s in sources}
    return {"name": "PlanGo-controlled-30-v1", "scope": "controlled_acceptance",
            "planned_case_ids": [t["case_id"] for t in tasks], "primary_source_groups": 15,
            "case_authoring": "Independent AI author did not inspect implementation/dev outputs; human review pending",
            "execution_scope": "20 raw-observation cases and 10 imported-state workflows; all ten workflows use real Electron"}


def materials(dataset=None):
    dataset = repository_path(dataset or DATA)
    tasks, sources, gold = [runner.read(dataset / name) for name in ("tasks.json", "sources.json", "gold.json")]
    assert len(tasks) == len(gold) == len({row["case_id"] for row in tasks})
    dataset_metadata(dataset, tasks, sources)
    by_source, by_gold = {s["source_id"]: s for s in sources}, {g["case_id"]: g for g in gold}
    assert len(by_source) == len(sources) and len(by_gold) == len(gold)
    assert set(by_gold) == {t["case_id"] for t in tasks}
    assert {sid for t in tasks for sid in t["source_ids"]} == set(by_source)
    for task in tasks:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", task["case_id"])
        assert len(task["source_ids"]) == 1, "Collector supports one declared primary source per case"
        assert task["environment"]["driver"] in {"read", *STATE_DRIVERS}
        assert not ({"gold", "gold_facts", "must_pass", "forbidden_claims"} & task.keys()), "Judging material must not enter actor tasks"
    fixtures = {t["source_ids"][0]: default_fixture(t["source_ids"][0]) for t in tasks if t["environment"]["driver"] in STATE_DRIVERS}
    path = dataset / "runtime-fixtures.json"
    if path.exists():
        assert runner.read(path) == fixtures, "Runtime fixture changed; never silently rewrite reviewed data"
    else:
        runner.write(path, fixtures)
    return tasks, by_source, by_gold, fixtures


def source_packets(case, source, fixtures):
    content = {"title": source["title"], "text": source["text"]}
    if "initial_state" in case["environment"]:
        original = case["environment"]["initial_state"]
        assert source["structured_initial_state"][case["case_id"]] == original
        content["structured_initial_state"] = {case["case_id"]: original}
    values = [{"source_id": source["source_id"], "source_kind": "explicit_synthetic",
               "content": content, "observed_at": source["observed_at"], "available_at_checkpoint_ids": [],
               "sha256": human.canonical_sha(content), "freshness_policy": "仅为所声明时间的受控资料，非实时商家事实"}]
    if case["environment"]["driver"] in STATE_DRIVERS:
        fixture = fixtures[source["source_id"]]
        values.append({"source_id": fixture["source_id"], "source_kind": "explicit_synthetic_world",
                       "content": fixture, "observed_at": case["as_of"], "available_at_checkpoint_ids": [],
                       "sha256": human.canonical_sha(fixture), "freshness_policy": "单点/单路段预声明受控初态；未保存；无真实业务"})
    return values


def prepare(work, *, dataset=None, freeze=None):
    dataset = repository_path(dataset or DATA)
    freeze = repository_path(freeze or dataset / "product-freeze.json")
    frozen = product(freeze)
    work = repository_path(work)
    assert work.is_relative_to(ROOT / "output")
    assert not work.exists(), "Never reuse a previous work directory"
    tasks, sources, gold, fixtures = materials(dataset)
    metadata = dataset_metadata(dataset, tasks, list(sources.values()))
    work.mkdir(mode=0o700, parents=True, exist_ok=False)
    cases = []
    for task in tasks:
        cid = task["case_id"]
        cases.append({**{key: task[key] for key in ("case_id", "case_class", "family", "group_ids")},
                      "packet": {"case_id": cid, "trial_id": cid + ":first", "task": task, "gold": gold[cid],
                                 "source_packets": source_packets(task, sources[task["source_ids"][0]], fixtures),
                                 "outputs": [], "independent_state": {}, "transport_checks": {},
                                 "all_delivery_checkpoints_captured": False, "attempt_outcome": "completed"}})
    bundle = human.seal_bundle({"schema_version": 1, "report_kind": "human_reviewed_controlled",
             "dataset": metadata,
             "product": frozen, "cases": cases, "collection": {"attempts": []}})
    runner.write(work / "gold-bundle.json", bundle)
    (work / "human-events.jsonl").touch(mode=0o600, exist_ok=False)
    inputs = {name: dataset / name for name in ("sources.json", "tasks.json", "gold.json", "runtime-fixtures.json")}
    inputs["product-freeze.json"] = freeze
    if (dataset / "protocol.json").exists():
        inputs["protocol.json"] = dataset / "protocol.json"
        protocol = metadata["protocol"]
        with runner.shared_budget(ROOT / protocol["budget_ledger"], protocol.get("limits") or {}):
            pass
    runner.write(work / "session.json", {"created_at": now(), "dataset_sha": bundle["dataset_sha"],
        "product_sha": bundle["product_sha"], "dataset_path": str(dataset.relative_to(ROOT)), "freeze_path": str(freeze.relative_to(ROOT)),
        "input_paths": {key: str(path.relative_to(ROOT)) for key, path in inputs.items()},
        "source_files": {name: file_sha(path) for name, path in inputs.items()},
        "mode": "awaiting_per_case_human_gold_review", "model_calls": 0})
    print(json.dumps({"bundle": str((work / "gold-bundle.json").relative_to(ROOT)), "cases": len(tasks),
                      "human_gold_approved": 0, "model_calls": 0}, ensure_ascii=False))


def session_inputs(work, *, dataset=None, freeze=None):
    session = runner.read(work / "session.json")
    bound_dataset = ROOT / session["dataset_path"] if "dataset_path" in session else DATA
    bound_freeze = ROOT / session["freeze_path"] if "freeze_path" in session else DATA / "product-freeze.json"
    selected, frozen = repository_path(dataset or bound_dataset), repository_path(freeze or bound_freeze)
    assert selected == repository_path(bound_dataset), "Session dataset path changed"
    assert frozen == repository_path(bound_freeze), "Session freeze path changed"
    paths = {name: repository_path(ROOT / session["input_paths"][name]) if "input_paths" in session else selected / name for name in session["source_files"]}
    for name, path in paths.items():
        assert file_sha(path) == session["source_files"][name], "Reviewed input changed: " + name
    return selected, frozen, paths


def run(work, case_ids, *, gold_review=None, dataset=None, freeze=None):
    work = repository_path(work)
    bundle = runner.read(work / "gold-bundle.json")
    protocol = bundle["dataset"].get("protocol")
    validate_protocol(bundle["dataset"])
    if protocol:
        ledger = repository_path(ROOT / protocol["budget_ledger"])
        assert ledger.exists(), "Shared budget ledger missing; never restart its allowance"
        budget = runner.shared_budget(ledger, protocol.get("limits") or {}) if protocol else nullcontext({"calls": [], "reported_tokens": 0, "stop": None})
    with budget as control:
        _run(work, case_ids, gold_review=gold_review, dataset=dataset, freeze=freeze, control=control)


def _run(work, case_ids, *, gold_review=None, dataset=None, freeze=None, control):
    work = Path(work).resolve()
    bundle = runner.read(work / "gold-bundle.json")
    dataset, freeze, inputs = session_inputs(work, dataset=dataset, freeze=freeze)
    if gold_review:
        from quality_ai_import import validate_gold
        issues = validate_gold(bundle, runner.read(gold_review))
        if issues:
            raise ValueError("Independent AI gold review is incomplete: " + str(issues))
    else:
        status = human.import_reviews(bundle, events(work))
        if status["release"]["gold_reviewed_cases"] != len(bundle["cases"]):
            raise ValueError("All preregistered source/gold cases need review before execution")
    assert product(freeze) == bundle["product"]
    tasks, sources, _, fixtures = materials(dataset)
    planned = [t["case_id"] for t in tasks]
    assert case_ids and len(case_ids) == len(set(case_ids)) and set(case_ids) <= set(planned)
    registry = work / "attempt-registry.jsonl"
    previous = [json.loads(x) for x in registry.read_text().splitlines()] if registry.exists() else []
    invalid = invalid_attempts(work)
    prior = {cid: [r for r in previous if r["case_id"] == cid] for cid in case_ids}
    assert all(not rows or rows[-1]["trial_id"] in invalid for rows in prior.values()), "Never replace a valid primary attempt"
    adapter_paths = [ROOT / "scripts" / name for name in ("quality_acceptance.py", "quality_runner.py", "quality_state_cases.py", "quality_desktop_cases.cjs", "export_quality_output.ts", "quality_ai_import.py", "quality_human_import.py", "quality_judge.py", "quality_scoring.py")]
    def manifest():
        product(freeze)
        return {"product_sha": bundle["product_sha"], "dataset_sha": bundle["dataset_sha"], "case_ids": case_ids,
                "dataset_path": str(dataset.relative_to(ROOT)), "freeze_path": str(freeze.relative_to(ROOT)),
                "input_paths": {name: str(path.relative_to(ROOT)) for name, path in inputs.items()},
                "inputs": {name: file_sha(path) for name, path in inputs.items()},
                "adapters": {str(path.relative_to(ROOT)): file_sha(path) for path in adapter_paths},
                "gold_review_sha256": file_sha(gold_review or work / "human-events.jsonl"),
                "invalidations_sha256": {p.name: file_sha(p) for p in (work / "invalidated-attempts.json", work / "preinput-invalidations.json", work / "transport-invalidations.json") if p.exists()},
                "reviewer_type": "independent_ai" if gold_review else "human",
                "limits": control.get("limits", {"calls": runner.MAX_BATCH_CALLS, "case_calls": runner.MAX_CASE_CALLS, "reported_tokens_stop": runner.MAX_BATCH_REPORTED_TOKENS})}
    frozen = manifest()
    source_sha = runner.digest(frozen)
    folder = work / ("batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    folder.mkdir(mode=0o700)
    runner.write(folder / "manifest.json", {**frozen, "source_sha": source_sha, "product_commit": bundle["product"]["revision"]})
    packets = {"packets": [{"packet_id": sid, "source_id": sid, "case_ids": [t["case_id"] for t in tasks if sid in t["source_ids"]],
        "kind": "explicit_synthetic", "source_url": "https://plango-eval.invalid/" + sid,
        "observed_at": s["observed_at"], "payload": {"title": s["title"], "text": s["text"]}} for sid, s in sources.items()]}
    initial_calls, initial_tokens, collected = len(control["calls"]), control["reported_tokens"], []
    for case in tasks:
        cid = case["case_id"]
        if cid not in case_ids:
            continue
        if control["stop"]:
            break
        directory = folder / cid
        directory.mkdir(mode=0o700)
        label = "replacement-" + str(len(prior[cid])) if prior[cid] else "first"
        replacement_for = prior[cid][-1]["trial_id"] if prior[cid] else None
        with registry.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"case_id": cid, "directory": str(directory.relative_to(ROOT)), "reserved_at": now(),
                                     "trial_id": cid + ":" + label, "replacement_for": replacement_for}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        settings = runner.project_settings(directory)
        fixture = fixtures.get(case["source_ids"][0]) if case["environment"]["driver"] in STATE_DRIVERS else None
        result = runner.collect_case(case, packets, settings, directory, control, source_sha, case_ids,
                    manifest_fn=manifest, desktop_edits=True, fixture=fixture, trial_id=label,
                    fixture_provenance={"path": str((dataset / "runtime-fixtures.json").relative_to(ROOT)), "json_pointer": "/" + case["source_ids"][0].replace("~", "~0").replace("/", "~1")} if fixture else None)
        collected.append(result)
        print(json.dumps({"case": cid, "stop": result["stop_reason"], "tokens": result["reported_tokens"]}), flush=True)
    runner.write(folder / "collection.json", {"planned": case_ids, "cases": collected, "stop": control["stop"],
        "reported_tokens": control["reported_tokens"] - initial_tokens, "model_invocations": len(control["calls"]) - initial_calls,
        "stage_reported_tokens": control["reported_tokens"], "stage_model_invocations": len(control["calls"]), "scope": "controlled_acceptance_collection"})
    print(json.dumps({"batch": str(folder.relative_to(ROOT)), "attempted": len(collected), "stop": control["stop"]}), flush=True)


def checkpoint_state(snapshot):
    """Only task/plan facts: full model messages and planning rationale stay private."""
    state = snapshot.get("state", {})
    result = {key: state.get(key) for key in ("trip_spec", "selected_poi", "plan_version", "turn_id", "turn_budget", "model_token_count", "tool_call_count")}
    plan = state.get("selected_plan")
    result["selected_plan"] = {key: plan.get(key) for key in ("plan_id", "version", "stops", "total_cost", "party_size", "party_counts", "evidence_ids")} if plan else None
    outcome = state.get("execution_outcome")
    result["execution_outcome"] = {**{key: outcome.get(key) for key in ("kind", "status", "evidence_ids")},
        "data": {key: (outcome.get("data") or {}).get(key) for key in ("kind", "scope", "interrupt_id", "plan_id", "plan_version", "business_completed", "execution_allowed")}} if outcome else None
    result["action_results"] = [{key: row.get(key) for key in ("action_id", "status", "idempotency_key")} for row in state.get("action_results", [])]
    result["evidence"] = [{key: row.get(key) for key in ("evidence_id", "source", "source_ref", "payload", "observed_at", "expires_at")} for row in state.get("evidence", [])]
    result["browser_artifacts"] = [{key: row.get(key) for key in ("artifact_id", "command_id", "url", "title", "observed_at", "source_id")} for row in state.get("browser_artifacts", [])]
    result["weather"] = state.get("weather")
    return result


def output_case(original, directory, *, fixtures_path=None, prior_capture_audit=None):
    """Retain static reviewed inputs; add actual outputs and independently read state."""
    case = copy.deepcopy(original)
    packet = case["packet"]
    result = runner.read(directory / "collection.json")
    packet["trial_id"] = case["case_id"] + ":" + result["trial_id"]
    checkpoints = result["checkpoints"]
    baseline_ids, outputs, available, hashes, desktop_events, desktop_states, desktop_ui = set(), [], [], {}, {}, {}, {}
    if prior_capture_audit:
        assert file_sha(directory / "collection.json") == prior_capture_audit["case_collection_sha256"], "Original case collection changed"
        for name, sha in prior_capture_audit["artifact_hashes"].items():
            path = (directory / name).resolve()
            assert path.is_relative_to(directory.resolve()) and file_sha(path) == sha, "Original captured artifact changed"
    def artifact(path):
        p = Path(path)
        p = p if p.is_absolute() else ROOT / p
        p = p.resolve()
        assert p.is_relative_to(directory.resolve())
        name = str(p.relative_to(directory))
        hashes[name] = file_sha(p)
        if prior_capture_audit and name in prior_capture_audit["artifact_hashes"]:
            assert hashes[name] == prior_capture_audit["artifact_hashes"][name], "Original captured artifact changed"
        return runner.read(p)
    for stage, capture in checkpoints.items():
        if capture.get("desktop_evidence"):
            envelope = artifact(capture["snapshot_path"])
            assert envelope.get("checkpoint", {}).get("captured_at", capture["captured_at"]) == capture["captured_at"]
            snapshot = envelope["snapshot"]
            assert snapshot.get("run_id") == result.get("run_id"), "Captured API run differs from attempt"
            raw_path = capture["desktop_evidence"].get("snapshot")
            if raw_path:
                raw = artifact(directory / raw_path)
                assert raw["snapshot"] == snapshot and raw["events"] == envelope["events"], "Original desktop API snapshot differs from exported envelope"
                assert raw["checkpoint"]["captured_at"] == capture["captured_at"]
            path = Path(capture["snapshot_path"])
            path = path if path.is_absolute() else ROOT / path
            state = snapshot.get("state", {})
            desktop_states[stage] = {"captured_at": capture["captured_at"], "source": "captured_API_snapshot_not_independent_SQL",
                "artifact_path": str(path.relative_to(directory)), "artifact_sha256": file_sha(path),
                "scope": "State at this checkpoint only; cannot substantiate any earlier output",
                **{key: snapshot.get(key) for key in ("run_id", "phase", "version", "event_seq", "selected_offer", "draft_review", "outcome")},
                "state": checkpoint_state(snapshot),
                "model_call_count": len(state.get("model_calls", []))}
            ui_path = capture["desktop_evidence"].get("ui")
            if ui_path:
                ui = artifact(directory / ui_path)
                desktop_ui[stage] = {"captured_at": capture["captured_at"], "source": "original_captured_desktop_UI_not_rerendered",
                    "artifact_path": ui_path, "artifact_sha256": file_sha(directory / ui_path), "active_run": (ui or {}).get("active_run")}
        if capture.get("scope") == "pre_task_context" and capture.get("visible_path"):
            baseline_ids.update(m["id"] for m in artifact(capture["visible_path"])["messages"])
    complete = "final" in checkpoints
    imported = runner.read(directory / "imported-state.json") if (directory / "imported-state.json").exists() else None
    if imported:
        sid = packet["task"]["source_ids"][0]
        env_source = next(s for s in packet["source_packets"] if s["source_id"] == "ENV-" + sid)
        assert imported["fixture_value_sha256"] == human.canonical_sha(env_source["content"])
        assert imported["fixture_sha256"] == file_sha(fixtures_path or DATA / "runtime-fixtures.json")
    for stage, capture in checkpoints.items():
        if capture.get("scope") in {"pre_task_context", "actual_owned_backend_restart_desktop_closed"}:
            continue
        if not capture.get("visible_path"):
            complete = False
            continue
        visible = artifact(capture["visible_path"])
        visible["messages"] = [m for m in visible["messages"] if m["id"] not in baseline_ids]
        visible["checkpoint"]["captured_at"] = capture["captured_at"]
        envelope = artifact(capture["snapshot_path"])
        observation = envelope["snapshot"].get("state", {}).get("browser_observation") or {}
        obtained = observation.get("fields", {}).get("evaluation_source", {}).get("packet_id") in packet["task"]["source_ids"]
        if imported and datetime.fromisoformat(imported["imported_at"]) <= datetime.fromisoformat(capture["captured_at"]):
            obtained = True
        if obtained:
            available.append(visible["checkpoint"]["id"])
        desktop = capture.get("desktop_evidence")
        if desktop:
            desktop_events[stage] = {"captured_at": capture["captured_at"], "events": envelope.get("events", [])}
        if desktop and desktop.get("ui"):
            ui = artifact(directory / desktop["ui"])
            if ui:
                visible.setdefault("context_output", {})["desktop_ui_text"] = ui["text"]
            if ui and ui.get("pending_status"):
                visible["cards"].append({"id": "actual-delivery-status", "kind": "desktop_delivery",
                    "views": [{"name": "actual captured UI", "rendered_text": ui["pending_status"]}],
                    "provenance": "Actual aria-label=消息发送状态 DOM text from this captured desktop checkpoint"})
            for index, notice in enumerate((ui or {}).get("outcome_notices", [])):
                visible["cards"].append({"id": "actual-outcome-notice-" + str(index), "kind": "desktop_notice",
                    "views": [{"name": "actual captured status", "rendered_text": notice}],
                    "provenance": "Actual role=status outcome notice from the frozen desktop UI"})
        outputs.append(visible)
    for source in packet["source_packets"]:
        source["available_at_checkpoint_ids"] = available.copy()
    transport = {}
    if imported:
        transport["initial_state_import"] = {key: imported[key] for key in ("run_id", "aliases", "imported_at", "fixture_sha256", "fixture_value_sha256", "scope") if key in imported}
        transport["initial_state_import"].update(source="original_import_binding_not_actor_output",
            declared_initial_plan_version=packet["task"]["environment"].get("initial_state", {}).get("plan_version"),
            artifact_sha256=file_sha(directory / "imported-state.json"))
        hashes["imported-state.json"] = file_sha(directory / "imported-state.json")
    for name in ("observations.jsonl", "egress.jsonl", "controlled-world.jsonl"):
        path = directory / name
        transport[name.removesuffix(".jsonl").replace("-", "_")] = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
        if path.exists():
            hashes[name] = file_sha(path)
    desktop_path = directory / "desktop-result.json"
    if desktop_path.exists():
        raw = runner.read(desktop_path)
        transport["desktop"] = {key: raw[key] for key in ("run_id", "started_at", "finished_at", "transport_counts", "driver_actions", "recovery", "restored_comparison", "cleanup", "desktop_restarted", "backend_restarted", "message_responses", "acceptances", "acceptance", "saved_baseline", "checkpoints") if key in raw}
        transport["desktop"]["checkpoint_events"] = desktop_events
        transport["desktop"]["checkpoint_states"] = desktop_states
        transport["desktop"]["checkpoint_ui"] = desktop_ui
        hashes[desktop_path.name] = file_sha(desktop_path)
    for name in ("desktop-result.json", "backend-lifecycle.json"):
        path = directory / "backend-restored" / name
        if path.exists():
            raw = artifact(path)
            if name == "desktop-result.json":
                transport["desktop_restore"] = {key: raw[key] for key in ("run_id", "phase", "status", "started_at", "finished_at", "transport_counts",
                    "driver_actions", "restored_comparison", "cleanup", "checkpoints", "original_desktop_result", "backend_restart", "error") if key in raw}
            else:
                transport["backend_restart"] = {key: raw[key] for key in ("stopped_at", "reopened_at", "closed_at", "original_desktop_result",
                    "run_id", "state_unchanged", "model_invocations", "scope")}
    stop = result["stop_reason"]
    outcome = stop if stop in {"timeout", "external_blocked", "budget_exhausted"} else "completed" if stop in {"completed", "script_finished"} else "product_failure"
    if stop == "unscripted_clarification" and packet["task"]["environment"]["expected_stop"] == "clarification":
        outcome = "completed"  # Reached a declared boundary; the human still checks its fields/content.
    packet.update(outputs=outputs, all_delivery_checkpoints_captured=complete, attempt_outcome=outcome,
        independent_state={"checkpoints": {stage: {"independent_state": c.get("independent_state"), "captured_at": c["captured_at"]} for stage, c in checkpoints.items()}},
        transport_checks=transport, capture_audit={"raw_stop_reason": stop, "excluded_baseline_message_ids": sorted(baseline_ids),
                                                  "artifact_hashes": hashes, "case_collection_sha256": file_sha(directory / "collection.json")})
    return case


def bundle_outputs(work, target, *, dataset=None, freeze=None):
    work = Path(work).resolve()
    before = runner.read(work / "gold-bundle.json")
    dataset, freeze, inputs = session_inputs(work, dataset=dataset, freeze=freeze)
    registry = [json.loads(x) for x in (work / "attempt-registry.jsonl").read_text().splitlines()]
    by_id = {row["case_id"]: row for row in registry}
    invalid = invalid_attempts(work)
    assert set(by_id) == set(before["dataset"]["planned_case_ids"]), "Every planned case needs a valid attempt"
    assert all(row["trial_id"] not in invalid for row in by_id.values()), "Every invalid case still needs its declared replacement"
    assert product(freeze) == before["product"]
    after = copy.deepcopy(before)
    after["collection"] = {"attempts": []}
    previous_bundle = runner.read(work / "output-bundle.json") if (work / "output-bundle.json").exists() else None
    if previous_bundle:
        assert all(previous_bundle[key] == before[key] for key in ("dataset_sha", "product_sha")), "Original bundle binding changed"
    for index, case in enumerate(before["cases"]):
        directory = (ROOT / by_id[case["case_id"]]["directory"]).resolve()
        assert directory.is_relative_to(work)
        manifest = runner.read(directory.parent / "manifest.json")
        assert all(manifest[key] == before[key] for key in ("product_sha", "dataset_sha")), "Mixed product/dataset collection"
        assert manifest["inputs"] == {name: file_sha(path) for name, path in inputs.items()}, "Collection input drift"
        prior = next((c["packet"]["capture_audit"] for c in (previous_bundle or {}).get("cases", [])
                      if c["case_id"] == case["case_id"] and c["packet"]["trial_id"] == by_id[case["case_id"]]["trial_id"]), None)
        row = output_case(case, directory, fixtures_path=dataset / "runtime-fixtures.json", prior_capture_audit=prior)
        after["cases"][index] = row
        for attempt in (r for r in registry if r["case_id"] == row["case_id"]):
            bad = invalid.get(attempt["trial_id"])
            after["collection"]["attempts"].append({"case_id": row["case_id"], "trial_id": attempt["trial_id"],
                 "valid_attempt": not bool(bad), "outcome": None if bad else row["packet"]["attempt_outcome"],
                 "replacement_for": attempt.get("replacement_for"), "invalid_reason": bad["invalid_reason"] if bad else None,
                 "observed_stop_reason": bad["raw_stop_reason"] if bad else row["packet"]["capture_audit"]["raw_stop_reason"],
                 "evidence_directory": attempt["directory"]})
    for key in ("dataset_sha", "product_sha", "collection_sha"):
        after.pop(key, None)
    after = human.seal_bundle(after)
    assert after["dataset_sha"] == before["dataset_sha"] and after["product_sha"] == before["product_sha"]
    runner.write(target, after)
    for case in after["cases"]:
        prepared = judge.prepare_case(case["packet"])
        print(json.dumps({"case_id": case["case_id"], "surfaces": len(prepared["required_output_surfaces"]), "issues": prepared["preparation_issues"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "prepare", "run", "bundle", "adjudicate-fixture", "adjudicate-preinput", "adjudicate-transport"))
    parser.add_argument("--work", type=Path)
    parser.add_argument("--dataset", type=Path, help="Explicit dataset directory; defaults to the original 30-case set or prepared session")
    parser.add_argument("--freeze", type=Path, help="Explicit product freeze; freeze action writes only a new file")
    parser.add_argument("--cases", help="Comma-separated IDs from the frozen plan; all planned cases require valid attempts and reviews")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--gold-review", type=Path, help="Actual independent AI gold review explicitly delegated by the user")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "freeze":
        if not args.freeze:
            parser.error("freeze requires an explicit new --freeze path")
        freeze_product(args.freeze)
        return
    if not args.work:
        parser.error("--work is required")
    if args.action == "prepare":
        prepare(args.work, dataset=args.dataset, freeze=args.freeze)
    elif args.action == "run":
        run(args.work, (args.cases or "").split(","), gold_review=args.gold_review, dataset=args.dataset, freeze=args.freeze)
    elif args.action == "adjudicate-fixture":
        adjudicate_fixture(args.work)
    elif args.action == "adjudicate-preinput":
        adjudicate_preinput(args.work)
    elif args.action == "adjudicate-transport":
        adjudicate_transport(args.work)
    else:
        assert args.output, "bundle requires a new --output path"
        bundle_outputs(args.work, args.output, dataset=args.dataset, freeze=args.freeze)


if __name__ == "__main__":
    main()
