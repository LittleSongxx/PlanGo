"""Run with conda run -n plango python backend/plango/migrations/check.py; temporary DB only."""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[3]
with tempfile.TemporaryDirectory(prefix="plango-migrations-") as temporary:
    database = Path(temporary) / "migrations.sqlite"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PLANGO_RUNTIME_PROFILE": "desktop",
        "PLANGO_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "PLANGO_DATA_DIR": temporary,
        # A sibling-style variable must never override PlanGo's explicit settings.
        "DATABASE_URL": "postgresql+asyncpg://unused:unused@127.0.0.1:1/unrelated",
        "PLANORA_RUNTIME_PROFILE": "service",
        "OPENAI_API_KEY": "",
    }
    command = [sys.executable, "-m", "alembic", "-c", str(root / "alembic.ini")]

    def migrate(*arguments):
        subprocess.run(
            command + list(arguments), cwd=root, env=environment, check=True, capture_output=True
        )

    # A legacy ledger fixture carries opaque identities and an UNKNOWN write result.
    migrate("upgrade", "0011_location_context")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO yoyu_browser_binding VALUES (?, ?, ?, ?, ?, ?)",
            ("run-old", "session-old", None, 7, '["reader"]', '{"city":"重庆"}'),
        )
        connection.execute(
            "INSERT INTO yoyu_browser_command VALUES (?, ?, ?, ?, ?, ?, ?)",
            (42, "command-old", "run-old", "session-old", '{"approved_action_id":"approval-old","idempotency_key":"yoyu:opaque-old"}', '{"status":"UNKNOWN","retryable":false}', 1.0),
        )
        connection.execute("INSERT INTO yoyu_reminder VALUES (?, ?, ?, ?)", ("reminder-old", "保留提醒", 1234, None))
        expected = {
            suffix: connection.execute(f"SELECT * FROM yoyu_{suffix}").fetchall()
            for suffix in ("browser_binding", "browser_command", "reminder")
        }
    for arguments in (
        ["upgrade", "head"],
        ["upgrade", "head"],
        ["downgrade", "-1"],
        ["upgrade", "head"],
    ):
        migrate(*arguments)
    with sqlite3.connect(database) as connection:
        names = {
            r[0] for r in connection.execute("select name from sqlite_master where type='table'")
        }
        assert {
            "agent_run",
            "agent_action",
            "run_plan",
            "plango_browser_binding",
            "plango_browser_command",
            "plango_reminder",
        } <= names
        assert (
            connection.execute("select version_num from alembic_version").fetchone()[0]
            == "0012_plango_rename"
        )
        fields = {r[1] for r in connection.execute("pragma table_info(plango_browser_binding)")}
        assert {"input_image", "generation", "enabled_skills", "location_context"} <= fields
        assert not any(name.startswith("yoyu_") for name in names)
        for suffix, rows in expected.items():
            assert connection.execute(f"SELECT * FROM plango_{suffix}").fetchall() == rows
        connection.execute(
            "INSERT INTO plango_browser_command (command_id,run_id,browser_session_id,payload,created_at) VALUES (?,?,?,?,?)",
            ("new-command", "run-old", "session-old", "{}", 2.0),
        )
        assert connection.execute("SELECT max(seq) FROM plango_browser_command").fetchone()[0] == 43
    # A separate empty database exercises the complete fresh-install chain.
    environment["PLANGO_DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(temporary) / 'fresh.sqlite'}"
    migrate("upgrade", "head")
print(
    "PlanGo migration check passed: fresh install, legacy ledger/UNKNOWN/session/sequence preservation, repeat upgrade and rollback/reapply"
)
