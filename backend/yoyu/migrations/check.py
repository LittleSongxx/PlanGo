"""Run with conda run -n planora python backend/yoyu/migrations/check.py; temporary DB only."""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[3]
with tempfile.TemporaryDirectory(prefix="yoyu-migrations-") as temporary:
    database = Path(temporary) / "migrations.sqlite"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "YOYU_RUNTIME_PROFILE": "desktop",
        "YOYU_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "YOYU_DATA_DIR": temporary,
        # A sibling-style variable must never override YOYU's explicit settings.
        "DATABASE_URL": "postgresql+asyncpg://unused:unused@127.0.0.1:1/unrelated",
        "PLANORA_RUNTIME_PROFILE": "service",
        "OPENAI_API_KEY": "",
    }
    command = [sys.executable, "-m", "alembic", "-c", str(root / "alembic.ini")]
    for arguments in (
        ["upgrade", "head"],
        ["upgrade", "head"],
        ["downgrade", "-1"],
        ["upgrade", "head"],
    ):
        subprocess.run(
            command + arguments, cwd=root, env=environment, check=True, capture_output=True
        )
    with sqlite3.connect(database) as connection:
        names = {
            r[0] for r in connection.execute("select name from sqlite_master where type='table'")
        }
        assert {
            "agent_run",
            "agent_action",
            "run_plan",
            "yoyu_browser_binding",
            "yoyu_browser_command",
            "yoyu_reminder",
        } <= names
        assert (
            connection.execute("select version_num from alembic_version").fetchone()[0]
            == "0011_location_context"
        )
        fields = {r[1] for r in connection.execute("pragma table_info(yoyu_browser_binding)")}
        assert {"input_image", "generation", "enabled_skills", "location_context"} <= fields
print(
    "YOYU migration check passed: own database, full revision chain, repeat upgrade and extension rollback/reapply"
)
