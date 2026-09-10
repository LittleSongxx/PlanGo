"""Isolated Compose service recovery gate; no model, browser, or merchant requests.

Run with the project's plango Python. Creates a fresh plango-e2e-* project using
the production Compose topology, then removes only its own containers/volumes.
The injected browser observation is a controlled transport fixture, not a TSR.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-build", action="store_true", help="Reuse plango-service-check:local")
    args = parser.parse_args()
    identity = uuid.uuid4().hex
    project = "plango-e2e-" + identity[:12]
    token = "isolated-service-check-" + identity
    (ROOT / "output").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="service-recovery-", dir=ROOT / "output"))
    private_log = work / "private.log"
    private_log.touch(mode=0o600)
    began = time.monotonic()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    http = build_opener(ProxyHandler({}))
    # Explicit env-file excludes the user's .env; all provider credentials are empty.
    empty_env = work / "empty.env"
    empty_env.touch(mode=0o600)
    env = {**os.environ, "PLANGO_BACKEND_TOKEN": token, "PLANGO_POSTGRES_PASSWORD": identity,
           "PLANGO_SERVICE_PORT": str(port), "PLANGO_AGENT_MODE": "single",
           "OPENAI_API_KEY": "", "OPENAI_MODEL": "", "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
           "PLANGO_EMBEDDING_API_KEY": "", "PLANGO_EMBEDDING_BASE_URL": "http://127.0.0.1:9/v1",
           "AMAP_WEBSERVICE_KEY": "", "PLANGO_BROWSER_VISION_ENABLED": "false"}
    override = work / "compose.json"
    override.write_text(json.dumps({
        "services": {name: {"image": "plango-service-check:local", "restart": "no"}
                     for name in ("api", "worker", "migrate")},
    }))
    command = ["docker", "compose", "--env-file", str(empty_env), "--project-name", project,
               "-f", str(ROOT / "docker-compose.yml"), "-f", str(override)]
    checks = []
    stage = "preflight"
    started = False
    report = {"scope": "controlled PostgreSQL/Redis/API/worker recovery; no Actor or real browser",
              "project": project, "checks": checks, "status": "failed",
              "started_at": datetime.now(timezone.utc).isoformat()}

    def docker(*arguments, input=None, timeout=60):
        with private_log.open("a") as log:
            log.write(f"\n[{stage}] docker {' '.join(arguments)}\n")
            log.flush()
            result = subprocess.run(["docker", *arguments], cwd=ROOT, env=env, input=input,
                                    text=True, stdout=subprocess.PIPE, stderr=log, timeout=timeout)
            log.write(result.stdout)
        if result.returncode:
            raise RuntimeError("isolated_compose_command_failed")
        return result.stdout.strip()

    def compose(*arguments, **kwargs):
        return docker(*command[1:], *arguments, **kwargs)

    def owned_resources():
        return {kind: docker(kind, "ls", *(["--all"] if kind == "container" else []), "--quiet",
                             "--filter", "label=com.docker.compose.project=" + project).splitlines()
                for kind in ("container", "volume", "network")}

    def request(method, path, body=None, expected=200, retry_unavailable=False):
        req = Request(base + path, json.dumps(body).encode() if body is not None else None,
                      {"Content-Type": "application/json", "Authorization": "Bearer " + token}, method=method)
        try:
            with http.open(req, timeout=5) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:
            if retry_unavailable and error.code == 503:
                error.close()
                raise URLError("service_not_ready") from None
            status, raw = error.code, error.read()
        assert status == expected, f"unexpected_http_status_{status}_expected_{expected}"
        return json.loads(raw)

    def sql(query):
        return json.loads(compose("exec", "-T", "postgres", "psql", "-X", "-U", "plango", "-d", "plango",
                                  "-tA", "-v", "ON_ERROR_STOP=1", input=query))

    def redis(code):
        # Run only fixed test code in the owned API container; never print its environment.
        return json.loads(compose("exec", "-T", "api", "python", "-c",
                                  "import json,os,redis; r=redis.Redis.from_url(os.environ['PLANGO_REDIS_URL'],decode_responses=True); " + code))

    def wait_for(check, timeout=40):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                value = check()
                if value:
                    return value
            except (URLError, TimeoutError, ConnectionError) as error:
                last_error = error
            time.sleep(.2)
        raise TimeoutError("service_check_wait_expired") from last_error

    def ready():
        value = request("GET", "/api/v1/health/ready", retry_unavailable=True)
        assert value["runtime_profile"] == "service" and not value["model_enabled"]
        return value["ready"]

    def checkpoint():
        assert re.fullmatch(r"[a-f0-9]{32}", run_id)
        return sql(f"""SELECT json_build_object(
            'run', (SELECT json_build_object('run_id',run_id,'phase',phase,'outcome',outcome,
                'version',version,'state',state_json,'pending_command',pending_command,'event_seq',last_event_seq)
                FROM agent_run WHERE run_id='{run_id}'),
            'requests', (SELECT count(*) FROM input_acceptance WHERE run_id='{run_id}'),
            'events', (SELECT coalesce(json_agg(row_to_json(e) ORDER BY seq),'[]'::json)
                FROM run_event e WHERE run_id='{run_id}'),
            'commands', (SELECT coalesce(json_agg(row_to_json(c) ORDER BY seq),'[]'::json)
                FROM plango_browser_command c WHERE run_id='{run_id}'),
            'checkpoints', (SELECT count(*) FROM checkpoints WHERE thread_id='{run_id}'),
            'actions', (SELECT count(*) FROM agent_action WHERE run_id='{run_id}'));
        """)

    def settled():
        queue = redis("print(json.dumps(r.xinfo_groups('plango:runs')))")
        assert any(row["name"] == "plango-workers" for row in queue), "worker_consumer_group_missing"
        idle = all(row["pending"] == 0 and row.get("lag") == 0 for row in queue)
        return idle and sql(f"SELECT to_json(count(*)=0) FROM agent_run WHERE run_id='{run_id}' AND lease_until>now();")

    def duplicate():
        redis(f"print(json.dumps(r.xadd('plango:runs',{{'run_id':'{run_id}','kind':'run','payload':'{{}}'}})))")
        wait_for(settled)

    def passed(name):
        checks.append(name)
        print("[service-recovery] " + name, flush=True)

    try:
        assert not any(owned_resources().values()), "fresh_project_required"
        stage = "build_and_start_api_without_worker"
        started = True
        if not args.no_build:
            # Shared api/migrate/worker image needs one build, avoiding same-tag Bake export races.
            compose("build", "api", timeout=900)
        compose("up", "-d", "--no-build", "api", timeout=120)
        wait_for(ready)
        passed("service_profile_postgres_redis_ready_without_model")
        stage = "durable_input_and_queue_redelivery"
        body = {"request_id": "service-" + identity, "user_id": "controlled-" + identity,
                "input_text": "读取当前网页菜单", "browser_session_id": "fixture-desktop"}
        accepted = request("POST", "/api/v1/runs", body, 202)
        run_id = accepted["run_id"]
        assert re.fullmatch(r"[a-f0-9]{32}", run_id)
        replay = request("POST", "/api/v1/runs", body, 202)
        assert replay["replayed"] and replay["run_id"] == run_id
        request("POST", "/api/v1/runs", {**body, "input_text": "different input"}, 409)
        initial = checkpoint()
        assert initial["requests"] == 1 and initial["run"]["phase"] == "CREATED"
        # Place a real Stream entry in a crashed-consumer PEL, then age only that test entry.
        pending = redis("rows=r.xreadgroup('plango-workers','controlled-abandoned',{'plango:runs':'>'},count=1); "
                        "assert rows; item=rows[0][1][0][0]; "
                        "r.xclaim('plango:runs','plango-workers','controlled-abandoned',0,[item],idle=91000); print(json.dumps(item))")
        compose("up", "-d", "--no-deps", "worker")
        wait_for(lambda: request("GET", "/api/v1/runs/" + run_id)["state"].get("browser_wait"))
        paused = request("GET", "/api/v1/runs/" + run_id)
        wait_for(settled)
        assert not redis("print(json.dumps(r.xpending_range('plango:runs','plango-workers'," + json.dumps(pending) + "," + json.dumps(pending) + ",1)))")
        assert paused["phase"] == "REQUIREMENTS_READY"
        baseline = checkpoint()
        assert baseline["requests"] == len(baseline["commands"]) == 1 and baseline["checkpoints"] > 0
        (work / "paused.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n")
        passed("abandoned_stream_redelivery_produces_one_browser_command")

        stage = "api_worker_restart_and_paused_checkpoint"
        compose("stop", "worker", "api")
        compose("start", "api")
        wait_for(ready)
        # Exercise loss of the isolated transport stream; PostgreSQL remains authoritative.
        redis("print(json.dumps(r.delete('plango:runs')))")
        compose("start", "worker")
        wait_for(lambda: redis("print(json.dumps(r.exists('plango:runs')))") == 1)
        duplicate()
        assert checkpoint() == baseline, "paused_checkpoint_changed_after_restart_or_redelivery"
        assert request("GET", "/api/v1/requests/" + body["request_id"])["run_id"] == run_id
        commands = request("GET", "/api/v1/browser/commands?browser_session_id=fixture-desktop&after=999")["commands"]
        assert len(commands) == 1 and commands[0]["command_id"] == baseline["commands"][0]["command_id"]
        passed("same_run_checkpoint_request_and_browser_command_survive_process_restart")

        stage = "resume_original_command_once"
        observation = {"command_id": commands[0]["command_id"], "browser_session_id": "fixture-desktop",
                       "ok": True, "outcome": "observed", "snapshot_id": "controlled-service-snapshot",
                       "tab_id": "controlled-service-tab", "url": "https://fixture.invalid/menu",
                       "title": "Controlled transport fixture", "text": "清炒时蔬 28元",
                       "tables": [{"headers": ["菜品", "价格"], "rows": [["清炒时蔬", "28元"], ["未知价格", "时价"]]}]}
        endpoint = "/api/v1/browser/commands/" + observation["command_id"] + "/result"
        request("POST", endpoint, {**observation, "browser_session_id": "wrong-session"}, 409)
        request("POST", endpoint, observation)
        assert request("POST", endpoint, observation)["replayed"]
        wait_for(lambda: request("GET", "/api/v1/runs/" + run_id)["phase"] == "SUCCEEDED")
        wait_for(settled)
        final = checkpoint()
        (work / "final.json").write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n")
        assert final["requests"] == len(final["commands"]) == 1 and final["actions"] == 0
        assert final["run"]["state"].get("model_token_count", 0) == 0
        assert final["run"]["state"].get("model_call_count", 0) == 0
        assert final["run"]["state"]["turn_id"] == 1
        passed("original_browser_result_resumes_checkpoint_once_without_new_turn_or_model")

        stage = "terminal_duplicate_and_worker_restart"
        duplicate()
        compose("restart", "worker")
        duplicate()
        assert checkpoint() == final, "terminal_state_changed_after_redelivery"
        assert request("POST", "/api/v1/runs", body, 202)["replayed"]
        assert checkpoint() == final, "accepted_input_replayed_after_restart"
        passed("terminal_redelivery_and_worker_restart_preserve_state_and_budget")
        report.update(status="passed", run_id=run_id, paused_sha256=digest(baseline), final_sha256=digest(final))
    except Exception as error:
        report.update(failed_stage=stage, error_type=type(error).__name__)
        with private_log.open("a") as log:
            traceback.print_exc(file=log)
        print(f"[service-recovery] failed at {stage} ({type(error).__name__}); provider output withheld", flush=True)
    finally:
        if started:
            try:
                compose("logs", "--no-color", "--tail", "250", timeout=30)
            except Exception as error:
                report["diagnostic_error_type"] = type(error).__name__
            try:
                compose("down", "--volumes", "--remove-orphans", timeout=120)
                report["remaining_resources"] = owned_resources()
                assert not any(report["remaining_resources"].values()), "owned_resources_remain"
                report["cleanup"] = "owned_project_removed"
            except Exception as error:
                report.update(status="failed", cleanup="failed", cleanup_error_type=type(error).__name__)
        report.update(finished_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.monotonic() - began, 3))
        target = work / "report.json"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(str(target.relative_to(ROOT)), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
