"""Shared authenticated Amap reads. No browser task, inferred server IP, or model is needed."""
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .location import LocationContext


class GeoSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=80)
    location_context: LocationContext
    types: str | None = Field(default=None, max_length=200)
    radius_m: int = Field(default=5000, ge=0, le=50000, strict=True)
    page: int = Field(default=1, ge=1, le=100, strict=True)
    limit: int = Field(default=20, ge=1, le=25, strict=True)
    refresh: bool = False


class GeoSearchResult(BaseModel):
    source: Literal["amap"] = "amap"
    scope: Literal["around", "city"]
    pois: list[dict[str, Any]]
    observed_at: datetime
    expires_at: datetime
    cache_hit: bool
    source_ref: str


class Geocode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    address: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=100)


class Reverse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)


class GeoLocation(Reverse):
    address: str
    city: str
    district: str
    granularity: Literal["address", "district", "city", "unknown"] = "unknown"


class GeoLocationResult(BaseModel):
    source: Literal["amap"] = "amap"
    location: GeoLocation
    observed_at: datetime
    expires_at: datetime


def _text(value):
    return value if isinstance(value, str) else ""


def install_geo_routes(app, runtime, protected):
    provider = runtime.world_service.provider.amap

    def unavailable():
        kind = provider.last_error_kind or "empty_result"
        return HTTPException(503 if kind == "missing_key" else 502, "amap_" + kind)

    @app.post("/api/v1/geo/search", dependencies=protected, response_model=GeoSearchResult)
    async def search(body: GeoSearch):
        context = body.location_context
        precise = context.granularity not in {"city", "district", "unknown"} and context.detail_source not in {"ip", "amap-ip", "amap-city", "pconline", "ip-api"}
        try:
            return await provider.search_pois(body.query, city=context.city, longitude=context.longitude if precise else None,
                latitude=context.latitude if precise else None, types=body.types, radius_m=body.radius_m, page=body.page,
                limit=body.limit, refresh=body.refresh)
        except ValueError as error:
            raise HTTPException(502, str(error)) from None

    @app.post("/api/v1/geo/geocode", dependencies=protected, response_model=GeoLocationResult)
    async def geocode(body: Geocode):
        data = await provider._get("geocode/geo", {"address": body.address, **({"city": body.city} if body.city else {})})
        rows = (data or {}).get("geocodes") or []
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise unavailable()
        row = rows[0]
        try:
            longitude, latitude = map(float, str(row["location"]).split(","))
            location = GeoLocation(longitude=longitude, latitude=latitude,
                address=_text(row.get("formatted_address")), city=_text(row.get("city")) or _text(row.get("province")),
                district=_text(row.get("district")), granularity=("city" if _text(row.get("level")) in {"国家", "省", "市", "城市"}
                    else "district" if _text(row.get("level")) in {"区县", "开发区", "乡镇", "村庄"}
                    else "address" if _text(row.get("level")) in {"兴趣点", "门牌号", "单元号", "道路", "道路交叉路口", "公交站点", "地铁站点"}
                    else "unknown"))
        except (ValueError, KeyError, TypeError):
            raise HTTPException(502, "amap_invalid_response") from None
        now = datetime.now(timezone.utc)
        return GeoLocationResult(location=location, observed_at=now, expires_at=now + timedelta(days=1))

    @app.post("/api/v1/geo/reverse", dependencies=protected, response_model=GeoLocationResult)
    async def reverse(body: Reverse):
        data = await provider._get("geocode/regeo", {"location": f"{body.longitude:.6f},{body.latitude:.6f}", "extensions": "base"})
        row = (data or {}).get("regeocode")
        if not isinstance(row, dict) or not _text(row.get("formatted_address")).strip():
            raise unavailable()
        component = row.get("addressComponent") or {}
        if not isinstance(component, dict):
            raise HTTPException(502, "amap_invalid_response")
        now = datetime.now(timezone.utc)
        return GeoLocationResult(location=GeoLocation(longitude=body.longitude, latitude=body.latitude,
            address=_text(row.get("formatted_address")), city=_text(component.get("city")) or _text(component.get("province")),
            district=_text(component.get("district")), granularity="address"), observed_at=now, expires_at=now + timedelta(days=1))
