from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import httpx

from plango_harness.agent.contracts import Evidence, Location, PlaceCandidate
from plango_harness.settings import Settings


def _observed(ttl_minutes: int = 30) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now, now + timedelta(minutes=ttl_minutes)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _opening_minutes(value: Any) -> dict[str, int]:
    """Use a single explicit provider opening interval; leave complex schedules unknown."""
    match = re.fullmatch(r"\s*(?:每天|每日)?\s*([0-2]?\d):([0-5]\d)\s*[-—–~～至]\s*([0-2]?\d):([0-5]\d)\s*", str(value or ""))
    if match is None:
        return {}
    a,b,c,d=map(int,match.groups())
    start,end=a*60+b,c*60+d
    return {'open_minute':start,'close_minute':end} if a<24 and c<=24 and start<end<=1440 else {}


def _normalize_category(value: Any) -> str:
    raw = str(value or "本地生活")
    if any(word in raw for word in ("咖啡", "茶馆")):
        return "咖啡"
    if any(word in raw for word in ("餐饮", "餐厅", "饭店", "美食")):
        return "餐厅"
    if any(word in raw for word in ("公园", "景区")):
        return "公园"
    if any(word in raw for word in ("影院", "电影院")):
        return "电影"
    if any(word in raw for word in ("博物馆", "展览", "美术馆")):
        return "展览"
    if any(word in raw for word in ("儿童", "亲子", "游乐")):
        return "亲子"
    return raw.split(";")[-1] or "本地生活"


def _semantic_tags(name: Any, raw_type: Any, category: str) -> list[str]:
    """Add small, deterministic tags needed by constraint verification."""
    text = f"{name or ''} {raw_type or ''} {category}"
    tags = [str(raw_type or "")]
    if any(word in text for word in ("儿童", "亲子", "游乐", "幼儿")):
        tags.append("亲子")
    if any(word in text for word in ("公园", "景区", "广场")):
        tags.extend(("户外", "散步"))
    if any(word in text for word in ("室内", "商场", "博物馆", "影院", "馆")):
        tags.append("室内")
    return list(dict.fromkeys(tag for tag in tags if tag))


