from __future__ import annotations

import json
from typing import Any

from plango_harness.agent.contracts import (
    Evidence,
    PlaceCandidate,
    PlanDraft,
    PlanDraftStop,
    TripSpec,
)
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.domain.planning import place_fits


class PlannerAgent:
    """Propose an ordered, grounded plan draft for deterministic compilation."""

    def __init__(self, model: ModelAdapter) -> None:
        self.model = model

    async def run(
        self,
        spec: TripSpec,
        places: list[PlaceCandidate],
        advocate_reports: list[Any],
        evidence: list[Evidence] | None = None,
    ) -> PlanDraft:
        fallback = self._fallback(spec, places, advocate_reports, evidence)
        catalog = [
            {
                "place_id": place.place_id,
                "name": place.name,
                "category": place.category,
                "average_price": place.average_price,
                "price_known": place.price_known,
                "distance_km": place.distance_km,
                "tags": place.tags,
                "evidence_ids": place.evidence_ids,
            }
            for place in places
        ]
        reports = [
            report.model_dump(mode="json") if hasattr(report, "model_dump") else report
            for report in advocate_reports
        ]
        columns = list(catalog[0]) if catalog else []
        compact_catalog = json.dumps({"columns": columns, "rows": [[row[key] for key in columns] for row in catalog]}, ensure_ascii=False, separators=(",", ":"))
        return await self.model.structured(
            PlanDraft,
            system=(
                "你是 PlanGo 的 Planner Agent。根据 TripSpec、已观测候选地点和 Advocate 报告，"
                "提出一个有序的 PlanDraft。候选目录用columns定义共享列名，rows每行按列名读取。每个 place_id 必须来自已观测地点目录，不能编造地点、路线、"
                "价格或营业事实；必须包含 must_visit_place_ids、覆盖 required_activities、遵守 activity_order，预算按明确人数计算。"
                "无须额外凑站点。只能调整顺序和建议停留时长。返回结构化结果。"
                "label用简短中文标题；rationale用不超过160字向用户解释已查到的选店理由，不提TripSpec、PlanDraft、Agent、角色报告或内部ID。"
                "候选distance_km只是距搜索中心的直线参考，不能据此保证实际步行路线、时长或从用户起点可达；这些交给后续工具核验。"
            ),
            user=(
                f"TripSpec：{spec.model_dump_json(exclude={'goal'})}\n候选目录：{compact_catalog}\n"
                f"Advocate 报告：{reports}"
            ),
            fallback=fallback,
        )

    @staticmethod
    def _fallback(
        spec: TripSpec,
        places: list[PlaceCandidate],
        advocate_reports: list[Any] | None = None,
        evidence: list[Evidence] | None = None,
    ) -> PlanDraft:
        if not places:
            raise ValueError("no_observed_places")
        party_size = spec.party_size or 1
        goal = f"{' '.join(spec.hard_constraints)} {' '.join(spec.soft_preferences)}"
        wants_child = any(word in goal for word in ("孩子", "亲子", "带娃", "儿童"))
        wants_indoor = any(word in goal for word in ("室内", "避雨", "下雨"))
        report_boost: dict[str, float] = {}
        report_penalty: dict[str, float] = {}
        for report in advocate_reports or []:
            preferred = (
                report.get("preferred_place_ids", [])
                if isinstance(report, dict)
                else getattr(report, "preferred_place_ids", [])
            )
            verdict = (
                report.get("verdict", "")
                if isinstance(report, dict)
                else getattr(report, "verdict", "")
            )
            for place_id in preferred:
                if verdict == "reject":
                    report_penalty[str(place_id)] = report_penalty.get(str(place_id), 0) + 0.35
                else:
                    report_boost[str(place_id)] = report_boost.get(str(place_id), 0) + 0.35

        def score(place: PlaceCandidate) -> float:
            value = place.rating - place.distance_km * 0.08
            text = " ".join([place.name, place.category, *place.tags])
            if wants_child and any(word in text for word in ("亲子", "儿童", "动物")):
                value += 0.8
            if wants_indoor and "室内" in text:
                value += 0.5
            value += report_boost.get(place.place_id, 0.0)
            value -= report_penalty.get(place.place_id, 0.0)
            return value

        eligible = [place for place in places if place_fits(spec, place, evidence)]
        prefer_nearby = spec.max_distance_km is not None or "距离优先" in spec.hard_constraints
        def priority(place: PlaceCandidate):
            return (spec.max_distance_km is not None and place.distance_km > spec.max_distance_km,
                    place.price_known and place.average_price * party_size > spec.total_budget,
                    not place.price_known, place.distance_km if prefer_nearby else 0.0,
                    place.average_price, -score(place))
        ordered = sorted(eligible or places, key=priority)
        goals = list(dict.fromkeys([*(c for c in spec.activity_order if c in spec.required_activities), *spec.required_activities]))
        selected: list[PlaceCandidate] = [place for place in ordered if place.place_id in spec.must_visit_place_ids]
        for category in goals:
            if any(place.category == category for place in selected):
                continue
            candidates = [p for p in ordered if p.category == category and p not in selected]
            if candidates:
                selected.append(candidates[0])
        if not goals and not selected:
            suitable = [p for p in ordered if not wants_child or set(p.tags) & {"亲子", "儿童", "动物"}]
            affordable = [p for p in (suitable or ordered) if p.average_price * party_size <= spec.total_budget]
            selected = (sorted(affordable or suitable or ordered, key=priority) if prefer_nearby else sorted(affordable or suitable or ordered, key=score, reverse=True))[:1]
        # Optional additions never consume the cost/window needed by a goal.
        total = sum(p.average_price * party_size for p in selected)
        for category in spec.optional_activities:
            candidates = [p for p in ordered if p.category == category and p not in selected]
            if candidates and len(selected) < 3 and total + candidates[0].average_price * party_size <= spec.total_budget:
                selected.append(candidates[0])
                total += candidates[0].average_price * party_size
        if not selected:
            selected = [ordered[0]]
        if spec.activity_order:
            selected.sort(key=lambda p: spec.activity_order.index(p.category) if p.category in spec.activity_order else len(spec.activity_order))
        return PlanDraft(
            label="基础方案",
            stops=[
                PlanDraftStop(
                    place_id=place.place_id,
                    duration_minutes=90 if place.category == "餐厅" else 80,
                    reason="结合评分、距离和预算挑选已查到的地点",
                )
                for place in selected
            ],
            rationale="依据已查到的地点整理",
        )
