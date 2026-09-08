from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, cast

import httpx

from planora.agent.contracts import Evidence, Location, PlaceCandidate
from planora.settings import Settings


def _observed(ttl_minutes: int = 30) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now, now + timedelta(minutes=ttl_minutes)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _opening_minutes(value: Any) -> dict[str, int]:
    """Use a single explicit provider opening interval; leave complex schedules unknown."""
    rows=re.findall(r"(?<!\d)([0-2]?\d):([0-5]\d)\s*[-—–~～至]\s*([0-2]?\d):([0-5]\d)",str(value or ''))
    if len(rows)!=1:
        return {}
    a,b,c,d=map(int,rows[0])
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
        self._search_cache: dict[str, tuple[list[PlaceCandidate], float]] = {}
        self.last_error_kind: str | None = None
        self._request_started = time.monotonic()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self._request_started = time.monotonic()
        self.last_error_kind = None
        if not self.settings.amap_webservice_key:
            self.last_error_kind = "missing_key"
            return None
        try:
            response = await self.client.get(
                f"https://restapi.amap.com/v3/{path}",
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

    async def search_places(
        self, query: str, location: Location, *, limit: int = 8
    ) -> tuple[list[PlaceCandidate], list[Evidence]]:
        cache_key = f"{query}|{location.latitude:.5f}|{location.longitude:.5f}|{limit}"
        cached = self._search_cache.get(cache_key)
        if cached and cached[1] > time.monotonic():
            cached_places = list(cached[0])
            now = datetime.now(timezone.utc)
            cache_evidence = [
                Evidence(
                    evidence_id=f"amap-cache:{hashlib.sha1(cache_key.encode()).hexdigest()[:12]}",
                    source="amap",
                    source_ref="place/text",
                    claim=f"使用高德地点缓存（{len(cached_places)} 个候选）",
                    payload={"cache": True, "count": len(cached_places)},
                    observed_at=now,
                    expires_at=now + timedelta(minutes=1),
                    confidence=0.8,
                )
            ]
            cache_evidence.extend(
                Evidence(
                    evidence_id=f"amap-poi:{place.place_id.removeprefix('amap:')}",
                    source="amap",
                    source_ref="place/text/cache",
                    claim=f"高德缓存地点：{place.name}",
                    payload={
                        "place_id": place.place_id,
                        "name": place.name,
                        "category": place.category,
                        "average_price": place.average_price,
                    "price_known": place.price_known,
                        "cache": True,
                    },
                    observed_at=now,
                    expires_at=now + timedelta(minutes=1),
                    confidence=0.8,
                )
                for place in cached_places
            )
            return cached_places, cache_evidence
        if cached:
            self._search_cache.pop(cache_key, None)
        data = await self._get(
            "place/text",
            {
                "keywords": query or "本地生活",
                "location": f"{location.longitude},{location.latitude}",
                "radius": 5000,
                "offset": limit,
            },
        )
        if not data:
            return [], [
                self._error_evidence(
                    "place/text", key=f"{query}:{location.latitude:.5f}:{location.longitude:.5f}"
                )
            ]
        places: list[PlaceCandidate] = []
        for item in (data.get("pois") or [])[:limit]:
            if not isinstance(item, dict):
                continue
            biz_ext_raw = item.get("biz_ext")
            biz_ext = cast(dict[str, Any], biz_ext_raw) if isinstance(biz_ext_raw, dict) else {}
            try:
                lon, lat = (float(x) for x in item["location"].split(",", 1))
            except (AttributeError, KeyError, ValueError, TypeError):
                continue
            poi_id = str(item.get("id") or hashlib.sha1(str(item.get("name", "")).encode()).hexdigest()[:12])
            places.append(
                PlaceCandidate(
                    place_id=f"amap:{poi_id}",
                    name=item.get("name", "未命名地点"),
                    category=_normalize_category(item.get("type")),
                    latitude=lat,
                    longitude=lon,
                    rating=max(0.0, min(5.0, _number(biz_ext.get("rating")))),
                    **_opening_minutes(biz_ext.get("opentime") or item.get("opentime")),
                    average_price=_number(biz_ext.get("cost")),
            **_opening_minutes(biz_ext.get("opentime") or item.get("opentime")),
                    price_known=bool(re.fullmatch(r"\d+(?:\.\d+)?", str(biz_ext.get("cost", "")).strip())),
                    distance_km=round(_distance_km(location, lat, lon), 2),
                    tags=_semantic_tags(
                        item.get("name"), item.get("type"), _normalize_category(item.get("type"))
                    ),
                    source="amap",
                    evidence_ids=[f"amap-poi:{poi_id}"],
                )
            )
            self._cache[places[-1].place_id] = (places[-1], time.monotonic() + 600)
        if not places:
            return [], [
                self._error_evidence(
                    "place/text",
                    empty=True,
                    key=f"{query}:{location.latitude:.5f}:{location.longitude:.5f}",
                )
            ]
        observed_at, expires_at = _observed(10)
        evidence = [
            Evidence(
                evidence_id=f"amap-search:{int(time.time() * 1000)}",
                source="amap",
                source_ref="place/text",
                claim=f"高德地点检索返回 {len(places)} 个候选",
                payload={"query": query, "count": len(places)},
                observed_at=observed_at,
                expires_at=expires_at,
                confidence=0.9,
            )
        ]
        evidence.extend(
            Evidence(
                evidence_id=f"amap-poi:{place.place_id.removeprefix('amap:')}",
                source="amap",
                source_ref="place/text/poi",
                claim=f"高德返回地点：{place.name}",
                payload={
                    "place_id": place.place_id,
                    "name": place.name,
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
        self._search_cache[cache_key] = (list(places), time.monotonic() + 60)
        return places, evidence

    async def get_place(self, place_id: str) -> PlaceCandidate | None:
        cached = self._cached(place_id)
        if cached is not None:
            return cached
        amap_id = place_id.removeprefix("amap:")
        data = await self._get("place/detail", {"id": amap_id})
        items = (data or {}).get("pois") or []
        item = items[0] if items else None
        if not isinstance(item, dict):
            return None
        try:
            lon, lat = (float(x) for x in item["location"].split(",", 1))
        except (AttributeError, KeyError, ValueError, TypeError):
            return None
        biz_ext_raw = item.get("biz_ext")
        biz_ext = cast(dict[str, Any], biz_ext_raw) if isinstance(biz_ext_raw, dict) else {}
        place = PlaceCandidate(
            place_id=f"amap:{item.get('id') or amap_id}",
            name=item.get("name", "未命名地点"),
            category=_normalize_category(item.get("type")),
            latitude=lat,
            longitude=lon,
            average_price=_number(biz_ext.get("cost")),
                    price_known=bool(re.fullmatch(r"\d+(?:\.\d+)?", str(biz_ext.get("cost", "")).strip())),
            tags=_semantic_tags(
                item.get("name"), item.get("type"), _normalize_category(item.get("type"))
            ),
            source="amap",
        )
        self._cache[place.place_id] = (place, time.monotonic() + 600)
        return place

    async def geocode(self, address: str) -> Location | None:
        data = await self._get("geocode/geo", {"address": address or "望京"})
        try:
            location = (data or {}).get("geocodes", [])[0].get("location", "")
            lon, lat = (float(item) for item in location.split(",", 1))
            return Location(
                name=address or "未知地点",
                latitude=lat,
                longitude=lon,
                city_code=(data or {}).get("geocodes", [])[0].get("adcode"),
            )
        except (IndexError, AttributeError, ValueError, TypeError):
            return None

    async def estimate_route(
        self, origin: Location, destination: PlaceCandidate
    ) -> tuple[dict[str, Any], Evidence]:
        data = await self._get(
            "direction/driving",
            {
                "origin": f"{origin.longitude},{origin.latitude}",
                "destination": f"{destination.longitude},{destination.latitude}",
                "strategy": 0,
            },
        )
        try:
            path = (data or {}).get("route", {}).get("paths", [])[0]
            distance_km = round(float(path.get("distance", 0)) / 1000, 2)
            driving_min = max(1, round(float(path.get("duration", 0)) / 60))
            route = {
                "distance_km": distance_km,
                "driving_min": driving_min,
                "transit_min": None,
                "walking_min": None,
                "recommended": "driving",
                "source": "amap",
            }
            observed_at, expires_at = _observed(10)
            route_key = (
                f"{destination.place_id}:{origin.latitude:.5f}:{origin.longitude:.5f}"
            )
            return route, Evidence(
                evidence_id=(
                    f"amap-route:{hashlib.sha1(route_key.encode()).hexdigest()[:12]}"
                ),
                source="amap",
                source_ref="direction/driving",
                claim=f"高德驾车路线约 {distance_km:.1f} km / {driving_min} 分钟",
                payload=route,
                observed_at=observed_at,
                expires_at=expires_at,
                confidence=0.9,
            )
        except (IndexError, AttributeError, ValueError, TypeError):
            evidence = self._error_evidence(
                "direction/driving",
                empty=bool(data),
                key=f"{destination.place_id}:{origin.latitude:.5f}:{origin.longitude:.5f}",
            )
            return (
                {
                    "distance_km": destination.distance_km,
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
        """Return an explicitly simulated supply snapshot.

        Amap's read APIs do not establish verified queue/seat availability;
        this data is therefore never labeled as Amap or real supply.
        """
        key = f"{self.settings.seed}:{place_id}:{at_minute // 30}"
        digest = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        observed_at, expires_at = _observed(5)
        return Supply(
            place_id=place_id,
            open_now=(digest % 17 != 0),
            reservable=(digest % 3 != 0),
            seats_left=digest % 9,
            estimated_wait_min=5 + digest % 46,
            source="simulated",
            observed_at=observed_at,
            expires_at=expires_at,
        )

    async def get_weather(self, location: Location) -> tuple[dict[str, Any], Evidence]:
        data = await self._get(
            "weather/weatherInfo",
            {"city": location.city_code or "110105", "extensions": "base"},
        )
        if not data or not data.get("lives"):
            evidence = self._error_evidence(
                "weather/weatherInfo",
                empty=bool(data),
                key=f"{location.city_code or location.name}",
            )
            weather = {
                "text": "未知",
                "rain": False,
                "source": "amap",
                "error_kind": evidence.payload.get("error_kind"),
            }
            return weather, evidence
        item = data["lives"][0]
        if not isinstance(item, dict):
            evidence = self._error_evidence(
                "weather/weatherInfo", empty=True, key=f"{location.city_code or location.name}"
            )
            return {"text": "未知", "rain": False, "source": "amap", "error_kind": "invalid_response"}, evidence
        weather = {
            "text": item.get("weather", "未知"),
            "rain": "雨" in item.get("weather", ""),
            "temp": int(float(item.get("temperature", 0) or 0)),
            "source": "amap",
        }
        observed_at, expires_at = _observed(10)
        return weather, Evidence(
            evidence_id=f"amap-weather:{int(time.time() * 1000)}",
            source="amap",
            source_ref="weather/weatherInfo",
            claim=f"高德天气：{weather['text']} {weather['temp']}℃",
            payload=weather,
            observed_at=observed_at,
            expires_at=expires_at,
            confidence=0.95,
        )

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
