"""Bounded, read-only local skill discovery. Skill content grants no tool permissions."""

from __future__ import annotations

import json
import os
import re
import stat
from contextlib import ExitStack
from itertools import islice
from pathlib import Path
from typing import Any

MAX_SKILL_BYTES = 24_000
MAX_ADVERT_BYTES = 8_000
MAX_SKILLS = 64
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_TRUNCATED = "\n\n[Skill truncated at the read limit.]"
SKILL_OPERATIONS = (
    "snapshot",
    "extract",
    "navigate",
    "scroll",
    "click",
    "type",
    "finish",
)
_ALLOWED_OPERATIONS = frozenset(SKILL_OPERATIONS)


def _root() -> Path:
    return (
        Path(os.environ.get("PLANGO_SKILLS_DIR", str(Path(__file__).resolve().parents[2] / "skills")))
        .expanduser()
        .resolve()
    )


def _validate_id(skill_id: str) -> None:
    if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
        raise ValueError("invalid_skill_id")


def _enabled(enabled_ids: list[str] | None) -> set[str] | None:
    if enabled_ids is None:
        return None
    for skill_id in enabled_ids:
        _validate_id(skill_id)
    return set(enabled_ids)


def read_skill(skill_id: str, enabled_ids: list[str] | None = None) -> str:
    """Read one enabled SKILL.md; reject symlinks, special files and path traversal."""
    _validate_id(skill_id)
    enabled = _enabled(enabled_ids)
    if enabled is not None and skill_id not in enabled:
        raise ValueError("skill_disabled")
    try:
        # Open each level relative to a held directory descriptor. No check/open symlink race.
        with ExitStack() as stack:
            root_fd = os.open(_root(), os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, root_fd)
            skill_fd = os.open(
                skill_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
            )
            stack.callback(os.close, skill_fd)
            fd = os.open("SKILL.md", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=skill_fd)
            stream = stack.enter_context(os.fdopen(fd, "rb"))
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("skill_not_regular_file")
            raw = stream.read(MAX_SKILL_BYTES + 1)
    except OSError as error:
        raise ValueError("skill_unavailable_or_unsafe") from error
    if len(raw) > MAX_SKILL_BYTES:
        return (
            raw[: MAX_SKILL_BYTES - len(_TRUNCATED.encode())].decode("utf-8", errors="ignore")
            + _TRUNCATED
        )
    return raw.decode("utf-8", errors="ignore")


def _normalize_operations(values: list[str]) -> list[str]:
    return [item for item in dict.fromkeys(values) if item in _ALLOWED_OPERATIONS]


def _parse_frontmatter(text: str) -> dict[str, Any] | None:
    # ponytail: name/description/operations only; use a YAML parser if the local format grows.
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    result: dict[str, Any] = {}
    position = 1
    while position < min(len(lines), 128):
        line = lines[position]
        position += 1
        if line.strip() == "---":
            return result
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if key == "operations":
            items: list[str] = []
            if value:
                items.extend(part.strip().strip("'\"") for part in value.split(",") if part.strip())
            while position < min(len(lines), 128):
                nxt = lines[position]
                stripped = nxt.strip()
                if stripped == "---" or (stripped and not nxt.startswith((" ", "\t")) and not stripped.startswith("-")):
                    break
                position += 1
                if stripped.startswith("-"):
                    items.append(stripped[1:].strip().strip("'\""))
            result["operations"] = _normalize_operations(items)
            continue
        if key not in {"name", "description"}:
            continue
        if value in {">", ">-", "|", "|-"}:
            parts = []
            while position < min(len(lines), 128) and (
                lines[position].startswith((" ", "\t")) or not lines[position].strip()
            ):
                parts.append(lines[position].strip())
                position += 1
            value = (" " if value.startswith(">") else "\n").join(parts)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].replace("''", "'")
        result[key] = value[: 120 if key == "name" else 600]
    return None  # Unterminated frontmatter is not a metadata document.


def _metadata(text: str) -> dict[str, str]:
    parsed = _parse_frontmatter(text)
    if not parsed:
        return {}
    return {
        key: parsed[key]
        for key in ("name", "description")
        if isinstance(parsed.get(key), str)
    }


def parse_skill(text: str) -> dict[str, Any]:
    parsed = _parse_frontmatter(text) or {}
    body = text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body = "\n".join(lines[index + 1 :]).lstrip("\n")
                break
    operations = parsed["operations"] if "operations" in parsed else list(SKILL_OPERATIONS)
    return {
        "name": parsed.get("name") or "",
        "description": parsed.get("description") or "",
        "operations": operations,
        "body": body,
    }


def skill_allows(operation: str, procedure: dict[str, Any] | None) -> bool:
    """Allow the current browser set until a procedure is loaded; then honor its operations."""
    if not procedure:
        return True
    if operation == "read_skill":
        return True
    allowed = procedure.get("operations")
    if allowed is None:
        return True
    return operation in allowed


def list_skill_adverts(enabled_ids: list[str] | None = None) -> str:
    """Return a bounded JSON array of {id, name, description}; omit unreadable skills."""
    enabled = _enabled(enabled_ids)
    root = _root()
    if not root.is_dir():
        return "[]"
    adverts: list[dict[str, str]] = []
    # Bound directory work as well as output. This repo currently contains ten local skills.
    try:
        with os.scandir(root) as entries:
            candidates = sorted(
                entry.name
                for entry in islice(entries, 256)
                if _ID.fullmatch(entry.name) and (enabled is None or entry.name in enabled)
            )
    except OSError:
        return "[]"
    for skill_id in candidates[:MAX_SKILLS]:
        try:
            metadata = _metadata(read_skill(skill_id, enabled_ids))
        except ValueError:
            continue
        advert = {
            "id": skill_id,
            "name": metadata.get("name") or skill_id,
            "description": metadata.get("description", ""),
        }
        candidate = json.dumps([*adverts, advert], ensure_ascii=False)
        if len(candidate.encode("utf-8")) > MAX_ADVERT_BYTES:
            break
        adverts.append(advert)
    return json.dumps(adverts, ensure_ascii=False)
