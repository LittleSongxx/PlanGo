from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Type

from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel, Field

from plango_harness.agent.contracts import (
    ActionItem,
    ActionProposal,
    Location,
    PlanCandidate,
    TripSpec,
    may_be_reservable,
)
from plango_harness.domain.planning import PlanEngine, ToolBudgetExceeded, parse_minute
from plango_harness.memory.repository import MemoryRepository
from plango_harness.observability import agent_span
from plango_harness.persistence.actions import ActionLedger
from plango_harness.persistence.runs import RunRepository
from plango_harness.providers.actions import ActionProvider
from plango_harness.providers.world import WorldProvider, WorldProviderError


class SearchPlacesArgs(BaseModel):
    query: str = Field(default="活动", min_length=1, max_length=100)
    limit: int = Field(default=8, ge=1, le=20)


class GeocodeArgs(BaseModel):
    address: str = Field(default="望京", min_length=1, max_length=200)


class SupplyArgs(BaseModel):
    place_id: str = Field(min_length=1, max_length=128)
    at_minute: int = Field(default=14 * 60, ge=0, le=1439)


class RouteArgs(BaseModel):
    place_id: str = Field(min_length=1, max_length=128)


class PlanArg(BaseModel):
    plan: dict[str, Any]


class ActionArg(BaseModel):
    proposal: dict[str, Any]


class EmptyArgs(BaseModel):
    """Explicit empty object schema for tools without parameters."""


