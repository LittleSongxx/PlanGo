from __future__ import annotations

from planora.agent.contracts import AdvocateReport, PlaceCandidate, TripSpec
from planora.agent.model_adapter import ModelAdapter


class AdvocateAgent:
    def __init__(self, model: ModelAdapter) -> None:
        self.model = model

    async def run(self, role: str, spec: TripSpec, places: list[PlaceCandidate]) -> AdvocateReport:
        fallback = self._fallback(role, spec, places)
        return await self.model.structured(
            AdvocateReport,
            system=(
                f"你是 Planora 的{role} Advocate。只从{role}视角评价已观测地点。"
                "必须引用给出的 place_id，不得编造地点事实；输出 accept/revise/reject 和简短理由。"
            ),
            user=f"TripSpec：{spec.model_dump_json()}\n地点目录：{[p.model_dump(mode='json') for p in places]}",
            fallback=fallback,
        )

    @staticmethod
    def _fallback(role: str, spec: TripSpec, places: list[PlaceCandidate]) -> AdvocateReport:
        if not places:
            return AdvocateReport(role=role, verdict="reject", score=0, concerns=["没有观测地点"])
        scored: list[tuple[float, PlaceCandidate]] = []
        for place in places:
            score = max(0.0, min(1.0, place.rating / 5))
            estimated = place.average_price * (spec.party_size or 1)
            if role in ("预算", "健康") and estimated <= spec.total_budget:
                score += 0.2
            if role == "家庭" and any(
                "亲子" in tag or "儿童" in tag for tag in place.tags
            ):
                score += 0.2
            scored.append((min(1.0, score), place))
        scored.sort(key=lambda item: item[0], reverse=True)
        score, best = scored[0]
        return AdvocateReport(
            role=role,
            verdict="accept" if score >= 0.65 else "revise",
            score=round(score, 2),
            preferred_place_ids=[best.place_id],
            concerns=[] if score >= 0.65 else [f"{role}视角需要进一步调整"],
            rationale=f"优先选择 {best.name}（{best.place_id}）",
        )
