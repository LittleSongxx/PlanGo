"""Discovery queries stay bound to stated activities or the user goal."""

from unittest.mock import AsyncMock

from plango_harness.agent.contracts import Location, PlaceCandidate, TripSpec
from plango_harness.agent.subagents.discovery import DiscoveryAgent


async def test_discovery_uses_goal_text_when_activities_are_empty():
    queries = []
    place = PlaceCandidate(
        place_id="observed", name="观测店", category="餐厅",
        latitude=29.56, longitude=106.57,
    )

    async def search(query, location, limit):
        queries.append(query)
        return [place], []

    spec = TripSpec(
        goal="这周六中午两个人从重庆出发，附近安排半天",
        party_size=2,
        location=Location(name="重庆", latitude=29.55, longitude=106.57),
    )
    model = AsyncMock()
    model.structured = AsyncMock(side_effect=AssertionError("empty activities must not invent a query"))
    model.tool_calls = AsyncMock(return_value=[])
    await DiscoveryAgent(model, world=object()).run(spec, search=search)
    assert queries and spec.goal.startswith(queries[0])
    assert model.structured.await_count == 0
    assert model.tool_calls.await_count == 0


async def test_discovery_prefers_stated_activities_over_goal_text():
    queries = []

    async def search(query, location, limit):
        queries.append(query)
        return [PlaceCandidate(
            place_id="exhibit", name="展馆", category="展览",
            latitude=29.56, longitude=106.57,
        )], []

    spec = TripSpec(
        goal="这周六先看展再吃饭",
        party_size=2,
        required_activities=["展览"],
        location=Location(name="重庆", latitude=29.55, longitude=106.57),
    )
    await DiscoveryAgent(AsyncMock(), world=object()).run(spec, search=search)
    assert queries == ["展览"]
