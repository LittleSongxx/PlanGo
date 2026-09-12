"""Read attempts and print the delivery shape a reviewer needs. Read-only, no API.

    conda run --no-capture-output -n plango python scripts/trustworthy/analyze_deliveries.py \
      output/trustworthy-v1/holdout-v3-attempts-r1.json \
      --dataset eval/trustworthy-v1/holdout-v3 [--show h3-unkn-001 ...]

TSR comes from the dataset oracle, so this prints the same programmatic score the
report carries, plus the delivery facts behind each failure: empty deliveries,
bare uncertainty markings (substance below the check threshold) and the failed
check ids. Use it to diagnose a run without spending judge calls.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from scripts.trustworthy.schema import load_dataset
from scripts.trustworthy.tsr import delivery_substance, delivery_text, score_attempt

LAYERS = ("calculate", "conflict", "sparse_edit", "persist", "unknown", "boundary")


def actor_line(actor: dict) -> str:
    git = actor.get("git") or {}
    product = str(actor.get("product_sha") or "")[:12]
    commit = str(git.get("commit") or "")[:8]
    model = (actor.get("model") or {}).get("model")
    return "actor product_sha={} commit={} dirty={} model={}".format(
        product, commit, git.get("dirty"), model
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect sealed attempts without scoring calls")
    parser.add_argument("attempts")
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--show", nargs="*", default=[], help="print full delivery text for these task ids"
    )
    args = parser.parse_args(argv)

    dataset = load_dataset(Path(args.dataset))
    bundle = json.loads(Path(args.attempts).read_text(encoding="utf-8"))
    attempts = bundle["attempts"] if isinstance(bundle, dict) else bundle
    actor = bundle.get("actor") if isinstance(bundle, dict) else None
    if actor:
        print(actor_line(actor))

    stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    fails: dict[str, list[tuple[str, list[str]]]] = collections.defaultdict(list)
    deliveries: dict[str, str] = {}
    for attempt in attempts:
        task_id = attempt["task_id"]
        layer = dataset["tasks"][task_id]["layer"]
        result = score_attempt(attempt, dataset["oracles"][task_id])
        text = delivery_text(attempt)
        substance = delivery_substance(attempt)
        deliveries[task_id] = text
        entry = stats[layer]
        entry["n"] += 1
        entry["ok"] += int(bool(result.get("task_success")))
        entry["empty"] += 1 if not text.strip() else 0
        entry["unknown_mark"] += 1 if "未知" in text else 0
        entry["substance_lt_12"] += 1 if substance < 12 else 0
        if not result.get("task_success"):
            fails[layer].append((task_id, list(result.get("failed_checks") or [])))

    print()
    print("{:14s} {:>10s} {:>6s} {:>8s} {:>7s}".format("layer", "TSR", "empty", "unknown", "sub<12"))
    total_ok = total_n = 0
    for layer in (*LAYERS, *(name for name in sorted(stats) if name not in LAYERS)):
        entry = stats.get(layer)
        if not entry:
            continue
        total_ok += entry["ok"]
        total_n += entry["n"]
        rate = entry["ok"] / entry["n"]
        print(
            "{:14s} {:4d}/{:<3d}{:6.3f} {:6d} {:8d} {:7d}".format(
                layer,
                entry["ok"],
                entry["n"],
                rate,
                entry["empty"],
                entry["unknown_mark"],
                entry["substance_lt_12"],
            )
        )
    if total_n:
        print("{:14s} {:4d}/{:<3d}{:6.3f}".format("ALL", total_ok, total_n, total_ok / total_n))

    for layer in LAYERS:
        rows = fails.get(layer)
        if not rows:
            continue
        print()
        print("{} 未过 ({}):".format(layer, len(rows)))
        counts = collections.Counter(check for _, checks in rows for check in checks)
        print("  失败检查:", dict(counts))
        for task_id, checks in rows:
            preview = deliveries.get(task_id, "").replace("\n", " ")[:90]
            print("  {} {} | {}".format(task_id, checks, preview))

    for task_id in args.show:
        print()
        print("=== {} ===".format(task_id))
        print(deliveries.get(task_id, "<missing>"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
