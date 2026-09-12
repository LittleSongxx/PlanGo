"""Faithfulness against this-run observations only."""

from __future__ import annotations

import re
from typing import Any, Callable

from .schema import world_pack

SENTENCE = re.compile(r"(?<=[。！？!?;；\n])\s*")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# "1. ", "2、", "(3)", "（4）" and friends enumerate a list; they are not
# asserted quantities, so they must not be read as invented numbers.
ORDINAL = re.compile(r"^\s*(?:[-*•·]\s*)?(?:[（(]\s*\d{1,3}\s*[)）]|\d{1,3}\s*[.、)）．])\s*")
UNCERTAINTY = re.compile(r"未知|无法确定|资料未写明|当前值未知|没有写明|未公布")
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


def _contract_label(claim: str, observation: str, *, conflicts: bool) -> dict[str, Any] | None:
    if UNCERTAINTY.search(claim):
        return {"text": claim, "label": "non-factual", "span": None, "by": "contract"}
    for number in asserted_numbers(claim):
        if conflicts:
            conflict = _conflict_span(claim, number, observation)
            if conflict:
                return {"text": claim, "label": "contradicted", "span": conflict, "by": "contract"}
        if number not in observation:
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
    pending: list[dict[str, str]] = []
    claims: list[dict[str, Any]] = []
    for index, claim in enumerate(split_claims(text), start=1):
        claim_id = f"S{index}"
        labeled = _contract_label(claim, observation, conflicts=(judge == "rules"))
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
