from __future__ import annotations

from plango_harness.agent.contracts import AdvocateReport, Evidence, PlaceCandidate, TripSpec
from plango_harness.agent.model_adapter import ModelAdapter


class AdvocateAgent:
    def __init__(self, model: ModelAdapter) -> None:
        self.model = model

    async def run(self, role: str, spec: TripSpec, places: list[PlaceCandidate], evidence: list[Evidence] | None = None) -> AdvocateReport:
        facts = [Evidence.model_validate(raw) for raw in (evidence or [])]
        facts = [fact for fact in facts if not fact.expired and fact.confidence > 0]
        fallback = self._fallback(role, spec, places, facts)
        return await self.model.structured(
            AdvocateReport,
            system=(
                f"你是 PlanGo 的{role} Advocate。只从{role}视角评价已观测地点。"
                "必须引用给出的 place_id 和 evidence_id，未知证据不得当作满足约束，不得编造地点事实；输出 accept/revise/reject 和简短理由。"
            ),
            user=f"TripSpec：{spec.model_dump_json(exclude={'goal'})}\n地点目录：{[p.model_dump(mode='json') for p in places]}\n有效证据：{[e.model_dump(mode='json') for e in facts]}",
            fallback=fallback,
        )

    @staticmethod
    def _fallback(role: str, spec: TripSpec, places: list[PlaceCandidate], evidence: list[Evidence] | None = None) -> AdvocateReport:
        if not places:
            return AdvocateReport(role=role, verdict="reject", score=0, concerns=["没有观测地点"])
        scored: list[tuple[float, PlaceCandidate]] = []
        for place in places:
            score = max(0.0, min(1.0, (place.rating or 0) / 5))
            estimated = place.average_price * (spec.party_size or 1)
            if role in ("预算", "健康") and place.price_known and estimated <= spec.total_budget:
                score += 0.2
            scored.append((min(1.0, score), place))
        scored.sort(key=lambda item: item[0], reverse=True)
        score, best = scored[0]
        return AdvocateReport(
            role=role,
            verdict="accept" if score >= 0.65 else "revise",
            score=round(score, 2),
            preferred_place_ids=[best.place_id],
            evidence_ids=[item.evidence_id for item in (evidence or []) if item.evidence_id in best.evidence_ids and item.payload.get("place_id") == best.place_id],
            concerns=[] if score >= 0.65 else [f"{role}视角需要进一步调整"],
            rationale=f"优先选择 {best.name}（{best.place_id}）",
        )
