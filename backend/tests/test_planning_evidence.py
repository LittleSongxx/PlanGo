"""Three-state planning checks against explicit synthetic evidence; no external calls."""

from datetime import datetime, timedelta, timezone

import pytest
from plango_harness.agent.contracts import (
    NEGATED_TAG_PREFIX,
    Evidence,
    PlaceCandidate,
    PlanCandidate,
    PlanDraft,
    PlanDraftStop,
    PlanStop,
    TripSpec,
)
from plango_harness.domain.planning import compile_plan_draft, place_fits, verify_plan


def observed_case(tags=(), claim="普通餐厅 人均50元", *, expired=False, foreign=False):
    now = datetime.now(timezone.utc)
    place = PlaceCandidate(
        place_id="restaurant", name="普通餐厅", category="餐厅", latitude=29.56, longitude=106.57,
        tags=list(tags), source="browser", evidence_ids=["evidence"],
    )
    plan = PlanCandidate(plan_id="plan", party_size=1, total_cost=50, stops=[PlanStop(
        place_id=place.place_id, name=place.name, category=place.category, tags=list(tags),
        start_minute=840, end_minute=900, estimated_cost=50, estimated_wait_min=0, evidence_ids=place.evidence_ids,
    )])
    evidence = [Evidence(
        evidence_id="evidence", source="browser", source_ref="https://fixture.invalid/restaurant",
        claim=claim, payload={"place_id": "other" if foreign else place.place_id, "tags": list(tags)},
        observed_at=now, expires_at=now + timedelta(minutes=-1 if expired else 10),
    )]
    return place, plan, evidence


def condition_check(result, name):
    return next((check for check in [*result.hard_violations, *result.soft_warnings] if check.name == name), None)


@pytest.mark.parametrize("requirement,check_name,condition", [
    ({"indoor_required": True}, "indoor:restaurant", "室内"),
    ({"outdoor_required": True}, "outdoor:restaurant", "户外"),
    ({"hard_constraints": ["清淡"]}, "fact:清淡:restaurant", "清淡"),
    ({"hard_constraints": ["过敏:花生"]}, "fact:过敏:花生:restaurant", "过敏:花生"),
    # An unfamiliar condition is checked the same way; the verifier holds no vocabulary.
    ({"hard_constraints": ["可携带滑板"]}, "fact:可携带滑板:restaurant", "可携带滑板"),
])
async def test_condition_is_satisfied_violated_or_unobserved_from_linked_tags(requirement, check_name, condition):
    spec = TripSpec(goal="规划午餐", **requirement)
    negated = NEGATED_TAG_PREFIX + condition
    for tags, expected in (([], None), ([condition], True), ([negated], False), ([condition, negated], None)):
        place, plan, evidence = observed_case(tags)
        result = await verify_plan(spec, plan, None, evidence=evidence)
        check = condition_check(result, check_name)
        assert check is not None and check.passed is expected
        assert place_fits(spec, place, evidence) is (expected is not False)
        # Only an observed violation blocks. An unobserved condition is reported and
        # constrains that conclusion alone, so the itinerary stays deliverable.
        assert result.executable is (expected is not False)
        assert bool(result.hard_violations) is (expected is False)
        assert (check.kind == "soft") is (expected is None)


@pytest.mark.parametrize("options", [{"expired": True}, {"foreign": True}])
async def test_expired_or_other_merchant_tags_cannot_prove_a_condition(options):
    spec = TripSpec(goal="午餐", hard_constraints=["过敏:花生"])
    place, plan, evidence = observed_case(["过敏:花生"], **options)
    result = await verify_plan(spec, plan, None, evidence=evidence)
    check = condition_check(result, "fact:过敏:花生:restaurant")
    assert check is not None and check.passed is None, "An unusable observation cannot clear an allergy"
    assert place_fits(spec, place, evidence), "Unproven is not violated; the venue stays available"
    _, plan, evidence = observed_case()
    assert (await verify_plan(TripSpec(goal="普通午餐"), plan, None, evidence=evidence)).executable
    assert TripSpec(goal="普通午餐").budget is None


async def test_unbound_visit_window_keeps_duration_as_unknown_draft():
    """An invented 14:00 window must not hard-fail a draft the user never timed."""
    _, plan, evidence = observed_case()
    plan = plan.model_copy(update={"stops": [plan.stops[0].model_copy(update={
        "end_minute": 1300, "tags": [*plan.stops[0].tags, "time_infeasible"]})]})
    result = await verify_plan(TripSpec(goal="午餐"), plan, None, evidence=evidence)
    assert not any(check.name in {"duration", "window_start"} for check in result.hard_violations)
    assert any(check.name == "duration" and check.kind == "unknown" for check in result.unknown_evidence)


async def test_unlinked_place_tags_are_not_observed_facts():
    """Candidate tags can be provider heuristics; only linked evidence proves a condition."""
    place, plan, evidence = observed_case(["室内"])
    spec = TripSpec(goal="室内午餐", indoor_required=True)
    evidence[0].payload["tags"] = []
    result = await verify_plan(spec, plan, None, evidence=evidence)
    check = condition_check(result, "indoor:restaurant")
    assert check is not None and check.passed is None
    assert "室内" in place.tags and "室内" in plan.stops[0].tags


@pytest.mark.parametrize("tags", ["室内", {"室内": True}, None, ["", 1]])
async def test_non_list_or_non_string_tags_are_ignored(tags):
    place, plan, evidence = observed_case(["室内"])
    spec = TripSpec(goal="室内午餐", indoor_required=True)
    evidence[0].payload["tags"] = tags
    result = await verify_plan(spec, plan, None, evidence=evidence)
    check = condition_check(result, "indoor:restaurant")
    assert check is not None and check.passed is None


@pytest.mark.parametrize("price,known,cost_text", [(50, True, "已知估算小计 ¥200"), (0, True, "已知估算小计 ¥0"), (0, False, "地点费用待核验")])
def test_compiled_summary_uses_observed_stops_and_preserves_untrusted_draft(price, known, cost_text):
    place, _, evidence = observed_case()
    place = place.model_copy(update={"average_price": price, "price_known": known})
    unsupported = "无需长时间排队，满足用户偏好室内避雨"
    draft = PlanDraft(stops=[PlanDraftStop(place_id=place.place_id, reason=unsupported), PlanDraftStop(place_id="invented")], rationale=unsupported)
    original = draft.model_dump()
    plan = compile_plan_draft(TripSpec(goal="四人午餐", party_size=4), draft, [place], evidence=evidence)
    assert plan is not None
    assert plan.rationale == f"行程草案：普通餐厅；4 人；{cost_text}。待核验：营业/排队、可订情况、路线、交通费用。"
    assert draft.model_dump() == original, "Original model output remains available for the synthesis audit artifact"
