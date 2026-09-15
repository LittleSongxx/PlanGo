from __future__ import annotations

from collections.abc import Awaitable, Callable

from plango_harness.agent.contracts import Evidence, Location, PlaceCandidate, TripSpec
from plango_harness.agent.decisions import DiscoveryOutput
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.providers.world import WorldProvider

_GOAL_QUERY_MAX = 80


class DiscoveryAgent:
    def __init__(
        self,
        model: ModelAdapter,
        world: WorldProvider,
        tool_schemas: list[dict] | None = None,
    ) -> None:
        self.model = model
        self.world = world
        self.tool_schemas = tool_schemas or []
        self.last_tool_calls = 0

    async def run(
        self,
        spec: TripSpec,
        search: Callable[[str, Location, int], Awaitable[tuple[list[PlaceCandidate], list[Evidence]]]]
        | None = None,
    ) -> tuple[list[PlaceCandidate], list[Evidence]]:
        self.last_tool_calls = 0
        async def model_queries() -> list[str]:
            fallback = DiscoveryOutput(queries=self._default_queries(spec))
            calls = (
                await self.model.tool_calls(
                    system=(
                        "你是 PlanGo 的 Discovery Agent。只能调用只读地点搜索工具，"
                        "每次调用使用一个简短查询词；不要调用写工具。"
                    ),
                    user=f"TripSpec：{spec.model_dump_json()}",
                    tools=[tool for tool in self.tool_schemas if tool["function"]["name"] == "search_places"],
                ) if self.tool_schemas else []
            )
            requested = [str(call["arguments"].get("query", ""))[:100] for call in calls
                         if call.get("name") == "search_places" and call.get("arguments", {}).get("query")]
            decision = DiscoveryOutput(queries=requested[:6]) if requested else await self.model.structured(
                DiscoveryOutput,
                system=(
                    "你是 PlanGo 的 Discovery Agent。根据结构化出行目标决定需要查询的地点类别。"
                    "只返回短查询词，不编造地点或事实。"
                ),
                user=f"TripSpec：{spec.model_dump_json()}",
                fallback=fallback,
            )
            return list(dict.fromkeys(decision.queries))

        # Canonical goals need no model synonyms once retrieval covers them.
        # A provider may not recognize a category word; retain one bounded
        # model-selected supplement for missing required categories.
        # Empty activities search the user goal instead of inventing an activity type.
        typed_queries = list(dict.fromkeys([*spec.required_activities, *spec.optional_activities]))
        goal_query = "" if typed_queries else self._goal_search_query(spec)
        queries = typed_queries or ([goal_query] if goal_query else await model_queries())
        primary = bool(typed_queries or goal_query)
        places: dict[str, PlaceCandidate] = {}
        evidence: list[Evidence] = []
        searched: set[str] = set()
        # A typed activity phrased so the provider retrieves nothing (verbatim user
        # words are not provider keywords). Its supplement rows are recorded under
        # the activity so stop-level activity coverage still binds to real evidence.
        starved: list[str] = []
        for attempt in range(2 if primary else 1):
            for query in queries:
                if query in searched:
                    continue
                searched.add(query)
                self.last_tool_calls += 1
                limit = 20 if query in typed_queries else 10  # Existing tool maximum.
                rows, refs = (
                    await search(query, spec.search_location or spec.location, limit)
                    if search
                    else await self.world.search_places(query, spec.search_location or spec.location, limit=limit)
                )
                place_ids = {row.place_id for row in rows}
                evidence.extend(_bind_search_query(refs, query, place_ids))
                if query in typed_queries:
                    if any(row.category == query for row in rows):
                        # A category query can also match unrelated venues' tags.
                        rows = [row for row in rows if row.category in typed_queries]
                    elif not rows:
                        starved.append(query)
                for row in rows:
                    places[row.place_id] = row
            if not attempt and primary and starved:
                # One bounded model-selected supplement per starved activity.
                supplements = [q for q in await model_queries() if q not in searched]
                for activity in list(starved):
                    for supplement in supplements:
                        if supplement in searched:
                            continue
                        searched.add(supplement)
                        self.last_tool_calls += 1
                        rows, refs = (
                            await search(supplement, spec.search_location or spec.location, 10)
                            if search
                            else await self.world.search_places(supplement, spec.search_location or spec.location, limit=10)
                        )
                        if not rows:
                            continue
                        starved.remove(activity)
                        place_ids = {row.place_id for row in rows}
                        evidence.extend(_bind_search_query(refs, activity, place_ids, actual=supplement))
                        for row in rows:
                            places[row.place_id] = row
                        break
            covered = {p.category for p in places.values()} | {
                item for item in spec.required_activities if item in searched and item not in starved
            }
            if not primary or attempt or (places and not (set(spec.required_activities) - covered)):
                break
            queries = await model_queries()
        return list(places.values()), evidence

    @staticmethod
    def _goal_search_query(spec: TripSpec) -> str:
        return " ".join(str(spec.goal or "").split())[:_GOAL_QUERY_MAX]

    @staticmethod
    def _default_queries(spec: TripSpec) -> list[str]:
        return (list(spec.required_activities) or list(filter(None, [DiscoveryAgent._goal_search_query(spec)])) or ["活动"])[:6]


def _bind_search_query(refs: list[Evidence], query: str, place_ids: set[str], actual: str | None = None) -> list[Evidence]:
    """Record which search produced each observed place, without inventing venue facts.

    ``actual`` keeps the provider keyword when rows retrieved on an activity's behalf
    used a model-shortened supplement; coverage binds to ``query``, provenance to both.
    """
    bound: list[Evidence] = []
    for ref in refs:
        payload = dict(ref.payload or {})
        if payload.get("query"):
            bound.append(ref)
            continue
        if payload.get("place_id") in place_ids:
            update = {"query": query}
            if actual and actual != query:
                update["search_keywords"] = actual
            bound.append(ref.model_copy(update={"payload": {**payload, **update}}))
        else:
            bound.append(ref)
    return bound
