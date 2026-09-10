"""Offline collector boundaries only: no API server, model or external network."""

import asyncio
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from plango import browser, offers
from plango.task import TaskDecision
from plango_harness.agent.contracts import Evidence
from plango_harness.persistence.database import utc_now

_paths = list(sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
try:
    import quality_runner as runner
finally:
    sys.path[:] = _paths


def test_business_clock_keeps_sql_wall_clock_and_restores_runtime():
    async def original(row, command=None):
        return "original-reference"

    runtime = SimpleNamespace(_requirement_reference=original)
    original_datetime = offers.datetime
    original_browser_datetime = browser.datetime
    before = utc_now()
    instant = "2001-02-03T04:05:06+00:00"
    proof = Evidence(evidence_id="business-clock-only", expires_at="2001-02-04T00:00:00+00:00")
    assert proof.expired
    with runner.business_clock(runtime, {"as_of": instant}) as clock:
        assert not proof.expired
        assert offers.datetime.now(timezone.utc).isoformat() == instant
        assert browser.datetime.now(timezone.utc).isoformat() == instant
        assert asyncio.run(runtime._requirement_reference({})) == instant
        assert before <= utc_now() <= datetime.now(timezone.utc)
        assert clock["kind"] == "selected_business_clock"
    assert offers.datetime is original_datetime
    assert browser.datetime is original_browser_datetime
    assert runtime._requirement_reference is original
    assert proof.expired
    observed = "2001-02-02T04:05:06+00:00"
    with runner.business_clock(runtime, {"as_of": instant}, observed_at=observed):
        assert offers.datetime.now(timezone.utc).isoformat() == instant
        assert browser.datetime.now(timezone.utc).isoformat() == observed
        timestamp = utc_now().timestamp()
        assert browser.datetime.fromtimestamp(timestamp, timezone.utc) == datetime.fromtimestamp(timestamp, timezone.utc)
        assert before <= utc_now() <= datetime.now(timezone.utc)
    with runner.business_clock(runtime, {"as_of": None}) as clock:
        assert clock["as_of"] is None
        assert offers.datetime is original_datetime
        assert browser.datetime is original_browser_datetime
        assert runtime._requirement_reference is original


def test_private_analysis_diagnostic_keeps_proposal_without_raw_transport(tmp_path):
    async def invoke(awaitable, *, timeout):
        return await awaitable

    async def response():
        return {"parsed": TaskDecision(operation="answer", answer="原文136元/份"),
                "raw": SimpleNamespace(usage_metadata={"total_tokens": 7}), "headers": {"authorization": "must-not-be-logged"}}

    runtime = SimpleNamespace(model=SimpleNamespace(_invoke=invoke))
    control = {"calls": [], "reported_tokens": 0, "stop": None}
    log = tmp_path / "model-calls.jsonl"
    runner.install_budget(runtime, "DEV-04", control, runner.digest(runner.manifest(["DEV-04"])), ["DEV-04"], log)
    asyncio.run(runtime.model._invoke(response(), timeout=None))
    saved = json.loads(log.read_text())
    assert saved["unverified_analysis"]["answer"] == "原文136元/份"
    assert "must-not-be-logged" not in log.read_text() and "headers" not in saved
    assert saved["usage"]["total_tokens"] == 7


def test_owned_loopback_is_reachable_without_the_ambient_proxy(tmp_path, monkeypatch):
    for key in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "")
    settings = runner.project_settings(tmp_path, offline=True)
    with runner.LocalAPI(runner.create_app(settings, token="offline-evaluator-only")) as api:
        with httpx.Client(trust_env=False, timeout=2) as client:
            assert client.get(api.url + "/health/live").json()["status"] == "ok"


def test_model_egress_normalizes_default_https_port_and_blocks_other_origins(tmp_path):
    seen = []

    def transport(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"offline": True})

    async def exercise():
        log = tmp_path / "egress.jsonl"
        settings = SimpleNamespace(openai_base_url="https://model.invalid/v1")
        with runner.model_only_egress(settings, log):
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                for url in ("https://model.invalid/v1/messages", "https://model.invalid:443/v1/messages"):
                    assert (await client.post(url, json={})).status_code == 200
                for url in ("https://third-party.invalid/", "https://model.invalid.evil.invalid/",
                            "https://model.invalid:8443/v1/messages", "http://model.invalid/v1/messages"):
                    with pytest.raises(RuntimeError, match="quality_undeclared_network_blocked"):
                        await client.post(url, json={})
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert [row["allowed"] for row in rows] == [True, True, False, False, False, False]

    asyncio.run(exercise())
    assert len(seen) == 2, "Blocked requests must never reach even the mock transport"


