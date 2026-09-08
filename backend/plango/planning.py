"""Real-source plan compilation and bounded, constraint-checked alternatives."""

from plango_harness.agent.contracts import (
    Evidence,
    PlanCandidate,
    PlanDraft,
    PlanDraftStop,
    VerifierResult,
)
from plango_harness.domain.planning import (
    PlanEngine,
    PlanEvaluation,
    ToolBudgetExceeded,
    compile_plan_draft,
    verify_plan,
)


def preserve_locks(plan: PlanCandidate, prior: PlanCandidate | None) -> PlanCandidate | None:
    """Compilation refreshes facts; the user's pinned identity/time is retained separately."""
    locked = {stop.place_id: stop for stop in prior.stops if stop.locked} if prior else {}
    if not locked.keys() <= {stop.place_id for stop in plan.stops}:
        return None
    stops = []
    for stop in plan.stops:
        original = locked.get(stop.place_id)
        stops.append(
            stop.model_copy(
                update={
                    "locked": True,
                    "start_minute": original.start_minute,
                    "end_minute": original.end_minute,
                    "requested_dwell_min": original.requested_dwell_min
                    or original.end_minute - original.start_minute,
                }
            )
            if original
            else stop
        )
    return plan.model_copy(update={"stops": stops})


class BrowserPlanEngine(PlanEngine):
    preserve_locks = staticmethod(preserve_locks)

    async def evaluate(self, spec, plan, *, evidence=None, weather=None, on_tool_call=None):
        enriched = await self.enrich(spec, plan, on_tool_call=on_tool_call)
        rows = {
            e.evidence_id: e
            for raw in [*(evidence or []), *self.last_evidence]
            for e in [Evidence.model_validate(raw)]
        }
        verifier = await verify_plan(
            spec, enriched, self.world, evidence=list(rows.values()), weather=weather
        )
        return PlanEvaluation(
            plan=enriched.model_copy(
                update={
                    "robustness": None,
                    "risk": "真实供给可能变化；未测算履约成功概率",
                    "plan_b": "变化后重新观测并核验",
                    "checks": verifier.hard_violations
                    + verifier.soft_warnings
                    + verifier.unknown_evidence,
                }
            ),
            verifier=verifier,
        )


async def variants(state, deps):
    selected = state.get("selected_plan")
    spec = state.get("trip_spec")
    if not selected or not spec or not state.get("verifier"):
        return {}
    candidates = [selected]
    seen = {tuple(s.place_id for s in selected.stops)}
    ctx = deps.tool_context(state)
    # Actual provider reads consume this budget; leave room for the next approval/execution stage.
    ctx.max_tool_calls = max(
        0, deps.max_tool_calls - max(6, 2 + sum(s.category == "餐厅" for s in selected.stops))
    )
    evidence = {
        e.evidence_id: e
        for raw in state.get("evidence", [])
        for e in [Evidence.model_validate(raw)]
    }
    styles = [
        (
            "经济方案",
            lambda p: (not p.price_known, p.average_price, p.distance_km),
            lambda p: (p.total_cost, sum(s.distance_km for s in p.stops)),
        ),
        (
            "就近方案",
            lambda p: (p.distance_km, not p.price_known, p.average_price),
            lambda p: (sum(s.distance_km for s in p.stops), p.total_cost),
        ),
    ]
    for label, priority, objective in styles:
        best = PlanEvaluation(
            plan=selected, verifier=VerifierResult.model_validate(state["verifier"])
        )
        attempted = set(seen)
        attempts = 0
        for index, original in enumerate(selected.stops):
            if original.locked:
                continue
            occupied = {s.place_id for i, s in enumerate(best.plan.stops) if i != index}
            options = sorted(
                [
                    p
                    for p in state.get("place_candidates", [])
                    if p.category == original.category and p.place_id not in occupied
                ],
                key=priority,
            )
            for place in options:
                ids = tuple(
                    place.place_id if i == index else s.place_id
                    for i, s in enumerate(best.plan.stops)
                )
                if ids in attempted:
                    continue
                # ponytail: inspect at most eight substitutions per style; broaden only if real cases need multi-stop search.
                if attempts >= 8 or ctx.tool_call_count >= ctx.max_tool_calls:
                    break
                attempted.add(ids)
                attempts += 1
                draft = PlanDraft(
                    label=label,
                    stops=[
                        PlanDraftStop(
                            place_id=pid,
                            duration_minutes=s.requested_dwell_min or s.end_minute - s.start_minute,
                        )
                        for pid, s in zip(ids, best.plan.stops, strict=True)
                    ],
                    rationale="逐项替换已观测候选，保留锁定节点与活动顺序，并重新计价校验",
                )
                candidate = compile_plan_draft(
                    spec,
                    draft,
                    state.get("place_candidates", []),
                    evidence=list(evidence.values()),
                    version=selected.version,
                )
                # The compiler may drop optional stops; an alternative must retain the user's current itinerary slots.
                if candidate is None or tuple(s.place_id for s in candidate.stops) != ids:
                    continue
                candidate = preserve_locks(candidate, selected)
                if candidate is None:
                    continue
                try:
                    evaluated = await deps.planner.evaluate(
                        spec,
                        candidate,
                        evidence=list(evidence.values()),
                        weather=state.get("weather"),
                        on_tool_call=ctx.consume,
                    )
                except ToolBudgetExceeded:
                    break
                for item in deps.planner.last_evidence:
                    evidence[item.evidence_id] = item
                if not evaluated.verifier.hard_constraints_pass:
                    continue
                # Verified execution outranks a cheap but incomplete proposal; remaining ordering uses actual cost/distance, not a made-up score.
                if (
                    not evaluated.verifier.hard_constraints_pass,
                    not evaluated.verifier.executable,
                    objective(evaluated.plan),
                ) < (
                    not best.verifier.hard_constraints_pass,
                    not best.verifier.executable,
                    objective(best.plan),
                ):
                    best = evaluated
        ids = tuple(s.place_id for s in best.plan.stops)
        if ids not in seen:
            candidates.append(best.plan)
            seen.add(ids)
    return {
        "candidate_plans": candidates,
        "evidence": list(evidence.values()),
        "tool_call_count": ctx.tool_call_count,
    }
