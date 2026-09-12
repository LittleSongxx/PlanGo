"""Programmatic task success. One failed check sinks the case."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .schema import require

NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def lookup(root: Any, path: str) -> Any:
    current = root
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        raise KeyError(path)
    return current


def delivery_text(attempt: dict[str, Any]) -> str:
    delivery = attempt.get("delivery") or {}
    if not isinstance(delivery, dict):
        return ""
    return str(delivery.get("text") or "")


def _haystack(attempt: dict[str, Any]) -> str:
    """Marker checks read the user-visible answer only.

    Searching the whole attempt let a state field satisfy a marker the user
    never saw, so a run with no visible answer could still pass.
    """
    return delivery_text(attempt)


UNCERTAINTY = re.compile(r"未知|无法确定|资料未写明|当前值未知|没有写明|未公布|未核对")
PUNCT = set("。！？!?；;，,、：: \t\n—…·-()（）[]【】\"'“”‘’")


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidOperation
    return Decimal(str(value))


def _number_equals(attempt: dict[str, Any], check: dict[str, Any]) -> bool:
    actual = lookup(attempt, check["path"])
    expected = check["expected"]
    decimals = check.get("decimals")
    left = _as_decimal(actual)
    right = _as_decimal(expected)
    if decimals is not None:
        quant = Decimal("1").scaleb(-int(decimals))
        left = left.quantize(quant)
        right = right.quantize(quant)
    return left == right


def _field_equals(attempt: dict[str, Any], check: dict[str, Any]) -> bool:
    actual = lookup(attempt, check["path"])
    if "equals_path" in check:
        return actual == lookup(attempt, check["equals_path"])
    return actual == check.get("expected")


def delivery_substance(attempt: dict[str, Any]) -> int:
    """Substantive characters the user can read, beyond the uncertainty marking.

    Counts characters that are not punctuation, not digits and not part of an
    uncertainty phrase, so "未知" alone scores 0.
    """
    text = UNCERTAINTY.sub("", delivery_text(attempt))
    return sum(1 for char in text if char not in PUNCT and not char.isdigit())


def _substance_min(attempt: dict[str, Any], check: dict[str, Any]) -> bool:
    try:
        need = int(check.get("chars"))
    except (TypeError, ValueError):
        raise ValueError("substance_min.chars must be an integer") from None
    return delivery_substance(attempt) >= need


def evaluate_check(attempt: dict[str, Any], check: dict[str, Any]) -> bool:
    kind = check["type"]
    if kind == "substance_min":
        return _substance_min(attempt, check)
    if kind == "number_equals":
        try:
            return _number_equals(attempt, check)
        except (KeyError, InvalidOperation, ArithmeticError):
            return False
    if kind == "field_equals":
        try:
            return _field_equals(attempt, check)
        except KeyError:
            return False
    haystack = _haystack(attempt)
    if kind == "marker_present":
        needle = str(check.get("needle") or "")
        return bool(needle) and needle in haystack
    if kind == "marker_absent":
        needle = str(check.get("needle") or "")
        return bool(needle) and needle not in haystack
    if kind == "forbidden_absent":
        needles = check.get("needles") or []
        require(isinstance(needles, list), "forbidden_absent.needles must be a list")
        return all(str(needle) not in haystack for needle in needles)
    raise ValueError(f"unknown check type: {kind}")


def score_attempt(attempt: dict[str, Any], oracle: dict[str, Any]) -> dict[str, Any]:
    if not attempt.get("valid_attempt"):
        return {
            "valid_attempt": False,
            "task_success": None,
            "failed_checks": [],
            "invalid_reason": attempt.get("invalid_reason") or "invalid_attempt",
        }
    scorable = set(oracle.get("scorable_outcomes") or ["completed"])
    outcome = attempt.get("outcome")
    failed = []
    if outcome not in scorable:
        failed.append(f"outcome:{outcome}")
    for check in oracle.get("checks") or []:
        if not evaluate_check(attempt, check):
            failed.append(check["id"])
    return {
        "valid_attempt": True,
        "task_success": int(not failed),
        "failed_checks": failed,
        "outcome": outcome,
    }
