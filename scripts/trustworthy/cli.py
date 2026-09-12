"""Score sealed attempts or validate a trustworthy-v1 dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trustworthy import PROTOCOL_KINDS, SCORER_VERSION
from scripts.trustworthy.faithfulness import JUDGES, score_delivery, with_calculator_evidence, with_user_turns
from scripts.trustworthy.faithfulness_judge import JudgeError, judge_meta as llm_judge_meta
from scripts.trustworthy.report import summarize
from scripts.trustworthy.actor import load_actor_dataset
from scripts.trustworthy.runner import IsolatedRunner, write_attempts
from scripts.trustworthy.schema import (
    file_sha,
    load_attempts,
    load_dataset,
    read_json,
    validate_dataset,
    world_pack,
)
from scripts.trustworthy.tsr import delivery_substance, delivery_text, score_attempt


def _score_rows(
    dataset: dict[str, Any],
    attempts: list[dict[str, Any]],
    *,
    judge: str,
    complete=None,
) -> list[dict[str, Any]]:
    rows = []
    version = f"{SCORER_VERSION}-{judge}"
    total = len(attempts)
    for index, attempt in enumerate(attempts, start=1):
        task_id = attempt["task_id"]
        task = dataset["tasks"][task_id]
        oracle = dataset["oracles"][task_id]
        world = dataset["worlds"][task["world_id"]]
        pack = attempt.get("observation_pack") or world_pack(world)
        pack = with_calculator_evidence(pack, attempt)
        pack = with_user_turns(pack, task)
        tsr = score_attempt(attempt, oracle)
        faith = score_delivery(attempt.get("delivery"), pack, judge=judge, complete=complete)
        text = delivery_text(attempt)
        rows.append(
            {
                "trial_id": attempt["trial_id"],
                "task_id": task_id,
                "layer": task.get("layer"),
                "split": task.get("split"),
                "expected_pass": attempt.get("expected_pass"),
                "delivery_chars": len(text.strip()),
                "delivery_substance": delivery_substance(attempt),
                "valid_attempt": tsr["valid_attempt"],
                "task_success": tsr.get("task_success"),
                "failed_checks": tsr.get("failed_checks") or [],
                "faithfulness": faith.get("faithfulness"),
                "faithfulness_applicable": faith.get("applicable"),
                "factual_claims": faith.get("factual_claims"),
                "supported_claims": faith.get("supported"),
                "claims": faith.get("claims") or [],
                "invalid_reason": tsr.get("invalid_reason"),
                "scorer_version": version,
            }
        )
        if judge == "llm" and (index == 1 or index == total or index % 20 == 0):
            print(f"faithfulness judge {index}/{total}", file=sys.stderr)
    return rows


def cmd_validate(args: argparse.Namespace) -> int:
    summary = validate_dataset(Path(args.dataset))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    judge = args.judge
    judge_meta = {"method": judge}
    if judge == "llm":
        from scripts.trustworthy.runner import live_model_config

        if not live_model_config().get("OPENAI_API_KEY"):
            raise SystemExit("score --judge llm requires OPENAI_API_KEY in the environment or project .env")
        judge_meta = llm_judge_meta()
    dataset = load_dataset(Path(args.dataset))
    attempts_path = Path(args.attempts)
    attempts = load_attempts(attempts_path)
    raw_bundle = read_json(attempts_path)
    actor_meta = raw_bundle.get("actor") if isinstance(raw_bundle, dict) else None
    try:
        rows = _score_rows(dataset, attempts, judge=judge)
    except JudgeError as error:
        raise SystemExit(f"faithfulness judge failed: {error}") from error
    meta = PROTOCOL_KINDS[dataset["protocol"]["evaluation_kind"]]
    report = summarize(
        rows,
        dataset_sha=dataset["dataset_sha"],
        attempts_sha=file_sha(attempts_path),
        split=meta["split"],
        report_kind=meta["report_kind"],
        judge=judge,
        judge_meta=judge_meta,
        actor_meta=actor_meta,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "n_valid": report["n_valid"],
                "tsr": report["tsr"]["point"],
                "faithfulness": report["faithfulness"]["macro_mean"],
                "judge": judge,
                "report_kind": report["report_kind"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    actor = load_actor_dataset(Path(args.dataset))
    if actor.get("oracles_opened"):
        raise SystemExit("runner opened oracles.json")
    runner = IsolatedRunner(Path(args.work_dir), live=args.live, timeout=args.timeout)
    ids = [item.strip() for item in (args.task_id or []) if item.strip()]
    bundle = runner.run_dataset(
        Path(args.dataset),
        task_ids=ids or None,
        limit=args.limit,
    )
    write_attempts(Path(args.output), bundle)
    print(
        json.dumps(
            {
                "output": args.output,
                "n_attempts": len(bundle["attempts"]),
                "n_invalid": sum(not row.get("valid_attempt") for row in bundle["attempts"]),
                "oracles_opened": bundle["oracles_opened"],
                "evaluation_kind": bundle["evaluation_kind"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trustworthy TSR / Faithfulness CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-dataset")
    validate.add_argument("--dataset", required=True)
    validate.set_defaults(func=cmd_validate)
    score = sub.add_parser("score")
    score.add_argument("--dataset", required=True)
    score.add_argument("--attempts", required=True)
    score.add_argument("--output", required=True)
    score.add_argument("--judge", choices=JUDGES, default="llm", help="Faithfulness judge; default llm")
    score.set_defaults(func=cmd_score)
    run = sub.add_parser("run", help="Drive sealed attempts from tasks and worlds only")
    run.add_argument("--dataset", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--work-dir", default="output/trustworthy-v1/runner-work")
    run.add_argument("--live", action="store_true", help="Use OPENAI_API_KEY; default is isolated/disabled model")
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--task-id", action="append", default=None)
    run.add_argument("--timeout", type=float, default=180)
    run.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
