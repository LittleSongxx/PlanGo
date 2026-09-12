"""Newly confirmed venue/party constraints become user-confirmed facts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from plango_harness.agent.contracts import Location, PartyMember, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps, build_graph
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.requirements import confirmed_constraint_proposals
from plango_harness.agent.state import initial_state
from plango_harness.settings import Settings


def test_new_venue_constraint_is_proposed():
    previous = TripSpec(goal="出去吃")
    current = TripSpec(goal="出去吃", hard_constraints=["清淡"])
    rows = confirmed_constraint_proposals(
        previous, current, source_event_id="user-confirmed:run:1"
    )
    assert len(rows) == 1
    assert rows[0].kind == "fact"
    assert rows[0].key == "constraint:清淡"
    assert rows[0].confidence == 1
    assert rows[0].source_event_id == "user-confirmed:run:1"
    assert rows[0].value == {"text": "清淡", "scope": "trip"}


def test_new_party_constraint_is_proposed():
    previous = TripSpec(goal="出去吃", party=[PartyMember(role="儿童")])
    current = TripSpec(
        goal="出去吃",
        party=[PartyMember(role="儿童", hard_constraints=["不要花生"])],
    )
    rows = confirmed_constraint_proposals(
        previous, current, source_event_id="user-confirmed:run:1"
    )
    assert [row.key for row in rows] == ["constraint:儿童:不要花生"]
    assert rows[0].value == {"text": "不要花生", "scope": "party", "role": "儿童"}


def test_identifier_slug_is_not_proposed():
    previous = TripSpec(goal="出去吃")
    current = TripSpec(goal="出去吃", hard_constraints=["no_ordering"])
    assert confirmed_constraint_proposals(previous, current, source_event_id="user-confirmed:run:1") == []


def test_empty_patch_writes_nothing():
    spec = TripSpec(goal="出去吃", hard_constraints=["清淡"])
    assert confirmed_constraint_proposals(spec, spec, source_event_id="user-confirmed:run:1") == []
    assert confirmed_constraint_proposals(None, TripSpec(goal="出去吃"), source_event_id="user-confirmed:run:1") == []


async def test_requirements_commits_new_constraint():
    memory = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda user, items: [item.model_dump(mode="json") for item in items])
    )
    model = ModelAdapter(Settings(runtime_profile="sandbox", openai_api_key="", _env_file=None))
    deps = GraphDeps(
        model=model,
        tools=SimpleNamespace(schemas=lambda: [], execute=AsyncMock()),
        world=SimpleNamespace(),
        planner=None,
        memory=memory,
        runs=SimpleNamespace(save_plan=AsyncMock()),
        action_provider=None,
    )
    nodes = {}
    build_graph(deps, extension=lambda graph: nodes.update(requirements=graph.nodes["requirements"].runnable))
    previous = TripSpec(
        goal="出去吃",
        location=Location(name="起点", latitude=29.5, longitude=106.5),
        party_size=2,
    )
    state = initial_state(run_id="constraint", user_id="alice", input_text="清淡一点")
    state.update(trip_spec=previous, previous_spec=previous)
    with patch(
        "plango_harness.agent.graph.RequirementAgent.run",
        AsyncMock(return_value=RequirementOutput(hard_constraints=["清淡"])),
    ):
        result = await nodes["requirements"].ainvoke(state)
    memory.commit.assert_awaited_once()
    user_id, proposals = memory.commit.await_args.args
    assert user_id == "alice"
    assert [item.key for item in proposals] == ["constraint:清淡"]
    assert proposals[0].source_event_id.startswith("user-confirmed:")
    assert result["trip_spec"].hard_constraints == ["清淡"]
