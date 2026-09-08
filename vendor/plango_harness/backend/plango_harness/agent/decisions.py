from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from .contracts import Activity, ContractModel, Location, PartyCounts, PartyMember, TripSpec


class SupervisorDecision(ContractModel):
    next_action: Literal[
        "requirements",
        "discover",
        "advocate",
        "synthesize",
        "critic",
        "verify",
        "propose_actions",
        "ask_user",
        "finish",
    ] = "requirements"
    reason: str = Field(default="", max_length=500)
    arguments: dict[str, Any] = Field(default_factory=dict)


class RequirementOutput(ContractModel):
    """A complete first-turn spec or a sparse patch for a later turn."""

    goal: str | None = None
    party: list[PartyMember] | None = None
    hard_constraints: list[str] | None = None
    soft_preferences: list[str] | None = None
    remove_hard_constraints: list[str] | None = None
    remove_soft_preferences: list[str] | None = None
    time_window_start: str | None = None
    duration_minutes: int | None = Field(default=None, ge=30, le=1440)
    budget: float | None = Field(default=None, ge=0)
    per_person_budget: float | None = Field(default=None, ge=0)
    clear_budget: bool = False
    clear_per_person_budget: bool = False
    party_size: int | None = Field(default=None, ge=1, le=12)
    party_size_unknown: bool = False
    party_counts: PartyCounts | None = Field(default=None, max_length=12)
    required_activities: list[Activity] | None = None
    optional_activities: list[Activity] | None = None
    remove_activities: list[Activity] | None = None
    activity_order: list[Activity] | None = None
    location_name: str | None = None
    clarification_needed: bool = False
    clarification_fields: list[str] = Field(default_factory=list)
    clarification_question: str = ""
    indoor_required: bool | None = None
    outdoor_required: bool | None = None
    max_queue_minutes: int | None = Field(default=None, ge=0, le=1440)
    max_distance_km: float | None = Field(default=None, ge=0, le=1000)

    def to_trip_spec(self, fallback_goal: str, base: TripSpec | None = None) -> TripSpec:
        """Apply a validated requirement patch without dropping prior constraints."""
        values: dict[str, Any] = base.model_dump(mode="python") if base else {
            "goal": fallback_goal,
            "party": [PartyMember(role="用户")],
            "party_size": 1,
            "party_counts": {"用户": 1},
            "hard_constraints": [],
            "soft_preferences": [],
            "time_window_start": "14:00",
            "duration_minutes": 360,
            "budget": 400.0,
            "location": Location(latitude=39.997, longitude=116.482),
            "weather_sensitive": True,
        }
        if self.goal:
            values["goal"] = self.goal
        previous_members = {member.role: member.model_dump() for member in base.party} if base else {}
        if self.party:
            values["party"] = [PartyMember.model_validate({**previous_members.get(member.role, {}), **member.model_dump(exclude_unset=True)}) for member in self.party]
        if self.party_size_unknown or self.party_size is not None:
            values["party_size"] = None if self.party_size_unknown else self.party_size
        counts = dict(values.get("party_counts") or {})
        if self.party_counts is not None:
            counts.update(self.party_counts)
        values["party_counts"] = counts
        members = [PartyMember.model_validate(member) if isinstance(member, dict) else member
                   for member in values["party"]]
        members = [member for member in members if counts.get(member.role) != 0]
        members += [PartyMember.model_validate(previous_members.get(role, {"role": role})) for role, count in counts.items()
                    if count > 0 and role not in {member.role for member in members}]
        values["party"] = members or [PartyMember(role="用户")]
        removed_activities = set(self.remove_activities or [])
        for field in ("required_activities", "optional_activities"):
            values[field] = list(dict.fromkeys(
                item for item in [*values.get(field, []), *(getattr(self, field) or [])]
                if item not in removed_activities
            ))
        values["required_activities"] = [item for item in values["required_activities"] if item not in (self.optional_activities or [])]
        values["excluded_activities"] = list(dict.fromkeys(
            item for item in [*values.get("excluded_activities", []), *removed_activities]
            if item not in [*(self.required_activities or []), *(self.optional_activities or [])]
        ))
        values["optional_activities"] = [item for item in values["optional_activities"] if item not in values["required_activities"]]
        if self.activity_order is not None:
            values["activity_order"] = self.activity_order
        values["activity_order"] = [item for item in values.get("activity_order", []) if item not in removed_activities]
        existing_hard = values.get("hard_constraints")
        existing_hard = existing_hard if isinstance(existing_hard, list) else []
        existing_soft = values.get("soft_preferences")
        existing_soft = existing_soft if isinstance(existing_soft, list) else []
        if self.hard_constraints is not None:
            values["hard_constraints"] = list(
                dict.fromkeys(
                    [*existing_hard, *self.hard_constraints]
                )
            )
        if self.remove_hard_constraints:
            removed = {str(item).strip() for item in self.remove_hard_constraints}
            current_hard = values.get("hard_constraints")
            current_hard = current_hard if isinstance(current_hard, list) else []
            values["hard_constraints"] = [
                item for item in current_hard if str(item).strip() not in removed
            ]
        if self.soft_preferences is not None:
            values["soft_preferences"] = list(
                dict.fromkeys(
                    [*existing_soft, *self.soft_preferences]
                )
            )
        if self.remove_soft_preferences:
            removed = {str(item).strip() for item in self.remove_soft_preferences}
            current_soft = values.get("soft_preferences")
            current_soft = current_soft if isinstance(current_soft, list) else []
            values["soft_preferences"] = [
                item for item in current_soft if str(item).strip() not in removed
            ]
        if self.time_window_start is not None:
            values["time_window_start"] = self.time_window_start
        if self.duration_minutes is not None:
            values["duration_minutes"] = self.duration_minutes
        if self.budget is not None:
            values["budget"] = self.budget
        if self.clear_budget:
            values["budget"] = None
        if self.per_person_budget is not None:
            values["per_person_budget"] = self.per_person_budget
        if self.clear_per_person_budget:
            values["per_person_budget"] = None
        for field in ("indoor_required", "outdoor_required", "max_queue_minutes", "max_distance_km"):
            value = getattr(self, field)
            if value is not None:
                values[field] = value
        for field, labels in (("indoor_required", {"室内", "必须室内", "全程室内"}), ("outdoor_required", {"户外", "必须户外", "全程户外"})):
            if getattr(self, field) is False:
                values["hard_constraints"] = [item for item in values["hard_constraints"] if item not in labels]
        if any("排队" in item for item in (self.remove_hard_constraints or [])):
            values["max_queue_minutes"] = None
        if "距离优先" in (self.remove_hard_constraints or []):
            values["max_distance_km"] = None
        time_start = str(values.get("time_window_start") or "14:00")
        if not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", time_start or ""):
            time_start = "14:00"
        values["time_window_start"] = time_start
        values["goal"] = str(values.get("goal") or fallback_goal)
        hard_values = values.get("hard_constraints")
        hard_values = hard_values if isinstance(hard_values, list) else []
        soft_values = values.get("soft_preferences")
        soft_values = soft_values if isinstance(soft_values, list) else []
        values["hard_constraints"] = [
            str(item).strip()
            for item in hard_values
            if str(item).strip() not in {":00", "00", "None"}
        ]
        values["soft_preferences"] = [
            str(item).strip() for item in soft_values if str(item).strip()
        ]
        # Keep the normalized mirror in sync; otherwise TripSpec's validator
        # would re-apply a stale base ``time_window`` over a user patch.
        values["time_window"] = {
            "start": values["time_window_start"],
            "duration_minutes": values["duration_minutes"],
        }
        return TripSpec.model_validate(values)


class DiscoveryOutput(ContractModel):
    queries: list[str] = Field(default_factory=lambda: ["活动", "餐厅"], min_length=1, max_length=6)
    rationale: str = ""


class CriticOutput(ContractModel):
    verdict: Literal["pass", "repair", "ask_user"] = "repair"
    issues: list[str] = Field(default_factory=list)
    repair_request: str = ""
    rationale: str = ""


class ReflectionOutput(ContractModel):
    remember: bool = False
    kind: Literal["fact", "episode", "rule"] = "episode"
    key: str = ""
    value: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0, le=1)
    rationale: str = ""
