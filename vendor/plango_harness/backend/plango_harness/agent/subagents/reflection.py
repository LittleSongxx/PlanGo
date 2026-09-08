from __future__ import annotations

from plango_harness.agent.contracts import ActionResult, MemoryProposal, PlanCandidate, TripSpec
from plango_harness.agent.decisions import ReflectionOutput
from plango_harness.agent.model_adapter import ModelAdapter


class ReflectionAgent:
    def __init__(self, model: ModelAdapter) -> None:
        self.model = model

    async def run(
        self,
        spec: TripSpec,
        plan: PlanCandidate | None,
        actions: list[ActionResult],
        event_id: str,
    ) -> MemoryProposal | None:
        fallback = ReflectionOutput(
            remember=bool(plan and actions),
            kind="episode",
            key="trip_outcome",
            value={
                "summary": f"完成一次 {plan.label if plan else '本地生活'} 规划",
                "plan_id": plan.plan_id if plan else None,
                "actions": [item.status.value for item in actions],
            },
            confidence=0.65,
        )
        output = await self.model.structured(
            ReflectionOutput,
            system=(
                "你是 PlanGo 的 Reflection Agent。只从已发生的任务终态中提炼可复用记忆。"
                "不能编造用户偏好；不确定时 remember=false。"
            ),
            user=f"目标：{spec.model_dump_json()}\n计划：{plan.model_dump_json() if plan else None}\n动作：{actions}",
            fallback=fallback,
        )
        if not output.remember or not output.key:
            return None
        return MemoryProposal(
            kind=output.kind,
            key=output.key,
            value=output.value,
            source_event_id=event_id,
            confidence=output.confidence,
            rationale=output.rationale,
        )
