"""Safe execution configuration and an explicit, bounded text connectivity check."""

import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from plango_harness.persistence.database import agent_run
from sqlalchemy import select


def provider_origin(value):
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname:
            return "invalid"
        host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        return f"{url.scheme}://{host}" + (f":{url.port}" if url.port else "")
    except ValueError:
        return "invalid"


def execution_summary(settings, check=None):
    return {
        "runtime_profile": settings.runtime_profile,
        "model": {
            "name": settings.openai_model,
            "provider_origin": provider_origin(settings.openai_base_url),
            "key_configured": settings.model_enabled,
            "check": check or {"status": "not_checked" if settings.model_enabled and settings.openai_model else "not_configured"},
        },
        "capabilities": {
            "amap_configured": bool(settings.amap_webservice_key),
            "browser_vision_enabled": settings.browser_vision_enabled,
            "browser_strategy": "dom_first",
            "image_input": "model_dependent",
            "transit": "limited",
        },
    }


async def check_model(settings):
    result = {"status": "failed", "checked_at": datetime.now(timezone.utc).isoformat()}
    if not settings.model_enabled or not settings.openai_model:
        return {**result, "status": "not_configured", "category": "missing_key_or_model"}
    try:
        # Diagnostic calls have their own small cap; never mutate any task's budget or model counters.
        async with asyncio.timeout(10), httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                settings.openai_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + settings.openai_api_key},
                json={"model": settings.openai_model, "messages": [{"role": "user", "content": "Reply ok."}], "max_tokens": 8},
            )
            if not response.is_success:
                category = {401: "authentication", 403: "permission", 404: "model_or_endpoint", 429: "rate_limit"}.get(
                    response.status_code, "provider" if response.status_code >= 500 else "request"
                )
                return {**result, "category": category}
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
                return {**result, "category": "invalid_response"}
            choice = data["choices"][0]
            message = choice.get("message") if isinstance(choice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                return {**result, "category": "invalid_response"}
            usage = (data.get("usage") or {}).get("total_tokens")
            return {**result, "status": "passed", **({"total_tokens": usage} if type(usage) is int and usage >= 0 else {})}
    except (TimeoutError, httpx.TimeoutException):
        return {**result, "category": "timeout"}
    except httpx.TransportError:
        return {**result, "category": "connection"}
    except Exception:
        # Provider bodies, network exceptions and credential-bearing URLs never enter the renderer.
        return {**result, "category": "invalid_response"}


def install_health_routes(app, runtime, protected):
    settings = runtime.settings
    last_check = None
    checking = False

    @app.get("/api/v1/health/ready", dependencies=protected)
    async def execution_ready():
        if not runtime._started or not await runtime.database.ping():
            raise HTTPException(503, "backend not ready")
        summary = execution_summary(settings, last_check)
        # ponytail: inspect the latest 10 task summaries; older history remains in its task, no new registry.
        async with runtime.database.session() as session:
            rows = (await session.execute(select(
                agent_run.c.run_id, agent_run.c.updated_at, agent_run.c.state_json["model_calls"].label("calls")
            ).where(agent_run.c.user_id == "desktop").order_by(agent_run.c.updated_at.desc()).limit(10))).mappings()
            for row in rows:
                calls = row["calls"] or []
                if calls:
                    call = calls[-1]
                    summary["recent_task_model"] = {"run_id": row["run_id"], "name": call.get("model", ""),
                                                    "status": call.get("status", "unknown"), "recorded_at": row["updated_at"].isoformat()}
                    break
        return {"ready": True, "status": "ready", "input_delivery_version": 1, "world_provider": "browser", "runtime_profile": settings.runtime_profile,
                "model_enabled": settings.model_enabled, "execution": summary}

    @app.post("/api/v1/health/model-check", dependencies=protected)
    async def model_check():
        nonlocal last_check, checking
        if checking:
            raise HTTPException(409, "model check already running")
        checking = True
        try:
            last_check = await check_model(settings)
            return last_check
        finally:
            checking = False
