"""Faithfulness against this-run observations only."""

from __future__ import annotations

import re
from typing import Any, Callable

from .schema import world_pack

SENTENCE = re.compile(r"(?<=[。！？!?;；\n])\s*")
# A clause split for atomic claims; the sentence split above stays the unit a
# structure exemption is recognised on, so a gap statement keeps the context
# clause it was written with.
CLAUSE = re.compile(r"(?<=[，])\s*")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# "1. ", "2、", "(3)", "（4）" and friends enumerate a list; they are not
# asserted quantities, so they must not be read as invented numbers.
ORDINAL = re.compile(r"^\s*(?:[-*•·]\s*)?(?:[（(]\s*\d{1,3}\s*[)）]|\d{1,3}\s*[.、)）．])\s*")
# Non-factual, group 1: the uncertainty family. "无法得出/推算/判断" is the same
# move as "无法确定" — the answer says it cannot conclude, not that something is so.
UNCERTAINTY = re.compile(
    "未知|无法确定|无法确认|资料未写明|当前值未知|没有写明|未公布|无法得出|无法推算|无法判断"
)
# Non-factual, group 2: an answer that names what this run's observation does not
# contain. It counts next to a word for the observation, because a bare 未包含 can
# describe a record rather than the run's own page.
GAP = re.compile(r"未包含|未提供|未标注|未给出|未列出|未显示|未记录|未提及")
OBSERVATION_WORD = re.compile(r"页面|资料|记录|告示|公示|快照|观测|文本|菜单|说明")
SLOT_LEFT = re.compile(r"([\u4e00-\u9fff]{2,8})\s*$")
SLOT_RIGHT = re.compile(r"^\s*([\u4e00-\u9fff]{1,4}|元|分钟|人|点)")
PUNCT = set("。！？!?；;，,、：: \t\n")
MIN_PIECE = 3
MIN_QUOTE = 6
MIN_SUBSTANCE = 3
MAX_EXTRA = 6
COVER_RATIO = 0.75
SHORT_CLAIM = 12
JUDGES = ("rules", "llm")


def observation_text(pack: dict[str, Any]) -> str:
    if pack.get("text"):
        return str(pack["text"])
    return world_pack(pack).get("text", "")


