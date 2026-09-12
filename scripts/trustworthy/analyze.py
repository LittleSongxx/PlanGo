"""Read reports and print the comparison a reviewer needs. Read-only.

    conda run --no-capture-output -n plango python scripts/trustworthy/analyze.py \
      output/trustworthy-v1/holdout-v2-report-r3-llm-v1.3.json \
      output/trustworthy-v1/holdout-v3-report-r1-llm.json

Prints one row per report (scorer, TSR, F, coverage, actor identity) and, for
holdout-v3 reports, the same numbers split by layer and by wording family.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

# v3 mixes surface forms on purpose; see eval/trustworthy-v1/holdout-v3/README.md.
NOVEL_CALCULATE_KINDS = frozenset({1, 3, 5, 6})


def wording_family(task_id: str) -> str:
    parts = task_id.split("-")
    layer = parts[1] if len(parts) > 1 else ""
    try:
        index = int(task_id.rsplit("-", 1)[-1])
    except ValueError:
        return "unknown"
    if layer == "calc":
        return "novel" if index % 7 in NOVEL_CALCULATE_KINDS else "shared"
    if layer == "pers":
        return "novel" if index % 2 == 0 else "shared"
    if layer in {"conf", "unkn"}:
        return "no-cue"
    return "plain"


def _fmt(value: Any, spec: str = "6.3f") -> str:
    return "  n/a" if value is None else format(value, spec)


def headline(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    faith = data.get("faithfulness") or {}
    tsr = data.get("tsr") or {}
    cov = data.get("coverage") or {}
    actor = data.get("actor") if isinstance(data.get("actor"), dict) else {}
    git = actor.get("git") or {}
    model = actor.get("model") or {}
    return {
        "file": path.name,
        "scorer": data.get("scorer_version"),
        "n": data.get("n_valid"),
        "tsr": tsr.get("point"),
        "succ": tsr.get("successes"),
        "f": faith.get("macro_mean"),
        "nf": faith.get("n_scored"),
        "share": cov.get("faithfulness_scored_share"),
        "empty": cov.get("delivery_empty"),
        "bare": cov.get("delivery_bare_uncertainty"),
        "low": cov.get("faithfulness_lower_bound"),
        "product": str(actor.get("product_sha") or "")[:12],
        "commit": str(git.get("commit") or "")[:8],
        "dirty": git.get("dirty"),
        "model": model.get("model"),
        "cases": data.get("cases") or [],
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    head = (
        f"{'report':44s} {'scorer':22s} {'n':>4s} {'TSR':>6s} {'F':>6s} {'nF':>4s} "
        f"{'cov':>5s} {'empty':>5s} {'bare':>5s} {'F0':>6s}"
    )
    print(head)
    print("-" * len(head))
    for row in rows:
        print(
            f"{row['file']:44s} {str(row['scorer']):22s} {str(row['n']):>4s} "
            f"{_fmt(row['tsr'])} {_fmt(row['f'])} {str(row['nf']):>4s} "
            f"{_fmt(row['share'], '5.2f')} {str(row['empty']):>5s} {str(row['bare']):>5s} {_fmt(row['low'])}"
        )
    print()
    for row in rows:
        if row["product"] or row["commit"]:
            print(
                f"{row['file']:44s} product={row['product']} commit={row['commit']} "
                f"dirty={row['dirty']} model={row['model']}"
            )


def print_split(row: dict[str, Any]) -> None:
    cases = row["cases"]
    if not cases:
        return
    print(f"\n=== {row['file']} ===")
    for key, label in ((lambda c: c.get("layer"), "layer"), (lambda c: wording_family(c["task_id"]), "wording")):
        agg: dict[str, dict[str, Any]] = collections.defaultdict(
            lambda: {"n": 0, "ok": 0, "f": [], "claims": 0, "sup": 0, "thin": 0}
        )
        for case in cases:
            entry = agg[key(case)]
            entry["n"] += 1
            entry["ok"] += int(case.get("task_success") or 0)
            if case.get("faithfulness") is not None:
                entry["f"].append(case["faithfulness"])
                entry["claims"] += case.get("factual_claims") or 0
                entry["sup"] += case.get("supported_claims") or 0
            if (case.get("delivery_substance") or 0) < 12:
                entry["thin"] += 1
        print(f"--- by {label} ---")
        for name, entry in sorted(agg.items()):
            macro = sum(entry["f"]) / len(entry["f"]) if entry["f"] else None
            print(
                f"  {name:10s} TSR {entry['ok']:3d}/{entry['n']:3d} = {entry['ok'] / entry['n']:.3f}   "
                f"F {_fmt(macro)}  计分 {len(entry['f'])} 题  断言 {entry['sup']}/{entry['claims']}  "
                f"实义<12 的 {entry['thin']} 题"
            )
    fails = [case["task_id"] for case in cases if not case.get("task_success")]
    print("TSR 未过:", " ".join(fails) if fails else "（无）")
    checks: collections.Counter[str] = collections.Counter()
    for case in cases:
        for name in case.get("failed_checks") or []:
            checks[name] += 1
    if checks:
        print("失败检查分布:", dict(checks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare trustworthy reports")
    parser.add_argument("reports", nargs="+")
    parser.add_argument("--no-split", action="store_true", help="only print the headline table")
    args = parser.parse_args(argv)
    rows = [headline(Path(path)) for path in args.reports]
    print_table(rows)
    if not args.no_split:
        for row in rows:
            print_split(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