@dataclass
class ToolContext:
    world: WorldProvider
    planner: PlanEngine
    memory: MemoryRepository
    runs: RunRepository
    action_provider: ActionProvider
    run_id: str
    user_id: str
    ledger: ActionLedger | None = None
    trip_spec: TripSpec | None = None
    selected_plan: PlanCandidate | None = None
    approved: bool = False
    tool_call_count: int = 0
    max_tool_calls: int = 48
    budget_exhausted: bool = False

    def consume(self, tool_name: str) -> None:
        """Consume one shared provider/tool budget unit."""
        if self.tool_call_count >= self.max_tool_calls:
            self.budget_exhausted = True
            raise ToolBudgetExceeded(tool_name)
        self.tool_call_count += 1


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: Type[BaseModel]
    read_only: bool
    risk: str
    handler: Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {
            "geocode": ToolSpec(
                "geocode",
                "将用户提供的地点名称解析为规划起点",
                GeocodeArgs,
                True,
                "low",
                self._geocode,
            ),
            "search_places": ToolSpec(
                "search_places",
                "按自然语言或品类搜索地点候选，并返回来源证据",
                SearchPlacesArgs,
                True,
                "low",
                self._search_places,
            ),
            "get_supply": ToolSpec(
                "get_supply",
                "获取地点在指定时间的营业、余位和排队状态",
                SupplyArgs,
                True,
                "low",
                self._get_supply,
            ),
            "estimate_route": ToolSpec(
                "estimate_route",
                "估计出发点到候选地点的路线和时间",
                RouteArgs,
                True,
                "low",
                self._estimate_route,
            ),
            "get_weather": ToolSpec(
                "get_weather",
                "获取当前位置天气",
                EmptyArgs,
                True,
                "low",
                self._get_weather,
            ),
            "propose_actions": ToolSpec(
                "propose_actions",
                "将已验证计划转换为需要用户确认的动作提案",
                PlanArg,
                True,
                "medium",
                self._propose_actions,
            ),
            "execute_action": ToolSpec(
                "execute_action",
                "执行已批准的单个动作；默认使用 Sandbox Provider",
                ActionArg,
                False,
                "high",
                self._execute_action,
            ),
        }

    def specs(self, *, include_write: bool = False) -> list[ToolSpec]:
        return [spec for spec in self._tools.values() if include_write or spec.read_only]

    def schemas(self, *, include_write: bool = False) -> list[dict[str, Any]]:
        out = []
        for spec in self.specs(include_write=include_write):
            schema = spec.args_model.model_json_schema()
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": schema,
                    },
                    "x-plango-read-only": spec.read_only,
                    "x-plango-risk": spec.risk,
                }
            )
        return out

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        spec = self._tools.get(name)
        if spec is None:
            return {"ok": False, "error": "unknown_tool", "tool": name}
        if not spec.read_only and not ctx.approved:
            return {"ok": False, "error": "approval_required", "tool": name}
        try:
            ctx.consume(name)
            async with agent_span(
                "execute_tool", tool_name=name, run_id=ctx.run_id, risk=spec.risk
            ):
                parsed = spec.args_model.model_validate(args or {})
                result = await spec.handler(ctx, parsed)
            return {"ok": True, "tool": name, "result": result}
        except ToolBudgetExceeded:
            return {"ok": False, "error": "tool_budget_exhausted", "tool": name}
        except WorldProviderError as exc:
            return {
                "ok": False,
                "tool": name,
                "error": exc.error_kind,
                "detail": exc.detail,
            }
        except GraphBubbleUp:
            raise
        except Exception as exc:  # tool boundary: return structured observation
            return {"ok": False, "tool": name, "error": type(exc).__name__, "detail": str(exc)}

    async def _search_places(self, ctx: ToolContext, args: SearchPlacesArgs) -> dict[str, Any]:
        assert ctx.trip_spec is not None
        places, evidence = await ctx.world.search_places(
            args.query, ctx.trip_spec.location, limit=args.limit
        )
        return {
            "places": [p.model_dump(mode="json") for p in places],
            "evidence": [e.model_dump(mode="json") for e in evidence],
        }

    async def _geocode(self, ctx: ToolContext, args: GeocodeArgs) -> dict[str, Any]:
        location = await ctx.world.geocode(args.address)
        if location is None:
            raise ValueError("location_not_found")
        return Location.model_validate(location).model_dump(mode="json")

    async def _get_supply(self, ctx: ToolContext, args: SupplyArgs) -> dict[str, Any]:
        supply = await ctx.world.get_supply(args.place_id, args.at_minute)
        return supply if isinstance(supply, dict) else supply.__dict__

    async def _estimate_route(self, ctx: ToolContext, args: RouteArgs) -> dict[str, Any]:
        assert ctx.trip_spec is not None
        place = await ctx.world.get_place(args.place_id)
        if place is None:
            raise ValueError("place_not_found")
        route, evidence = await ctx.world.estimate_route(ctx.trip_spec.location, place,
            mode=ctx.trip_spec.travel_mode, visit_date=ctx.trip_spec.visit_date, timezone_name=ctx.trip_spec.timezone,
            at_minute=parse_minute(ctx.trip_spec.time_window_start) if ctx.trip_spec.time_window_start else None)
        route["evidence"] = evidence.model_dump(mode="json")
        return route

    async def _get_weather(self, ctx: ToolContext, args: BaseModel) -> dict[str, Any]:
        del args
        assert ctx.trip_spec is not None
        if ctx.trip_spec.visit_date is not None and getattr(ctx.world, "strict_location", False):
            weather, evidence = await ctx.world.get_weather(ctx.trip_spec.location, ctx.trip_spec.visit_date, ctx.trip_spec.timezone)  # type: ignore[call-arg]
        else:
            weather, evidence = await ctx.world.get_weather(ctx.trip_spec.location)
        return {"weather": weather, "evidence": evidence.model_dump(mode="json")}

    async def _propose_actions(self, ctx: ToolContext, args: PlanArg) -> dict[str, Any]:
        assert ctx.trip_spec is not None
        plan = PlanCandidate.model_validate(args.plan)
        if ctx.selected_plan is None:
            raise ValueError("selected_plan_required")
        if plan.plan_id != ctx.selected_plan.plan_id or plan.version != ctx.selected_plan.version:
            raise ValueError("stale_plan_version")
        stored_plan = await ctx.runs.get_plan(ctx.run_id, plan.version)
        if not stored_plan or stored_plan.get("plan_id") != plan.plan_id:
            raise ValueError("unknown_plan_version")
        proposal_id = f"proposal_{hashlib.sha256(f'{ctx.run_id}:{plan.plan_id}:{plan.version}'.encode()).hexdigest()[:20]}"
        actions: list[ActionItem] = []
        for index, stop in enumerate(plan.stops):
            tool_name = (
                "reserve_place"
                if may_be_reservable(stop)
                else "visit_place"
            )
            idem = hashlib.sha256(
                f"{ctx.run_id}:{plan.plan_id}:{plan.version}:{index}:{stop.place_id}".encode()
            ).hexdigest()[:32]
            actions.append(
                ActionItem(
                    action_id=f"act_{idem[:16]}",
                    tool_name=tool_name,
                    arguments={
                        "place_id": stop.place_id,
                        "name": stop.name,
                        "at_minute": stop.start_minute,
                        "party_size": ctx.trip_spec.party_size,
                        "party_counts": dict(ctx.trip_spec.party_counts),
                        "estimated_cost": stop.estimated_cost,
                        "proposal_id": proposal_id,
                    },
                    risk_level="medium" if tool_name == "reserve_place" else "low",
                    requires_approval=True,
                    idempotency_key=idem,
                )
            )
        proposal = ActionProposal(
            proposal_id=proposal_id,
            run_id=ctx.run_id,
            plan_id=plan.plan_id,
            plan_version=plan.version,
            actions=actions,
            risk_level=("high" if any(item.risk_level == "high" for item in actions) else "medium"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            rationale="仅在用户确认后执行；当前动作由 SandboxActionProvider 模拟",
        )
        return proposal.model_dump(mode="json")

    async def _execute_action(self, ctx: ToolContext, args: ActionArg) -> dict[str, Any]:
        proposal = ActionProposal.model_validate(args.proposal)
        if proposal.run_id != ctx.run_id:
            raise ValueError("run_id_mismatch")
        if ctx.selected_plan is None or (
            proposal.plan_id != ctx.selected_plan.plan_id
            or proposal.plan_version != ctx.selected_plan.version
        ):
            raise ValueError("stale_plan_version")
        stored_plan = await ctx.runs.get_plan(ctx.run_id, proposal.plan_version)
        if not stored_plan:
            raise ValueError("unknown_plan_version")
        if stored_plan.get("plan_id") != proposal.plan_id:
            raise ValueError("unknown_plan_version")
        latest_version = await ctx.runs.latest_plan_version(ctx.run_id)
        if latest_version is not None and proposal.plan_version < latest_version:
            raise ValueError("stale_plan_version")
        expires_at = proposal.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and expires_at <= datetime.now(timezone.utc):
            raise ValueError("action_proposal_expired")
        allowed_stops = {
            (stop.place_id, stop.start_minute) for stop in ctx.selected_plan.stops
        }
        results = []
        for action in proposal.actions:
            if not action.requires_approval:
                raise ValueError("action_approval_flag_required")
            if action.tool_name not in {"visit_place", "reserve_place"}:
                raise ValueError("unknown_write_action")
            place_id = str(action.arguments.get("place_id", ""))
            at_minute = int(action.arguments.get("at_minute", -1))
            if (place_id, at_minute) not in allowed_stops:
                raise ValueError("action_not_in_plan")
            if ctx.trip_spec is not None and (action.arguments.get("party_size") != ctx.trip_spec.party_size or action.arguments.get("party_counts", {}) != ctx.trip_spec.party_counts or action.arguments.get("proposal_id") != proposal.proposal_id):
                raise ValueError("action_requirement_mismatch")
            request_hash = hashlib.sha256(
                json.dumps(
                    {"tool_name": action.tool_name, "arguments": action.arguments},
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            ledger = ctx.ledger or ActionLedger(ctx.runs)
            existing = await ledger.reserve(
                action_id=action.action_id,
                run_id=ctx.run_id,
                plan_id=proposal.plan_id,
                plan_version=proposal.plan_version,
                tool_name=action.tool_name,
                idempotency_key=action.idempotency_key,
                request_hash=request_hash,
                arguments=action.arguments,
                status="RUNNING",
            )
            if not existing.get("_created"):
                # A previous worker may have committed the request but lost
                # its response. No redelivery may issue the side effect again;
                # UNKNOWN is surfaced for manual/provider resolution.
                if existing.get("status") in {"RUNNING", "UNKNOWN"} and hasattr(ctx.action_provider, "resume_action"):
                    replay = await ctx.action_provider.resume_action(existing)
                    await ledger.complete(action.action_id, replay.get("status", "UNKNOWN"), replay)
                    results.append(replay)
                    continue
                replay = existing.get("result_json") or {
                    "status": str(existing.get("status", "UNKNOWN")),
                    "replayed": True,
                    "action_id": action.action_id,
                }
                if str(replay.get("status", "")).upper() == "RUNNING":
                    replay = {
                        **replay,
                        "status": "UNKNOWN",
                        "unknown": True,
                        "resolution_required": True,
                    }
                    await ledger.complete(action.action_id, "UNKNOWN", replay)
                results.append(replay)
                continue
            result: dict[str, Any]
            if action.tool_name == "reserve_place":
                try:
                    ctx.consume("get_supply")
                    supply = await ctx.world.get_supply(
                        place_id, at_minute
                    )
                    if isinstance(supply, dict):
                        from plango_harness.providers.world import Supply

                        supply = Supply(**supply)
                    if not supply.open_now or not supply.reservable:
                        result = {
                            "ok": False,
                            "status": "FAILED",
                            "error": "reservation_unavailable",
                            "detail": (
                                "最新供给快照显示地点未营业"
                                if not supply.open_now
                                else "最新供给快照不可预约"
                            ),
                            "supply_source": supply.source,
                            "action_id": action.action_id,
                        }
                    else:
                        result = await ctx.action_provider.execute(
                            action.tool_name, action.arguments
                        )
                except ToolBudgetExceeded as exc:
                    result = {
                        "ok": False,
                        "unknown": True,
                        "status": "UNKNOWN",
                        "error": "tool_budget_exhausted",
                        "detail": str(exc),
                        "action_id": action.action_id,
                    }
                except GraphBubbleUp:
                    raise
                except Exception as exc:
                    # A lost response is not proof that the side effect did
                    # not happen. Persist UNKNOWN so redelivery cannot issue
                    # it twice.
                    result = {
                        "ok": False,
                        "unknown": True,
                        "status": "UNKNOWN",
                        "error": type(exc).__name__,
                        "detail": str(exc),
                        "retryable": isinstance(exc, (TimeoutError, ConnectionError, OSError)),
                        "action_id": action.action_id,
                    }
            else:
                try:
                    result = await ctx.action_provider.execute(
                        action.tool_name, action.arguments
                    )
                except GraphBubbleUp:
                    raise
                except Exception as exc:
                    # A lost response is not proof that the side effect did
                    # not happen. Persist UNKNOWN so redelivery cannot issue
                    # it twice.
                    result = {
                        "ok": False,
                        "unknown": True,
                        "status": "UNKNOWN",
                        "error": type(exc).__name__,
                        "detail": str(exc),
                        "retryable": isinstance(exc, (TimeoutError, ConnectionError, OSError)),
                        "action_id": action.action_id,
                    }
            provider_status = str(result.get("status", "")).upper()
            status = (
                "UNKNOWN"
                if result.get("unknown") or provider_status == "UNKNOWN"
                else (
                    "CANCELLED"
                    if provider_status == "CANCELLED"
                    else (
                        "FAILED"
                        if provider_status in {"FAILED", "PARTIAL_FAILED"}
                        or not result.get("ok")
                        else "SUCCEEDED"
                    )
                )
            )
            await ledger.complete(action.action_id, status, result)
            results.append(result)
        return {"proposal_id": proposal.proposal_id, "results": results}
