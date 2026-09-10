from __future__ import annotations

import hashlib
import math
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from plango_harness.agent.contracts import (
    DEFAULT_DWELL_MINUTES,
    INDOOR_TAG,
    LONG_QUEUE_MINUTES,
    NEGATED_TAG_PREFIX,
    OUTDOOR_TAG,
    ConstraintCheck,
    Evidence,
    Location,
    PlaceCandidate,
    PlanCandidate,
    PlanDraft,
    PlanStop,
    TripSpec,
    VerifierResult,
)
from plango_harness.providers.world import Supply, WorldProvider, _distance_km


class ToolBudgetExceeded(RuntimeError):
    """Raised before a provider read would exceed the run's shared budget."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"tool_budget_exhausted:{tool_name}")
        self.tool_name = tool_name


def _contains(text: str, words: Iterable[str]) -> bool:
    value = (text or "").lower()
    return any(word.lower() in value for word in words)


def parse_minute(value: str | int | None, default: int = 14 * 60) -> int:
    if isinstance(value, int):
        return max(0, min(1439, value))
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        return max(0, min(1439, hour * 60 + minute))
    except (TypeError, ValueError):
        return default


def format_minute(value: int) -> str:
    value = max(0, min(1439, int(value)))
    return f"{value // 60:02d}:{value % 60:02d}"


@dataclass(frozen=True)
class PlanEvaluation:
    plan: PlanCandidate
    verifier: VerifierResult


def goal_errors(spec: TripSpec, stops: list[Any]) -> list[str]:
    categories = [stop.category for stop in stops]
    errors = [f"required_place:{place_id}" for place_id in spec.must_visit_place_ids if place_id not in {stop.place_id for stop in stops}]
    errors += [f"activity:{category}" for category in spec.required_activities if category not in categories]
    errors.extend(f"activity_excluded:{category}" for category in spec.excluded_activities if category in categories)
    cursor = 0
    for category in spec.activity_order:
        if category in spec.optional_activities and category not in categories:
            continue
        try:
            cursor = categories.index(category, cursor) + 1
        except ValueError:
            errors.append("activity_order")
            break
    return errors


def _place_facts(place: PlaceCandidate | PlanStop, evidence: list[Evidence] | None) -> tuple[list[Evidence], set[str]]:
    """Collect the grounded, still-valid observations linked to this place and their tags.

    Interpreting page prose into facts is the reading step's job: whoever observed the
    page records `payload["tags"]`, and a `非<condition>` tag states an explicit
    violation. This function only enforces provenance — linked id, matching place,
    unexpired, sourced — so no untraceable claim can prove a constraint.
    """
    rows = [item for raw in (evidence or []) for item in [Evidence.model_validate(raw)]
            if item.evidence_id in place.evidence_ids and item.payload.get("place_id") == place.place_id
            and not item.expired and item.confidence > 0 and item.source_ref and item.observed_at is not None
            and item.source in {"browser", "user", "dataset"}]
    tags: set[str] = set()
    for item in rows:
        raw_tags = item.payload.get("tags")
        if isinstance(raw_tags, list):
            tags.update(tag for tag in raw_tags if isinstance(tag, str) and tag)
    return rows, tags


def _condition_checks(conditions: list[tuple[str, str, str | None]], place: PlaceCandidate | PlanStop,
                      tags: set[str]) -> list[ConstraintCheck]:
    checks: list[ConstraintCheck] = []
    for name, condition, opposite in dict.fromkeys(conditions):
        satisfied = condition in tags
        violated = NEGATED_TAG_PREFIX + condition in tags or (opposite is not None and opposite in tags)
        if satisfied == violated:
            # No observation either way, or contradictory ones. Report it and let the
            # user judge; an unobserved preference is not a violation.
            checks.append(ConstraintCheck(
                name=f"{name}:{place.place_id}", kind="soft", passed=None,
                detail=f"{place.name} 是否满足「{condition}」没有可核验观测，需要你确认"))
        else:
            checks.append(ConstraintCheck(
                name=f"{name}:{place.place_id}", kind="hard", passed=satisfied,
                detail=f"{place.name} 的「{condition}」条件"
                       + ("已有观测支持" if satisfied else "被观测明确排除")))
    return checks


def place_fact_checks(spec: TripSpec, place: PlaceCandidate | PlanStop, evidence: list[Evidence] | None = None) -> list[ConstraintCheck]:
    """One three-state decision per explicit condition, for pruning and final verification."""
    _, tags = _place_facts(place, evidence)
    conditions: list[tuple[str, str, str | None]] = [
        (f"fact:{condition}", condition, None)
        for condition in [*spec.hard_constraints,
                          *(c for member in spec.party for c in member.hard_constraints)]
        if condition
    ]
    # The two typed venue booleans name their tag and each other: they are mutually
    # exclusive by definition, so an observed opposite is a violation, not a gap.
    conditions += [("indoor", INDOOR_TAG, OUTDOOR_TAG)] if spec.indoor_required else []
    conditions += [("outdoor", OUTDOOR_TAG, INDOOR_TAG)] if spec.outdoor_required else []
    return _condition_checks(conditions, place, tags)


def place_fits(spec: TripSpec, place: PlaceCandidate, evidence: list[Evidence] | None = None) -> bool:
    """Prune known violations; unknown candidates remain available for evidence gathering."""
    if place.category in spec.excluded_activities:
        return False
    if spec.max_distance_km == 0 and spec.location is not None and _distance_km(spec.location, place.latitude, place.longitude) > 0:
        return False
    return not any(check.passed is False for check in place_fact_checks(spec, place, evidence))


def _draft_error(code: str, detail: str, *, place_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "detail": detail}
    if place_id:
        payload["place_id"] = place_id
    return payload


class FallbackPlanBuilder:
    """Build a reproducible plan only when Planner model output is unavailable.

    The LLM chooses which searches and repairs to request; this builder keeps
    identifiers, times and costs grounded in provider data.
    """

    def build(
        self,
        spec: TripSpec,
        places: list,
        evidence: list[Evidence] | None = None,
        *,
        labels: tuple[str, ...] = ("松弛", "探索", "心意"),
    ) -> list[PlanCandidate]:
        evidence_ids = [item.evidence_id for item in (evidence or [])]
        if not places:
            return []
        start = parse_minute(spec.time_window_start)
        party_size = spec.party_size or 1
        # Preference terms are the user's own words matched against observed place tags;
        # this builder holds no vocabulary of its own and infers no intent from phrasing.
        prefer = tuple(dict.fromkeys([*spec.soft_preferences, *spec.hard_constraints]))

        def rank(place, *, category: str | None) -> float:
            score = (place.rating if place.rating is not None else -1) - place.distance_km * 0.08
            if category is not None and place.category == category:
                score += 1.5
            text = " ".join([place.name, place.category, *place.tags]).lower()
            return score + sum(0.35 for item in prefer if item and item.lower() in text)

        def choose(category: str | None, used: set[str]):
            ordered = sorted(places, key=lambda p: rank(p, category=category), reverse=True)
            fresh = [p for p in ordered if p.place_id not in used]
            return (fresh or ordered)[0] if ordered else None

        # Categories come from the typed requirement fields, in the requested order.
        requested = [category for category in dict.fromkeys(
            [*spec.activity_order, *spec.required_activities, *spec.optional_activities])
            if category not in spec.excluded_activities]
        observed_order = [category for category in dict.fromkeys(place.category for place in places)
                          if category not in spec.excluded_activities]
        wanted = requested or observed_order
        plans: list[PlanCandidate] = []
        for index in range(len(labels)):
            label = labels[index]
            used: set[str] = set()
            selected = []
            # Each variant starts from a different requested category so the candidates
            # differ, without any recipe of hardcoded activity types.
            rotation = wanted[index % len(wanted):] + wanted[:index % len(wanted)] if wanted else []
            for category in (rotation or [None])[:3]:
                pick = choose(category, used)
                if pick is not None and pick.place_id not in used:
                    used.add(pick.place_id)
                    selected.append(pick)
            if len(selected) < 2:
                continue
            blocks: list[PlanStop] = []
            cursor = start
            for place in selected:
                # One dwell length for every category; an observed unit price is used when
                # published, and a missing price stays unknown instead of being invented.
                duration = DEFAULT_DWELL_MINUTES
                known = bool(place.price_known) and place.average_price > 0
                cost = float(place.average_price) * party_size if known else 0.0
                if cursor >= 1440:
                    break
                stop_start = min(cursor, 1439)
                stop_end = min(1440, stop_start + duration)
                if stop_end <= stop_start:
                    break
                blocks.append(
                    PlanStop(
                        place_id=place.place_id,
                        name=place.name,
                        address=getattr(place, "address", None),
                        category=place.category,
                        start_minute=stop_start,
                        end_minute=stop_end,
                        estimated_cost=round(cost, 2),
                        unit_price=place.average_price if known else None,
                        distance_km=place.distance_km,
                        tags=list(dict.fromkeys([*place.tags, *([] if known else ["price_unknown"])])),
                        evidence_ids=list(place.evidence_ids) + evidence_ids,
                    )
                )
                cursor = stop_end + 25
            total = round(sum(item.estimated_cost for item in blocks), 2)
            raw_id = f"{spec.goal}:{label}:{','.join(item.place_id for item in blocks)}"
            plan_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:14]
            plans.append(
                PlanCandidate(
                    plan_id=plan_id,
                    label=label,
                    stops=blocks,
                    total_cost=total,
                    evidence_ids=sorted(
                        set(evidence_ids + [e for b in blocks for e in b.evidence_ids])
                    ),
                    rationale=_plan_rationale(blocks, spec.party_size),
                )
            )
        return plans


# Kept as a narrow import alias for existing offline fixtures; the graph never
# uses it as a second Planner.
PlanBuilder = FallbackPlanBuilder


def _plan_rationale(stops: list[PlanStop], party_size: int | None) -> str:
    price_known = all(stop.unit_price is not None and "price_unknown" not in stop.tags for stop in stops)
    cost = f"已知估算小计 ¥{sum(stop.estimated_cost for stop in stops):g}" if price_known else "地点费用待核验"
    pending = []
    if any(stop.supply_source == "unknown" or stop.estimated_wait_min is None or "supply_unknown" in stop.tags for stop in stops):
        pending.append("营业/排队")
    if any(stop.supply_source == "unknown" or "reservation_unknown" in stop.tags for stop in stops):
        pending.append("可订情况")
    if any(stop.distance_kind != "route" or "route_unknown" in stop.tags for stop in stops):
        pending.append("路线")
    if any(stop.transport_cost is None for stop in stops):
        pending.append("交通费用")
    people = f"{party_size} 人" if party_size is not None else "人数待确认"
    return f"行程草案：{' → '.join(stop.name for stop in stops)}；{people}；{cost}。" + (f"待核验：{'、'.join(pending)}。" if pending else "")


def compile_plan_draft(
    spec: TripSpec,
    draft: PlanDraft,
    observed_items: list[PlanCandidate] | list[PlaceCandidate] | list[dict[str, Any]],
    *,
    evidence: list[Evidence] | list[dict[str, Any]] | None = None,
    version: int = 1,
    errors: list[dict[str, Any]] | None = None,
) -> PlanCandidate | None:
    """Compile an LLM draft using only facts present in observed places.

    Invalid references are dropped at the trust boundary, but every drop is
    optionally returned to the caller so a UI/audit trail never hides a bad
    model draft.
    """
    if errors is not None:
        errors.clear()
    if spec.party_size is None:
        if errors is not None:
            errors.append(_draft_error("party_size_unknown", "人数未确认，不能核算或生成执行计划"))
        return None
    observed: dict[str, PlanStop] = {}
    evidence_rows = [
        Evidence.model_validate(item) if isinstance(item, dict) else item
        for item in (evidence or [])
    ]
    evidence_by_id = {item.evidence_id: item for item in evidence_rows}
    candidate_evidence = {item.evidence_id for item in evidence_rows}
    party_size = spec.party_size
    for raw_item in observed_items:
        item: PlanCandidate | PlaceCandidate
        if isinstance(raw_item, dict):
            item = (
                PlanCandidate.model_validate(raw_item)
                if "stops" in raw_item
                else PlaceCandidate.model_validate(raw_item)
            )
        else:
            item = raw_item
        if isinstance(item, PlanCandidate):
            candidate_evidence.update(item.evidence_ids)
            for stop in item.stops:
                invalid_evidence = [
                    evidence_id
                    for evidence_id in stop.evidence_ids
                    if evidence_id not in evidence_by_id
                    or evidence_by_id[evidence_id].confidence <= 0
                    or evidence_by_id[evidence_id].expired
                ]
                if invalid_evidence:
                    if errors is not None:
                        errors.append(
                            _draft_error(
                                "unresolved_evidence",
                                "候选计划引用了当前回合不存在或已失效的证据",
                                place_id=stop.place_id,
                            )
                        )
                    stop = stop.model_copy(
                        update={"tags": list(dict.fromkeys([*stop.tags, "evidence_unknown"]))}
                    )
                observed.setdefault(stop.place_id, stop)
            continue
        candidate_evidence.update(item.evidence_ids)
        place_tags = list(item.tags)
        invalid_evidence = [
            evidence_id
            for evidence_id in item.evidence_ids
            if evidence_id not in evidence_by_id
            or evidence_by_id[evidence_id].confidence <= 0
            or evidence_by_id[evidence_id].expired
        ]
        if invalid_evidence:
            place_tags.append("evidence_unknown")
        observed.setdefault(
            item.place_id,
            PlanStop(
                place_id=item.place_id,
                name=item.name,
                address=item.address,
                category=item.category,
                start_minute=0,
                end_minute=1,
                estimated_cost=round(item.average_price * party_size, 2),
                unit_price=item.average_price if item.price_known else None,
                distance_km=item.distance_km,
                tags=list(dict.fromkeys(place_tags)),
                evidence_ids=list(item.evidence_ids),
            ),
        )
    if not observed:
        if errors is not None:
            errors.append(_draft_error("no_observed_places", "候选目录没有可引用地点"))
        return None

    cursor = parse_minute(spec.time_window_start)
    window_end = min(1440, cursor + spec.duration_minutes)
    used: set[str] = set()
    stops: list[PlanStop] = []
    compiled_cost = 0.0
    for draft_stop in draft.stops:
        source = observed.get(draft_stop.place_id)
        if source is None:
            if errors is not None:
                errors.append(
                    _draft_error(
                        "unobserved_place",
                        "地点不在 Discovery 观测目录中",
                        place_id=draft_stop.place_id,
                    )
                )
            continue
        if draft_stop.place_id in used:
            if errors is not None:
                errors.append(
                    _draft_error(
                        "duplicate_place", "PlanDraft 重复引用地点", place_id=draft_stop.place_id
                    )
                )
            continue
        projected_cost = compiled_cost + float(source.estimated_cost or 0)
        if projected_cost > spec.total_budget:
            if errors is not None:
                errors.append(
                    _draft_error(
                        "budget_exceeded",
                        "停留点加入后会超过用户预算，已在编译边界丢弃",
                        place_id=draft_stop.place_id,
                    )
                )
            continue
        start = min(cursor, 1439)
        end = min(window_end, start + draft_stop.duration_minutes)
        if end <= start:
            if errors is not None:
                errors.append(
                    _draft_error(
                        "outside_window", "停留时长无法放入时间窗口", place_id=draft_stop.place_id
                    )
                )
            continue
        if start + draft_stop.duration_minutes > window_end and errors is not None:
            errors.append(
                _draft_error(
                    "truncated_stop", "停留时长被时间窗口截断", place_id=draft_stop.place_id
                )
            )
        stops.append(
            source.model_copy(
                update={
                    "start_minute": start,
                    "end_minute": end,
                }
            )
        )
        used.add(draft_stop.place_id)
        compiled_cost += float(source.estimated_cost or 0)
        cursor = end + 25
        if cursor >= window_end:
            break
    if not stops:
        if errors is not None and not errors:
            errors.append(_draft_error("empty_compilation", "PlanDraft 没有可编译站点"))
        return None

    missing_goals = goal_errors(spec, stops)
    if missing_goals:
        if errors is not None:
            errors.extend(_draft_error(code, "计划未保留必达活动或明确顺序") for code in missing_goals)
        return None
    evidence_ids = sorted({evidence_id for stop in stops for evidence_id in stop.evidence_ids})
    label = (draft.label or "候选方案").strip()[:64] or "候选方案"
    raw_id = f"{spec.goal}:draft:{version}:{label}:{','.join(stop.place_id for stop in stops)}"
    return PlanCandidate(
        plan_id=hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:14],
        version=max(1, version),
        label=label,
        stops=stops,
        total_cost=round(sum(stop.estimated_cost for stop in stops), 2),
        party_size=spec.party_size,
        party_counts=dict(spec.party_counts),
        evidence_ids=evidence_ids,
        rationale=_plan_rationale(stops, spec.party_size),
    )


def visit_payload(payload: dict[str, Any], spec: TripSpec, observed_at: datetime | None = None) -> dict[str, Any]:
    """A newly fetched today fact is not proof of a different service date."""
    if spec.visit_date is None:
        return payload
    if payload.get("visit_date") == spec.visit_date.isoformat() and payload.get("timezone", "Asia/Shanghai") == spec.timezone:
        return payload
    weekly = payload.get("weekly_schedule")
    if isinstance(weekly, dict) and isinstance(weekly.get(str(spec.visit_date.isoweekday())), dict) and payload.get("timezone", "Asia/Shanghai") == spec.timezone:
        hours = weekly[str(spec.visit_date.isoweekday())]
        return {key: hours[key] for key in ("open_minute", "close_minute") if key in hours}
    if observed_at is not None and spec.timezone == "Asia/Shanghai":
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date() == spec.visit_date:
            return payload
    return {}


class PlanEngine:
    def __init__(self, world: WorldProvider, seed: int = 20260903) -> None:
        self.world = world
        self.seed = seed
        self.last_evidence: list[Evidence] = []
        self._read_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}

    async def enrich(
        self,
        spec: TripSpec,
        plan: PlanCandidate,
        *,
        on_tool_call: Callable[[str], None] | None = None,
        evidence: list[Evidence] | None = None,
    ) -> PlanCandidate:
        updated: list[PlanStop] = []
        evidence_ids: set[str] = set()
        self.last_evidence = []
        cursor = parse_minute(spec.time_window_start)
        window_end = min(1440, cursor + spec.duration_minutes)
        origin = spec.location
        dated_evidence = [Evidence.model_validate(item) for item in (evidence or [])]
        scope = (spec.visit_date.isoformat() if spec.visit_date else None, spec.timezone, spec.time_window_start, spec.party_size, tuple(sorted(spec.party_counts.items())), spec.travel_mode)
        party_size = spec.party_size or 1

        def call(name: str) -> None:
            if on_tool_call is not None:
                on_tool_call(name)

        async def read(name: str, key: tuple[Any, ...], fetch):
            cached = self._read_cache.get((name, *scope, *key))
            if cached and cached[0] > time.monotonic():
                return cached[1]
            call(name)
            value = await fetch()
            evidence = value[1] if isinstance(value, tuple) else value
            expires = getattr(evidence, "expires_at", None)
            ttl = min(60., (expires - datetime.now(timezone.utc)).total_seconds()) if expires and expires.tzinfo else 60.
            if value is not None and ttl > 0 and getattr(evidence, "confidence", 1) > 0:
                if len(self._read_cache) >= 512:
                    self._read_cache.pop(next(iter(self._read_cache)))
                self._read_cache[(name, *scope, *key)] = (time.monotonic() + ttl, value)
            return value

        def route_minutes(route: dict[str, Any]) -> int:
            recommended = str(route.get("recommended") or "")
            values = [
                route.get(f"{recommended}_min") if recommended else None,
                route.get("transit_min"),
                route.get("driving_min"),
                route.get("walking_min"),
            ]
            for value in values:
                try:
                    if value is not None and float(value) >= 0:
                        return min(1440, max(0, round(float(value))))
                except (TypeError, ValueError):
                    continue
            return 0

        for stop in plan.stops:
            place = await read("get_place", (stop.place_id,), lambda: self.world.get_place(stop.place_id))
            if not place:
                updated.append(
                    stop.model_copy(
                        update={"tags": list(dict.fromkeys([*stop.tags, "place_unknown"]))}
                    )
                )
                continue

            route_known = False
            route_evidence = None
            if origin is None:
                route = {}
            else:
                route, route_evidence = await read("estimate_route", (origin.latitude, origin.longitude, place.place_id, cursor),
                    lambda: self.world.estimate_route(origin, place, mode=spec.travel_mode, visit_date=spec.visit_date,
                                                     timezone_name=spec.timezone, at_minute=cursor))
                if isinstance(route_evidence, dict):
                    route_evidence = Evidence.model_validate(route_evidence)
                route_evidence = route_evidence.model_copy(update={
                    "evidence_id": route_evidence.evidence_id + ":" + hashlib.sha1(f"{origin.latitude},{origin.longitude}".encode()).hexdigest()[:8],
                    "payload": {**route_evidence.payload, "destination_place_id": place.place_id,
                                "requested_visit_date": scope[0], "requested_timezone": spec.timezone,
                                "origin": [origin.latitude, origin.longitude], "origin_name": origin.name,
                                "departure_minute": cursor, "requested_mode": spec.travel_mode},
                })
                route_known = route_evidence.confidence > 0 and not route_evidence.expired
                evidence_ids.add(route_evidence.evidence_id)
                self.last_evidence.append(route_evidence)

            dwell = stop.requested_dwell_min or max(1, stop.end_minute - stop.start_minute)
            travel = route_minutes(route)
            arrival = cursor + travel

            supply = await read("get_supply", (stop.place_id, min(1439, arrival)), lambda: self.world.get_supply(stop.place_id, min(1439, arrival)))
            if isinstance(supply, dict):
                supply = Supply(**supply)
            applicable = [visit_payload(item.payload, spec, item.observed_at) for item in dated_evidence if item.evidence_id in stop.evidence_ids and item.payload.get("place_id") == stop.place_id and not item.expired and item.confidence > 0]
            dated = [payload for payload in applicable if payload]
            source_scope = visit_payload({"open_now": supply.open_now}, spec, supply.observed_at)
            if spec.visit_date is not None and not source_scope:
                exact = next((payload for payload in reversed(dated) if payload.get("visit_date") == spec.visit_date.isoformat() and payload.get("at_minute") == min(1439, arrival)), {})
                hours = next((payload for payload in reversed(dated) if payload.get("open_minute") is not None or payload.get("close_minute") is not None), {})
                place = place.model_copy(update={"open_minute": hours.get("open_minute"), "close_minute": hours.get("close_minute")})
                scheduled_open = (hours["open_minute"] <= arrival < hours["close_minute"]) if hours.get("open_minute") is not None and hours.get("close_minute") is not None else None
                supply = replace(supply, open_now=exact.get("open_now", scheduled_open), reservable=exact.get("reservable"), seats_left=exact.get("seats_left"), estimated_wait_min=exact.get("estimated_wait_min"))
            known_wait = supply.estimated_wait_min
            wait_min = max(0, int(known_wait)) if known_wait is not None else 0
            # Missing waits stay unverified. Treating them as zero only schedules a
            # provisional clock; it must not invent a completed queue.
            visit_start = arrival + wait_min
            if stop.locked:
                visit_start = max(visit_start, stop.start_minute)
            service_supply = supply
            # A known wait-cap violation already rejects this stop; a second
            # restaurant-availability read cannot make it executable.
            wait_rejected = spec.max_queue_minutes is not None and supply.estimated_wait_min is not None and supply.estimated_wait_min > spec.max_queue_minutes
            if place.category == "餐厅" and visit_start != arrival and not wait_rejected and (spec.visit_date is None or bool(source_scope)):
                service_supply = await read("get_supply", (stop.place_id, min(1439, visit_start)), lambda: self.world.get_supply(stop.place_id, min(1439, visit_start)))
                if isinstance(service_supply, dict):
                    service_supply = Supply(**service_supply)
            remaining = min(window_end, place.close_minute if place.close_minute is not None else 1440) - visit_start
            # Draft durations are suggestions. Deduct observed travel and
            # waiting before fitting flexible dwell inside opening hours, without manufacturing a
            # one-minute activity or relaxing an explicitly locked stop.
            adjusted_dwell = min(dwell, remaining) if not stop.locked and remaining >= 15 else dwell
            timing_tags = []
            if remaining < 15 or visit_start + adjusted_dwell > window_end:
                timing_tags.append("time_infeasible")
            if stop.locked and visit_start != stop.start_minute:
                # Travel (and a known queue, if any) already misses the pin. An
                # unpublished wait is not turned into a hard miss by assuming zero.
                timing_tags.append("locked_time_conflict")
            scheduled_start = min(1439, visit_start)
            scheduled_end = min(1440, scheduled_start + adjusted_dwell)
            raw_supply_source = str(getattr(supply, "source", "unknown"))
            supply_source = cast(
                Literal["amap", "dataset", "simulated", "unknown", "browser"],
                raw_supply_source
                if raw_supply_source in {"amap", "dataset", "simulated", "browser"}
                else "unknown",
            )
            supply_observed = getattr(supply, "observed_at", None) or datetime.now(timezone.utc)
            supply_expires = getattr(supply, "expires_at", None)
            supply_evidence = Evidence(
                evidence_id=f"supply:{stop.place_id}:{min(1439, arrival)}",
                source=(
                    supply_source
                ),
                source_ref="world.get_supply+world.get_place",
                claim=(
                    f"{place.name} 供给快照：营业={supply.open_now if supply.open_now is not None else '未知'}，"
                    f"可预约={supply.reservable if supply.reservable is not None else '未知'}，排队分钟={supply.estimated_wait_min if supply.estimated_wait_min is not None else '未知'}"
                ),
                payload={
                    "place_id": stop.place_id,
                    "requested_visit_date": scope[0], "requested_timezone": spec.timezone,
                    "date_verified": spec.visit_date is None or bool(source_scope) or bool(dated),
                    "open_now": getattr(service_supply, "open_now", None),
                    "reservable": getattr(service_supply, "reservable", None),
                    "seats_left": getattr(supply, "seats_left", None),
                    "estimated_wait_min": getattr(supply, "estimated_wait_min", None),
                    "open_minute": place.open_minute,
                    "close_minute": place.close_minute,
                    "source": supply_source,
                },
                observed_at=supply_observed,
                expires_at=supply_expires,
                confidence=0.5 if supply_source == "simulated" else 0.8,
            )
            self.last_evidence.append(supply_evidence)
            evidence_ids.add(supply_evidence.evidence_id)
            fare = route.get("cost_per_person")
            transport_cost = (round(float(fare) * spec.party_size, 2)
                if isinstance(fare, (int, float)) and not isinstance(fare, bool) and math.isfinite(fare) and fare >= 0
                and spec.party_size is not None and route_known else None)
            fresh_cost = round(float(place.average_price) * party_size + (transport_cost or 0), 2)
            transport_summary = route.get("summary") or (
                f"{origin.name} → {place.name}；交通费用待核验" if origin is not None else f"起点未确认 → {place.name}；路线待核验"
            )
            active_stop_ids = [ref for ref in stop.evidence_ids if ref in place.evidence_ids or any(item.evidence_id == ref and not item.expired and item.confidence > 0 and item.payload.get("place_id") == stop.place_id for item in dated_evidence)]
            active_stop_ids += [supply_evidence.evidence_id, *([route_evidence.evidence_id] if route_evidence is not None else [])]
            evidence_ids.update(active_stop_ids)
            tags = list(
                dict.fromkeys(
                    [
                        # These describe the previous observation/arrival,
                        # not immutable venue properties. A repair can move
                        # a stop into an open slot or refresh a failed route.
                        *(tag for tag in stop.tags if tag not in {
                            "time_infeasible", "locked_time_conflict",
                            "closed", "not_reservable", "route_unknown", "place_unknown", "price_unknown", "supply_unknown", "reservation_unknown", "transport_cost_unknown",
                        }),
                        *place.tags,
                        *([] if place.price_known else ["price_unknown"]),
                        *(["transport_cost_unknown"] if transport_cost is None else []),
                        *timing_tags,
                        *(["closed"] if service_supply.open_now is False else []),
                        *(["supply_unknown"] if service_supply.open_now is None or supply.estimated_wait_min is None else []),
                        *(["not_reservable"] if service_supply.reservable is False else []),
                        *(["reservation_unknown"] if service_supply.reservable is None else []),
                        *(["route_unknown"] if not route_known else []),
                    ]
                )
            )
            updated.append(
                stop.model_copy(
                    update={
                        "start_minute": scheduled_start,
                        "end_minute": scheduled_end,
                        "estimated_cost": fresh_cost,
                        "unit_price": place.average_price if place.price_known else None,
                        "transport_cost": transport_cost,
                        "transport_summary": transport_summary,
                        "category": place.category,
                        "address": getattr(place, "address", None) or stop.address,
                        "estimated_wait_min": supply.estimated_wait_min,
                        "supply_source": supply_source,
                        "supply_observed_at": supply_observed,
                        "supply_expires_at": supply_expires,
                        "distance_km": float(route["distance_km"] if route.get("distance_km") is not None else (_distance_km(origin, place.latitude, place.longitude) if origin is not None else 0)),
                        "distance_kind": route.get("distance_kind") or ("route" if route_known else "straight_line_lower_bound"),
                        "travel_min": travel,
                        "requested_dwell_min": dwell,
                        "tags": tags,
                        "evidence_ids": list(dict.fromkeys(active_stop_ids)),
                    }
                )
            )
            cursor = scheduled_end
            origin = Location(
                name=place.name,
                latitude=place.latitude,
                longitude=place.longitude,
                city_code=spec.location.city_code if spec.location else None,
            )
        return plan.model_copy(
            update={
                "stops": updated,
                "total_cost": round(sum(stop.estimated_cost for stop in updated), 2),
                "party_size": spec.party_size,
                "party_counts": dict(spec.party_counts),
                "evidence_ids": sorted(evidence_ids),
                "rationale": _plan_rationale(updated, spec.party_size),
            }
        )

    async def evaluate(
        self,
        spec: TripSpec,
        plan: PlanCandidate,
        *,
        evidence: list[Evidence] | None = None,
        weather: dict[str, Any] | None = None,
        on_tool_call: Callable[[str], None] | None = None,
    ) -> PlanEvaluation:
        enriched = await self.enrich(spec, plan, on_tool_call=on_tool_call, evidence=evidence)
        evidence_rows = [
            Evidence.model_validate(item) if isinstance(item, dict) else item
            for item in [*(evidence or []), *self.last_evidence]
        ]
        evidence_by_id = {item.evidence_id: item for item in evidence_rows}
        verifier = await verify_plan(
            spec,
            enriched,
            self.world,
            evidence=list(evidence_by_id.values()),
            weather=weather,
        )
        robustness, risk, plan_b = await simulate_plan(spec, enriched, self.world, self.seed)
        final = enriched.model_copy(
            update={
                "robustness": robustness,
                "risk": risk,
                "plan_b": plan_b,
                "checks": verifier.hard_violations
                + verifier.soft_warnings
                + verifier.unknown_evidence,
            }
        )
        return PlanEvaluation(plan=final, verifier=verifier)


async def verify_plan(
    spec: TripSpec,
    plan: PlanCandidate,
    world: WorldProvider | None,
    *,
    evidence: list[Evidence] | None = None,
    weather: dict[str, Any] | None = None,
) -> VerifierResult:
    hard: list[ConstraintCheck] = []
    soft: list[ConstraintCheck] = []
    unknown: list[ConstraintCheck] = []
    evidence_rows = [
        Evidence.model_validate(item) if isinstance(item, dict) else item
        for item in (evidence or [])
    ]
    evidence_by_id = {item.evidence_id: item for item in evidence_rows}
    # A queue limit lives in the typed field. Reading one out of free text would only
    # recognise the phrasings it was written against; every explicit condition gets its
    # own three-state check per stop from place_fact_checks.
    strict_queue = spec.max_queue_minutes == 0
    distance_limit = spec.max_distance_km
    cannot_queue = any(not member.can_queue for member in spec.party)
    per_person_budget = plan.total_cost / (spec.party_size or 1)
    if spec.party_size is None:
        unknown.append(ConstraintCheck(name="party_size", kind="unknown", passed=None, detail="同行人数尚未确认，无法核算预算"))
    if spec.party_size is not None and plan.party_size is not None and plan.party_size != spec.party_size:
        hard.append(ConstraintCheck(name="party_pricing", kind="hard", passed=False, detail="计划计价人数与需求不同"))
    roles = {member.role for member in spec.party}
    if (plan.party_counts != spec.party_counts
            or (spec.party_size is not None and sum(spec.party_counts.values()) > spec.party_size)
            or any((count > 0) != (role in roles) for role, count in spec.party_counts.items())):
        hard.append(ConstraintCheck(name="party_composition", kind="hard", passed=False,
                                    detail="明确角色人数、总人数与计划组成不一致；需要确认"))
    if abs(plan.total_cost - sum(stop.estimated_cost for stop in plan.stops)) > .01:
        hard.append(ConstraintCheck(name="total_pricing", kind="hard", passed=False, detail="计划总价与站点合计不符"))
    for code in goal_errors(spec, plan.stops):
        hard.append(ConstraintCheck(name=code, kind="hard", passed=False, detail=f"未满足必达活动或顺序：{code}"))
    window_start = parse_minute(spec.time_window_start)
    window_end = min(1440, window_start + spec.duration_minutes)
    explicit_window = spec.time_window_start is not None
    # Every explicit condition already gets a three-state check per stop from
    # place_fact_checks; an unobserved one is reported there, not counted as a defect.
    if plan.total_cost > spec.total_budget:
        hard.append(
            ConstraintCheck(
                name="budget",
                kind="hard",
                passed=False,
                detail=f"总成本 {plan.total_cost:.0f} > 有效总预算 {spec.total_budget:.0f}",
            )
        )
    for member in spec.party:
        if member.budget_cap is not None and per_person_budget > member.budget_cap:
            hard.append(
                ConstraintCheck(
                    name=f"budget:{member.role}",
                    kind="hard",
                    passed=False,
                    detail=(
                        f"人均估算 {per_person_budget:.0f} > {member.role} 预算上限 "
                        f"{member.budget_cap:.0f}"
                    ),
                )
            )
    previous_end = None
    for stop in plan.stops:
        if "price_unknown" in stop.tags:
            check = ConstraintCheck(name=f"price:{stop.place_id}", kind="unknown" if spec.total_budget < float("inf") else "soft", passed=None, detail=f"{stop.name} 未提供可核验单价，不能把缺失价格当免费")
            (unknown if check.kind == "unknown" else soft).append(check)
        if "transport_cost_unknown" in stop.tags:
            check = ConstraintCheck(name=f"transport_cost:{stop.place_id}", kind="unknown" if spec.total_budget < float("inf") else "soft", passed=None,
                detail=f"前往{stop.name}的交通费未估；预算仅覆盖已知费用，不能保证全部支出")
            (unknown if check.kind == "unknown" else soft).append(check)
        if stop.unit_price is not None and spec.party_size is not None and abs(stop.estimated_cost - stop.unit_price * spec.party_size - (stop.transport_cost or 0)) > .01:
            hard.append(ConstraintCheck(name=f"pricing:{stop.place_id}", kind="hard", passed=False, detail="站点价格未按确认人数与本段交通估算计算"))
        if not stop.evidence_ids:
            unknown.append(
                ConstraintCheck(
                    name=f"evidence:{stop.place_id}",
                    kind="unknown",
                    passed=None,
                    detail=f"{stop.name} 没有可追溯观测证据",
                )
            )
        else:
            for evidence_id in stop.evidence_ids:
                observed = evidence_by_id.get(evidence_id)
                if observed is None:
                    unknown.append(
                        ConstraintCheck(
                            name=f"evidence_missing:{stop.place_id}:{evidence_id}",
                            kind="unknown",
                            passed=None,
                            detail=f"{stop.name} 引用了未找到的证据 {evidence_id}",
                        )
                    )
                elif observed.confidence <= 0 or observed.expired:
                    unknown.append(
                        ConstraintCheck(
                            name=f"evidence_stale:{stop.place_id}:{evidence_id}",
                            kind="unknown",
                            passed=None,
                            detail=f"{stop.name} 的证据 {evidence_id} 已失效或置信度为零",
                        )
                    )
                elif observed.payload.get("place_id") != stop.place_id and observed.payload.get("destination_place_id") != stop.place_id:
                    unknown.append(ConstraintCheck(name=f"evidence_irrelevant:{stop.place_id}:{evidence_id}", kind="unknown", passed=None, detail="证据未关联当前站点"))
        if stop.start_minute < window_start:
            if explicit_window:
                hard.append(
                    ConstraintCheck(
                        name="window_start",
                        kind="hard",
                        passed=False,
                        detail=f"{stop.name} 早于时间窗口 {format_minute(window_start)}",
                    )
                )
            else:
                unknown.append(
                    ConstraintCheck(
                        name="window_start",
                        kind="unknown",
                        passed=None,
                        detail=f"{stop.name} 的开始时刻尚未绑定用户窗口，当前时刻为待核验草案",
                    )
                )
        venue_hours = [visit_payload(evidence_by_id[ref].payload, spec, evidence_by_id[ref].observed_at) for ref in stop.evidence_ids if ref in evidence_by_id and evidence_by_id[ref].payload.get("place_id") == stop.place_id]
        if spec.visit_date is not None and not any(hours.get("open_minute") is not None or hours.get("close_minute") is not None or hours.get("visit_date") == spec.visit_date.isoformat() for hours in venue_hours):
            unknown.append(ConstraintCheck(name=f"visit_date:{stop.place_id}", kind="unknown", passed=None, detail=f"尚无适用于 {spec.visit_date.isoformat()}（{spec.timezone}）的营业或预约证据"))
        if any(
            (hours.get("open_minute") is not None and stop.start_minute < hours["open_minute"])
            or (hours.get("close_minute") is not None and stop.end_minute > hours["close_minute"])
            for hours in venue_hours
        ):
            hard.append(ConstraintCheck(name=f"hours:{stop.place_id}", kind="hard", passed=False,
                                        detail=f"{stop.name} 的完整停留区间超出已知营业时间，需要替代或重新编排"))
        if previous_end is not None and stop.start_minute < previous_end:
            hard.append(
                ConstraintCheck(
                    name="time_window",
                    kind="hard",
                    passed=False,
                    detail=f"{stop.name} 与上一站重叠",
                )
            )
        previous_end = stop.end_minute
        if stop.end_minute > window_end or "time_infeasible" in stop.tags:
            if explicit_window:
                hard.append(
                    ConstraintCheck(
                        name="duration", kind="hard", passed=False, detail=f"{stop.name} 超出时间窗口"
                    )
                )
            else:
                unknown.append(
                    ConstraintCheck(
                        name="duration",
                        kind="unknown",
                        passed=None,
                        detail=f"{stop.name} 的时间安排尚未绑定用户窗口，当前时刻为待核验草案",
                    )
                )
        if "locked_time_conflict" in stop.tags:
            detail = f"{stop.name} 无法在锁定时间前到达"
            if stop.estimated_wait_min is not None:
                detail += "并完成排队"
            hard.append(ConstraintCheck(name="locked_time", kind="hard", passed=False, detail=detail))
        if stop.requested_dwell_min and stop.end_minute - stop.start_minute < stop.requested_dwell_min:
            soft.append(ConstraintCheck(name="dwell_adjusted", kind="soft", passed=False, detail=f"{stop.name} 停留调整为 {stop.end_minute - stop.start_minute} 分钟，已为出行和排队预留时间"))
        for check in place_fact_checks(spec, stop, evidence_rows):
            # Satisfied and unobserved conditions are both recorded so the plan shows
            # what was actually verified; only an observed violation is a hard failure.
            (hard if check.passed is False else soft).append(check)
        if distance_limit is not None and stop.distance_km > distance_limit:
            distance_label = "直线距离下界" if stop.distance_kind == "straight_line_lower_bound" else "路线"
            hard.append(ConstraintCheck(name=f"distance:{stop.place_id}", kind="hard", passed=False, detail=f"{distance_label} {stop.distance_km:g} 公里超过 {distance_limit:g} 公里上限"))
        if stop.estimated_wait_min is None or "supply_unknown" in stop.tags:
            unknown.append(ConstraintCheck(name=f"supply:{stop.place_id}",kind="unknown",passed=None,detail=f"{stop.name} 的营业或排队信息尚未完整核实；当前时间安排为待核验草案"))
        if stop.estimated_wait_min is not None and ((strict_queue and stop.estimated_wait_min > 0) or (spec.max_queue_minutes is not None and stop.estimated_wait_min > spec.max_queue_minutes)):
            hard.append(
                ConstraintCheck(
                    name=f"queue:{stop.place_id}",
                    kind="hard",
                    passed=False,
                    detail=f"预计排队 {stop.estimated_wait_min} 分钟，超过用户明确排队上限",
                )
            )
        elif cannot_queue and stop.estimated_wait_min is not None and stop.estimated_wait_min > 0:
            hard.append(
                ConstraintCheck(
                    name=f"queue:{stop.place_id}",
                    kind="hard",
                    passed=False,
                    detail=f"同行人不能排队，但预计排队 {stop.estimated_wait_min} 分钟",
                )
            )
        elif stop.estimated_wait_min is not None and stop.estimated_wait_min >= LONG_QUEUE_MINUTES:
            # One threshold for reporting a long wait. A tighter limit belongs in the
            # typed max_queue_minutes above, which is checked as a hard constraint.
            soft.append(
                ConstraintCheck(
                    name=f"queue:{stop.place_id}",
                    kind="soft",
                    passed=False,
                    detail=f"预计排队 {stop.estimated_wait_min} 分钟",
                )
            )
        if weather and weather.get("rain") and "户外" in stop.tags:
            soft.append(
                ConstraintCheck(
                    name=f"weather:{stop.place_id}",
                    kind="soft",
                    passed=False,
                    detail=f"天气观测为{weather.get('text', '雨天')}，{stop.name}为户外地点",
                )
            )
        if "closed" in stop.tags:
            hard.append(
                ConstraintCheck(
                    name=f"open:{stop.place_id}",
                    kind="hard",
                    passed=False,
                    detail=f"{stop.name} 当前未营业",
                )
            )
        if any(tag in stop.tags for tag in ("route_unknown", "evidence_unknown", "place_unknown")):
            unknown.append(
                ConstraintCheck(
                    name=f"route:{stop.place_id}",
                    kind="unknown",
                    passed=None,
                    detail=f"{stop.name} 的路线或环境证据不可用",
                )
            )
    if not plan.stops:
        unknown.append(
            ConstraintCheck(name="places", kind="unknown", passed=None, detail="没有可验证的地点")
        )
    return VerifierResult(
        plan_id=plan.plan_id,
        hard_constraints_pass=not hard,
        evidence_complete=not unknown,
        hard_violations=hard,
        soft_warnings=soft,
        unknown_evidence=unknown,
        checked_at=datetime.now(timezone.utc),
    )


async def simulate_plan(
    spec: TripSpec, plan: PlanCandidate, world: WorldProvider, seed: int
) -> tuple[float, str, str]:
    del world  # The plan already contains the observed supply snapshot.
    rng = random.Random(f"{seed}:{plan.plan_id}:{spec.location.name if spec.location else ''}")
    failures: list[str] = []
    for _ in range(120):
        elapsed = 0
        failed: str | None = None
        for stop in plan.stops:
            wait = max(0, int(rng.gauss(stop.estimated_wait_min or 0, 4)))
            elapsed += (stop.end_minute - stop.start_minute) + wait
            if wait >= 50:
                failed = "排队过久"
                break
            if rng.random() < 0.025:
                failed = "临时供给变化"
                break
        if elapsed > spec.duration_minutes + 45:
            failed = "超出时间窗口"
        if failed:
            failures.append(failed)
    robustness = round(1 - len(failures) / 120, 3)
    risk = max(set(failures), key=failures.count) if failures else "无明显风险"
    plan_b = {
        "排队过久": "改为低峰时段或线上取号",
        "临时供给变化": "替换为同类候选",
        "超出时间窗口": "删除最后一站并保留核心节点",
    }.get(risk, "保留当前方案")
    return robustness, risk, plan_b
