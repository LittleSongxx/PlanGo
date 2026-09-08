#!/usr/bin/env python3
"""Bounded, read-only live checks using only this checkout's ignored .env.

Run: .venv/bin/python scripts/check_live_services.py
Two model requests at most (one structured request and its optional repair), one
public Amap geocode, no browser execution or business side effects. The model
input is synthetic test data. Reports never contain keys, prompts or raw replies.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import re
import sys
import time
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "vendor/planora/backend")]

from dotenv import dotenv_values  # noqa: E402
from planora.agent.model_adapter import (  # noqa: E402
    ACCOUNT_ERRORS,
    ModelAdapter,
    ModelProviderUnavailable,
)
from planora.providers.world import AmapWorldProvider  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402
from yoyu.settings import DesktopSettings  # noqa: E402


class MenuItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    price: float | None = Field(..., ge=0)
    quote: str


class MenuProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MenuItem] = Field(min_length=2, max_length=2)


class ProbeStop(BaseException):
    """A probe quota/auth stop must bypass the adapter's normal repair catch."""

    def __init__(self, kind, error_class, http_status=None):
        self.kind = kind
        self.error_class = error_class
        self.http_status = http_status


PUBLIC_ERROR_CODES = ACCOUNT_ERRORS | {
    "invalid_api_key",
    "authentication_error",
    "permission_denied",
    "rate_limit_exceeded",
    "quota_exceeded",
    "context_length_exceeded",
    "invalid_request_error",
    "invalid_json_schema",
    "model_not_found",
    "Unauthorized",
    "Forbidden",
    "TooManyRequests",
    "RateLimitExceeded",
}


def provider_code(error):
    code = getattr(error, "code", None)
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict):
            code = inner.get("code", code)
    return code if isinstance(code, str) else ""


def account_or_quota(error):
    code = provider_code(error)
    return (
        getattr(error, "status_code", None) in {401, 402, 403, 429}
        or code in ACCOUNT_ERRORS
        or bool(re.search(r"quota|arrear|balance|ratelimit|rate_limit", code, re.I))
    )


def safe_error(error):
    result = {"error_class": type(error).__name__}
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        result["http_status"] = status
    code = provider_code(error)
    if code:
        result["provider_error_code"] = (
            code if code in PUBLIC_ERROR_CODES else "unrecognized_provider_code"
        )
    return result


async def check_model(settings):
    report = {
        "model": settings.openai_model,
        "provider_available": False,
        "extraction_correct": False,
        "model_requests": 0,
        "status": "not_run",
    }
    if not settings.openai_api_key:
        return {**report, "status": "missing_key"}, False
    adapter = ModelAdapter(settings)
    adapter.set_run_budget(time.time() + 45, cleanup_reserve_seconds=2)
    invoke = adapter._invoke
    attempts = 0

    async def bounded_invoke(awaitable, *, timeout):
        nonlocal attempts
        if attempts >= 2:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise ProbeStop("request_limit", "ProbeRequestLimit")
        attempts += 1
        try:
            return await invoke(awaitable, timeout=timeout)
        except Exception as error:
            detail = safe_error(error)
            if account_or_quota(error):
                raise ProbeStop(
                    "account_or_quota", detail["error_class"], detail.get("http_status")
                ) from None
            raise

    adapter._invoke = bounded_invoke
    fallback = MenuProbe(
        items=[MenuItem(name="__fallback__", price=None, quote="") for _ in range(2)]
    )
    expected = [
        ("番茄牛腩饭", 28.0, "番茄牛腩饭：28元"),
        ("香菇青菜面", None, "香菇青菜面：价格未公布"),
    ]
    started = time.perf_counter()
    halted = False
    try:
        result = await adapter.structured(
            MenuProbe,
            system="按原顺序提取下面两条合成菜单记录。name保留菜名，price只填明确金额，未公布填null；quote逐字引用该条原文。必须包含两个items及所有必需字段，不推测价格。",
            user="公开合成测试数据，不代表任何真实商家：\n番茄牛腩饭：28元\n香菇青菜面：价格未公布",
            fallback=fallback,
        )
        native = result is not fallback
        checks = {
            "provider_output_validated": native,
            "names_and_order_correct": native
            and [v.name for v in result.items] == [v[0] for v in expected],
            "known_price_correct": native and result.items[0].price == 28,
            "missing_price_is_unknown": native and result.items[1].price is None,
            "quotes_exactly_grounded": native
            and [v.quote for v in result.items] == [v[2] for v in expected],
        }
        report.update(
            provider_available=native and attempts > 0,
            checks=checks,
            extraction_correct=all(checks.values()),
            status="completed" if native else "fallback",
        )
    except ProbeStop as error:
        halted = error.kind == "account_or_quota"
        report.update(status="stopped", stop_reason=error.kind, error_class=error.error_class)
        if error.http_status is not None:
            report["http_status"] = error.http_status
    except ModelProviderUnavailable as error:
        halted = True
        report.update(status="stopped", stop_reason="account_or_quota", **safe_error(error))
    except Exception as error:
        report.update(status="failed", **safe_error(error))
    finally:
        report.update(
            model_requests=attempts,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage={"total_tokens": adapter.total_tokens, "last_usage": adapter.last_usage},
            adapter_call_count=adapter.call_count,
            fallback_count=adapter.fallback_count,
            calls=[
                {
                    **r,
                    **(
                        {
                            "provider_error_code": r["provider_error_code"]
                            if r["provider_error_code"] in PUBLIC_ERROR_CODES
                            else "unrecognized_provider_code"
                        }
                        if r.get("provider_error_code")
                        else {}
                    ),
                }
                for r in adapter.call_records
            ],
            prompt_version=adapter.metadata.prompt_version,
        )
        await adapter.close()
    return report, halted


