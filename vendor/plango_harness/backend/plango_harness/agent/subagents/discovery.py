from __future__ import annotations

from collections.abc import Awaitable, Callable

from plango_harness.agent.contracts import Evidence, Location, PlaceCandidate, TripSpec
from plango_harness.agent.decisions import DiscoveryOutput
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.providers.world import WorldProvider


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
        typed_queries = list(dict.fromkeys([*spec.required_activities, *spec.optional_activities]))
        queries = typed_queries or await model_queries()
        places: dict[str, PlaceCandidate] = {}
        evidence: list[Evidence] = []
        searched: set[str] = set()
        for attempt in range(2 if typed_queries else 1):
            for query in queries:
                if query in searched:
                    continue
                searched.add(query)
                self.last_tool_calls += 1
                limit = 20 if query in typed_queries else 10  # Existing tool maximum.
                rows, refs = (
                    await search(query, spec.location, limit)
                    if search
                    else await self.world.search_places(query, spec.location, limit=limit)
                )
                evidence.extend(refs)
                if query in typed_queries and any(row.category == query for row in rows):
                    # A category query can also match unrelated venues' tags.
                    rows = [row for row in rows if row.category in typed_queries]
                for row in rows:
                    places[row.place_id] = row
            if not typed_queries or attempt or (places and not (set(spec.required_activities) - {p.category for p in places.values()})):
                break
            queries = await model_queries()
        return list(places.values()), evidence

    @staticmethod
    def _default_queries(spec: TripSpec) -> list[str]:
        text = f"{spec.goal} {' '.join(spec.hard_constraints)}"
        queries: list[str] = list(spec.required_activities) or ["活动"]
        if any(word in text for word in ("孩子", "亲子", "娃")):
            queries.insert(0, "亲子")
        if any(word in text for word in ("展", "博物馆", "文化")):
            queries.insert(0, "展览")
        if any(word in text for word in ("室内", "下雨")):
            queries.insert(0, "室内")
        return queries[:6]
