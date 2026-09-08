from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class RunPhase(StrEnum):
    CREATED = "CREATED"
    REQUIREMENTS_READY = "REQUIREMENTS_READY"
    RESEARCHING = "RESEARCHING"
    PLAN_DRAFTED = "PLAN_DRAFTED"
    REVIEWING = "REVIEWING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    REPLANNING = "REPLANNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_FAILED = "PARTIAL_FAILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INFEASIBLE = "INFEASIBLE"


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class PartyMember(ContractModel):
    role: str = "同行人"
    age: int | None = Field(default=None, ge=0, le=130)
    hard_constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    budget_cap: float | None = Field(default=None, ge=0)
    can_queue: bool = True


# Absent means unknown; zero explicitly records that a role is not attending.
PartyCounts = dict[str, Annotated[int, Field(ge=0, le=12, strict=True)]]


class Location(ContractModel):
    name: str = "望京"
    latitude: float = Field(39.997, ge=-90, le=90)
    longitude: float = Field(116.482, ge=-180, le=180)
    city_code: str | None = None


Activity = Literal["展览", "餐厅", "咖啡", "公园", "citywalk", "电影", "亲子", "动物园"]


class TripSpec(ContractModel):
    goal: str = Field(min_length=1, max_length=4000)
    party: list[PartyMember] = Field(
        default_factory=lambda: [PartyMember(role="用户")], min_length=1, max_length=12
    )
    hard_constraints: list[str] = Field(default_factory=list, max_length=32)
    soft_preferences: list[str] = Field(default_factory=list, max_length=32)
    visit_date: date | None = None
    timezone: str = "Asia/Shanghai"
    time_window_start: str | None = None
    duration_minutes: int = Field(default=360, ge=30, le=1440)
    time_window: dict[str, Any] | None = None
    budget: float | None = Field(default=400, ge=0, le=1_000_000)
    per_person_budget: float | None = Field(default=None, ge=0, le=1_000_000)
    party_size: int | None = Field(default=1, ge=1, le=12)
    party_counts: PartyCounts = Field(default_factory=dict, max_length=12)
    required_activities: list[Activity] = Field(default_factory=list, max_length=8)
    optional_activities: list[Activity] = Field(default_factory=list, max_length=8)
    excluded_activities: list[Activity] = Field(default_factory=list, max_length=8)
    activity_order: list[Activity] = Field(default_factory=list, max_length=8)
    location: Location = Field(default_factory=lambda: Location(latitude=39.997, longitude=116.482))
    search_location: Location | None = None
    must_visit_place_ids: list[str] = Field(default_factory=list, max_length=8)
    weather_sensitive: bool = True
    indoor_required: bool = False
    outdoor_required: bool = False
    max_queue_minutes: int | None = Field(default=None, ge=0, le=1440)
    max_distance_km: float | None = Field(default=None, ge=0, le=1000)
    travel_mode: Literal["driving", "walking", "transit"] = "driving"

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value

    @model_validator(mode="before")
    @classmethod
    def legacy_party_count(cls, value: Any) -> Any:
        # Old programmatic/checkpoint contracts represented one person per row.
        # Requirement v2 always supplies an explicit count or None.
        if isinstance(value, dict) and "party_size" not in value and value.get("party"):
            return {**value, "party_size": len(value["party"])}
        return value

    @property
    def total_budget(self) -> float:
        caps = [self.budget] if self.budget is not None else []
        if self.per_person_budget is not None and self.party_size is not None:
            caps.append(self.per_person_budget * self.party_size)
        return min(caps, default=float("inf"))

    @model_validator(mode="after")
    def normalize_time_window(self):
        if self.time_window:
            start = self.time_window.get("start", self.time_window_start)
            self.time_window_start = str(start) if start is not None else None
            self.duration_minutes = int(
                self.time_window.get("duration_minutes", self.duration_minutes)
            )
            if not 30 <= self.duration_minutes <= 1440:
                raise ValueError("duration_minutes must be between 30 and 1440")
        else:
            self.time_window = {
                "start": self.time_window_start,
                "duration_minutes": self.duration_minutes,
            }
        return self


class Evidence(ContractModel):
    evidence_id: str
    source: Literal["amap", "dataset", "simulated", "user", "browser", "unknown"] = "simulated"
    source_ref: str = ""
    claim: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)

    @property
    def expired(self) -> bool:
        from datetime import datetime, timezone

        expires_at = self.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return bool(expires_at and expires_at <= datetime.now(timezone.utc))


class PlaceCandidate(ContractModel):
    place_id: str
    name: str
    address: str | None = Field(default=None, max_length=1000)
    category: str
    latitude: float
    longitude: float
    rating: float = Field(default=4.0, ge=0, le=5)
    average_price: float = Field(default=0, ge=0)
    price_known: bool = True
    open_minute: int | None = Field(default=None, ge=0, le=1440)
    close_minute: int | None = Field(default=None, ge=0, le=1440)
    distance_km: float = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    source: Literal["amap", "dataset", "simulated", "browser"] = "simulated"
    evidence_ids: list[str] = Field(default_factory=list)


