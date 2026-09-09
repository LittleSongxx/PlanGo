"""Explicit desktop edits use the same sparse requirement contract as chat."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequirementFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    location_name: str | None = Field(default=None, min_length=1, max_length=200)
    search_location_name: str | None = Field(default=None, min_length=1, max_length=200)
    max_distance_km: float | None = Field(default=None, ge=0.1, le=50, allow_inf_nan=False, strict=True)
    visit_date: date | None = None
    time_window_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    party_size: int | None = Field(default=None, ge=1, le=12, strict=True)
    budget: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False, strict=True)
    per_person_budget: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False, strict=True)
    travel_mode: Literal["driving", "walking", "transit"] | None = None

    @model_validator(mode="after")
    def required_values(self):
        for name in {"location_name", "search_location_name", "party_size", "travel_mode"} & self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name}_cannot_be_cleared")
        return self


class StopLock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1, max_length=128)
    plan_version: int = Field(ge=1, strict=True)
    place_id: str = Field(min_length=1, max_length=128)
    locked: bool = Field(strict=True)


class RequirementEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    fields: RequirementFields = Field(default_factory=RequirementFields)
    stop_lock: StopLock | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.fields.model_fields_set and self.stop_lock is None:
            raise ValueError("requirement_edit_is_empty")
        return self
