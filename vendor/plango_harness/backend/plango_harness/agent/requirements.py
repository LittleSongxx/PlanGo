"""Normalize requirement changes before deciding which observations to invalidate."""

from __future__ import annotations

from typing import Any

from .contracts import Evidence, MemoryProposal, TripSpec
from .decisions import _venue_hard_constraint


def requirement_delta(previous: TripSpec | None, current: TripSpec, *, explicit_unknown: set[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    before = previous.model_dump(mode="json") if previous else {}
    after = current.model_dump(mode="json")
    unknown = explicit_unknown or set()
    # The merged contract is authoritative. goal is conversation history and
    # time_window is a mirror, neither should force a second provider search.
    changed = {name for name, value in after.items() if name not in {"goal", "time_window"} and before.get(name) != value}
    patch = [{"field": name, "operation": "unknown" if name in unknown else "clear" if after[name] is None else "set", "value": after[name], "source": "requirement_agent"} for name in after if name in changed or name in unknown]
    if previous is None:
        return patch, dict(discovery=True, weather=True, supply=True, routes=True)
    location = bool(changed & {"location", "search_location"})
    temporal = bool(changed & {"visit_date", "timezone", "time_window_start", "duration_minutes"})
    party = bool(changed & {"party", "party_size", "party_counts"})
    new_activities = set(current.required_activities + current.optional_activities) - set(previous.required_activities + previous.optional_activities)
    stricter_place = (current.indoor_required and not previous.indoor_required) or (current.outdoor_required and not previous.outdoor_required)
    stricter_place |= current.max_distance_km is not None and (previous.max_distance_km is None or current.max_distance_km < previous.max_distance_km)
    discovery = location or bool(new_activities) or bool(stricter_place) or bool(changed & {"must_visit_place_ids", "max_distance_km", "search_radius_km"})
    transport = "travel_mode" in changed
    return patch, {"discovery": discovery, "weather": location or temporal, "supply": discovery or temporal or party or transport or "selected_offer" in changed, "routes": location or temporal or party or transport or "max_distance_km" in changed}


def confirmed_constraint_proposals(
    previous: TripSpec | None,
    current: TripSpec,
    *,
    source_event_id: str,
) -> list[MemoryProposal]:
    """Persist newly added venue/party hard constraints the user just confirmed.

    Only strings that pass the same venue filter as RequirementOutput.to_trip_spec.
    Identifier slugs, merchant hours, and queue facts are not written here.
    """
    previous_trip = set(previous.hard_constraints) if previous else set()
    previous_party: dict[str, set[str]] = {}
    if previous:
        for member in previous.party:
            previous_party.setdefault(member.role, set()).update(member.hard_constraints)
    proposals: list[MemoryProposal] = []
    seen: set[str] = set()
    for text in current.hard_constraints:
        if text in previous_trip or not _venue_hard_constraint(text):
            continue
        key = f"constraint:{text}"
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            MemoryProposal(
                kind="fact",
                key=key,
                value={"text": text, "scope": "trip"},
                source_event_id=source_event_id,
                confidence=1,
            )
        )
    for member in current.party:
        known = previous_party.get(member.role, set())
        for text in member.hard_constraints:
            if text in known or not _venue_hard_constraint(text):
                continue
            key = f"constraint:{member.role}:{text}"
            if key in seen:
                continue
            seen.add(key)
            proposals.append(
                MemoryProposal(
                    kind="fact",
                    key=key,
                    value={"text": text, "scope": "party", "role": member.role},
                    source_event_id=source_event_id,
                    confidence=1,
                )
            )
    return proposals


def retain_evidence(rows: list[Any], refresh: dict[str, bool]) -> list[Evidence]:
    kept = []
    for raw in rows:
        item = Evidence.model_validate(raw)
        if item.source == "user" and item.payload.get("kind") == "merchant_identity_confirmation":
            kept.append(item)  # Retain the historical user declaration; it does not renew either merchant source.
            continue
        if refresh["discovery"]:
            continue
        fields = set(item.payload)
        source = f"{item.evidence_id} {item.source_ref}".lower()
        supply = bool(fields & {"open_now", "reservable", "seats_left", "estimated_wait_min"}) or "supply" in source
        weather = bool(fields & {"rain_probability", "temperature", "weather"}) or "weather" in source
        route = "destination_place_id" in fields or "route" in source
        if not item.expired and not (refresh["supply"] and supply or refresh["weather"] and weather or refresh["routes"] and route):
            kept.append(item)
    return kept
