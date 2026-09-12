"""Load and validate a trustworthy-v1 dataset. Actor files must not leak oracles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import ACTOR_FORBIDDEN_KEYS, LAYERS, PROTOCOL_KINDS, TRIP_SPEC_LEAVES

CHECK_TYPES = frozenset(
    {
        "number_equals",
        "field_equals",
        "marker_present",
        "marker_absent",
        "forbidden_absent",
        "substance_min",
    }
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _index(rows: list[Any], key: str, name: str) -> dict[str, Any]:
    require(isinstance(rows, list) and rows, f"{name} must be a nonempty list")
    index: dict[str, Any] = {}
    for row in rows:
        require(isinstance(row, dict), f"{name} row must be an object")
        ident = row.get(key)
        require(isinstance(ident, str) and ident.strip(), f"{name} missing {key}")
        require(ident not in index, f"duplicate {key}: {ident}")
        index[ident] = row
    return index


def _scan_forbidden(value: Any, trail: str) -> None:
    if isinstance(value, dict):
        leaked = ACTOR_FORBIDDEN_KEYS & set(value)
        require(not leaked, f"{trail} leaks oracle keys {sorted(leaked)}")
        for name, child in value.items():
            _scan_forbidden(child, f"{trail}.{name}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden(child, f"{trail}[{index}]")


def load_dataset(root: Path) -> dict[str, Any]:
    root = Path(root)
    protocol = read_json(root / "protocol.json")
    require(protocol.get("schema_version") == 1, "protocol.schema_version must be 1")
    kind = protocol.get("evaluation_kind")
    require(kind in PROTOCOL_KINDS, f"unknown evaluation_kind: {kind}")
    expected = PROTOCOL_KINDS[kind]
    require(protocol.get("report_kind") == expected["report_kind"], f"report_kind must be {expected['report_kind']}")
    require(int(protocol.get("holdout_planned") or 0) >= 200, "holdout_planned must be at least 200")
    tasks = _index(read_json(root / "tasks.json"), "task_id", "tasks")
    worlds = _index(read_json(root / "worlds.json"), "world_id", "worlds")
    oracles = _index(read_json(root / "oracles.json"), "task_id", "oracles")
    return {
        "root": root,
        "protocol": protocol,
        "tasks": tasks,
        "worlds": worlds,
        "oracles": oracles,
        "dataset_sha": canonical_sha(
            {
                "protocol": file_sha(root / "protocol.json"),
                "tasks": file_sha(root / "tasks.json"),
                "worlds": file_sha(root / "worlds.json"),
                "oracles": file_sha(root / "oracles.json"),
            }
        ),
    }


def world_pack(world: dict[str, Any]) -> dict[str, Any]:
    documents = world.get("documents")
    require(isinstance(documents, list) and documents, f"{world.get('world_id')}: documents required")
    texts = []
    for document in documents:
        require(isinstance(document, dict), "document must be an object")
        text = document.get("text")
        require(isinstance(text, str) and text.strip(), "document.text required")
        texts.append(text)
    return {"documents": documents, "text": "\n".join(texts)}


def validate_dataset(root: Path) -> dict[str, Any]:
    dataset = load_dataset(root)
    protocol = dataset["protocol"]
    layers = protocol.get("layers") or list(LAYERS)
    require(list(layers) == list(LAYERS), "protocol.layers must be the six capability layers in order")
    minimum = int(protocol.get("min_tasks_per_layer") or 2)
    expected_split = PROTOCOL_KINDS[protocol["evaluation_kind"]]["split"]
    counts = {layer: 0 for layer in LAYERS}
    for task_id, task in dataset["tasks"].items():
        _scan_forbidden(task, f"task.{task_id}")
        layer = task.get("layer")
        require(layer in LAYERS, f"{task_id}: unknown layer")
        require(task.get("split") == expected_split, f"{task_id}: split must be {expected_split}")
        _validate_initial_spec(task_id, task.get("initial_trip_spec"))
        require(isinstance(task.get("as_of"), str) and task["as_of"], f"{task_id}: as_of required")
        turns = task.get("user_turns")
        require(isinstance(turns, list) and turns, f"{task_id}: user_turns required")
        world_id = task.get("world_id")
        require(world_id in dataset["worlds"], f"{task_id}: missing world {world_id}")
        require(task_id in dataset["oracles"], f"{task_id}: missing oracle")
        _scan_forbidden(dataset["worlds"][world_id], f"world.{world_id}")
        world_pack(dataset["worlds"][world_id])
        counts[layer] += 1
        oracle = dataset["oracles"][task_id]
        checks = oracle.get("checks")
        require(isinstance(checks, list) and checks, f"{task_id}: checks required")
        seen = set()
        for check in checks:
            require(isinstance(check, dict), f"{task_id}: check must be an object")
            ident = check.get("id")
            require(isinstance(ident, str) and ident not in seen, f"{task_id}: check id required and unique")
            seen.add(ident)
            kind = check.get("type")
            require(kind in CHECK_TYPES, f"{task_id}.{ident}: unknown check type")
            _validate_check_shape(task_id, check)
            if protocol["evaluation_kind"] != "dev_seed":
                _validate_check_paths(task_id, check)
        observation = world_pack(dataset["worlds"][world_id])["text"]
        for span in _collect_spans(oracle):
            require(span in observation, f"{task_id}: span not in world: {span!r}")
        outcomes = oracle.get("scorable_outcomes") or ["completed"]
        require(isinstance(outcomes, list) and outcomes, f"{task_id}: scorable_outcomes required")
    missing = [layer for layer, count in counts.items() if count < minimum]
    require(not missing, f"layers below min_tasks_per_layer: {missing}")
    nested = Path(dataset["root"]) / "holdout" / "protocol.json"
    if not nested.exists():
        holdout = Path(dataset["root"]) / "holdout" / "tasks.json"
        if holdout.exists():
            extra = read_json(holdout)
            require(extra == [], "holdout/tasks.json must stay empty unless holdout/ is its own dataset")
    return {
        "ok": True,
        "tasks": len(dataset["tasks"]),
        "layers": counts,
        "dataset_sha": dataset["dataset_sha"],
    }


def allowed_value_path(path: str) -> bool:
    if path in {"delivery.answer_number", "end_state.execution_outcome.data.business_completed"}:
        return True
    for prefix in ("end_state.trip_spec.", "end_state.previous_spec.", "end_state.prior_trip_spec."):
        if path.startswith(prefix) and path[len(prefix) :] in TRIP_SPEC_LEAVES:
            return True
    return False


def _validate_check_paths(task_id: str, check: dict[str, Any]) -> None:
    prefix = f"{task_id}.{check['id']}"
    for key in ("path", "equals_path"):
        value = check.get(key)
        if value is None:
            continue
        require(isinstance(value, str) and allowed_value_path(value), f"{prefix}: {key} not in attempt contract")


def _validate_initial_spec(task_id: str, spec: Any) -> None:
    if spec is None:
        return
    require(isinstance(spec, dict), f"{task_id}: initial_trip_spec must be an object")
    for key, value in spec.items():
        if key == "location":
            require(isinstance(value, dict) and isinstance(value.get("name"), str), f"{task_id}: location.name required")
            continue
        require(key in TRIP_SPEC_LEAVES, f"{task_id}: initial_trip_spec.{key} not in product inventory")


def _validate_check_shape(task_id: str, check: dict[str, Any]) -> None:
    ident = check["id"]
    kind = check["type"]
    prefix = f"{task_id}.{ident}"
    if kind == "number_equals":
        require(isinstance(check.get("path"), str) and check["path"], f"{prefix}: path required")
        require("expected" in check, f"{prefix}: expected required")
        return
    if kind == "field_equals":
        require(isinstance(check.get("path"), str) and check["path"], f"{prefix}: path required")
        require("expected" in check or "equals_path" in check, f"{prefix}: expected or equals_path required")
        return
    if kind in {"marker_present", "marker_absent"}:
        require(isinstance(check.get("needle"), str) and check["needle"], f"{prefix}: needle required")
        return
    if kind == "substance_min":
        chars = check.get("chars")
        require(isinstance(chars, int) and not isinstance(chars, bool) and chars > 0, f"{prefix}: chars must be a positive integer")
        return
    needles = check.get("needles")
    require(isinstance(needles, list) and needles, f"{prefix}: needles required")
    require(all(isinstance(item, str) and item for item in needles), f"{prefix}: needles must be strings")


def _collect_spans(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        span = value.get("span")
        if isinstance(span, str) and span:
            found.append(span)
        quotes = value.get("evidence_spans")
        if isinstance(quotes, list):
            found.extend(item for item in quotes if isinstance(item, str) and item)
        for child in value.values():
            found.extend(_collect_spans(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_spans(child))
    return found


def load_attempts(path: Path) -> list[dict[str, Any]]:
    raw = read_json(path)
    rows = raw.get("attempts") if isinstance(raw, dict) else raw
    require(isinstance(rows, list) and rows, "attempts must be a nonempty list")
    seen = set()
    for row in rows:
        require(isinstance(row, dict), "attempt must be an object")
        trial = row.get("trial_id")
        require(isinstance(trial, str) and trial and trial not in seen, "trial_id must be unique")
        seen.add(trial)
        require(isinstance(row.get("task_id"), str) and row["task_id"], "attempt.task_id required")
        require(type(row.get("valid_attempt")) is bool, "valid_attempt must be an explicit boolean")
    return rows
