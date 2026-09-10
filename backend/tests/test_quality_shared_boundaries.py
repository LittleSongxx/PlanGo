"""Controlled contract checks; these are not merchant or model quality results."""
from datetime import datetime, timedelta, timezone

from plango.outcomes import _fresh_artifact
from plango.world import ObservedPlace
from plango_harness.agent.contracts import TripSpec
from plango_harness.agent.state import planning_reset


def test_replanning_keeps_accepted_input_but_revokes_previous_decisions():
    spec = TripSpec(goal='原计划', party_size=3, budget=280, must_visit_place_ids=['selected'])
    state = {'trip_spec': spec, 'selected_plan': {'plan_id': 'old', 'version': 2},
             'approval_decision': 'approve', 'execution_goal': {'plan_id': 'old'},
             'browser_wait': {'command_id': 'old-extract'}, 'browser_action': {'operation': 'extract'}}
    reset = planning_reset(state)
    assert reset['trip_spec'] == reset['previous_spec'] == spec
    assert reset['selected_plan'] is None and reset['approval_decision'] is None
    assert reset['execution_goal'] is None
    assert reset['browser_wait'] is None and reset['browser_action'] is None
    assert planning_reset(reset)['trip_spec'] == spec


def test_source_expiry_and_unknown_category_do_not_become_merchant_claims():
    now = datetime.now(timezone.utc)
    item = {'observed_at': (now - timedelta(seconds=30)).isoformat()}
    assert _fresh_artifact(item, now)
    for expiry in (now.isoformat(), (now - timedelta(seconds=1)).isoformat(), 'unknown', now.replace(tzinfo=None).isoformat()):
        assert not _fresh_artifact({**item, 'expires_at': expiry}, now)
    assert _fresh_artifact({**item, 'expires_at': (now + timedelta(seconds=1)).isoformat()}, now)
    place = ObservedPlace(name='艺术空间', quote='艺术空间开放时间十点至十七点')
    assert place.category == '未分类' and place.average_price is None
