"""Offline prior-state fixture; real Runtime/requirements/checkpoint, no merchant result."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock

from plango_harness.agent.contracts import (
    ActionProposal,
    Location,
    PlanCandidate,
    PlanStop,
    RunPhase,
    TripSpec,
    VerifierResult,
)
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.state import initial_state
from plango_harness.runtime import PlanGoRuntime
from plango_harness.settings import Settings


def test_runtime_replan_keeps_spec_through_location_pause_and_restart(tmp_path):
    async def exercise():
        config = Settings(
            _env_file=None, runtime_profile="sandbox", world_provider="sandbox",
            redis_url="local://", database_url=f"sqlite+aiosqlite:///{tmp_path}/runs.sqlite",
            checkpoint_path=tmp_path / "checkpoints.sqlite", data_dir=tmp_path,
            openai_api_key="", embedding_api_key="", amap_webservice_key="",
            sandbox_use_model=False,
        )
        spec = TripSpec(
            goal="受控已接受行程", party_size=3, budget=280, per_person_budget=100,
            visit_date=date(2026, 9, 20), time_window_start="18:30",
            location=Location(name="受控原起点", latitude=29.56, longitude=106.57),
            must_visit_place_ids=["fixture-place"], weather_sensitive=False,
        )
        plan = PlanCandidate(plan_id="fixture-plan", version=4, stops=[
            PlanStop(place_id="fixture-place", name="受控地点", category="餐厅",
                     start_minute=1110, end_minute=1170),
        ])
        runtime = PlanGoRuntime(config, embedded_worker=False)
        await runtime.start()
        try:
            rid = (await runtime.create_run("fixture", spec.goal))["run_id"]
            row = await runtime.runs.get(rid)
            # Import a declared prior planning state, not a fabricated successful execution.
            prior = initial_state(run_id=rid, user_id="fixture", input_text=spec.goal)
            prior.update(
                trip_spec=spec, selected_plan=plan, plan_version=plan.version, turn_count=1,
                phase=RunPhase.PARTIAL_FAILED, outcome="PARTIAL_FAILED",
                verifier=VerifierResult(plan_id=plan.plan_id), approval_decision="approve",
                action_proposal=ActionProposal(proposal_id="fixture-approval", run_id=rid,
                                               plan_id=plan.plan_id, plan_version=plan.version),
                execution_goal={"kind": "itinerary_preparation", "plan_id": plan.plan_id},
                execution_started=True,
            )
            await runtime.runs.save_state_and_events(prior, expected_version=row["version"], events=[])
            request = "出发地点改为受控未决地址，其他条件不变"
            model = AsyncMock(return_value=RequirementOutput(
                location_name="受控未决地址", field_evidence={"location_name": "出发地点改为受控未决地址"},
            ))
            runtime.model.structured = model
            world = runtime.world_service.provider
            world.strict_location = True
            world.geocode = AsyncMock(return_value=None)
            runtime.action_provider.execute = AsyncMock(side_effect=AssertionError("No execution in this fixture"))
            await runtime.replan(rid, request)
            paused = await runtime._run_graph(rid)
            assert paused["phase"] == "REQUIREMENTS_READY" and paused["interrupt_id"]
            assert ":location:" in paused["interrupt_id"]
            model.assert_awaited_once()
            world.geocode.assert_awaited_once_with("受控未决地址")
            runtime.action_provider.execute.assert_not_awaited()
            budget = paused["state"]["turn_budget"]
            await runtime.close()
            runtime = PlanGoRuntime(config, embedded_worker=False)
            await runtime.start()
            runtime.model.structured = AsyncMock(side_effect=AssertionError("Restart must not call a model"))
            restored = await runtime._run_graph(rid)  # No new user input or resume permission.
            checkpoint = await runtime.graph.aget_state({"configurable": {"thread_id": rid}})
            for state in (paused["state"], restored["state"], checkpoint.values):
                assert TripSpec.model_validate(state["trip_spec"]) == spec
                assert TripSpec.model_validate(state["previous_spec"]) == spec
                assert PlanCandidate.model_validate(state["previous_plan"]) == plan
                assert state["turn_id"] == 2
                for field in ("selected_plan", "verifier", "action_proposal", "approval_decision", "execution_goal"):
                    assert state[field] is None, field
                assert state["execution_started"] is False
            assert restored["run_id"] == rid and restored["interrupt_id"] == paused["interrupt_id"]
            assert restored["state"]["turn_budget"] == budget
            runtime.model.structured.assert_not_awaited()
        finally:
            await runtime.close()

    asyncio.run(exercise())
