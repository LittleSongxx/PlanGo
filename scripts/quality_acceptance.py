#!/usr/bin/env python3
"""Prepare and execute the frozen 30-case controlled acceptance release.

Human gold approval is required before run. Only evaluation adapters change;
the product/model freeze and the first-attempt registry are enforced throughout.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import quality_human_import as human
import quality_judge as judge
import quality_runner as runner

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
                             ("preinput-invalidations.json", "startup-adjudication.json")):
        path = Path(work) / filename
        if path.exists():
            ledger = runner.read(path)
            assert ledger["adjudication_sha256"] == file_sha(Path(work) / review)
            for row in ledger["attempts"]:
                assert row["trial_id"] not in result
                result[row["trial_id"]] = row
    return result


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


def product():
    frozen = runner.read(DATA / "product-freeze.json")
    for path, digest in frozen["files"].items():
        if file_sha(ROOT / path) != digest:
            raise ValueError("Frozen product changed: " + path)
    settings = runner.project_settings(ROOT / "output/quality-v2-30-unstarted")
    actual = {field: getattr(settings, field) for field in ("agent_mode", "max_model_tokens", "max_tool_calls", "max_run_seconds")}
    actual.update(model=settings.openai_model, timeout_seconds=settings.openai_timeout_seconds,
                  max_retries=settings.openai_max_retries, vision_enabled=settings.browser_vision_enabled)
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


def materials():
    tasks, sources, gold = [runner.read(DATA / name) for name in ("tasks.json", "sources.json", "gold.json")]
    assert len(tasks) == len(gold) == 30 and len(sources) == 15
    assert len({row["case_id"] for row in tasks}) == 30
    assert Counter(t["case_class"] for t in tasks) == {"delivery": 20, "bounded_answer": 10}
    assert Counter(t["family"] for t in tasks) == {key: 5 for key in ("reading", "offers", "edits", "routes", "recovery", "boundaries")}
    assert Counter(s for t in tasks for s in t["source_ids"]) == {s["source_id"]: 2 for s in sources}
    by_source, by_gold = {s["source_id"]: s for s in sources}, {g["case_id"]: g for g in gold}
    assert set(by_gold) == {t["case_id"] for t in tasks}
    fixtures = {t["source_ids"][0]: default_fixture(t["source_ids"][0]) for t in tasks if t["environment"]["driver"] in STATE_DRIVERS}
    path = DATA / "runtime-fixtures.json"
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


def prepare(work):
    frozen = product()
    tasks, sources, gold, fixtures = materials()
    work = Path(work).resolve()
    assert work.is_relative_to(ROOT / "output")
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
             "dataset": {"name": "PlanGo-controlled-30-v1", "scope": "controlled_acceptance",
                         "planned_case_ids": [t["case_id"] for t in tasks], "primary_source_groups": 15,
                         "case_authoring": "Independent AI author did not inspect implementation/dev outputs; human review pending",
                         "execution_scope": "20 raw-observation cases and 10 imported-state workflows; all ten workflows use real Electron"},
             "product": frozen, "cases": cases, "collection": {"attempts": []}})
    runner.write(work / "gold-bundle.json", bundle)
    (work / "human-events.jsonl").touch(mode=0o600, exist_ok=False)
    runner.write(work / "session.json", {"created_at": now(), "dataset_sha": bundle["dataset_sha"],
        "product_sha": bundle["product_sha"], "source_files": {name: file_sha(DATA / name) for name in (
            "sources.json", "tasks.json", "gold.json", "runtime-fixtures.json", "product-freeze.json")},
        "mode": "awaiting_per_case_human_gold_review", "model_calls": 0})
    print(json.dumps({"bundle": str((work / "gold-bundle.json").relative_to(ROOT)), "cases": 30,
                      "human_gold_approved": 0, "model_calls": 0}, ensure_ascii=False))


def run(work, case_ids, *, gold_review=None):
    work = Path(work).resolve()
    bundle = runner.read(work / "gold-bundle.json")
    if gold_review:
        from quality_ai_import import validate_gold
        issues = validate_gold(bundle, runner.read(gold_review))
        if issues:
            raise ValueError("Independent AI gold review is incomplete: " + str(issues))
    else:
        status = human.import_reviews(bundle, events(work))
        if status["release"]["gold_reviewed_cases"] != 30:
            raise ValueError("All 30 source/gold cases need review before execution")
    assert product() == bundle["product"]
    session = runner.read(work / "session.json")
    for name, digest in session["source_files"].items():
        assert file_sha(DATA / name) == digest, "Reviewed input changed: " + name
    tasks, sources, _, fixtures = materials()
    planned = [t["case_id"] for t in tasks]
    assert case_ids and len(case_ids) == len(set(case_ids)) and set(case_ids) <= set(planned)
    registry = work / "attempt-registry.jsonl"
    previous = [json.loads(x) for x in registry.read_text().splitlines()] if registry.exists() else []
    invalid = invalid_attempts(work)
    prior = {cid: [r for r in previous if r["case_id"] == cid] for cid in case_ids}
    assert all(not rows or rows[-1]["trial_id"] in invalid for rows in prior.values()), "Never replace a valid primary attempt"
    adapter_paths = [ROOT / "scripts" / name for name in ("quality_acceptance.py", "quality_runner.py", "quality_state_cases.py", "quality_desktop_cases.cjs", "export_quality_output.ts", "quality_ai_import.py", "quality_human_import.py", "quality_judge.py", "quality_scoring.py")]
    def manifest():
        product()
        return {"product_sha": bundle["product_sha"], "dataset_sha": bundle["dataset_sha"], "case_ids": case_ids,
                "inputs": {name: file_sha(DATA / name) for name in session["source_files"]},
                "adapters": {str(path.relative_to(ROOT)): file_sha(path) for path in adapter_paths},
                "gold_review_sha256": file_sha(gold_review or work / "human-events.jsonl"),
                "invalidations_sha256": {p.name: file_sha(p) for p in (work / "invalidated-attempts.json", work / "preinput-invalidations.json") if p.exists()},
                "reviewer_type": "independent_ai" if gold_review else "human",
                "limits": {"calls": runner.MAX_BATCH_CALLS, "case_calls": runner.MAX_CASE_CALLS, "reported_tokens_stop": runner.MAX_BATCH_REPORTED_TOKENS}}
    frozen = manifest()
    source_sha = runner.digest(frozen)
    folder = work / ("batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    folder.mkdir(mode=0o700)
    runner.write(folder / "manifest.json", {**frozen, "source_sha": source_sha, "product_commit": bundle["product"]["revision"]})
    packets = {"packets": [{"packet_id": sid, "source_id": sid, "case_ids": [t["case_id"] for t in tasks if sid in t["source_ids"]],
        "kind": "explicit_synthetic", "source_url": "https://plango-eval.invalid/" + sid,
        "observed_at": s["observed_at"], "payload": {"title": s["title"], "text": s["text"]}} for sid, s in sources.items()]}
    control, collected = {"calls": [], "reported_tokens": 0, "stop": None}, []
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
                    fixture_provenance={"path": str((DATA / "runtime-fixtures.json").relative_to(ROOT)), "json_pointer": "/" + case["source_ids"][0]} if fixture else None)
        collected.append(result)
        print(json.dumps({"case": cid, "stop": result["stop_reason"], "tokens": result["reported_tokens"]}), flush=True)
    runner.write(folder / "collection.json", {"planned": case_ids, "cases": collected, "stop": control["stop"],
        "reported_tokens": control["reported_tokens"], "model_invocations": len(control["calls"]), "scope": "controlled_acceptance_collection"})
    print(json.dumps({"batch": str(folder.relative_to(ROOT)), "attempted": len(collected), "stop": control["stop"]}), flush=True)


def output_case(original, directory):
    """Retain static reviewed inputs; add actual outputs and independently read state."""
    case = copy.deepcopy(original)
    packet = case["packet"]
    result = runner.read(directory / "collection.json")
    packet["trial_id"] = case["case_id"] + ":" + result["trial_id"]
    checkpoints = result["checkpoints"]
    baseline_ids, outputs, available, hashes = set(), [], [], {}
    def artifact(path):
        p = Path(path)
        p = p if p.is_absolute() else ROOT / p
        p = p.resolve()
        assert p.is_relative_to(directory.resolve())
        hashes[str(p.relative_to(directory))] = file_sha(p)
        return runner.read(p)
    for stage, capture in checkpoints.items():
        if capture.get("scope") == "pre_task_context" and capture.get("visible_path"):
            baseline_ids.update(m["id"] for m in artifact(capture["visible_path"])["messages"])
    complete = "final" in checkpoints
    imported = runner.read(directory / "imported-state.json") if (directory / "imported-state.json").exists() else None
    if imported:
        sid = packet["task"]["source_ids"][0]
        env_source = next(s for s in packet["source_packets"] if s["source_id"] == "ENV-" + sid)
        assert imported["fixture_value_sha256"] == human.canonical_sha(env_source["content"])
        assert imported["fixture_sha256"] == file_sha(DATA / "runtime-fixtures.json")
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
        if desktop and desktop.get("ui"):
            ui = artifact(directory / desktop["ui"])
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
    for name in ("observations.jsonl", "egress.jsonl", "controlled-world.jsonl"):
        path = directory / name
        transport[name.removesuffix(".jsonl").replace("-", "_")] = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
        if path.exists():
            hashes[name] = file_sha(path)
    desktop_path = directory / "desktop-result.json"
    if desktop_path.exists():
        raw = runner.read(desktop_path)
        transport["desktop"] = {key: raw[key] for key in ("transport_counts", "driver_actions", "recovery", "restored_comparison", "cleanup", "desktop_restarted") if key in raw}
        hashes[desktop_path.name] = file_sha(desktop_path)
    stop = result["stop_reason"]
    outcome = stop if stop in {"timeout", "external_blocked", "budget_exhausted"} else "completed" if stop in {"completed", "script_finished"} else "product_failure"
    if stop == "unscripted_clarification" and packet["task"]["environment"]["expected_stop"] == "clarification":
        outcome = "completed"  # Reached a declared boundary; the human still checks its fields/content.
    packet.update(outputs=outputs, all_delivery_checkpoints_captured=complete, attempt_outcome=outcome,
        independent_state={"checkpoints": {stage: {"independent_state": c.get("independent_state"), "captured_at": c["captured_at"]} for stage, c in checkpoints.items()}},
        transport_checks=transport, capture_audit={"raw_stop_reason": stop, "excluded_baseline_message_ids": sorted(baseline_ids),
                                                  "artifact_hashes": hashes, "case_collection_sha256": file_sha(directory / "collection.json")})
    return case


def bundle_outputs(work, target):
    work = Path(work).resolve()
    before = runner.read(work / "gold-bundle.json")
    registry = [json.loads(x) for x in (work / "attempt-registry.jsonl").read_text().splitlines()]
    by_id = {row["case_id"]: row for row in registry}
    invalid = invalid_attempts(work)
    assert len(by_id) == 30 and set(by_id) == set(before["dataset"]["planned_case_ids"])
    assert all(row["trial_id"] not in invalid for row in by_id.values()), "Every invalid case still needs its declared replacement"
    assert product() == before["product"]
    after = copy.deepcopy(before)
    after["collection"] = {"attempts": []}
    for index, case in enumerate(before["cases"]):
        directory = (ROOT / by_id[case["case_id"]]["directory"]).resolve()
        assert directory.is_relative_to(work)
        row = output_case(case, directory)
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
    parser.add_argument("action", choices=("prepare", "run", "bundle", "adjudicate-fixture", "adjudicate-preinput"))
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--cases", help="Predeclared subset, normally ten cases per serial batch")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--gold-review", type=Path, help="Actual independent AI gold review explicitly delegated by the user")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "prepare":
        prepare(args.work)
    elif args.action == "run":
        run(args.work, (args.cases or "").split(","), gold_review=args.gold_review)
    elif args.action == "adjudicate-fixture":
        adjudicate_fixture(args.work)
    elif args.action == "adjudicate-preinput":
        adjudicate_preinput(args.work)
    else:
        assert args.output, "bundle requires a new --output path"
        bundle_outputs(args.work, args.output)


if __name__ == "__main__":
    main()