async def check_amap(settings):
    report = {
        "provider_available": False,
        "location_correct": False,
        "requests": 0,
        "status": "not_run",
    }
    if not settings.amap_webservice_key:
        return {**report, "status": "missing_key"}
    provider = AmapWorldProvider(settings)
    started = time.perf_counter()
    try:
        report["requests"] = 1
        location = await provider.geocode("上海市东方明珠广播电视塔")
        if location is None:
            report.update(status="failed", error_kind=provider.last_error_kind or "no_location")
        else:
            finite = math.isfinite(location.latitude) and math.isfinite(location.longitude)
            shanghai_code = bool(location.city_code and location.city_code.startswith("31"))
            public_area = 120 < location.longitude < 123 and 30 < location.latitude < 33
            checks = {
                "finite_coordinates": finite,
                "shanghai_city_code": shanghai_code,
                "public_landmark_region": public_area,
            }
            report.update(
                provider_available=True,
                location_correct=all(checks.values()),
                checks=checks,
                status="completed",
            )
    except Exception as error:
        report.update(status="failed", **safe_error(error))
    finally:
        report["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        await provider.close()
    return report


async def main():
    # Never inherit another project's shell/database/provider settings.
    values = dotenv_values(ROOT / ".env")
    explicit = {
        field: values[env]
        for field, env in {
            "openai_api_key": "OPENAI_API_KEY",
            "openai_base_url": "OPENAI_BASE_URL",
            "openai_model": "OPENAI_MODEL",
            "amap_webservice_key": "AMAP_WEBSERVICE_KEY",
        }.items()
        if values.get(env)
    }
    settings = DesktopSettings.model_validate(
        {
            **explicit,
            "openai_max_retries": 0,
            "openai_timeout_seconds": 20,
            "amap_timeout_seconds": 8,
            "max_model_tokens": 4000,
        }
    )
    created = datetime.now(timezone.utc)
    with warnings.catch_warnings(record=True) as captured:
        model, halted = await check_model(settings)
        amap = (
            {
                "status": "skipped_after_model_account_or_quota",
                "provider_available": False,
                "location_correct": False,
                "requests": 0,
            }
            if halted
            else await check_amap(settings)
        )
    report = {
        "created_at": created.isoformat(),
        "purpose": "live_read_only_service_probe",
        "input_kind": "public_synthetic_menu_and_public_shanghai_landmark",
        "limits": {
            "max_model_requests": 2,
            "sdk_retries": 0,
            "max_amap_requests": 1,
            "max_model_tokens": 4000,
            "model_timeout_seconds": 20,
            "amap_timeout_seconds": 8,
        },
        "model": model,
        "amap": amap,
        "warning_classes": sorted({type(w.message).__name__ for w in captured}),
    }
    directory = ROOT / "eval"
    directory.mkdir(exist_ok=True)
    path = (
        directory / f"live_service_{created.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}.json"
    )
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "report": str(path),
                "model_available": model["provider_available"],
                "extraction_correct": model["extraction_correct"],
                "model_requests": model["model_requests"],
                "model_tokens": model.get("usage", {}).get("total_tokens", 0),
                "amap_available": amap["provider_available"],
                "location_correct": amap["location_correct"],
                "amap_requests": amap["requests"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if model["extraction_correct"] and amap["location_correct"] else 1


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    raise SystemExit(asyncio.run(main()))
