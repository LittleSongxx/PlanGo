"""Aggregate TSR and faithfulness into a provisional-dev report."""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any

from . import SCORER_VERSION

Z95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z95) -> dict[str, float] | None:
    if n <= 0:
        return None
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = (z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)) / denom
    return {"low": max(0.0, center - margin), "high": min(1.0, center + margin)}


def bootstrap_mean(values: list[float], draws: int = 2000, seed: int = 17) -> dict[str, float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    samples = []
    count = len(values)
    for _ in range(draws):
        samples.append(sum(values[rng.randrange(count)] for _ in range(count)) / count)
    samples.sort()
    low_index = int(0.025 * (draws - 1))
    high_index = int(0.975 * (draws - 1))
    return {
        "low": samples[low_index],
        "high": samples[high_index],
        "draws": draws,
        "seed": seed,
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


DISCLAIMERS = {
    "provisional_dev": (
        "provisional_dev seed report, not a holdout official score. "
        "N is far below the planned 200-task holdout, so intervals are wide."
    ),
    "provisional_holdout": (
        "provisional_holdout report. Gold may still be unreviewed; "
        "do not report this as an official unseen holdout score."
    ),
}


def summarize(
    rows: list[dict[str, Any]],
    *,
    dataset_sha: str,
    attempts_sha: str,
    split: str = "dev",
    report_kind: str = "provisional_dev",
    judge: str = "rules",
    judge_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    valid = [row for row in rows if row.get("valid_attempt")]
    invalid = [row for row in rows if not row.get("valid_attempt")]
    successes = sum(int(row.get("task_success") or 0) for row in valid)
    faith_values = [
        float(row["faithfulness"])
        for row in valid
        if row.get("faithfulness") is not None
    ]
    n = len(valid)
    tsr = (successes / n) if n else None
    faith = _mean(faith_values)
    version = f"{SCORER_VERSION}-{judge}"
    meta = {"method": judge, **(judge_meta or {})}
    fingerprint = json_fingerprint(version, meta)
    return {
        "schema_version": 1,
        "report_kind": report_kind,
        "split": split,
        "scorer_version": version,
        "dataset_sha": dataset_sha,
        "attempts_sha": attempts_sha,
        "scorer_sha": fingerprint,
        "n_attempts": len(rows),
        "n_valid": n,
        "n_invalid": len(invalid),
        "tsr": {
            "point": tsr,
            "successes": successes,
            "denominator": n,
            "wilson_95": wilson_interval(successes, n),
        },
        "faithfulness": {
            "macro_mean": faith,
            "n_scored": len(faith_values),
            "bootstrap_95": bootstrap_mean(faith_values),
            "judge": meta,
        },
        "disclaimer": DISCLAIMERS.get(report_kind, DISCLAIMERS["provisional_dev"]),
        "cases": rows,
    }


def json_fingerprint(*parts: Any) -> str:
    payload = hashlib.sha256()
    for part in parts:
        payload.update(repr(part).encode("utf-8"))
    return payload.hexdigest()
