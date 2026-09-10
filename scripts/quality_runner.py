#!/usr/bin/env python3
"""Bounded known-dev collection. Real model, isolated local API, frozen observations.

Default: preflight only. --run-dev requires the exact preflight digest. This
collector does not read gold answers, grade itself, or run held-out evaluations.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "vendor/plango_harness/backend")]

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from check_live_services import account_or_quota, safe_error  # noqa: E402
from check_live_task_quality import usage_of  # noqa: E402
from migrate_config import parse_config  # noqa: E402
from plango.app import create_app  # noqa: E402
from plango.settings import DesktopSettings  # noqa: E402
from quality_state_cases import case_driver, run_edits, seed  # noqa: E402

DATA = ROOT / "eval/quality-v1"
STATE_CASES = {"DEV-05", "DEV-06", "DEV-09", "DEV-10"}
MAX_BATCH_CALLS = 80
MAX_CASE_CALLS = 12
MAX_BATCH_REPORTED_TOKENS = 120000
TERMINAL = {"SUCCEEDED", "PARTIAL_FAILED", "FAILED", "INFEASIBLE", "CANCELLED"}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def append(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


_BUDGET_STOPS = frozenset({"batch_limit", "case_request_limit"})


@contextmanager
def shared_budget(path, limits):
    """Append-only usage log. Call and token counts are not an admission gate.

    An unfinished started row still blocks: that is missing usage, not a cap.
    Historic batch/case budget stops stay in the file and are ignored.
    """
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream.seek(0)
        rows = [json.loads(line) for line in stream if line.strip()]
        header = {"schema": "plango.quality-stage-budget.v1", "limits": limits or {}}
        if not rows:
            append(path, header)
        started = [row for row in rows[1:] if row["event"] == "started"]
        completed = [row for row in rows[1:] if row["event"] == "completed"]
        ids = [row["call_id"] for row in started]
        if len(ids) != len(set(ids)) or sorted(ids) != sorted(row["call_id"] for row in completed):
            raise ValueError("Shared stage has an unfinished model call; retain ledger and resolve actual usage")
        stop = next((row["stop"] for row in completed if row.get("stop") and row["stop"] not in _BUDGET_STOPS), None)
        tokens = sum(row["reported_tokens"] for row in completed)
        if stop:
            raise ValueError("Shared stage stopped: " + stop)
        yield {"calls": started, "reported_tokens": tokens, "stop": None, "limits": limits or {}, "budget_ledger": path}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def manifest(case_ids):
    paths = [*sorted((ROOT / "backend/plango").rglob("*.py")),
             *sorted((ROOT / "vendor/plango_harness/backend/plango_harness").rglob("*.py")),
             *sorted((ROOT / "src").rglob("*.ts")), *sorted((ROOT / "src").rglob("*.tsx")),
             ROOT / "scripts/quality_runner.py", ROOT / "scripts/quality_state_cases.py", ROOT / "scripts/quality_desktop_cases.cjs",
             ROOT / "scripts/export_quality_output.ts", ROOT / "scripts/check_live_task_quality.py",
             ROOT / "scripts/check_live_services.py", ROOT / "scripts/migrate_config.py",
             *[DATA / name for name in ("tasks.dev.json", "source-packets.dev.json", "runtime-fixtures.dev.json", "gold.dev.json", "oracle-checks.dev.json")],
             ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "package-lock.json"]
    for path in (ROOT / "out").rglob("*"):
        if path.is_file():
            paths.append(path)
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(paths))}
    return {"files": hashes, "case_ids": case_ids, "limits": {"batch_model_invocations": MAX_BATCH_CALLS,
            "case_model_invocations": MAX_CASE_CALLS, "batch_reported_tokens": MAX_BATCH_REPORTED_TOKENS},
            "scope": "known_dev_provisional_collection", "human_gold_verified": False}


def project_settings(directory, *, offline=False):
    config = parse_config((ROOT / ".env").read_text()) if not offline else {}
    values = {field: config[key] for field, key in {
        "openai_api_key": "OPENAI_API_KEY", "openai_base_url": "OPENAI_BASE_URL", "openai_model": "OPENAI_MODEL",
        "openai_timeout_seconds": "PLANGO_MODEL_TIMEOUT_SECONDS", "openai_max_retries": "PLANGO_MODEL_MAX_RETRIES",
        "max_model_tokens": "PLANGO_MAX_MODEL_TOKENS", "max_tool_calls": "PLANGO_MAX_TOOL_CALLS",
        "max_run_seconds": "PLANGO_MAX_RUN_SECONDS", "max_turns": "PLANGO_MAX_TURNS",
        "max_repair_rounds": "PLANGO_MAX_REPAIR_ROUNDS", "agent_mode": "PLANGO_AGENT_MODE",
    }.items() if config.get(key)}
    values.update(database_url=f"sqlite+aiosqlite:///{directory / 'runs.sqlite'}", data_dir=directory,
                  checkpoint_path=directory / "checkpoints.sqlite", runtime_profile="desktop", redis_url="local://",
                  embedded_worker=True, amap_webservice_key="", embedding_api_key="", browser_vision_enabled=False)
    if offline:
        values["openai_api_key"] = ""
    settings = DesktopSettings.model_validate(values)
    if not offline and not settings.model_enabled:
        raise RuntimeError("Project model configuration is missing; no quality run started")
    endpoint = urlsplit(settings.openai_base_url)
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise RuntimeError("Model base URL must not contain credentials or query parameters")
    return settings


@contextmanager
def business_clock(runtime, case, observed_at=None):
    """Only evidence/requirement business time; SQL leases and execution timers stay real."""
    value = case.get("as_of")
    if value is None:
        yield {"kind": "historical_response_no_original_timestamp", "as_of": None}
        return
    instant = datetime.fromisoformat(value)

    class ReferenceDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    source_time = datetime.fromisoformat(observed_at) if observed_at else instant

    class ReceiptDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return source_time.astimezone(tz) if tz else source_time.replace(tzinfo=None)

    async def reference(row, command=None):
        return instant.isoformat()

    def evidence_expired(proof):
        expiry = proof.expires_at
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return bool(expiry and expiry <= instant)

    import plango.booking_preview
    import plango.browser
    import plango.offers
    import plango.outcomes
    import plango_harness.agent.contracts
    with ExitStack() as stack:
        for module in (plango.booking_preview, plango.offers, plango.outcomes):
            stack.enter_context(patch.object(module, "datetime", ReferenceDateTime))
        stack.enter_context(patch.object(runtime, "_requirement_reference", reference))
        # Evidence.expired imports datetime inside its getter; module patching
        # cannot move that business clock. Preserve its comparison with as_of.
        stack.enter_context(patch.object(plango_harness.agent.contracts.Evidence, "expired", property(evidence_expired)))
        # The receipt owns its timestamp. Replay changes only that module's now();
        # command deadlines still use fromtimestamp(time.time()), SQL uses utc_now.
        stack.enter_context(patch.object(plango.browser, "datetime", ReceiptDateTime))
        yield {"kind": "selected_business_clock", "as_of": value,
               "replayed_source_observed_at": source_time.isoformat(),
               "real_clocks": ["SQL leases", "model timeout", "run deadline", "monotonic timers", "capture timestamps"]}


def install_budget(runtime, case_id, control, expected_sha, case_ids, log, *, manifest_fn=None):
    original = runtime.model._invoke
    calls = []

    async def invoke(awaitable, *, timeout):
        if digest(manifest_fn() if manifest_fn else manifest(case_ids)) != expected_sha:
            if hasattr(awaitable, "close"):
                awaitable.close()
            control["stop"] = "source_drift"
            raise RuntimeError("quality_source_drift")
        row = {"case_id": case_id, "invocation": len(calls) + 1, "started_at": datetime.now(timezone.utc).isoformat()}
        call_id = uuid.uuid4().hex
        if control.get("budget_ledger"):
            append(control["budget_ledger"], {"event": "started", "call_id": call_id, **row,
                                             "log": str(log.relative_to(ROOT))})
        calls.append(row)
        control["calls"].append(row)
        started = time.monotonic()
        try:
            result = await original(awaitable, timeout=timeout)
            parsed = result.get("parsed") if isinstance(result, dict) else None
            if type(parsed).__name__ in {"SourceAnalysis", "TaskDecision"}:
                # Private dev diagnostics only; never supplied to the judge as
                # source evidence, and never persisted as product facts.
                row["unverified_analysis"] = parsed.model_dump(mode="json")
            elif type(parsed).__name__ in {"TaskIntent", "RequirementOutput"}:
                row["unverified_requirements"] = {"schema": type(parsed).__name__, "proposal": parsed.model_dump(mode="json")}
            usage = usage_of(result)
            row.update(status="response", usage=usage, usage_missing=not bool(usage))
            if not usage:
                control["stop"] = "usage_missing"
            control["reported_tokens"] += usage.get("total_tokens", usage.get("input_tokens", usage.get("prompt_tokens", 0)) + usage.get("output_tokens", usage.get("completion_tokens", 0)))
            return result
        except Exception as error:
            row.update(status="error", **safe_error(error))
            if account_or_quota(error):
                control["stop"] = "account_or_quota"
            raise
        finally:
            row["latency_ms"] = round((time.monotonic() - started) * 1000)
            append(log, row)
            if control.get("budget_ledger"):
                usage = row.get("usage", {})
                append(control["budget_ledger"], {"event": "completed", "call_id": call_id,
                    "reported_tokens": usage.get("total_tokens", usage.get("input_tokens", usage.get("prompt_tokens", 0)) + usage.get("output_tokens", usage.get("completion_tokens", 0))),
                    "status": row["status"], "stop": control["stop"], "finished_at": datetime.now(timezone.utc).isoformat()})
    runtime.model._invoke = invoke
    return calls


@contextmanager
def model_only_egress(settings, log):
    """Only the configured model origin may use asynchronous HTTP in this collector."""
    expected = urlsplit(settings.openai_base_url)
    origin = (expected.scheme, expected.hostname, expected.port or (443 if expected.scheme == "https" else 80))
    original = httpx.AsyncClient.send

    async def send(client, request, *args, **kwargs):
        target = (request.url.scheme, request.url.host,
                  request.url.port or (443 if request.url.scheme == "https" else 80))
        allowed = target == origin
        append(log, {"method": request.method, "host": request.url.host, "allowed": allowed,
                     "role": "configured_model" if allowed else "blocked_undeclared_egress"})
        if not allowed:
            raise RuntimeError("quality_undeclared_network_blocked")
        return await original(client, request, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "send", send):
        yield


class LocalAPI:
    def __init__(self, app):
        self.app = app

    def __enter__(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error", access_log=False))

        async def serve():
            self.loop = asyncio.get_running_loop()
            await self.server.serve(sockets=[self.socket])
        self.thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True, name="quality-owned-api")
        self.thread.start()
        until = time.monotonic() + 20
        while not self.server.started and time.monotonic() < until and self.thread.is_alive():
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("quality_api_start_failed")
        self.url = f"http://127.0.0.1:{self.port}"
        # Owned loopback traffic must not inherit a machine-wide HTTP proxy.
        # Confirm the actual HTTP path before importing any scenario state.
        with httpx.Client(trust_env=False, timeout=2) as probe:
            for attempt in range(3):
                try:
                    probe.get(self.url + "/health/live").raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 2:
                        self.__exit__()
                        raise RuntimeError("quality_owned_api_unreachable") from None
        return self

    def call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=60)

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=15)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError("Owned API did not stop; do not open another consumer")


def independent_state(path, run_id):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        record = db.execute("SELECT state_json,version,pending_command FROM agent_run WHERE run_id=?", (run_id,)).fetchone()
        if not record:
            return {"missing_run": True}
        state = json.loads(record[0])
        counts = {table: db.execute(f"SELECT count(*) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[0]
                  for table in ("agent_action", "run_plan", "run_event", "input_acceptance")}
    pending = json.loads(record[2]) if isinstance(record[2], str) else record[2]
    return {"run_id": run_id, "spec": state.get("trip_spec"), "selected_poi": state.get("selected_poi"),
            "selected_plan": state.get("selected_plan"), "plan_version": state.get("plan_version"), "version": record[1],
            "turn_id": state.get("turn_id"), "turn_budget": state.get("turn_budget"),
            "model_token_count": state.get("model_token_count"), "model_call_count": len(state.get("model_calls", [])),
            "tool_call_count": state.get("tool_call_count"), "action_results": state.get("action_results", []),
            "pending_command": bool(pending), "counts": counts, "source": "independent_SQLite_query"}


def packet_observation(case, packets):
    relevant = [p for p in packets["packets"] if case["case_id"] in p["case_ids"]]
    packet = relevant[0]
    payload = packet["payload"]
    text = payload.get("text") if isinstance(payload, dict) else None
    if text is None:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    # This is an encountered replay document, never a fresh live merchant page.
    title = payload.get("title") or ("冻结历史资料" if case["scenario_origin"] == "real_snapshot_derived" else "明确提供的受控资料")
    source_url = packet.get("source_url") or payload.get("url") or f"https://quality-fixture.invalid/{case['case_id']}"
    return {"url": source_url, "title": title, "text": text, "tables": [], "elements": [],
            "observed_at": packet.get("observed_at") or case.get("as_of") or datetime.now(timezone.utc).isoformat(),
            "fields": {"evaluation_source": {"historical_or_controlled": True, "original_observed_at": packet.get("observed_at"),
                        "packet_id": packet["packet_id"]}}}, relevant


def drive(client, rid, observation, session_id, case, control, directory, collect):
    sent, captures = set(), 0
    deadline = time.monotonic() + 320
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/v1/runs/{rid}").json()
        if control["stop"] or control.get("case_stop"):
            collect("final", snapshot)
            return "budget_exhausted"
        if snapshot.get("phase") in TERMINAL:
            collect("final", snapshot)
            return "completed"
        if snapshot.get("phase") == "WAITING_APPROVAL":
            collect("final", snapshot)
            return "unexpected_write_proposal"
        pending = client.get("/api/v1/browser/commands", params={"browser_session_id": session_id}).json()["commands"]
        for command in pending:
            if command["run_id"] != rid or command["command_id"] in sent:
                continue
            op = command["operation"]
            allowed = op in {"extract", "snapshot", "read_page", "extract_tables", "current", "scroll"} or (
                op in {"navigate", "open_tab"} and command["arguments"].get("url") == observation["url"])
            if (case["case_id"] == "DEV-12" or case.get("environment", {}).get("allow_navigation") is False) and op in {"navigate", "open_tab"}:
                allowed = False
            result = {"command_id": command["command_id"], "browser_session_id": session_id, "ok": allowed,
                      "outcome": "observed" if allowed else "blocked"}
            if allowed and not command.get("approved_action_id"):
                result.update(observation)
                result.update(tab_id="replay-" + case["case_id"], snapshot_id=command["command_id"], page_version="replay-" + str(len(sent)))
            else:
                result.update(ok=False, outcome="blocked", error_kind="quality_source_or_write_not_allowed")
            response = client.post(f"/api/v1/browser/commands/{command['command_id']}/result", json=result)
            response.raise_for_status()
            append(directory / "observations.jsonl", {"command": command, "result": result})
            sent.add(command["command_id"])
        wait = snapshot.get("state", {}).get("browser_wait") or {}
        if wait.get("error_kind"):
            collect("final", snapshot)
            return "external_blocked"
        if snapshot.get("interrupt_id") and not wait and not snapshot.get("command_pending"):
            collect("final", snapshot)
            return "unscripted_clarification"
        captures += 1
        if captures % 50 == 0:
            print(json.dumps({"case": case["case_id"], "waiting": snapshot.get("phase")}), flush=True)
        time.sleep(0.2)
    snapshot = client.get(f"/api/v1/runs/{rid}").json()
    collect("final", snapshot)
    return "timeout"


def run_desktop(config, *, timeout):
    evidence = Path(config.get("evidence_dir", config["case_dir"]))
    path = evidence / "desktop-config.json"
    write(path, config)
    with (evidence / "desktop-driver.log").open("x") as log:
        process = subprocess.Popen(["node", "scripts/quality_desktop_cases.cjs", str(path)], cwd=ROOT, stdout=log, stderr=log)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=45)  # driver closes only its own Electron
    return read(evidence / "desktop-result.json"), process.returncode


def restore_saved_desktop(directory, settings, *, stopped_at):
    """Reopen the owned API, then only read the saved run in its original profile."""
    config = read(directory / "desktop-config.json")
    assert config["driver"] == "save_restart" and Path(config["case_dir"]).resolve() == directory.resolve()
    evidence = directory / "backend-restored"
    evidence.mkdir(mode=0o700)  # Never overwrite a previous restore execution.
    original = directory / "desktop-result.json"
    config.update(phase="read_only_restore", evidence_dir=str(evidence),
                  original_desktop_result={"path": original.name, "sha256": hashlib.sha256(original.read_bytes()).hexdigest()})
    before = independent_state(directory / "runs.sqlite", config["run_id"])
    assert not before.get("missing_run") and not before["pending_command"]
    assert not any(row.get("status") in {"RUNNING", "UNKNOWN"} for row in before["action_results"])
    app = create_app(settings, token=config["backend_token"])
    async def blocked_model(awaitable, *, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise RuntimeError("quality_restore_is_read_only_no_model")
    async def blocked_egress(client, request, *args, **kwargs):
        append(evidence / "egress.jsonl", {"method": request.method, "host": request.url.host, "allowed": False})
        raise RuntimeError("quality_restore_is_read_only_no_network")
    app.state.runtime.model._invoke = blocked_model
    with patch.object(httpx.AsyncClient, "send", blocked_egress), LocalAPI(app) as reopened:
        config.update(backend_url=reopened.url, backend_restart={"stopped_at": stopped_at,
                      "reopened_at": datetime.now(timezone.utc).isoformat(), "scope": "actual_owned_API_database_reopen"})
        result, code = run_desktop(config, timeout=120)
        with httpx.Client(base_url=reopened.url, headers={"Authorization": "Bearer " + config["backend_token"]}, trust_env=False) as client:
            snapshot = client.get(f"/api/v1/runs/{config['run_id']}").json()
        final = {"snapshot": snapshot, "events": reopened.call(app.state.runtime.get_events(config["run_id"], 0)),
                 "checkpoint": {"id": "after_restart", "captured_at": datetime.now(timezone.utc).isoformat()}}
        state = independent_state(directory / "runs.sqlite", config["run_id"])
    lifecycle = {**config["backend_restart"], "closed_at": datetime.now(timezone.utc).isoformat(),
                 "original_desktop_result": config["original_desktop_result"], "run_id": config["run_id"],
                 "independent_state_before": before, "independent_state_after": state,
                 "state_unchanged": before == state, "model_invocations": 0,
                 "scope": "later_actual_read_only_restore_not_evidence_for_earlier_outputs"}
    write(evidence / "backend-lifecycle.json", lifecycle)
    assert before == state, "Read-only restore changed the persisted task"
    return result, code, final, state


def collect_case(case, packets, settings, directory, control, source_sha, case_ids, *, manifest_fn=None, fixture=None, fixture_provenance=None, desktop_edits=False, trial_id="first"):
    token, browser_session = uuid.uuid4().hex, uuid.uuid4().hex
    app = create_app(settings, token=token)
    control["case_stop"] = None
    calls = install_budget(app.state.runtime, case["case_id"], control, source_sha, case_ids, directory / "model-calls.jsonl", manifest_fn=manifest_fn)
    driver = case.get("environment", {}).get("driver") or ("read" if case["case_id"] not in STATE_CASES else None)
    driver = "read" if driver == "read" else case_driver(case)
    checkpoints = {}
    rid = None
    observation, relevant = packet_observation(case, packets)
    write(directory / "case-input.json", {"case": case, "source_packets": relevant, "gold_provided_to_actor": False})
    started = time.monotonic()
    def export(stage, envelope, *, database_state=None, desktop=None):
        input_path = directory / f"{stage}.snapshot.json"
        output_path = directory / f"{stage}.visible.json"
        write(input_path, envelope)
        process = subprocess.run([str(ROOT / "node_modules/.bin/tsx"), "--tsconfig", "tsconfig.web.json",
                                  "scripts/export_quality_output.ts", str(input_path), str(output_path)],
                                 cwd=ROOT, capture_output=True, text=True, timeout=45)
        checkpoints[stage] = {"snapshot_path": str(input_path.relative_to(ROOT)),
            "visible_path": str(output_path.relative_to(ROOT)) if process.returncode == 0 else None,
            "output_export_error": None if process.returncode == 0 else "exporter_failed",
            "independent_state": database_state, "captured_at": envelope["checkpoint"]["captured_at"],
            "desktop_evidence": desktop, "scope": "pre_task_context" if stage == "initial" or stage.endswith("initial-reviewable-draft") else "delivered_output"}
        if process.returncode:
            (directory / f"{stage}.export-error.log").write_text(process.stderr)

    with model_only_egress(settings, directory / "egress.jsonl"), business_clock(app.state.runtime, case, observation["observed_at"]) as clock, LocalAPI(app) as server:
        with httpx.Client(base_url=server.url, headers={"Authorization": "Bearer " + token}, timeout=15, trust_env=False) as client:
            def collect(stage, snapshot):
                nonlocal rid
                rid = snapshot["run_id"]
                if stage in checkpoints:
                    return
                events = server.call(app.state.runtime.get_events(rid, 0))
                captured = datetime.now(timezone.utc).isoformat()
                export(stage, {"snapshot": snapshot, "events": events,
                               "checkpoint": {"id": stage, "as_of": case.get("as_of"), "captured_at": captured}},
                       database_state=independent_state(directory / "runs.sqlite", rid))
            try:
                if driver != "read":
                    imported = server.call(seed(app.state.runtime, case, directory, user_id="desktop", browser_session_id=browser_session,
                                                fixture=fixture, fixture_provenance=fixture_provenance))
                    rid = imported["run_id"]
                    write(directory / "imported-initial.json", imported)
                    collect("initial", client.get(f"/api/v1/runs/{rid}").json())
                    if driver == "edit" and not desktop_edits:
                        result = run_edits(client, case, rid, collect)
                        stop = result["stop_reason"]
                    else:
                        turns = case.get("agent_input", {}).get("user_turns", [])
                        config = {"case_id": case["case_id"], "driver": driver, "message": turns[0]["message"] if turns else None,
                                  "messages": [turn["message"] for turn in turns],
                                  "run_id": rid, "backend_url": server.url,
                                  "backend_token": token, "browser_session_id": browser_session, "case_dir": str(directory),
                                  "client_data_dir": str(directory / "client"), "profile_dir": str(directory / "profile")}
                        result, code = run_desktop(config, timeout=60 + 330 * len(turns) if driver == "edit" else 450)
                        stop = "script_finished" if code == 0 else "desktop_driver_failed"
                        write(directory / "desktop-record.json", result)
                        for checkpoint in result.get("checkpoints", []):
                            envelope = read(directory / checkpoint["snapshot"])
                            stage = "desktop-" + checkpoint["id"]
                            envelope["checkpoint"].update(id=stage, as_of=case.get("as_of"))
                            export(stage, envelope, desktop=checkpoint)
                        if result.get("stop_reason") not in {None, "script_finished"}:
                            stop = result["stop_reason"]
                        collect("before_restart", client.get(f"/api/v1/runs/{rid}").json())
                    level = "imported_state_workflow" if driver == "edit" and not desktop_edits else "imported_state_Electron_API"
                else:
                    turns = case["agent_input"]["user_turns"]
                    if len(turns) != 1:
                        raise ValueError("reading_case_requires_one_preregistered_turn")
                    response = client.post("/api/v1/runs", json={"input_text": turns[0]["message"], "browser_session_id": browser_session,
                        "enabled_skills": [], "location_context": {"city": "重庆", "source": "config"}})
                    response.raise_for_status()
                    rid = response.json()["run_id"]
                    stop = drive(client, rid, observation, browser_session, case, control, directory, collect)
                    level = "frozen_raw_observation_injection"
                if rid and "final" not in checkpoints:
                    collect("final", client.get(f"/api/v1/runs/{rid}").json())
            except Exception as error:
                stop, level = "collector_error", "incomplete_collection"
                write(directory / "collector-error.json", safe_error(error))
                if rid and "final" not in checkpoints:
                    collect("final", client.get(f"/api/v1/runs/{rid}").json())
            finally:
                if rid:
                    current = client.get(f"/api/v1/runs/{rid}").json()
                    if not current.get("outcome"):
                        client.post(f"/api/v1/runs/{rid}/cancel")
    # No main/sibling service is touched; reopen only this case's API and database.
    if driver == "save_restart" and rid and "before_restart" in checkpoints:
        stopped_at = datetime.now(timezone.utc).isoformat()
        if stop == "script_finished":
            try:
                restored, code, envelope, state = restore_saved_desktop(directory, settings, stopped_at=stopped_at)
                for checkpoint in restored.get("checkpoints", []):
                    captured = read(directory / checkpoint["snapshot"])
                    stage = "desktop-" + checkpoint["id"]
                    captured["checkpoint"].update(id=stage, as_of=case.get("as_of"))
                    export(stage, captured, desktop=checkpoint)
                envelope["checkpoint"]["as_of"] = case.get("as_of")
                export("after_restart", envelope, database_state=state)
                checkpoints["after_restart"]["scope"] = "actual_owned_backend_restart_desktop_closed"
                if code:
                    stop = "desktop_restore_failed"
            except Exception as error:
                stop = "collector_error"
                write(directory / "restore-error.json", safe_error(error))
    result = {"case_id": case["case_id"], "trial_id": trial_id, "run_id": rid, "stop_reason": "budget_exhausted" if control.get("case_stop") else stop,
              "raw_stop_reason": stop, "collector_limit_reason": control.get("case_stop") or control["stop"],
              "environment_level": level, "clock": clock, "checkpoints": checkpoints,
              "model_invocations": len(calls), "reported_tokens": sum(c.get("usage", {}).get("total_tokens", 0) for c in calls),
              "usage_missing_invocations": sum(c.get("status") == "response" and c.get("usage_missing", False) for c in calls),
              "elapsed_ms": round((time.monotonic() - started) * 1000), "human_gold_verified": False,
              "score": None, "data_retained": True}
    write(directory / "collection.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dev", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--cases", default="all")
    parser.add_argument("--offline-wiring", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    tasks, packets = read(DATA / "tasks.dev.json"), read(DATA / "source-packets.dev.json")
    selected = [case for case in tasks if args.cases == "all" or case["case_id"] in args.cases.split(",")]
    if not selected:
        parser.error("No declared dev cases selected")
    ids = [case["case_id"] for case in selected]
    frozen = manifest(ids)
    sha = digest(frozen)
    if not args.run_dev and not args.offline_wiring:
        print(json.dumps({"source_sha": sha, "cases": ids, "limits": frozen["limits"],
                          "mode": "preflight_only", "model_calls": 0, "formal_evaluation_ready": False}, indent=2))
        return
    if args.run_dev and args.source_sha != sha:
        parser.error("Source manifest mismatch; run preflight first")
    parent = ROOT / "output/quality-v1"
    parent.mkdir(exist_ok=True)
    work = parent / (("offline-" if args.offline_wiring else "dev-") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    work.mkdir(mode=0o700)
    write(work / "manifest.json", {**frozen, "source_sha": sha, "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                                  "offline_wiring_not_quality": args.offline_wiring})
    control = {"calls": [], "reported_tokens": 0, "stop": None}
    results = []
    print(json.dumps({"work": str(work.relative_to(ROOT)), "planned": ids, "real_model": not args.offline_wiring}), flush=True)
    for case in selected:
        if control["stop"]:
            results.append({"case_id": case["case_id"], "stop_reason": "not_run_after_batch_stop"})
            continue
        directory = work / case["case_id"]
        directory.mkdir(mode=0o700)
        settings = project_settings(directory, offline=args.offline_wiring)
        write(directory / "execution-settings.json", {"model": settings.openai_model,
              "provider_origin": urlsplit(settings.openai_base_url).hostname, "model_enabled": settings.model_enabled,
              "max_model_tokens": settings.max_model_tokens, "max_tool_calls": settings.max_tool_calls,
              "max_run_seconds": settings.max_run_seconds, "timeout_seconds": settings.openai_timeout_seconds,
              "provider_retries": settings.openai_max_retries, "vision_enabled": settings.browser_vision_enabled,
              "agent_mode": settings.agent_mode, "amap_live_enabled": False, "embedded_owned_worker": True})
        result = collect_case(case, packets, settings, directory, control, sha, ids)
        results.append(result)
        print(json.dumps({"case": case["case_id"], "stop": result["stop_reason"], "model_invocations": result["model_invocations"], "reported_tokens": result["reported_tokens"]}), flush=True)
    write(work / "collection.json", {"scope": "offline_wiring_not_quality" if args.offline_wiring else "known_dev_provisional_collection",
          "planned": ids, "cases": results, "model_invocations": len(control["calls"]), "reported_tokens": control["reported_tokens"],
          "stop": control["stop"], "human_gold_verified": False, "quality_scores": None})
    print(json.dumps({"collection": str((work / "collection.json").relative_to(ROOT)), "quality_scores": None}), flush=True)


if __name__ == "__main__":
    main()