def with_calculator_evidence(pack: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
    """Add this run's verified arithmetic to the observation the judge may use.

    A page that prints only the parts leaves the total nowhere to quote, so a
    correct calculator answer used to die at the contract's number gate: the
    figure was "not in the observation". The ok-rows of
    execution_outcome.data.calculations are this run's own verified facts and
    belong in the observation the same way page text does. The pack is copied,
    never mutated; attempts without ok rows get an identical text back.
    """
    outcome = ((attempt.get("end_state") or {}).get("execution_outcome") or {})
    rows = (outcome.get("data") or {}).get("calculations") or []
    lines = [
        f"本跑计算器验算：{row.get('id')} = {row.get('value')}"
        for row in rows
        if isinstance(row, dict) and row.get("ok") and row.get("value") is not None
    ]
    if not lines:
        return pack
    augmented = dict(pack)
    base = observation_text(pack)
    augmented["text"] = (base + "\n" if base else "") + "\n".join(lines)
    return augmented


def with_user_turns(pack: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """Add the task's own user turns to the observation the judge may use.

    An answer that restates the user's figures ("3名大人和1名小孩…226元") is
    not inventing them: the question itself supplied 3 and 1, and the contract
    number gate killed such sentences anyway because the question never entered
    the observation. The turns join the observation the same way the calculator
    rows do — labelled as the user's own words — so both the gate and the judge
    can see them. The pack is copied; tasks without turns get it back as-is.
    """
    turns = task.get("user_turns") or []
    texts = [str(t.get("text") or "") if isinstance(t, dict) else str(t or "") for t in turns]
    texts = [t for t in texts if t.strip()]
    if not texts:
        return pack
    augmented = dict(pack)
    base = observation_text(pack)
    lines = ["用户全部输入（用户自述数字视为给定）：\n" + "\n".join(texts)]
    augmented["text"] = (base + "\n" if base else "") + "\n".join(lines)
    return augmented


def with_contract_values(pack: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """Add the task's seeded card to the observation the judge may use.

    A persist read-back recites the card the run was seeded with — preset
    fields the user never typed this run and no page states. Those values are
    the contract the task itself handed the product, not the product's own
    output, so they join the observation like the user's turns do. Only the
    task-defined initial spec is read; anything the product wrote this run
    stays out, so a wrong write cannot vouch for itself.
    """
    initial = task.get("initial_trip_spec")
    if not isinstance(initial, dict) or not initial:
        return pack
    rows = [f"{key}={value}" for key, value in sorted(initial.items()) if value is not None]
    if not rows:
        return pack
    augmented = dict(pack)
    base = observation_text(pack)
    lines = ["任务初始卡片（任务给定合同，视为给定）：\n" + "\n".join(rows)]
    augmented["text"] = (base + "\n" if base else "") + "\n".join(lines)
    return augmented


def split_claims(text: str) -> list[str]:
    parts = [part.strip() for part in SENTENCE.split(text or "") if part and part.strip()]
    return parts or ([text.strip()] if (text or "").strip() else [])


def _slots(text: str, number: str) -> list[tuple[str, str]]:
    found = []
    for match in re.finditer(re.escape(number), text):
        left = SLOT_LEFT.search(text[: match.start()])
        right = SLOT_RIGHT.search(text[match.end() :])
        if left and right:
            found.append((left.group(1), right.group(1)))
    return found


def _conflict_span(claim: str, number: str, observation: str) -> str | None:
    slots = _slots(claim, number)
    if not slots:
        return None
    for other in NUMBER.finditer(observation):
        token = other.group(0)
        if token == number:
            continue
        left_ctx = observation[max(0, other.start() - 12) : other.start()]
        right_ctx = observation[other.end() : other.end() + 6]
        for left, right in slots:
            if left in left_ctx and right in right_ctx:
                start = max(0, other.start() - 8)
                return observation[start : other.end() + 8]
    return None


def _cover(claim: str, observation: str) -> tuple[list[str], int, int]:
    taken = [False] * len(claim)
    spans: list[str] = []
    while True:
        best = ""
        best_at = -1
        limit = min(len(claim), 80)
        for length in range(limit, MIN_PIECE - 1, -1):
            for start in range(0, len(claim) - length + 1):
                if any(taken[start : start + length]):
                    continue
                piece = claim[start : start + length]
                if piece.strip() and piece in observation:
                    best = piece
                    best_at = start
                    break
            if best:
                break
        if not best:
            break
        spans.append(best)
        for index in range(best_at, best_at + len(best)):
            taken[index] = True
    content = covered = 0
    for index, char in enumerate(claim):
        if char in PUNCT:
            continue
        content += 1
        if taken[index]:
            covered += 1
    return spans, covered, content


def _substance(text: str) -> int:
    return sum((char not in PUNCT) and (not char.isdigit()) and (char != ".") for char in text)


def _enough_overlap(spans: list[str], covered: int, content: int) -> bool:
    if content <= 0 or covered <= 0:
        return False
    longest = max(spans, key=len) if spans else ""
    uncovered = content - covered
    ratio = covered / content
    if len(longest) >= MIN_QUOTE and _substance(longest) >= MIN_SUBSTANCE and uncovered <= MAX_EXTRA:
        return True
    return content <= SHORT_CLAIM and ratio >= COVER_RATIO and len(longest) >= content * COVER_RATIO


def _empty_score() -> dict[str, Any]:
    return {
        "applicable": False,
        "faithfulness": None,
        "claims": [],
        "factual_claims": 0,
        "supported": 0,
        "judge": "rules",
    }


def asserted_numbers(claim: str) -> list[str]:
    """Numbers the claim actually asserts, ignoring a leading list ordinal."""
    return NUMBER.findall(ORDINAL.sub("", claim, count=1))


def _contract_label(
    claim: str,
    observation: str,
    *,
    conflicts: bool,
    declared_missing: bool = False,
    subject: str = "",
    records: str = "",
    declaration_context: bool = False,
) -> dict[str, Any] | None:
    if UNCERTAINTY.search(claim) or (GAP.search(claim) and OBSERVATION_WORD.search(claim)):
        return {"text": claim, "label": "non-factual", "span": None, "by": "contract"}
    # A declared missing value is structure, not wording: the page holds no
    # value to cite, so a numberless sentence naming the declared subject can
    # only be the gap statement itself. Conflict answers keep their citations
    # in the denominator; the records the declaration quotes are a legal
    # number surface for them.
    if declared_missing and subject and subject in claim and not asserted_numbers(claim):
        return {"text": claim, "label": "non-factual", "span": None, "by": "structure"}
    # A clause of a declaration sentence that carries no number is the
    # statement's own context ("两份记录不一致", "页面上只有展陈介绍"), not an
    # independent factual claim; its sentence-mates with numbers are still
    # judged on their own.
    if declaration_context and not asserted_numbers(claim):
        return {"text": claim, "label": "non-factual", "span": None, "by": "structure"}
    for number in asserted_numbers(claim):
        if conflicts:
            conflict = _conflict_span(claim, number, observation)
            if conflict:
                return {"text": claim, "label": "contradicted", "span": conflict, "by": "contract"}
        if number not in observation and number not in records:
            return {"text": claim, "label": "unsupported", "span": None, "by": "contract"}
    return None


def _rules_overlap(claim: str, observation: str) -> dict[str, Any]:
    spans, covered, content = _cover(claim, observation)
    span = max(spans, key=len) if spans else None
    if _enough_overlap(spans, covered, content):
        return {"text": claim, "label": "supported", "span": span, "by": "rules"}
    return {"text": claim, "label": "unsupported", "span": None, "by": "rules"}


def _finalize(claims: list[dict[str, Any]], judge: str) -> dict[str, Any]:
    factual = [row for row in claims if row["label"] != "non-factual"]
    supported = sum(row["label"] == "supported" for row in factual)
    return {
        "applicable": bool(factual),
        "faithfulness": (supported / len(factual)) if factual else None,
        "claims": claims,
        "factual_claims": len(factual),
        "supported": supported,
        "judge": judge,
    }


def score_delivery(
    delivery: dict[str, Any] | None,
    observation_pack: dict[str, Any],
    *,
    judge: str = "rules",
    complete: Callable[[list[dict[str, str]]], str] | None = None,
) -> dict[str, Any]:
    if judge not in JUDGES:
        raise ValueError("unknown faithfulness judge")
    text = ""
    if isinstance(delivery, dict):
        text = str(delivery.get("text") or "")
    if not text.strip():
        return _empty_score() | {"judge": judge}
    observation = observation_text(observation_pack)
    uncertainty = delivery.get("uncertainty") if isinstance(delivery, dict) else None
    uncertainty = uncertainty if isinstance(uncertainty, dict) else {}
    declared_missing = uncertainty.get("kind") == "missing_value"
    subject = str(uncertainty.get("subject") or "")
    records = "\n".join(str(row) for row in uncertainty.get("records") or [])
    pending: list[dict[str, str]] = []
    claims: list[dict[str, Any]] = []
    index = 0
    for sentence in split_claims(text):
        declared_sentence = bool(uncertainty.get("kind")) and bool(subject) and subject in sentence
        parts = [part.strip() for part in CLAUSE.split(sentence) if part and part.strip()]
        for claim in parts or [sentence]:
            index += 1
            claim_id = f"S{index}"
            labeled = _contract_label(
                claim,
                observation,
                conflicts=(judge == "rules"),
                declared_missing=declared_missing,
                subject=subject,
                records=records,
                declaration_context=declared_sentence,
            )
            if labeled is not None:
                labeled["claim_id"] = claim_id
                claims.append(labeled)
                continue
            if judge == "rules":
                labeled = _rules_overlap(claim, observation)
                labeled["claim_id"] = claim_id
                claims.append(labeled)
                continue
            pending.append({"claim_id": claim_id, "text": claim})
            claims.append({"claim_id": claim_id, "text": claim, "label": None, "span": None, "by": "llm"})
    if pending:
        from .faithfulness_judge import judge_claims

        judged = judge_claims(observation, pending, complete=complete)
        by_id = {row["claim_id"]: row for row in claims}
        for item in pending:
            verdict = judged[item["claim_id"]]
            by_id[item["claim_id"]].update(
                {"label": verdict["label"], "span": verdict.get("span"), "by": "llm"}
            )
    return _finalize(claims, judge)