class PlanStop(ContractModel):
    place_id: str
    name: str
    address: str | None = Field(default=None, max_length=1000)
    category: str
    start_minute: int = Field(ge=0, le=1439)
    end_minute: int = Field(ge=1, le=1440)
    estimated_cost: float = Field(default=0, ge=0)
    unit_price: float | None = Field(default=None, ge=0)
    estimated_wait_min: int | None = Field(default=0, ge=0, le=1440)
    supply_source: Literal["amap", "dataset", "simulated", "unknown", "browser"] = "unknown"
    supply_observed_at: datetime | None = None
    supply_expires_at: datetime | None = None
    distance_km: float = Field(default=0, ge=0)
    distance_kind: Literal["route", "straight_line_lower_bound"] | None = None
    travel_min: int = Field(default=0, ge=0, le=1440)
    requested_dwell_min: int | None = Field(default=None, ge=1, le=1440)
    tags: list[str] = Field(default_factory=list)
    locked: bool = False
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("end_minute")
    @classmethod
    def end_after_start(cls, value: int, info) -> int:
        start = info.data.get("start_minute")
        if start is not None and value <= start:
            raise ValueError("end_minute must be greater than start_minute")
        return value


class ConstraintCheck(ContractModel):
    name: str
    kind: Literal["hard", "soft", "unknown"]
    passed: bool | None
    detail: str = ""


class PlanCandidate(ContractModel):
    plan_id: str
    version: int = Field(default=1, ge=1)
    label: str = "候选方案"
    stops: list[PlanStop] = Field(default_factory=list, min_length=1)
    total_cost: float = Field(default=0, ge=0)
    party_size: int | None = Field(default=None, ge=1, le=12)
    party_counts: PartyCounts = Field(default_factory=dict, max_length=12)
    robustness: float | None = Field(default=None, ge=0, le=1)
    risk: str = ""
    plan_b: str = ""
    checks: list[ConstraintCheck] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class PlanDraftStop(ContractModel):
    """LLM proposal that can only reference an observed place."""

    place_id: str = Field(min_length=1, max_length=128)
    duration_minutes: int = Field(default=80, ge=15, le=480)
    reason: str = ""


class PlanDraft(ContractModel):
    """Bounded plan proposal compiled and re-verified by deterministic code."""

    label: str = Field(default="候选方案", max_length=64)
    stops: list[PlanDraftStop] = Field(default_factory=list, min_length=1, max_length=8)
    rationale: str = ""


class VerifierResult(ContractModel):
    plan_id: str
    hard_constraints_pass: bool = True
    evidence_complete: bool = True
    executable: bool | None = None
    hard_violations: list[ConstraintCheck] = Field(default_factory=list)
    soft_warnings: list[ConstraintCheck] = Field(default_factory=list)
    unknown_evidence: list[ConstraintCheck] = Field(default_factory=list)
    checked_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_pass_field(cls, value: Any) -> Any:
        # Checkpoints created before the contract split can still be read.
        if isinstance(value, dict) and "hard_pass" in value:
            value = dict(value)
            legacy = bool(value["hard_pass"])
            value.setdefault("hard_constraints_pass", legacy)
            value.setdefault("evidence_complete", legacy)
            value.setdefault("executable", legacy)
        return value

    @model_validator(mode="after")
    def derive_executable(self) -> "VerifierResult":
        expected = self.hard_constraints_pass and self.evidence_complete
        if self.executable is None or self.executable != expected:
            self.executable = expected
        return self

    @property
    def hard_pass(self) -> bool:
        """Compatibility accessor; execution uses ``executable`` explicitly."""
        return bool(self.executable)


class AgentArtifact(ContractModel):
    """Common envelope exchanged between Supervisor and specialists."""

    artifact_id: str
    task_id: str
    agent_id: str
    status: Literal["pending", "succeeded", "failed", "needs_input"] = "succeeded"
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class AdvocateReport(ContractModel):
    run_id: str | None = None
    turn_id: int = Field(default=1, ge=1)
    role: str
    verdict: Literal["accept", "revise", "reject"]
    score: float = Field(default=0.5, ge=0, le=1)
    must_have: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    preferred_place_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class CritiqueReport(ContractModel):
    verdict: Literal["pass", "repair", "ask_user"]
    issues: list[ConstraintCheck] = Field(default_factory=list)
    repair_actions: list[str] = Field(default_factory=list)
    rationale: str = ""


class ActionItem(ContractModel):
    action_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_approval: bool = True
    idempotency_key: str


class ActionProposal(ContractModel):
    proposal_id: str
    run_id: str
    plan_id: str
    plan_version: int = Field(ge=1)
    actions: list[ActionItem] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    expires_at: datetime | None = None
    rationale: str = ""


class ActionResult(ContractModel):
    action_id: str
    status: ActionStatus
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    retryable: bool = False
    resolution_required: bool = False


class MemoryProposal(ContractModel):
    kind: Literal["fact", "episode", "rule"]
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    source_event_id: str
    confidence: float = Field(default=0.5, ge=0, le=1)
    valid_until: datetime | None = None
    operation: Literal["upsert", "forget"] = "upsert"
    rationale: str = ""


class RunEvent(ContractModel):
    run_id: str
    seq: int = Field(ge=1)
    event_type: str
    phase: RunPhase
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
