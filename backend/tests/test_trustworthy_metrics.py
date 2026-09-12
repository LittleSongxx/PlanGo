"""Pure scoring and report aggregation for trustworthy-v1. No product run."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from datetime import date  # noqa: E402

from plango.graph import _card_hold_summary  # noqa: E402
from plango_harness.agent.contracts import TripSpec  # noqa: E402

from scripts.trustworthy.cli import main as cli_main  # noqa: E402
from scripts.trustworthy.faithfulness import (  # noqa: E402
    asserted_numbers,
    score_delivery,
    with_calculator_evidence,
)
from scripts.trustworthy.faithfulness_judge import (  # noqa: E402
    JudgeError,
    judge_user_payload,
    parse_labels,
    prompt_sha,
)
from scripts.trustworthy.report import (  # noqa: E402
    json_fingerprint,
    scorer_sources_sha,
    summarize,
    wilson_interval,
)
from scripts.trustworthy.schema import load_attempts, load_dataset, validate_dataset  # noqa: E402
from scripts.trustworthy.tsr import delivery_substance, score_attempt  # noqa: E402

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
    assert report["scorer_version"] == "trustworthy.v1.6-rules"
    coverage = report["coverage"]
    assert coverage["faithfulness_lower_bound"] is not None
    assert coverage["faithfulness_scored"] == len([row for row in report["cases"] if row["faithfulness"] is not None])
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


def test_scorer_fingerprint_covers_the_scorer_sources():
    """A version label alone must not be the fingerprint of the scorer build."""
    sources = scorer_sources_sha()
    assert len(sources) == 64
    assert sources == scorer_sources_sha()
    label_only = json_fingerprint("trustworthy.v1.3-rules", {"method": "rules"})
    assert label_only != json_fingerprint("trustworthy.v1.3-rules", {"method": "rules"}, sources)


def test_list_ordinals_are_not_asserted_numbers():
    """A numbered list must reach the judge instead of being forced unsupported."""
    assert asserted_numbers("1. 包含两站：渔梁渡船、苗圃。") == []
    assert asserted_numbers("2、状态：未提交的草稿。") == []
    assert asserted_numbers("（3）门票合计 52 元。") == ["52"]
    assert asserted_numbers("合计 187 元。") == ["187"]
    assert asserted_numbers("1. 门票 52 元。") == ["52"]

    seen: list[list[dict[str, str]]] = []
    observation = {"text": "整理稿列了渔梁渡船和苗圃两站。这是未提交的草稿，不是已经办完的业务。"}

    def complete(messages):
        payload = json.loads(messages[-1]["content"])
        seen.append(payload["claims"])
        return json.dumps(
            {"labels": [{"claim_id": row["claim_id"], "label": "supported", "span": None} for row in payload["claims"]]},
            ensure_ascii=False,
        )

    scored = score_delivery(
        {"text": "整理稿要点：\n1. 包含两站：渔梁渡船、苗圃。\n2. 状态说明：这是未提交的草稿，并非已经办完的业务。"},
        observation,
        judge="llm",
        complete=complete,
    )
    judged = [row["text"] for batch in seen for row in batch]
    assert any(text.startswith("1.") for text in judged)
    assert any(text.startswith("2.") for text in judged)
    assert [row["label"] for row in scored["claims"]] == ["supported", "supported", "supported"]


def test_invented_number_still_fails_before_the_judge():
    def complete(messages):
        raise AssertionError("a number absent from the observation must not reach the judge")

    scored = score_delivery(
        {"text": "一共 999 元。"},
        {"text": "一共 186 元。"},
        judge="llm",
        complete=complete,
    )
    assert scored["claims"][0]["label"] == "unsupported"
    assert scored["claims"][0]["by"] == "contract"


def test_markers_read_the_delivery_not_the_state():
    attempt = {
        "valid_attempt": True,
        "outcome": "completed",
        "delivery": {"text": "柜台和门口写的不一致。"},
        "end_state": {"trip_spec": {"goal": "当前值未知"}},
    }
    oracle = {"scorable_outcomes": ["completed"], "checks": [{"id": "unknown", "type": "marker_present", "needle": "未知"}]}
    assert score_attempt(attempt, oracle)["task_success"] == 0
    attempt["delivery"]["text"] = "两处记录不一致，当前值未知。"
    assert score_attempt(attempt, oracle)["task_success"] == 1


def test_substance_min_rejects_a_bare_uncertainty_answer():
    oracle = {
        "scorable_outcomes": ["completed"],
        "checks": [
            {"id": "unknown", "type": "marker_present", "needle": "未知"},
            {"id": "explain", "type": "substance_min", "chars": 12},
        ],
    }
    bare = {"valid_attempt": True, "outcome": "completed", "delivery": {"text": "未知"}, "end_state": {}}
    thin = {"valid_attempt": True, "outcome": "completed", "delivery": {"text": "末班时间未知。"}, "end_state": {}}
    full = {
        "valid_attempt": True,
        "outcome": "completed",
        "delivery": {"text": "末班时间未知：柜台告示写 21 点，门口告示写 23 点，两份记录未核对。"},
        "end_state": {},
    }
    terse = {
        "valid_attempt": True,
        "outcome": "completed",
        "delivery": {"text": "停车费未知，柜台5元门口27元。"},
        "end_state": {},
    }
    assert delivery_substance(bare) == 0
    assert score_attempt(bare, oracle)["task_success"] == 0
    assert score_attempt(thin, oracle)["task_success"] == 0
    # A short answer that still reports both records keeps its content.
    assert delivery_substance(terse) == 12
    assert score_attempt(terse, oracle)["task_success"] == 1
    assert score_attempt(full, oracle)["task_success"] == 1


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


def test_declared_missing_value_sentence_is_non_factual_by_structure():
    scored = score_delivery(
        {
            "text": "页面上只有展陈介绍，关门时间拿不到。展陈分三个展区，都在一层。",
            "uncertainty": {"kind": "missing_value", "subject": "关门时间", "records": []},
        },
        {"text": "展陈分三个展区，都在一层。"},
    )
    by_id = {row["claim_id"]: row for row in scored["claims"]}
    assert by_id["S1"]["label"] == "non-factual" and by_id["S1"]["by"] == "structure"
    assert by_id["S2"]["label"] == "supported"


def test_declared_conflict_keeps_citations_factual_and_covers_record_numbers():
    observation = {"text": "柜台告示：余票 6 张。门口公示：余票 24 张。"}
    declared = {
        "text": "柜台写余票 6 张，门口公示余票 24 张。当前确定值未知。",
        "uncertainty": {
            "kind": "conflicting_records",
            "subject": "余票数量",
            "records": ["柜台告示：余票 6 张", "门口公示：余票 24 张"],
        },
    }
    scored = score_delivery(declared, observation)
    by_id = {row["claim_id"]: row for row in scored["claims"]}
    assert by_id["S1"]["label"] == "supported"
    assert by_id["S2"]["label"] == "non-factual"
    assert scored["faithfulness"] == 1.0 and scored["factual_claims"] == 1


def test_records_declaration_allows_numbers_the_pack_omits():
    observation = {"text": "两份告示内容一致地只写了入场须知。"}
    scored = score_delivery(
        {
            "text": "柜台记余票 6 张，门口记余票 24 张。",
            "uncertainty": {
                "kind": "conflicting_records",
                "subject": "余票数量",
                "records": ["柜台记余票 6 张", "门口记余票 24 张"],
            },
        },
        observation,
        judge="rules",
    )
    assert all(row["label"] != "unsupported" or row["by"] != "contract" for row in scored["claims"])


def test_with_calculator_evidence_takes_ok_rows_and_copies_the_pack():
    attempt = {
        "end_state": {
            "execution_outcome": {
                "data": {
                    "calculations": [
                        {"id": "calc-1", "ok": True, "value": 112},
                        {"id": "calc-2", "ok": False, "value": None},
                        {"id": "calc-3", "ok": True, "value": 35},
                    ]
                }
            }
        }
    }
    pack = {"text": "日场 45 元。夜场 67 元。"}
    augmented = with_calculator_evidence(pack, attempt)
    assert "本跑计算器验算：calc-1 = 112" in augmented["text"]
    assert "calc-3 = 35" in augmented["text"]
    assert "calc-2" not in augmented["text"]
    assert pack["text"] == "日场 45 元。夜场 67 元。"


def test_with_calculator_evidence_without_rows_returns_the_same_pack():
    attempt = {"end_state": {"execution_outcome": {"data": {"calculations": []}}}}
    pack = {"text": "只有页文。"}
    assert with_calculator_evidence(pack, attempt) is pack


def test_calculator_number_reaches_the_judge_instead_of_the_contract_gate():
    attempt = {
        "end_state": {
            "execution_outcome": {
                "data": {"calculations": [{"id": "calc-1", "ok": True, "value": 112}]}
            }
        }
    }
    pack = with_calculator_evidence({"text": "日场 45 元。夜场 67 元。"}, attempt)
    seen = []

    def complete(messages):
        seen.append(messages)
        return json.dumps(
            {"labels": [{"claim_id": "S1", "label": "supported", "span": None}]},
            ensure_ascii=False,
        )

    scored = score_delivery(
        {"text": "两场合买 112 元。"}, pack, judge="llm", complete=complete
    )
    assert all(row["by"] != "contract" for row in scored["claims"])
    assert seen, "the claim must be judged, not killed by the contract number gate"
    user_payload = "".join(str(m.get("content") or "") for m in seen[0])
    assert "本跑计算器验算：calc-1 = 112" in user_payload


def test_uncalculated_number_still_dies_at_the_contract_gate():
    scored = score_delivery(
        {"text": "两场合买 112 元。"}, {"text": "日场 45 元。夜场 67 元。"}, judge="rules"
    )
    assert any(row["label"] == "unsupported" and row["by"] == "contract" for row in scored["claims"])