def test_manifest_hashes_gold_but_actor_observation_excludes_judging_material():
    frozen = runner.manifest(["DEV-01"])
    assert "eval/quality-v1/tasks.dev.json" in frozen["files"]
    assert "eval/quality-v1/source-packets.dev.json" in frozen["files"]
    for name in ("gold.dev.json", "oracle-checks.dev.json"):
        frozen_hash = frozen["files"]["eval/quality-v1/" + name]
        assert isinstance(frozen_hash, str) and len(frozen_hash) == 64
        int(frozen_hash, 16)
    assert not any(Path(path).name == ".env" for path in frozen["files"])
    secret_gold = "GOLD_ANSWER_MUST_NOT_REACH_ACTOR"
    case = {"case_id": "DEV-01", "scenario_origin": "real_snapshot_derived", "as_of": None,
            "gold": secret_gold}
    packets = {"packets": [{"packet_id": "RAW", "case_ids": ["DEV-01"], "payload": {"text": "原始券售价47元"},
                            "observed_at": None, "provenance": {"auditor_note": secret_gold}}],
               "supplemental_audit_packets": [{"payload": {"text": secret_gold}}]}
    observation, _ = runner.packet_observation(case, packets)
    assert observation["text"] == "原始券售价47元"
    assert secret_gold not in json.dumps(observation, ensure_ascii=False)
    assert observation["fields"]["evaluation_source"]["original_observed_at"] is None


def test_restore_reopens_api_before_read_only_driver_without_new_identity_or_model(tmp_path, monkeypatch):
    order = []
    runner.write(tmp_path / "desktop-config.json", {"case_id": "RESTORE-01", "driver": "save_restart", "case_dir": str(tmp_path),
        "run_id": "original", "backend_url": "http://127.0.0.1:11111", "backend_token": "original-token",
        "profile_dir": str(tmp_path / "profile"), "client_data_dir": str(tmp_path / "client"), "browser_session_id": "original-browser"})
    runner.write(tmp_path / "desktop-result.json", {"original": True})
    original = (tmp_path / "desktop-result.json").read_bytes()
    state = {"run_id": "original", "pending_command": False, "action_results": [], "model_call_count": 0}
    monkeypatch.setattr(runner, "independent_state", lambda *args: state.copy())
    async def events(*args):
        return []
    app = SimpleNamespace(state=SimpleNamespace(runtime=SimpleNamespace(model=SimpleNamespace(), get_events=events)))
    monkeypatch.setattr(runner, "create_app", lambda *args, **kwargs: app)
    @contextmanager
    def local_api(actual):
        assert actual is app
        order.append("api_reopened")
        yield SimpleNamespace(url="http://127.0.0.1:22222", call=asyncio.run)
        order.append("api_closed")
    monkeypatch.setattr(runner, "LocalAPI", local_api)
    @contextmanager
    def client(**kwargs):
        assert kwargs["base_url"] == "http://127.0.0.1:22222"
        yield SimpleNamespace(get=lambda path: SimpleNamespace(json=lambda: {"run_id": "original"}))
    monkeypatch.setattr(runner.httpx, "Client", client)
    def driver(config, **kwargs):
        assert order == ["api_reopened"]
        assert config["phase"] == "read_only_restore" and config["backend_url"] == "http://127.0.0.1:22222"
        assert config["profile_dir"] == str(tmp_path / "profile") and config["browser_session_id"] == "original-browser"
        assert config["evidence_dir"] == str(tmp_path / "backend-restored")
        order.append("read_only_desktop")
        return {"status": "completed"}, 0
    monkeypatch.setattr(runner, "run_desktop", driver)
    runner.restore_saved_desktop(tmp_path, None, stopped_at="2026-09-09T12:00:00Z")
    assert order == ["api_reopened", "read_only_desktop", "api_closed"]
    assert (tmp_path / "desktop-result.json").read_bytes() == original
    lifecycle = runner.read(tmp_path / "backend-restored/backend-lifecycle.json")
    assert lifecycle["state_unchanged"] is True and lifecycle["model_invocations"] == 0
    with pytest.raises(RuntimeError, match="read_only_no_model"):
        asyncio.run(app.state.runtime.model._invoke(events(), timeout=None))
    with pytest.raises(FileExistsError):
        runner.restore_saved_desktop(tmp_path, None, stopped_at="2026-09-09T12:00:00Z")
