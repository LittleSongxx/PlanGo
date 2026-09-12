"""Project a product snapshot into a sealed attempt. No oracle access."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .schema import world_pack


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_jsonable(child) for child in value]
    return value


def observation_pack(snapshot: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
    state = snapshot.get("state") or {}
    texts: list[str] = []
    seen: set[str] = set()

    def add(text: Any) -> None:
        value = str(text or "").strip()
        if value and value not in seen:
            seen.add(value)
            texts.append(value)

    add((state.get("browser_observation") or {}).get("text"))
    for artifact in state.get("browser_artifacts") or []:
        data = artifact.get("data") if isinstance(artifact, dict) else {}
        add((data or {}).get("text"))
    pack = world_pack(world)
    if not texts:
        add(pack["text"])
    return {"text": "\n".join(texts), "documents": pack["documents"]}


def answer_number(outcome: dict[str, Any] | None) -> float | int | None:
    rows = ((outcome or {}).get("data") or {}).get("calculations") or []
    for row in reversed(rows):
        if not isinstance(row, dict) or not row.get("ok"):
            continue
        raw = row.get("value")
        if raw is None:
            continue
        try:
            number = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
        if number == number.to_integral_value():
            return int(number)
        return float(number)
    return None


def delivery_from(snapshot: dict[str, Any]) -> dict[str, Any]:
    state = snapshot.get("state") or {}
    outcome = state.get("execution_outcome") or {}
    text = str(outcome.get("summary") or state.get("reason") or "")
    delivery: dict[str, Any] = {"text": text}
    number = answer_number(outcome if isinstance(outcome, dict) else None)
    if number is not None:
        delivery["answer_number"] = number
    uncertainty = (outcome.get("data") or {}).get("uncertainty") if isinstance(outcome, dict) else None
    if isinstance(uncertainty, dict) and uncertainty:
        delivery["uncertainty"] = uncertainty
    return delivery


def _spec(value: Any) -> dict[str, Any]:
    dumped = _jsonable(value)
    return dumped if isinstance(dumped, dict) else {}


def attempt_outcome(snapshot: dict[str, Any]) -> str:
    phase = str(snapshot.get("phase") or "")
    if phase == "SUCCEEDED":
        return "completed"
    return phase.lower() or "unknown"


# Provider/runtime cutoffs are not task capability. TSR excludes them.
_INFRA_MARKERS = (
    "模型请求超时",
    "模型服务额度不足",
    "模型服务请求过于频繁",
    "无法连接模型服务",
    "模型服务暂时异常",
    "模型鉴权失败",
    "模型服务拒绝访问",
    "超过本次运行时间上限",
    "trustworthy_runner_timeout",
)
_RETRYABLE_INFRA = (
    "模型请求超时",
    "模型服务请求过于频繁",
    "无法连接模型服务",
    "模型服务暂时异常",
    "超过本次运行时间上限",
    "trustworthy_runner_timeout",
)


def infrastructure_reason(snapshot: dict[str, Any]) -> str | None:
    state = snapshot.get("state") or {}
    outcome = state.get("execution_outcome") if isinstance(state.get("execution_outcome"), dict) else {}
    blob = "\n".join(
        str(part or "")
        for part in (state.get("reason"), (outcome or {}).get("summary"), snapshot.get("phase"))
    )
    if state.get("timeout_stage"):
        return "timeout_stage:" + str(state["timeout_stage"])
    for marker in _INFRA_MARKERS:
        if marker in blob:
            return marker
    return None


def retryable_infrastructure(reason: str | None) -> bool:
    return bool(reason) and any(marker in reason for marker in _RETRYABLE_INFRA)


def project_attempt(
    *,
    task: dict[str, Any],
    world: dict[str, Any],
    snapshot: dict[str, Any],
    trial_id: str,
    prior_trip_spec: dict[str, Any] | None = None,
    valid_attempt: bool,
    invalid_reason: str | None = None,
) -> dict[str, Any]:
    state = snapshot.get("state") or {}
    outcome = _jsonable(state.get("execution_outcome") or {})
    if not isinstance(outcome, dict):
        outcome = {}
    attempt = {
        "trial_id": trial_id,
        "task_id": task["task_id"],
        "valid_attempt": valid_attempt,
        "outcome": attempt_outcome(snapshot) if valid_attempt else snapshot.get("outcome") or "invalid",
        "delivery": delivery_from(snapshot),
        "end_state": {
            "trip_spec": _spec(state.get("trip_spec")),
            "previous_spec": _spec(state.get("previous_spec")),
            "prior_trip_spec": _spec(prior_trip_spec),
            "execution_outcome": outcome,
        },
        "observation_pack": observation_pack(snapshot, world),
        "run_id": snapshot.get("run_id"),
        "phase": snapshot.get("phase"),
    }
    if invalid_reason:
        attempt["invalid_reason"] = invalid_reason
    return attempt
