from __future__ import annotations

from plango_harness.agent.contracts import CritiqueReport, PlanCandidate, TripSpec, VerifierResult
from plango_harness.agent.decisions import CriticOutput
from plango_harness.agent.model_adapter import ModelAdapter


class CriticAgent:
    def __init__(self, model: ModelAdapter) -> None:
        self.model = model

    async def run(
        self, spec: TripSpec, plan: PlanCandidate, verifier: VerifierResult
    ) -> CritiqueReport:
        fallback = CritiqueReport(
            verdict=(
                "pass"
                if verifier.executable
                else "repair"
                if not verifier.hard_constraints_pass
                else "ask_user"
            ),
            issues=verifier.hard_violations + verifier.blocking_evidence,
            repair_actions=[item.detail for item in verifier.hard_violations],
            rationale="确定性 Verifier 结果已作为审查依据",
        )
        if self.model.settings.agent_mode == "single":
            return fallback
        output = await self.model.structured(
            CriticOutput,
            system=(
                "你是 PlanGo 的独立 Critic Agent。检查候选计划是否满足用户硬约束，"
                "只基于给定计划和 Verifier 结果，不编造外部事实。若有问题给出可执行的修复方向。"
            ),
            user=f"TripSpec：{spec.model_dump_json(exclude={'goal'})}\n计划：{plan.model_dump_json()}\nVerifier：{verifier.model_dump_json()}",
            fallback=CriticOutput(
                verdict=fallback.verdict,
                issues=[item.detail for item in fallback.issues],
                repair_request="；".join(fallback.repair_actions),
                rationale=fallback.rationale,
            ),
        )
        if not verifier.hard_constraints_pass and output.verdict == "pass":
            # Hard failures stay repairable; incomplete evidence may ask the user.
            output = output.model_copy(update={"verdict": "repair"})
        return CritiqueReport(
            verdict=output.verdict,
            issues=verifier.hard_violations + verifier.blocking_evidence,
            repair_actions=output.issues
            or ([output.repair_request] if output.repair_request else []),
            rationale=output.rationale,
        )
