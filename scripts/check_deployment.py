"""Check the running PlanGo Docker service without models, websites, or real-user edits.

Run: conda run --no-capture-output -n plango python scripts/check_deployment.py
The PostgreSQL checks verify committed rows from a separate connection, not a restart.
"""

import json
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8011"
COMPOSE = ["docker", "compose", "--project-name", "plango", "-f", str(ROOT / "docker-compose.yml")]


def main():
    token = dotenv_values(ROOT / ".env").get("PLANGO_BACKEND_TOKEN")
    if not token:
        raise SystemExit("PLANGO_BACKEND_TOKEN is required in this project's .env")
    user = "plango-deployment-check-" + uuid.uuid4().hex
    preference = user + "-preference"
    favorite = user + "-favorite"
    reminder_text = user + "-reminder"
    checks = {}
    owned_user = False

    def check(name, condition, count=None):
        checks[name] = {"status": "passed" if condition else "failed"}
        if count is not None:
            checks[name]["count"] = count
        if not condition:
            raise AssertionError(name)

    def request(method, path, body=None, *, auth=True, expected=200):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + token
        req = Request(
            BASE + path,
            json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(req, timeout=15) as response:
                status, raw = response.status, response.read()
        except HTTPError as exc:
            status, raw = exc.code, exc.read()
        if status != expected:
            raise RuntimeError(f"unexpected_http_status_{status}")
        return json.loads(raw)

    def compose(*args, input=None):
        result = subprocess.run(
            [*COMPOSE, *args], cwd=ROOT, input=input, text=True, capture_output=True, timeout=30
        )
        if result.returncode:
            raise RuntimeError("compose_check_failed")
        return result.stdout.strip()

    def sql(query):
        return json.loads(
            compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-X",
                "-U",
                "plango",
                "-d",
                "plango",
                "-tA",
                "-v",
                "ON_ERROR_STOP=1",
                input=query,
            )
        )

    def profile():
        return request("GET", "/api/v1/memory/profile?" + urlencode({"user_id": user}))

    def health():
        for path in ("/health/live", "/api/v1/health/live"):
            live = request("GET", path, auth=path.startswith("/api/"))
            check(
                path,
                live["status"] == "ok"
                and live["app"] == "PlanGo"
                and live["world_provider"] == "browser",
            )
        ready = request("GET", "/api/v1/health/ready")
        check(
            "service_browser_profile",
            ready["ready"]
            and ready["runtime_profile"] == "service"
            and ready["world_provider"] == "browser",
        )

    def authentication():
        nonlocal owned_user
        check("unique_user_initially_empty", all(not value for value in profile().values()))
        owned_user = True
        paths = [
            "/api/v1/health/ready",
            "/api/v1/memory/profile",
            "/api/v1/runs",
            "/api/v1/reminders",
            "/api/v1/browser/commands?browser_session_id=" + user,
        ]
        for path in paths:
            request("GET", path, auth=False, expected=401)
        request(
            "POST",
            "/api/v1/memory/preferences",
            {"user_id": user, "text": preference},
            auth=False,
            expected=401,
        )
        request(
            "DELETE",
            "/api/v1/memory/profile?" + urlencode({"user_id": user}),
            auth=False,
            expected=401,
        )
        check("unauthenticated_read_write_rejected", True, len(paths) + 2)

    def skills():
        # The skills surface is a local loader, not an HTTP route. Execute reads only.
        output = compose(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            """
import hashlib, json, os
from pathlib import Path
from plango.skills import MAX_ADVERT_BYTES, MAX_SKILL_BYTES, list_skill_adverts, read_skill
root = Path(os.environ['PLANGO_SKILLS_DIR'])
files = sorted(root.glob('*/SKILL.md'))
before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
advert = list_skill_adverts()
rows = json.loads(advert)
assert rows and len(advert.encode()) <= MAX_ADVERT_BYTES
for row in rows:
    content = read_skill(row['id'])
    assert content and len(content.encode()) <= MAX_SKILL_BYTES
assert list_skill_adverts([]) == '[]'
for skill_id, enabled in [('../outside', None), (rows[0]['id'], [])]:
    try:
        read_skill(skill_id, enabled)
    except ValueError:
        pass
    else:
        raise AssertionError('unsafe_or_disabled_skill_read')
assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
print(json.dumps({'count': len(rows)}))
""",
        )
        count = json.loads(output)["count"]
        check("deployed_skills_read_only_and_bounds", count > 0, count)

    def memory():
        if not owned_user:
            raise RuntimeError("test_user_ownership_not_verified")
        for polarity in ("like", "dislike"):
            value = request(
                "POST",
                "/api/v1/memory/preferences",
                {"user_id": user, "text": preference, "polarity": polarity},
            )
            check(
                "preference_" + polarity,
                len(value["preferences"]) == 1 and value["preferences"][0]["polarity"] == polarity,
            )
        for poi in (user + "-poi-a", user + "-poi-b"):
            value = request(
                "POST",
                "/api/v1/memory/favorites",
                {"user_id": user, "name": favorite, "poiId": poi},
            )
            check(
                "favorite_" + poi[-1],
                len(value["favorites"]) == 1 and value["favorites"][0]["poiId"] == poi,
            )
        value = profile()
        check(
            "profile_read_after_update",
            len(value["preferences"]) == 1
            and len(value["favorites"]) == 1
            and len(value["facts"]) == 2,
        )
        counts = sql(f"""SELECT json_build_object(
            'facts', (SELECT count(*) FROM user_fact WHERE user_id = '{user}'),
            'events', (SELECT count(*) FROM memory_event WHERE user_id = '{user}'),
            'persistent', (SELECT count(*) = 3 AND bool_and(relpersistence = 'p') FROM pg_class
                          WHERE relname IN ('user_fact', 'memory_event', 'plango_reminder')));
        """)
        check(
            "postgres_committed_memory",
            counts["facts"] == 2 and counts["events"] == 4 and counts["persistent"],
            counts["facts"],
        )
        request(
            "DELETE",
            "/api/v1/memory/preferences?" + urlencode({"user_id": user, "text": preference}),
        )
        request(
            "DELETE", "/api/v1/memory/favorites?" + urlencode({"user_id": user, "name": favorite})
        )
        check("memory_delete", all(not value for value in profile().values()))

    def reminders():
        when = datetime.now(timezone.utc) + timedelta(seconds=5)
        for body in (
            {"text": " ", "at": when.isoformat()},
            {"text": reminder_text, "at": "2020-01-01T00:00:00Z"},
            {"text": reminder_text, "at": "2030-01-01T00:00:00"},
        ):
            request("POST", "/api/v1/reminders", body, expected=422)
        check("reminder_validation", True, 3)
        created = request(
            "POST",
            "/api/v1/reminders",
            {"text": reminder_text, "at": when.isoformat()},
            expected=201,
        )
        rows = [r for r in created["reminders"] if r["text"] == reminder_text]
        check("reminder_create", len(rows) == 1 and not rows[0]["fired"], len(rows))
        reminder_id = rows[0]["id"]
        endpoint = "/api/v1/reminders/" + reminder_id
        request("POST", endpoint + "/ack", expected=409)
        check("reminder_early_ack_rejected", True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            due = request("GET", "/api/v1/reminders/due")["reminders"]
            if any(r["id"] == reminder_id for r in due):
                break
            time.sleep(0.25)
        else:
            raise AssertionError("reminder_not_due")
        check("reminder_due", True)
        fired = request("POST", endpoint + "/ack")
        first = next(r for r in fired["history"] if r["id"] == reminder_id)
        time.sleep(0.02)
        repeated = request("POST", endpoint + "/ack")
        second = next(r for r in repeated["history"] if r["id"] == reminder_id)
        check("reminder_duplicate_ack_idempotent", first == second)
        due = request("GET", "/api/v1/reminders/due")["reminders"]
        check("acknowledged_reminder_no_longer_due", all(r["id"] != reminder_id for r in due))
        counts = sql(f"""SELECT json_build_object('count', count(*),
                        'fired', count(fired_at_ms)) FROM plango_reminder
                        WHERE text = '{reminder_text}';""")
        check(
            "postgres_committed_reminder_ack",
            counts["count"] == counts["fired"] == 1,
            counts["count"],
        )
        request("DELETE", endpoint)
        request("DELETE", endpoint, expected=404)
        check("reminder_delete", True)

    def run(name, operation):
        try:
            operation()
        except Exception as exc:
            checks[name] = {"status": "failed"}
            # Exception messages and HTTP bodies may contain secrets or user content.
            print(f"{name}: failed ({type(exc).__name__})", flush=True)

    try:
        for name, operation in (
            ("health", health),
            ("authentication", authentication),
            ("skills", skills),
            ("memory", memory),
            ("reminders", reminders),
        ):
            run(name, operation)
    finally:

        def cleanup_memory():
            if owned_user:
                request("DELETE", "/api/v1/memory/profile?" + urlencode({"user_id": user}))
                check("test_user_cleanup", all(not value for value in profile().values()))

        def cleanup_reminders():
            for row in request("GET", "/api/v1/reminders")["reminders"]:
                if row["text"] == reminder_text:
                    request("DELETE", "/api/v1/reminders/" + row["id"])

        def cleanup_database_check():
            counts = sql(f"""SELECT json_build_object('count',
                (SELECT count(*) FROM user_fact WHERE user_id = '{user}') +
                (SELECT count(*) FROM memory_event WHERE user_id = '{user}') +
                (SELECT count(*) FROM plango_reminder WHERE text = '{reminder_text}'));
            """)
            check("postgres_test_data_removed", counts["count"] == 0, counts["count"])

        run("cleanup_memory", cleanup_memory)
        run("cleanup_reminders", cleanup_reminders)
        run("cleanup_database", cleanup_database_check)
        failed = sum(c["status"] == "failed" for c in checks.values())
        report = {
            "status": "failed" if failed else "passed",
            "passed": len(checks) - failed,
            "failed": failed,
            "checks": checks,
        }
        target = ROOT / "eval/plango-r0/deployment_api_checks.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n")
        print(
            f"Deployment API checks: {report['passed']} passed, {failed} failed; {target.relative_to(ROOT)}"
        )
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
