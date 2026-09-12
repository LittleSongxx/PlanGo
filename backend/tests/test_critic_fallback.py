"""Critic is a Verifier-derived station; perspective must not spend a model call."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from plango_harness.agent.contracts import ConstraintCheck, PlanCandidate, PlanStop, TripSpec, VerifierResult
from plango_harness.agent.subagents.critic import CriticAgent


def _plan() -> PlanCandidate:
    return PlanCandidate(
        plan_id="p",
        stops=[
            PlanStop(
                place_id="a",
                name="店",
                category="餐厅",
                start_minute=12 * 60,
                end_minute=13 * 60,
            )
        ],
    )


async def test_multi_perspective_does_not_call_critic_model():
    model = SimpleNamespace(settings=SimpleNamespace(agent_mode="multi"), structured=AsyncMock())
    report = await CriticAgent(model).run(
        TripSpec(goal="出去吃"),
        _plan(),
        VerifierResult(
            plan_id="p",
            hard_constraints_pass=False,
            hard_violations=[ConstraintCheck(name="budget", kind="hard", passed=False, detail="超预算")],
        ),
    )
    model.structured.assert_not_called()
    assert report.verdict == "repair"
    assert report.rationale == "确定性 Verifier 结果已作为审查依据"


async def test_verdict_follows_executable_and_hard_constraints():
    model = SimpleNamespace(settings=SimpleNamespace(agent_mode="multi"), structured=AsyncMock())
    critic = CriticAgent(model)
    passing = await critic.run(TripSpec(goal="出去吃"), _plan(), VerifierResult(plan_id="p"))
    assert passing.verdict == "pass"
    blocked = await critic.run(
        TripSpec(goal="出去吃"),
        _plan(),
        VerifierResult(
            plan_id="p",
            unknown_evidence=[ConstraintCheck(name="evidence:missing", kind="unknown", passed=None)],
        ),
    )
    assert blocked.verdict == "ask_user"
    model.structured.assert_not_called()
