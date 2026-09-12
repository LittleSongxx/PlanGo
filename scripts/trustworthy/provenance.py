"""Identify the product revision a run actually executed.

`actor_sha` only fingerprints the dataset (protocol/tasks/worlds), so two runs
of the same dataset under different product code carry the same actor id. The
bundles written here add the product sources, the git state and the model that
served the run, so a report can be traced back to the code that produced it.

Never reads oracles.json.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .schema import canonical_sha, file_sha

ROOT = Path(__file__).resolve().parents[2]

# Sources that change product behaviour. Skills are prompt content the product
# loads, so they are part of the actor too.
PRODUCT_GLOBS: tuple[tuple[str, str], ...] = (
    ("backend/plango", "**/*.py"),
    ("vendor/plango_harness/backend/plango_harness", "**/*.py"),
    ("skills", "*/SKILL.md"),
)

MODEL_KEYS = ("OPENAI_MODEL", "OPENAI_BASE_URL")


def _tracked_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative, pattern in PRODUCT_GLOBS:
        base = root / relative
        if not base.exists():
            continue
        files.extend(sorted(path for path in base.glob(pattern) if path.is_file()))
    return sorted(set(files))


def _git_state(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    if commit is None:
        return {"available": False}
    status = run("status", "--porcelain")
    return {
        "available": True,
        "commit": commit,
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": None if status is None else bool(status),
    }


def product_identity(root: Path | None = None) -> dict[str, Any]:
    """Hash the product sources and record the git state at run time."""
    base = Path(root) if root else ROOT
    rows = []
    for path in _tracked_files(base):
        try:
            rows.append({"path": str(path.relative_to(base)), "sha": file_sha(path)})
        except OSError:
            continue
    return {
        "product_sha": canonical_sha(rows),
        "product_files": len(rows),
        "git": _git_state(base),
    }


def model_identity(config: dict[str, str] | None) -> dict[str, Any]:
    """Record the served model without ever writing a credential."""
    values = config or {}
    host = None
    base_url = values.get("OPENAI_BASE_URL") or ""
    if base_url:
        host = base_url.split("//", 1)[-1].split("/", 1)[0]
    return {
        "model": values.get("OPENAI_MODEL") or None,
        "base_url_host": host,
        "configured": bool(values.get("OPENAI_API_KEY")),
    }


def run_identity(live: bool, config: dict[str, str] | None) -> dict[str, Any]:
    identity = product_identity()
    identity["model"] = (
        model_identity(config) if live else {"model": None, "base_url_host": None, "configured": False}
    )
    return identity
