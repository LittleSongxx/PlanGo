"""Synthetic local upgrade failure cases; never touches the user's database."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from plango.settings import settings_from_env


def test_rename_preflight_and_ddl_failure_leave_legacy_database_unchanged(tmp_path):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    for failure in ("table", "index", "ddl"):
        database = tmp_path / f"{failure}.sqlite"
        config.attributes["database_url"] = f"sqlite+aiosqlite:///{database}"
        command.upgrade(config, "0011_location_context")
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO yoyu_browser_binding (run_id,browser_session_id) VALUES ('old-run','old-session')")
            if failure == "table":
                connection.execute("CREATE TABLE plango_browser_command (existing TEXT)")
            if failure == "index":
                connection.execute("CREATE INDEX ix_plango_browser_command_run_id ON yoyu_browser_binding(run_id)")
        with sqlite3.connect(database) as connection:
            before = list(connection.iterdump())
        original = Operations.rename_table

        def fail_second_rename(self, old, new, **kwargs):
            if failure == "ddl" and old == "yoyu_browser_command":
                raise RuntimeError("injected_ddl_failure")
            return original(self, old, new, **kwargs)

        with patch.object(Operations, "rename_table", fail_second_rename):
            with pytest.raises(RuntimeError):
                command.upgrade(config, "head")
        with sqlite3.connect(database) as connection:
            assert list(connection.iterdump()) == before
        if failure == "ddl":
            # The same migration must be retryable after the unexpected failure is removed.
            command.upgrade(config, "head")
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT browser_session_id FROM plango_browser_binding").fetchone() == ("old-session",)


def test_default_desktop_settings_refuse_silent_empty_data_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PLANGO_DATA_DIR", raising=False)
    monkeypatch.setenv("PLANGO_RUNTIME_PROFILE", "desktop")
    legacy = tmp_path / "data" / "yoyu"
    legacy.mkdir(parents=True)
    (legacy / "checkpoints.sqlite").write_bytes(b"retained checkpoint fixture")
    with pytest.raises(RuntimeError, match="Legacy desktop data exists"):
        settings_from_env()
    assert not (tmp_path / "data" / "plango").exists()
    monkeypatch.setenv("PLANGO_DATA_DIR", str(legacy))
    assert settings_from_env().checkpoint_path == legacy / "checkpoints.sqlite"
    monkeypatch.delenv("PLANGO_DATA_DIR")
    legacy.rename(tmp_path / "data" / "plango")
    assert settings_from_env().checkpoint_path.read_bytes() == b"retained checkpoint fixture"


def test_legacy_shell_configuration_is_rejected_without_disclosing_values(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANGO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PLANORA_RUNTIME_PROFILE", "sandbox")
    for key in ("YOYU_DATA_DIR", "XIAONIAN_CITY"):
        monkeypatch.setenv(key, "private-value-never-in-errors")
        with pytest.raises(RuntimeError, match="require migration to PLANGO_") as error:
            settings_from_env()
        assert key in str(error.value)
        assert "private-value-never-in-errors" not in str(error.value)
        monkeypatch.delenv(key)
    assert settings_from_env().data_dir == tmp_path