def _poi_candidate(item: Any, location: Location | None = None) -> PlaceCandidate | None:
    """Search and detail share one parser; a malformed row is not an invented POI."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip() or not isinstance(item.get("name"), str) or not item["name"].strip():
        return None
    try:
        lon, lat = (float(value) for value in item["location"].split(",", 1))
        if not math.isfinite(lon) or not math.isfinite(lat) or abs(lon) > 180 or abs(lat) > 90:
            return None
        business = item.get("business") or item.get("biz_ext")
        business = business if isinstance(business, dict) else {}
        return PlaceCandidate(
            place_id="amap:" + item["id"], name=item["name"],
            address=item.get("address") if isinstance(item.get("address"), str) else None,
            category=_normalize_category(item.get("type")), latitude=lat, longitude=lon,
            rating=max(0.0, min(5.0, _number(business.get("rating")))),
            average_price=max(0.0, _number(business.get("cost"))),
            price_known=bool(re.fullmatch(r"\d+(?:\.\d+)?", str(business.get("cost", "")).strip())),
            **_opening_minutes(business.get("opentime_today") or business.get("opentime") or item.get("opentime")),
            distance_km=round(_distance_km(location, lat, lon), 2) if location else 0.0,
            tags=_semantic_tags(item["name"], item.get("type"), _normalize_category(item.get("type"))),
            source="amap", evidence_ids=["amap-poi:" + item["id"]],
        )
    except (AttributeError, KeyError, ValueError, TypeError):
        return None


def _sandbox_query(value: str) -> str:
    """Normalize a few stable Sandbox synonyms before lexical matching."""
    query = (value or "").lower().strip()
    aliases = {
        "看展": "展览",
        "逛展": "展览",
        "展馆": "展览",
        "看电影": "电影",
        "火锅": "川味",
        "轻食": "清淡",
        "逛街": "citywalk",
        "亲子游": "亲子",
    }
    return aliases.get(query, query)


def _distance_km(a: Location, lat: float, lon: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(a.latitude), math.radians(lat)
    dp = math.radians(lat - a.latitude)
    dl = math.radians(lon - a.longitude)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(x), math.sqrt(max(0.0, 1 - x)))


@dataclass(frozen=True)
class Supply:
    place_id: str
    open_now: bool | None
    reservable: bool | None
    seats_left: int | None
    estimated_wait_min: int | None
    source: Literal["amap", "dataset", "simulated", "unknown", "browser"]
    observed_at: datetime | None = None
    expires_at: datetime | None = None


class WorldProvider(Protocol):
    async def geocode(self, address: str) -> Location | None: ...

    async def search_places(
        self, query: str, location: Location, *, limit: int = 8
    ) -> tuple[list[PlaceCandidate], list[Evidence]]: ...

    async def get_place(self, place_id: str) -> PlaceCandidate | None: ...

    async def get_supply(self, place_id: str, at_minute: int) -> Supply: ...

    async def estimate_route(
        self, origin: Location, destination: PlaceCandidate
    ) -> tuple[dict[str, Any], Evidence]: ...

    async def get_weather(self, location: Location) -> tuple[dict[str, Any], Evidence]: ...


class WorldProviderError(RuntimeError):
    """A read-provider failure with a stable, auditable error kind."""

    def __init__(self, error_kind: str, detail: str = "") -> None:
        super().__init__(detail or error_kind)
        self.error_kind = error_kind
        self.detail = detail or error_kind


class SandboxWorldProvider:
    """Deterministic local world selected by the explicit sandbox profile."""

    def __init__(self, seed: int = 20260903) -> None:
        self.seed = seed
        self._places = self._build_places()

    def _build_places(self) -> list[PlaceCandidate]:
        base_lat, base_lon = 39.997, 116.482
        rows = [
            ("p-kids-park", "望京亲子探索乐园", "亲子", 4.6, 80, ["亲子", "室内", "儿童"]),
            ("p-museum", "望京当代艺术馆", "展览", 4.5, 45, ["展览", "室内", "安静"]),
            ("p-park", "朝来森林公园", "公园", 4.4, 20, ["公园", "户外", "散步"]),
            ("p-aquarium", "北区海洋探索馆", "动物园", 4.7, 120, ["动物", "亲子", "室内"]),
            ("p-cafe", "树下独立咖啡", "咖啡", 4.3, 42, ["咖啡", "安静", "出片"]),
            ("p-light", "青禾轻食餐厅", "餐厅", 4.5, 68, ["清淡", "减脂", "儿童餐"]),
            ("p-yunnan", "云岭云南菜", "餐厅", 4.4, 88, ["清淡", "可预约", "家庭"]),
            ("p-hotpot", "川味小馆", "餐厅", 4.6, 76, ["川菜", "能吃辣", "团购"]),
            ("p-noodle", "热汤面馆", "餐厅", 4.2, 35, ["快餐", "清淡", "低价"]),
            ("p-market", "望京文创小街", "citywalk", 4.1, 30, ["文创", "户外", "散步"]),
            ("p-cinema", "星河影城", "电影", 4.3, 65, ["电影", "室内", "约会"]),
            ("p-play-cafe", "小象儿童咖啡", "咖啡", 4.2, 55, ["亲子", "儿童", "咖啡"]),
        ]
        out: list[PlaceCandidate] = []
        for idx, (pid, name, category, rating, price, tags) in enumerate(rows):
            # Spread deterministic locations within roughly 4 km of the default origin.
            lat = base_lat + ((idx * 17) % 11 - 5) * 0.003
            lon = base_lon + ((idx * 23) % 13 - 6) * 0.004
            out.append(
                PlaceCandidate(
                    place_id=pid,
                    name=name,
                    category=category,
                    latitude=lat,
                    longitude=lon,
                    rating=rating,
                    average_price=price,
                    tags=tags,
                    source="simulated",
                    evidence_ids=[f"sandbox-place:{pid}"],
                )
            )
        return out

    async def geocode(self, address: str) -> Location | None:
        # A sandbox has one stable origin; retaining the requested name keeps
        # the contract useful in offline tests without pretending to know GPS.
        return Location(name=address or "望京", latitude=39.997, longitude=116.482)

    async def search_places(
        self, query: str, location: Location, *, limit: int = 8
    ) -> tuple[list[PlaceCandidate], list[Evidence]]:
        q = _sandbox_query(query)
        candidates: list[PlaceCandidate] = []
        for place in self._places:
            distance = _distance_km(location, place.latitude, place.longitude)
            text = " ".join([place.name, place.category, *place.tags]).lower()
            if q and q not in text and not any(token in text for token in q.split() if token):
                continue
            candidates.append(place.model_copy(update={"distance_km": round(distance, 2)}))
        if not candidates:
            candidates = [
                p.model_copy(
                    update={
                        "distance_km": round(_distance_km(location, p.latitude, p.longitude), 2)
                    }
                )
                for p in self._places
            ]
        candidates.sort(key=lambda p: p.rating - p.distance_km * 0.08, reverse=True)
        candidates = candidates[:limit]
        observed_at, expires_at = _observed(60)
        evidence = [
            Evidence(
                evidence_id=f"sandbox-search:{hashlib.sha1((q + str(self.seed)).encode()).hexdigest()[:12]}",
                source="simulated",
                source_ref="sandbox-world",
                claim=f"本地模拟世界返回 {len(candidates)} 个候选地点",
                payload={"query": query, "count": len(candidates)},
                observed_at=observed_at,
                expires_at=expires_at,
                confidence=0.7,
            )
        ]
        # Keep the place-level IDs carried by PlaceCandidate resolvable.  A
        # search summary alone is not enough to prove which candidate was
        # observed, especially after a replan.
        evidence.extend(
            Evidence(
                evidence_id=f"sandbox-place:{place.place_id}",
                source="simulated",
                source_ref="sandbox-world/place",
                claim=f"模拟世界观测到地点：{place.name}",
                payload={
                    "place_id": place.place_id,
                    "name": place.name,
                    "category": place.category,
                    "average_price": place.average_price,
                    "price_known": place.price_known,
                },
                observed_at=observed_at,
                expires_at=expires_at,
                confidence=0.7,
            )
            for place in candidates
        )
        return candidates, evidence

    async def get_place(self, place_id: str) -> PlaceCandidate | None:
        return next((p for p in self._places if p.place_id == place_id), None)

    async def get_supply(self, place_id: str, at_minute: int) -> Supply:
        key = f"{self.seed}:{place_id}:{at_minute // 30}"
        digest = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        wait = 5 + digest % 46
        seats = digest % 9
        observed_at, expires_at = _observed(5)
        return Supply(
            place_id=place_id,
            open_now=(digest % 17 != 0),
            reservable=(digest % 3 != 0),
            seats_left=seats,
            estimated_wait_min=wait,
            source="simulated",
            observed_at=observed_at,
            expires_at=expires_at,
        )

    async def estimate_route(
        self, origin: Location, destination: PlaceCandidate
    ) -> tuple[dict[str, Any], Evidence]:
        distance = _distance_km(origin, destination.latitude, destination.longitude)
        driving = max(6, round(distance * 4 + 8))
        transit = max(8, round(distance * 7 + 10))
        walking = max(5, round(distance * 14))
        route = {
            "distance_km": round(distance, 2),
            "driving_min": driving,
            "transit_min": transit,
            "walking_min": walking,
            "recommended": "transit" if distance < 3 else "driving",
            "source": "simulated",
        }
        observed_at, expires_at = _observed(60)
        return route, Evidence(
            evidence_id=f"sandbox-route:{destination.place_id}",
            source="simulated",
            source_ref="sandbox-world",
            claim=f"到 {destination.name} 的估算路线约 {distance:.1f} km",
            payload=route,
            observed_at=observed_at,
            expires_at=expires_at,
            confidence=0.6,
        )

    async def get_weather(self, location: Location) -> tuple[dict[str, Any], Evidence]:
        digest = int(hashlib.sha256(f"weather:{self.seed}".encode()).hexdigest()[:8], 16)
        rain = digest % 5 == 0
        weather = {
            "text": "小雨" if rain else "多云",
            "rain": rain,
            "temp": 22 + digest % 8,
            "source": "simulated",
        }
        observed_at, expires_at = _observed(15)
        return weather, Evidence(
            evidence_id=f"sandbox-weather:{self.seed}",
            source="simulated",
            source_ref="sandbox-world",
            claim=f"模拟天气：{weather['text']}",
            payload=weather,
            observed_at=observed_at,
            expires_at=expires_at,
            confidence=0.5,
        )


class AmapWorldProvider:
    """Strict Amap read adapter; it never inherits the Sandbox world."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.amap_timeout_seconds)
        self._cache: dict[str, tuple[PlaceCandidate, float]] = {}
        self._poi_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._request_lock = asyncio.Lock()
        self._last_request = 0.0
        self.last_error_kind: str | None = None
        self._request_started = time.monotonic()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        # ponytail: per-process pacing; share a limiter only if multiple workers exceed the provider quota.
        async with self._request_lock:
            await asyncio.sleep(max(0.0, 0.22 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            return await self._request(path, params)

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self._request_started = time.monotonic()
        self.last_error_kind = None
        if not self.settings.amap_webservice_key:
            self.last_error_kind = "missing_key"
            return None
        try:
            response = await self.client.get(
                f"https://restapi.amap.com/{path if path.startswith('v5/') else 'v3/' + path}",
                params={**params, "key": self.settings.amap_webservice_key, "output": "json"},
            )
            if getattr(response, "status_code", 200) == 429:
                self.last_error_kind = "quota"
                return None
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                self.last_error_kind = "invalid_response"
                return None
            if data.get("status") != "1":
                info = str(data.get("infocode") or "")
                message = str(data.get("info") or "").lower()
                self.last_error_kind = (
                    "quota"
                    if info in {"10003", "10004", "10005", "10020", "10044", "10045"}
                    or any(word in message for word in ("limit", "quota", "频繁", "次数"))
                    else "provider_error"
                )
                return None
            return data
        except httpx.TimeoutException:
            self.last_error_kind = "timeout"
        except httpx.HTTPStatusError as exc:
            self.last_error_kind = "quota" if exc.response.status_code == 429 else "http_error"
        except httpx.HTTPError:
            self.last_error_kind = "http_error"
        except OSError:
            self.last_error_kind = "network_error"
        except (ValueError, TypeError):
            self.last_error_kind = "invalid_response"
        return None

    def _error_evidence(
        self, operation: str, *, empty: bool = False, key: str = ""
    ) -> Evidence:
        observed_at, expires_at = _observed(2 if empty else 1)
        error_kind = "empty_result" if empty else (self.last_error_kind or "provider_error")
        stable_key = hashlib.sha1(f"{operation}:{key}".encode()).hexdigest()[:12]
        return Evidence(
            evidence_id=f"amap-error:{operation}:{stable_key}",
            source="amap",
            source_ref=operation,
            claim=f"高德 {operation} 未返回可用结果（{error_kind}）",
            payload={
                "primary_failed": True,
                "fallback_used": False,
                "error_kind": error_kind,
                "source": "amap",
                "latency_ms": round((time.monotonic() - self._request_started) * 1000, 2),
                "observed_at": observed_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            },
            observed_at=observed_at,
            expires_at=expires_at,
            confidence=0.0,
        )

    def _cached(self, key: str) -> PlaceCandidate | None:
        item = self._cache.get(key)
        if item is None:
            return None
        place, expires_at = item
        if expires_at <= time.monotonic():
            self._cache.pop(key, None)
            return None
        return place

    async def search_pois(
        self, query: str, *, city: str = "", longitude: float | None = None,
        latitude: float | None = None, types: str | None = None, radius_m: int = 5000,
        page: int = 1, limit: int = 20, refresh: bool = False,
    ) -> dict[str, Any]:
        """One bounded v5 source for both UI discovery and planning candidates."""
        if (longitude is None) != (latitude is None):
            raise ValueError("coordinate_pair_required")
        if longitude is not None and (not math.isfinite(longitude) or not math.isfinite(latitude or 0)
                or abs(longitude) > 180 or abs(latitude or 0) > 90):
            raise ValueError("invalid_coordinates")
        if not query.strip() or len(query) > 80 or not 0 <= radius_m <= 50000 or not 1 <= page <= 100 or not 1 <= limit <= 25:
            raise ValueError("invalid_poi_search")
        around = longitude is not None
        if not around and not city.strip():
            raise ValueError("city_or_coordinates_required")
        path = "v5/place/around" if around else "v5/place/text"
        params: dict[str, Any] = {"keywords": query, "page_size": limit, "page_num": page, "show_fields": "business,photos"}
        if city:
            params.update(region=city, city_limit="true")
        if types:
            params["types"] = types
        if around:
            params.update(location=f"{longitude:.6f},{latitude:.6f}", radius=radius_m, sortrule="distance")
        key = json.dumps([path, params], ensure_ascii=False, sort_keys=True)
        cached = self._poi_cache.get(key)
        if not refresh and cached and cached[1] > time.monotonic():
            return {**cached[0], "cache_hit": True}
        data = await self._get(path, params)
        if data is None:
            raise ValueError("amap_" + (self.last_error_kind or "provider_error"))
        pois = data.get("pois")
        if not isinstance(pois, list):
            raise ValueError("amap_invalid_response")
        now, expires = _observed(5)
        result = {"source": "amap", "scope": "around" if around else "city", "pois": [p for p in pois[:limit] if isinstance(p, dict)],
                  "observed_at": now.isoformat(), "expires_at": expires.isoformat(), "cache_hit": False,
                  "source_ref": "https://restapi.amap.com/" + path}
        self._poi_cache[key] = (result, time.monotonic() + 300)
        while len(self._poi_cache) > 128:
            self._poi_cache.pop(next(iter(self._poi_cache)))
        return result

    async def search_places(
        self, query: str, location: Location, *, limit: int = 8, radius_m: int = 5000
    ) -> tuple[list[PlaceCandidate], list[Evidence]]:
        try:
            result = await self.search_pois(query or "本地生活", longitude=location.longitude,
                                            latitude=location.latitude, limit=min(25, limit), radius_m=radius_m)
        except ValueError:
            return [], [self._error_evidence("v5/place/around", key=f"{query}:{location.latitude:.5f}:{location.longitude:.5f}")]
        places = [place for item in result["pois"][:limit] if (place := _poi_candidate(item, location)) is not None]
        observed_at = datetime.fromisoformat(result["observed_at"])
        expires_at = datetime.fromisoformat(result["expires_at"])
        remaining = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
        for place in places:
            self._cache[place.place_id] = (place, time.monotonic() + remaining)
        if not places:
            return [], [
                self._error_evidence(
                    "v5/place/around",
                    empty=True,
                    key=f"{query}:{location.latitude:.5f}:{location.longitude:.5f}",
                )
            ]
        evidence = [
            Evidence(
                evidence_id=f"amap-search:{int(time.time() * 1000)}",
                source="amap",
                source_ref=result["source_ref"],
                claim=f"高德地点检索返回 {len(places)} 个候选",
                payload={"query": query, "count": len(places), "radius_m": radius_m},
                observed_at=observed_at,
                expires_at=expires_at,
                confidence=0.9,
            )
        ]
        evidence.extend(
            Evidence(
                evidence_id=f"amap-poi:{place.place_id.removeprefix('amap:')}",
                source="amap",
                source_ref=result["source_ref"],
                claim=f"高德返回地点：{place.name}",
                payload={
                    "place_id": place.place_id,
                    "name": place.name,
                    "address": place.address,
                    "latitude": place.latitude,
                    "longitude": place.longitude,
                    "open_minute": place.open_minute,
                    "close_minute": place.close_minute,
                    "category": place.category,
                    "average_price": place.average_price,
                    "price_known": place.price_known,
                },
                observed_at=observed_at,
                expires_at=expires_at,
                confidence=0.9,
            )
            for place in places
        )
        return places, evidence

    async def get_place(self, place_id: str, *, refresh: bool = False) -> PlaceCandidate | None:
        cached = None if refresh else self._cached(place_id)
        if cached is not None:
            return cached
        amap_id = place_id.removeprefix("amap:")
        data = await self._get("v5/place/detail", {"id": amap_id, "show_fields": "business,photos"})
        items = (data or {}).get("pois") or []
        item = items[0] if isinstance(items, list) and items else None
        place = _poi_candidate(item)
        if place is None or place.place_id != "amap:" + amap_id:
            return None
        self._cache[place.place_id] = (place, time.monotonic() + 600)
        return place

    async def refresh_place(self, previous: PlaceCandidate) -> tuple[PlaceCandidate, Evidence]:
        """A forced ID read refreshes identity without repeating the place search."""
        try:
            fresh = await self.get_place(previous.place_id, refresh=True)
        except Exception:
            fresh = None
        if fresh is None or fresh.place_id != previous.place_id or fresh.source != "amap":
            self._cache.pop(previous.place_id, None)
            proof = self._error_evidence("v5/place/detail", key=previous.place_id)
            return previous.model_copy(update={"price_known": False, "average_price": 0, "evidence_ids": []}), proof
        observed_at, expires_at = _observed(10)
        proof = Evidence(evidence_id="amap-detail:" + uuid.uuid4().hex, source="amap",
            source_ref="https://restapi.amap.com/v5/place/detail?id=" + previous.place_id.removeprefix("amap:"),
            claim="高德详情返回：" + fresh.name,
            payload={key: value for key, value in fresh.model_dump(mode="json").items() if key in {"place_id", "name", "address", "category", "latitude", "longitude", "average_price", "price_known", "open_minute", "close_minute"}},
            observed_at=observed_at, expires_at=expires_at, confidence=0.9)
        fresh = fresh.model_copy(update={"evidence_ids": [proof.evidence_id]})
        self._cache[fresh.place_id] = (fresh, time.monotonic() + 600)
        return fresh, proof

    async def geocode(self, address: str, *, city: str | None = None) -> Location | None:
        if not isinstance(address, str) or not address.strip():
            return None

        def parse(data):
            rows = (data or {}).get("geocodes")
            row = rows[0] if isinstance(rows, list) and rows else None
            if not isinstance(row, dict):
                return None
            actual = row.get("city") if isinstance(row.get("city"), str) else row.get("province")
            actual = actual if isinstance(actual, str) else ""
            short = actual.removesuffix("市")
            scope = str(city or "")
            raw_adcode = row.get("adcode")
            adcode = raw_adcode if isinstance(raw_adcode, str) else ""
            same_city = (adcode[:4] == scope[:4] if scope.isdigit() and len(scope) == 6 else actual.removesuffix("市") == scope.removesuffix("市")) if scope else True
            # A city name or full city-prefixed address is explicit cross-city
            # input. A venue/category merely found elsewhere is not.
            explicit_city = bool(actual and (address in {actual, short} or address.startswith(actual + ("" if actual.endswith("市") else "市"))))
            if city and not same_city and not explicit_city:
                return None
            try:
                lon, lat = (float(part) for part in row["location"].split(",", 1))
                if not math.isfinite(lon) or not math.isfinite(lat):
                    return None
                return Location(name=address, latitude=lat, longitude=lon, city_code=adcode or None)
            except (AttributeError, KeyError, ValueError, TypeError):
                return None

        data = await self._get("geocode/geo", {"address": address, **({"city": city} if city else {})})
        result = parse(data)
        if result is not None or not city or data is None:
            return result
        # One compatibility lookup can resolve an explicitly named other city;
        # its returned city must still satisfy the source-scope check above.
        return parse(await self._get("geocode/geo", {"address": address}))

    async def estimate_route(
        self, origin: Location, destination: PlaceCandidate, *, mode: str = "driving"
    ) -> tuple[dict[str, Any], Evidence]:
        lower_bound = math.floor(_distance_km(origin, destination.latitude, destination.longitude) * 1000) / 1000
        if mode not in {"driving", "walking"}:
            evidence = self._error_evidence("direction/" + mode, key=destination.place_id)
            evidence = evidence.model_copy(update={"claim": "当前适配器尚不能核验所选交通方式", "payload": {**evidence.payload, "error_kind": "unsupported_route_mode", "requested_mode": mode}})
            return {"distance_km": lower_bound, "distance_kind": "straight_line_lower_bound", "driving_min": None, "walking_min": None, "transit_min": None, "recommended": "unknown", "source": "amap", "error_kind": "unsupported_route_mode"}, evidence
        operation = "direction/" + mode
        data = await self._get(
            operation,
            {
                "origin": f"{origin.longitude},{origin.latitude}",
                "destination": f"{destination.longitude},{destination.latitude}",
                **({"strategy": 0} if mode == "driving" else {}),
            },
        )
        try:
            path = (data or {}).get("route", {}).get("paths", [])[0]
            distance, duration = float(path["distance"]), float(path["duration"])
            if not math.isfinite(distance) or not math.isfinite(duration) or distance < 0 or duration <= 0:
                raise ValueError("invalid_route_measurements")
            distance_km = round(distance / 1000, 2)
            minutes = max(1, round(duration / 60))
            route = {
                "distance_km": distance_km,
                "distance_kind": "route",
                "driving_min": minutes if mode == "driving" else None,
                "transit_min": None,
                "walking_min": minutes if mode == "walking" else None,
                "recommended": mode,
                "source": "amap",
            }
            observed_at, expires_at = _observed(10)
            route_key = (
                f"{destination.place_id}:{origin.latitude:.5f}:{origin.longitude:.5f}:{mode}"
            )
            return route, Evidence(
                evidence_id=(
                    f"amap-route:{hashlib.sha1(route_key.encode()).hexdigest()[:12]}"
                ),
                source="amap",
                source_ref=operation,
                claim=f"高德{'步行' if mode == 'walking' else '驾车'}路线约 {distance_km:.1f} km / {minutes} 分钟",
                payload=route,
                observed_at=observed_at,
                expires_at=expires_at,
                confidence=0.9,
            )
        except (IndexError, KeyError, AttributeError, ValueError, TypeError):
            evidence = self._error_evidence(
                operation,
                empty=bool(data),
                key=f"{destination.place_id}:{origin.latitude:.5f}:{origin.longitude:.5f}:{mode}",
            )
            return (
                {
                    "distance_km": lower_bound,
                    "distance_kind": "straight_line_lower_bound",
                    "driving_min": None,
                    "transit_min": None,
                    "walking_min": None,
                    "recommended": "unknown",
                    "source": "amap",
                    "error_kind": evidence.payload.get("error_kind"),
                },
                evidence,
            )

    async def get_supply(self, place_id: str, at_minute: int) -> Supply:
        # Amap does not provide live seats/queues; an actual provider never creates simulated supply.
        observed_at, expires_at = _observed(1)
        return Supply(place_id=place_id, open_now=None, reservable=None, seats_left=None,
                      estimated_wait_min=None, source="unknown", observed_at=observed_at, expires_at=expires_at)

    async def get_weather(self, location: Location, visit_date: date | None = None,
                          timezone_name: str = "Asia/Shanghai") -> tuple[dict[str, Any], Evidence]:
        source_zone = ZoneInfo("Asia/Shanghai")
        now = datetime.now(timezone.utc)
        today = now.astimezone(source_zone).date()
        target = visit_date or today
        city = location.city_code

        def unknown(kind: str):
            evidence = self._error_evidence("weather/weatherInfo", key=f"{city}:{target}")
            payload = {**evidence.payload, "text": "未知", "rain": None, "error_kind": kind,
                       "requested_visit_date": target.isoformat(), "timezone": "Asia/Shanghai"}
            return payload, evidence.model_copy(update={"payload": payload, "claim": f"高德天气待核验（{kind}）"})

        if timezone_name != "Asia/Shanghai":
            return unknown("unsupported_weather_timezone")  # A date alone cannot convert the Chinese day interval.
        if not city:
            area = await self._get("geocode/regeo", {"location": f"{location.longitude:.6f},{location.latitude:.6f}", "extensions": "base"})
            reverse = (area or {}).get("regeocode")
            component = reverse.get("addressComponent") if isinstance(reverse, dict) else None
            city = component.get("adcode") if isinstance(component, dict) else None
        if not isinstance(city, str) or not city:
            return unknown(self.last_error_kind or "missing_city_code")
        forecast = target != today
        data = await self._get("weather/weatherInfo", {"city": city, "extensions": "all" if forecast else "base"})
        if data is None:
            return unknown(self.last_error_kind or "provider_error")
        item = None
        reporttime = None
        if forecast:
            forecasts = data.get("forecasts")
            for row in forecasts if isinstance(forecasts, list) else []:
                if not isinstance(row, dict) or not isinstance(row.get("casts"), list):
                    continue
                item = next((cast for cast in row["casts"] if isinstance(cast, dict) and cast.get("date") == target.isoformat()), None)
                if item is not None:
                    reporttime = row.get("reporttime")
                    break
        else:
            lives = data.get("lives")
            item = lives[0] if isinstance(lives, list) and lives else None
            reporttime = item.get("reporttime") if isinstance(item, dict) else None
        if not isinstance(item, dict):
            return unknown("missing_weather_date")
        try:
            reported = datetime.fromisoformat(str(reporttime))
            if reported.tzinfo is None:
                reported = reported.replace(tzinfo=source_zone)
            reported = reported.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return unknown("missing_weather_reporttime")
        # Publication time belongs to the source. Fetching the same old report
        # again cannot grant it a new validity period.
        expires_at = min(now + timedelta(minutes=10), reported + (timedelta(days=1) if forecast else timedelta(hours=2)))
        if expires_at <= now or reported > now + timedelta(minutes=5):
            return unknown("stale_weather_report")
        if not forecast and reported.astimezone(source_zone).date() != target:
            return unknown("mismatched_weather_date")
        condition = item.get("dayweather") if forecast else item.get("weather")
        if not isinstance(condition, str) or not condition.strip() or condition.strip() == "未知":
            return unknown("missing_weather_condition")
        temperature = item.get("daytemp") if forecast else item.get("temperature")
        try:
            temperature = float(temperature) if temperature is not None else None
            if temperature is not None and not math.isfinite(temperature):
                temperature = None
        except (ValueError, TypeError):
            temperature = None
        night = item.get("nightweather")
        rain: bool | None = "雨" in condition or isinstance(night, str) and "雨" in night
        if forecast and not rain and (not isinstance(night, str) or not night.strip() or night == "未知"):
            rain = None
        weather = {"text": condition, "rain": rain, "temp": temperature, "source": "amap",
                   "forecast": forecast, "timezone": "Asia/Shanghai", "visit_date": target.isoformat(),
                   "source_reported_at": reported.isoformat(), "retrieved_at": now.isoformat()}
        return weather, Evidence(evidence_id=f"amap-weather:{city}:{target}:{reported.isoformat()}", source="amap",
            source_ref="https://restapi.amap.com/v3/weather/weatherInfo",
            claim=f"高德{'预报' if forecast else '天气'}：{target} {condition}", payload=weather,
            observed_at=reported, expires_at=expires_at, confidence=0.8 if forecast else 0.95)

    async def close(self) -> None:
        await self.client.aclose()


class WorldService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.runtime_profile == "sandbox" and settings.world_provider != "sandbox":
            raise ValueError("sandbox profile requires world_provider=sandbox")
        if settings.runtime_profile == "service" and settings.world_provider != "amap":
            raise ValueError("service profile requires world_provider=amap")
        self.provider: WorldProvider
        if settings.world_provider == "sandbox":
            self.provider = SandboxWorldProvider(settings.seed)
        else:
            if not settings.amap_webservice_key:
                raise ValueError("AMAP_WEBSERVICE_KEY is required for the service Amap provider")
            self.provider = AmapWorldProvider(settings)

    async def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if close:
            await close()
