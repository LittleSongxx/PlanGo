"""Pure scoring and report aggregation for trustworthy-v1. No product run."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from datetime import date

from plango.graph import _card_hold_summary
from plango_harness.agent.contracts import TripSpec

from scripts.trustworthy.cli import main as cli_main
from scripts.trustworthy.faithfulness import score_delivery
from scripts.trustworthy.faithfulness_judge import JudgeError, judge_user_payload, parse_labels, prompt_sha
from scripts.trustworthy.report import summarize, wilson_interval
from scripts.trustworthy.schema import load_attempts, load_dataset, validate_dataset
from scripts.trustworthy.tsr import score_attempt

DATASET = ROOT / "eval" / "trustworthy-v1"
ATTEMPTS = DATASET / "fixtures" / "attempts.json"


def _rows():
    dataset = load_dataset(DATASET)
    scored = []
    for attempt in load_attempts(ATTEMPTS):
        task = dataset["tasks"][attempt["task_id"]]
        oracle = dataset["oracles"][attempt["task_id"]]
        tsr = score_attempt(attempt, oracle)
        pack = attempt.get("observation_pack") or dataset["worlds"][task["world_id"]]
        faith = score_delivery(attempt.get("delivery"), pack)
        scored.append((attempt, tsr, faith, task))
    return dataset, scored


def test_dataset_contract_accepts_dev_seed():
    summary = validate_dataset(DATASET)
    assert summary["ok"] is True
    assert summary["tasks"] == 18
    assert summary["layers"] == {
        "calculate": 3,
        "conflict": 3,
        "sparse_edit": 3,
        "persist": 3,
        "unknown": 3,
        "boundary": 3,
    }


def test_actor_files_do_not_leak_oracle_fields():
    dataset = load_dataset(DATASET)
    leaked = {"expected", "checks", "forbidden", "oracle", "needles", "equals_path"}
    blob = json.dumps({"tasks": dataset["tasks"], "worlds": dataset["worlds"]}, ensure_ascii=False)
    for key in leaked:
        assert f'"{key}"' not in blob


def test_holdout_dataset_is_unreviewed_and_sized():
    holdout = DATASET / "holdout"
    summary = validate_dataset(holdout)
    protocol = json.loads((holdout / "protocol.json").read_text(encoding="utf-8"))
    assert summary["ok"] is True
    assert summary["tasks"] >= 200
    assert all(count >= 25 for count in summary["layers"].values())
    assert protocol["evaluation_kind"] == "holdout_reviewed"
    assert protocol["gold_review"] == "accepted"
    leaked = {"expected", "checks", "forbidden", "oracle", "needles", "equals_path"}
    actor = json.dumps(
        {
            "tasks": json.loads((holdout / "tasks.json").read_text(encoding="utf-8")),
            "worlds": json.loads((holdout / "worlds.json").read_text(encoding="utf-8")),
        },
        ensure_ascii=False,
    )
    for key in leaked:
        assert f'"{key}"' not in actor


def test_holdout_v2_is_a_separate_unreviewed_set():
    holdout = DATASET / "holdout-v2"
    summary = validate_dataset(holdout)
    protocol = json.loads((holdout / "protocol.json").read_text(encoding="utf-8"))
    v1_ids = {row["task_id"] for row in json.loads((DATASET / "holdout" / "tasks.json").read_text(encoding="utf-8"))}
    v2_ids = {row["task_id"] for row in json.loads((holdout / "tasks.json").read_text(encoding="utf-8"))}
    v1_places = json.dumps(json.loads((DATASET / "holdout" / "worlds.json").read_text(encoding="utf-8")), ensure_ascii=False)
    v2_places = json.dumps(json.loads((holdout / "worlds.json").read_text(encoding="utf-8")), ensure_ascii=False)
    assert summary["ok"] is True
    assert summary["tasks"] >= 200
    assert all(count >= 25 for count in summary["layers"].values())
    assert protocol["name"] == "trustworthy-v1-holdout-v2"
    assert protocol["evaluation_kind"] == "holdout_unreviewed"
    assert protocol["gold_review"] == "pending"
    assert v1_ids.isdisjoint(v2_ids)
    assert "青石" not in v2_places and "河湾步道" not in v2_places
    assert "岚岫" in v2_places and "矾溪" in v2_places
    assert "河湾步道" in v1_places
    leaked = {"expected", "checks", "forbidden", "oracle", "needles", "equals_path"}
    actor = json.dumps(
        {
            "tasks": json.loads((holdout / "tasks.json").read_text(encoding="utf-8")),
            "worlds": json.loads((holdout / "worlds.json").read_text(encoding="utf-8")),
        },
        ensure_ascii=False,
    )
    for key in leaked:
        assert f'"{key}"' not in actor


def test_fixture_pass_and_fail_are_caught():
    _, scored = _rows()
    pairs = {}
    for attempt, tsr, faith, task in scored:
        if not attempt["valid_attempt"]:
            assert tsr["task_success"] is None
            continue
        key = attempt["task_id"]
        pairs.setdefault(key, {})[bool(attempt["expected_pass"])] = (tsr, faith, task)
    assert len(pairs) == 18
    for task_id, sides in pairs.items():
        passed = sides[True][0]
        failed = sides[False][0]
        assert passed["task_success"] == 1, task_id
        assert failed["task_success"] == 0, task_id
        pass_f = sides[True][1]["faithfulness"]
        fail_f = sides[False][1]["faithfulness"]
        if pass_f is not None and fail_f is not None:
            assert fail_f < pass_f, task_id


def test_invalid_attempt_is_excluded_from_tsr_denominator():
    report = summarize(
        [
            {"valid_attempt": False, "task_success": None, "faithfulness": None},
            {"valid_attempt": True, "task_success": 1, "faithfulness": 1.0},
            {"valid_attempt": True, "task_success": 0, "faithfulness": 0.0},
        ],
        dataset_sha="a",
        attempts_sha="b",
    )
    assert report["n_valid"] == 2
    assert report["n_invalid"] == 1
    assert report["tsr"]["point"] == 0.5
    assert report["tsr"]["denominator"] == 2
    assert report["report_kind"] == "provisional_dev"


def test_empty_delivery_is_not_perfect_faithfulness():
    scored = score_delivery({"text": ""}, {"text": "单人票 62 元。"})
    assert scored["applicable"] is False
    assert scored["faithfulness"] is None


def test_unknown_claim_is_non_factual():
    scored = score_delivery({"text": "当前价格未知。"}, {"text": "一份写着当前价格 128 元。另一份写着当前价格 168 元。"})
    assert scored["applicable"] is False
    assert scored["claims"][0]["label"] == "non-factual"


def test_supported_requires_observation_span():
    observation = {"text": "三人同行合计 186 元。"}
    supported = score_delivery({"text": "三人同行合计 186 元。"}, observation)
    assert supported["faithfulness"] == 1.0
    assert supported["claims"][0]["span"]
    assert supported["claims"][0]["span"] in observation["text"]
    paraphrase = score_delivery({"text": "总数大概是一百八十六。"}, observation)
    assert paraphrase["claims"][0]["label"] == "unsupported"
    assert paraphrase["claims"][0]["span"] is None


def test_page_number_does_not_support_invented_sentence():
    observation = {"text": "关闭前快照：总预算是 460，开始时刻是 12:00。"}
    scored = score_delivery(
        {
            "text": (
                "冻结世界无法确认重启后字段是否仍在，"
                "尽管页上写了总预算是 460，开始时刻是 12:00。"
            )
        },
        observation,
    )
    assert scored["claims"][0]["label"] == "unsupported"
    assert scored["faithfulness"] == 0.0


def test_number_and_unit_alone_do_not_support_a_rewrite():
    observation = {"text": "预收 184 元。扣留 40 元。退还后剩余 144 元。"}
    rewrite = score_delivery({"text": "退还后还能拿回 144 元。"}, observation)
    assert rewrite["claims"][0]["label"] == "unsupported"
    quoted = score_delivery({"text": "退还后剩余 144 元。"}, observation)
    assert quoted["claims"][0]["label"] == "supported"
    assert "退还后剩余 144 元" in (quoted["claims"][0]["span"] or "")


def test_topic_prefix_still_needs_the_page_wording():
    observation = {"text": "停车 12 元。门票 40 元。两项合计 52 元。"}
    scored = score_delivery({"text": "停车和门票两项合计 52 元。"}, observation)
    assert scored["claims"][0]["label"] == "supported"
    assert "两项合计 52 元" in (scored["claims"][0]["span"] or "")


def test_disjoint_words_are_not_a_quote():
    scored = score_delivery(
        {"text": "预览表中有日期栏。"},
        {"text": "预览表看得到日期栏。"},
    )
    assert scored["claims"][0]["label"] == "unsupported"


def test_short_inventory_recitation_can_be_supported():
    scored = score_delivery(
        {"text": "人数是 3。"},
        {"text": "关闭前快照：人数是 3，日期是 2026-12-10。"},
    )
    assert scored["claims"][0]["label"] == "supported"
    assert scored["claims"][0]["span"] in "关闭前快照：人数是 3，日期是 2026-12-10。"


def test_card_hold_summary_is_supported_against_persist_world():
    summary = _card_hold_summary(TripSpec(goal="已有需求卡", party_size=3, visit_date=date(2026, 12, 10)))
    scored = score_delivery(
        {"text": summary},
        {"text": "关闭前快照：人数是 3，日期是 2026-12-10。"},
    )
    assert summary == "人数是 3，日期是 2026-12-10。"
    assert scored["faithfulness"] == 1.0
    assert scored["claims"][0]["span"]


def test_conflicted_number_is_contradicted():
    scored = score_delivery(
        {"text": "当前价格 128 元。"},
        {"text": "一份写着当前价格 128 元。另一份写着当前价格 168 元。"},
    )
    assert scored["claims"][0]["label"] == "contradicted"
    assert "168" in (scored["claims"][0]["span"] or "")


def test_number_equals_and_field_equals():
    oracle = {
        "scorable_outcomes": ["completed"],
        "checks": [
            {"id": "n", "type": "number_equals", "path": "delivery.answer_number", "expected": 186, "decimals": 0},
            {"id": "f", "type": "field_equals", "path": "end_state.trip.note", "equals_path": "end_state.prior.note"},
        ],
    }
    passed = score_attempt(
        {
            "valid_attempt": True,
            "outcome": "completed",
            "delivery": {"text": "ok", "answer_number": 186.0},
            "end_state": {"prior": {"note": "携带折叠车"}, "trip": {"note": "携带折叠车"}},
        },
        oracle,
    )
    assert passed["task_success"] == 1
    failed = score_attempt(
        {
            "valid_attempt": True,
            "outcome": "completed",
            "delivery": {"text": "ok", "answer_number": 180},
            "end_state": {"prior": {"note": "携带折叠车"}, "trip": {"note": ""}},
        },
        oracle,
    )
    assert failed["task_success"] == 0
    assert "n" in failed["failed_checks"]
    assert "f" in failed["failed_checks"]


def test_wilson_interval_is_not_wald():
    interval = wilson_interval(1, 2)
    assert interval is not None
    assert interval["low"] > 0
    assert interval["high"] < 1
    # Wald on 0/1 extremes collapses; Wilson stays open on n=1 all-fail.
    edge = wilson_interval(0, 1)
    assert edge["low"] == 0.0
    assert edge["high"] > 0.0


def test_cli_validate_and_score(tmp_path, capsys):
    assert cli_main(["validate-dataset", "--dataset", str(DATASET)]) == 0
    output = tmp_path / "report.json"
    assert cli_main(["score", "--dataset", str(DATASET), "--attempts", str(ATTEMPTS), "--output", str(output), "--judge", "rules"]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["report_kind"] == "provisional_dev"
    assert report["split"] == "dev"
    assert report["n_valid"] == 36
    assert report["n_invalid"] == 1
    assert report["tsr"]["point"] == pytest.approx(0.5)
    assert report["tsr"]["wilson_95"]["low"] < report["tsr"]["point"] < report["tsr"]["wilson_95"]["high"]
    assert "not a holdout official score" in report["disclaimer"]
    assert report["faithfulness"]["bootstrap_95"]["draws"] == 2000
    assert report["scorer_version"] == "trustworthy.v1.2-rules"
    assert report["faithfulness"]["judge"]["method"] == "rules"


def test_validate_rejects_oracle_leak_and_missing_span(tmp_path):
    for name in ("protocol.json", "tasks.json", "worlds.json", "oracles.json"):
        target = tmp_path / name
        target.write_bytes((DATASET / name).read_bytes())
    (tmp_path / "holdout").mkdir()
    (tmp_path / "holdout" / "tasks.json").write_text("[]\n", encoding="utf-8")
    tasks = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    tasks[0]["expected"] = 186
    (tmp_path / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="leaks oracle keys"):
        validate_dataset(tmp_path)
    tasks[0].pop("expected")
    (tmp_path / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    oracles = json.loads((tmp_path / "oracles.json").read_text(encoding="utf-8"))
    oracles[0]["evidence_spans"] = ["this span is not in the world"]
    (tmp_path / "oracles.json").write_text(json.dumps(oracles, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="span not in world"):
        validate_dataset(tmp_path)


def test_llm_judge_accepts_entailed_paraphrase():
    observation = {"text": "预收 184 元。扣留 40 元。退还后剩余 144 元。"}

    def complete(messages):
        return json.dumps(
            {"labels": [{"claim_id": "S1", "label": "supported", "span": "退还后剩余 144 元"}]},
            ensure_ascii=False,
        )

    scored = score_delivery(
        {"text": "退还后还能拿回 144 元。"},
        observation,
        judge="llm",
        complete=complete,
    )
    assert scored["judge"] == "llm"
    assert scored["claims"][0]["label"] == "supported"
    assert scored["claims"][0]["by"] == "llm"
    assert scored["faithfulness"] == 1.0


def test_llm_judge_rejects_invented_commentary():
    def complete(messages):
        return json.dumps(
            {"labels": [{"claim_id": "S1", "label": "unsupported", "span": None}]},
            ensure_ascii=False,
        )

    scored = score_delivery(
        {"text": "冻结世界无法确认重启后字段是否仍在，尽管页上写了总预算是 460。"},
        {"text": "关闭前快照：总预算是 460，开始时刻是 12:00。"},
        judge="llm",
        complete=complete,
    )
    assert scored["claims"][0]["label"] == "unsupported"
    assert scored["faithfulness"] == 0.0


def test_llm_judge_keeps_contract_filters_and_hides_oracles():
    captured: list[list[dict[str, str]]] = []

    def complete(messages):
        captured.append(messages)
        raise AssertionError("judge must not run for contract-filtered claims")

    unknown = score_delivery(
        {"text": "当前价格未知。"},
        {"text": "一份写着当前价格 128 元。另一份写着当前价格 168 元。"},
        judge="llm",
        complete=complete,
    )
    missing = score_delivery(
        {"text": "一共 999 元。"},
        {"text": "一共 186 元。"},
        judge="llm",
        complete=complete,
    )
    assert unknown["claims"][0]["label"] == "non-factual"
    assert missing["claims"][0]["label"] == "unsupported"
    assert captured == []
    payload = judge_user_payload("观测正文", [{"claim_id": "S1", "text": "还剩 144 元。"}])
    assert "oracle" not in payload and "expected" not in payload and "checks" not in payload


def test_llm_judge_can_support_reporting_both_conflict_values():
    def complete(messages):
        return json.dumps(
            {"labels": [{"claim_id": "S1", "label": "supported", "span": "当前价格 128 元"}]},
            ensure_ascii=False,
        )

    scored = score_delivery(
        {"text": "一份写着当前价格 128 元，另一份写着当前价格 168 元。"},
        {"text": "一份写着当前价格 128 元。另一份写着当前价格 168 元。"},
        judge="llm",
        complete=complete,
    )
    assert scored["claims"][0]["label"] == "supported"


def test_judge_parser_requires_every_claim_and_drops_foreign_span():
    observation = "退还后剩余 144 元。"
    parsed = parse_labels(
        '{"labels":[{"claim_id":"S1","label":"supported","span":"不是原文"}]}',
        ["S1"],
        observation,
    )
    assert parsed["S1"]["label"] == "supported"
    assert parsed["S1"]["span"] is None
    with pytest.raises(JudgeError, match="missed claims"):
        parse_labels('{"labels":[]}', ["S1"], observation)
    assert len(prompt_sha()) == 64
