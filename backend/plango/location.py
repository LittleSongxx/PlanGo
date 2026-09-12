"""Client location is a scoped default; explicit user destinations outrank it."""

from typing import Literal

from plango_harness.agent.contracts import Location
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class LocationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    city: str = Field(min_length=1, max_length=100)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False, strict=True)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False, strict=True)
    source: Literal["config", "manual", "device"]
    coordinate_system: Literal["GCJ02"] = "GCJ02"
    detail_source: Literal["config", "manual", "address", "gps", "amap-gps", "ip", "amap-ip", "amap-city", "pconline", "ip-api"] | None = None
    accuracy: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False, strict=True)
    granularity: Literal["point", "address", "district", "city", "unknown"] | None = None
    observed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def coordinate_pair(self):
        if (self.longitude is None) != (self.latitude is None):
            raise ValueError("longitude_and_latitude_must_be_provided_together")
        if self.accuracy is not None and self.detail_source not in {"gps", "amap-gps"}:
            raise ValueError("accuracy_requires_observed_device_location")
        if self.granularity in {"point", "address"} and self.longitude is None:
            raise ValueError("precise_location_requires_coordinates")
        return self


def _same_place_name(left, right):
    return str(left or "").strip().removesuffix("市") == str(right or "").strip().removesuffix("市")


def _latest_user_text(state):
    for item in reversed(state.get("messages") or []):
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        role = (item.get("type") or item.get("role")) if isinstance(item, dict) else getattr(item, "type", None)
        if content and str(role or "") in {"human", "user"}:
            return str(content).strip()
    pending = str(state.get("pending_message") or "").strip()
    if pending:
        return pending
    text = str(state.get("input_text") or "")
    return text.rsplit("\n", 1)[-1].strip() if "\n" in text else text.strip()


def _answering_origin_prompt(state):
    question = str((state.get("clarification") or {}).get("question") or "")
    return "起点" in question or "出发地点" in question


def select_origin(state, extracted_name, previous_spec, context):
    def precise_client_point():
        return (
            context is not None
            and context.granularity in {"point", "address"}
            and context.detail_source not in {"ip", "amap-ip", "amap-city", "pconline", "ip-api"}
            and context.latitude is not None
            and context.longitude is not None
        )

    explicit = str(extracted_name or "").strip()
    answer = _latest_user_text(state)
    # A location interrupt already has the user's origin sentence. If the
    # structured extract collapsed it to the search city, geocode that sentence.
    if (
        context
        and _answering_origin_prompt(state)
        and explicit
        and _same_place_name(explicit, context.city)
        and answer
        and not _same_place_name(answer, context.city)
    ):
        explicit = answer[:200]
    previous_origin = state.get("location_origin") or {}
    previous_location = previous_spec.location if previous_spec is not None else None
    selected_raw = state.get("selected_poi") or {}
    selected_name = str(selected_raw.get("name") or "").strip() if isinstance(selected_raw, dict) else ""
    previous_is_destination = bool(selected_name) and (
        previous_origin.get("reference") == "selected_place"
        or (previous_location is not None and _same_place_name(previous_location.name, selected_name))
    )
    if previous_is_destination and explicit and not _same_place_name(explicit, selected_name):
        return explicit, None, {"source": "user", "name": explicit}
    if explicit:
        name = explicit
        source = "user"
    elif previous_location is not None and previous_origin.get("source", "user") == "user":
        return (
            previous_location.name,
            previous_location,
            previous_origin or {"source": "user", "name": previous_location.name},
        )
    elif context:
        name = context.city
        source = context.source
    elif previous_location is not None:
        return (
            previous_location.name,
            previous_location,
            previous_origin or {"source": "user", "name": previous_location.name},
        )
    else:
        return None, None, {"source": "unknown", "name": None}
    metadata = {"source": source, "name": name}
    if context and _same_place_name(name, context.city) and precise_client_point():
        return (
            name,
            Location(name=name, latitude=context.latitude, longitude=context.longitude),
            {**metadata, "coordinate_source": context.source},
        )
    if previous_location is not None and _same_place_name(name, previous_location.name):
        return name, previous_location, metadata
    return name, None, metadata
