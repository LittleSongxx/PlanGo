from __future__ import annotations

from plango_harness.agent.contracts import CritiqueReport, PlanCandidate, TripSpec, VerifierResult
from plango_harness.agent.model_adapter import ModelAdapter


class CriticAgent:
    def __init__(self, model: ModelAdapter) -> None:
        self.model = model

    async def run(
        self, spec: TripSpec, plan: PlanCandidate, verifier: VerifierResult
    ) -> CritiqueReport:
        # Repair is a deterministic station. Perspective must not spend a model call here.
        return CritiqueReport(
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
