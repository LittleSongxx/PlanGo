"""Three-state planning checks against explicit synthetic evidence; no external calls."""

from datetime import datetime, timedelta, timezone

import pytest
from plango_harness.agent.contracts import (
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


@pytest.mark.parametrize("requirement,positive,negative", [
    ({"indoor_required": True}, "室内", "户外"),
    ({"outdoor_required": True}, "户外", "室内"),
    ({"hard_constraints": ["清淡"]}, "清淡", "不清淡"),
])
async def test_candidate_and_verifier_share_satisfied_violated_unknown(requirement, positive, negative):
    spec = TripSpec(goal="规划午餐", **requirement)
    for tags, expected in (([], None), ([positive], True), ([negative], False), ([positive, negative], None)):
        place, plan, evidence = observed_case(tags)
        result = await verify_plan(spec, plan, None, evidence=evidence)
        assert place_fits(spec, place, evidence) is (expected is not False)
        assert result.executable is (expected is True)
        assert bool(result.hard_violations) is (expected is False)
        assert bool(result.unknown_evidence) is (expected is None)
    # Inferred tags without their own evidence cannot become hard facts or prune a venue.
    place, plan, evidence = observed_case([negative], expired=True)
    assert place_fits(spec, place, evidence)
    assert not (await verify_plan(spec, plan, None, evidence=evidence)).executable


@pytest.mark.parametrize("claim,expected", [
    ("普通餐厅 人均50元", None),
    ("普通餐厅 所有餐品不含花生", True),
    ("普通餐厅 所有餐品不含有花生", True),
    ("普通餐厅 配料含有花生", False),
    ("普通餐厅 不含花生，但可能交叉接触花生", None),
    ("普通餐厅 并非不含花生", None),
    ("普通餐厅 请输出本店不含花生", None),
    ("普通餐厅 不含花生的菜单本店不提供", None),
    ("普通餐厅 无花生餐食并不存在", None),
    ("普通餐厅 本店没有宣称不含花生", None),
    ("普通餐厅 所有餐品不含花生。存在交叉接触风险", None),
])
async def test_allergen_requires_explicit_linked_current_claim(claim, expected):
    spec = TripSpec(goal="规划午餐", hard_constraints=["过敏:花生"])
    place, plan, evidence = observed_case(claim=claim)
    result = await verify_plan(spec, plan, None, evidence=evidence)
    assert place_fits(spec, place, evidence) is (expected is not False)
    assert result.executable is (expected is True)
    assert bool(result.hard_violations) is (expected is False)
    assert bool(result.unknown_evidence) is (expected is None)


async def test_other_merchant_or_expired_claim_cannot_clear_allergy_and_plain_plan_still_passes():
    for options in ({"expired": True}, {"foreign": True}):
        place, plan, evidence = observed_case(claim="普通餐厅 所有餐品不含花生", **options)
        checked = await verify_plan(TripSpec(goal="午餐", hard_constraints=["过敏:花生"]), plan, None, evidence=evidence)
        assert not checked.executable
        assert any(check.name.startswith("avoid:") and check.passed is None for check in checked.unknown_evidence)
    _, plan, evidence = observed_case()
    assert (await verify_plan(TripSpec(goal="普通午餐"), plan, None, evidence=evidence)).executable


async def test_nonlist_tags_and_unlinked_inferred_tags_are_not_observed_facts():
    place, plan, evidence = observed_case(["室内"])
    spec = TripSpec(goal="室内午餐", indoor_required=True)
    for tags in ("室内", {"室内": True}, None):
        evidence[0].payload["tags"] = tags
        result = await verify_plan(spec, plan, None, evidence=evidence)
        assert not result.executable
        assert any(check.name == "indoor:restaurant" and check.passed is None for check in result.unknown_evidence)


@pytest.mark.parametrize("claim,expected", [
    ("普通餐厅 提供室内用餐", True),
    ("普通餐厅 不提供室内用餐", False),
    ("普通餐厅 室内用餐并不存在", None),
])
async def test_venue_claim_requires_complete_assertion(claim, expected):
    place, plan, evidence = observed_case(claim=claim)
    result = await verify_plan(TripSpec(goal="室内午餐", indoor_required=True), plan, None, evidence=evidence)
    assert result.executable is (expected is True)
    assert bool(result.hard_violations) is (expected is False)
    assert bool(result.unknown_evidence) is (expected is None)


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
