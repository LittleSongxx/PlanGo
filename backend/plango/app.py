from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from plango_harness.agent.contracts import MemoryProposal
from plango_harness.persistence.database import memory_episode, memory_fact
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select, update

from .browser import Observation, bindings
from .geo import install_geo_routes
from .health import install_health_routes
from .location import LocationContext
from .reminders import install_reminder_routes, setup_reminders
from .requirements import MerchantCandidatesRequest, OfferSelection, RequirementEdit
from .runtime import DesktopRuntime, _utc
from .settings import settings_from_env


def validate_image(value):
    if value is None:
        return value
    if not re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+", value):
        raise ValueError("image_requires_png_jpeg_webp_data_url")
    try:
        decoded = base64.b64decode(value.split(",", 1)[1], validate=True)
    except ValueError:
        raise ValueError("invalid_image_base64") from None
    if not decoded or len(decoded) > 8_000_000:
        raise ValueError("image_exceeds_8mb")
    return value


class SelectedPoi(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    poi_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(default="", max_length=1000)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)
    source: Literal["amap"] = "amap"
    observed_at: AwareDatetime | None = None


class CreateRun(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    request_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    selected_poi: SelectedPoi | None = None
    location_context: LocationContext | None = None
    user_id: str = Field(default="desktop", min_length=1, max_length=128)
    input_text: str = Field(min_length=1, max_length=12000)
    browser_session_id: str = Field(min_length=1, max_length=128)
    enabled_skills: list[str] | None = Field(default=None, max_length=64)
    image: str | None = Field(default=None, max_length=12000000)
    _image = field_validator("image")(validate_image)


class Message(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    request_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    location_context: LocationContext | None = None
    text: str = Field(min_length=1, max_length=12000)
    image: str | None = Field(default=None, max_length=12000000)
    _image = field_validator("image")(validate_image)


class Resume(BaseModel):
    decision: Literal["approve", "reject", "edit", "resume"]
    text: str = ""
    interrupt_id: str | None = None


class ResolveAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["SUCCEEDED", "FAILED"]
    note: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=200)


class SelectPlan(BaseModel):
    plan_id: str
    plan_version: int = Field(ge=1)


class DraftDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["save", "prepare"]
    interrupt_id: str = Field(min_length=1, max_length=256)
    plan_id: str = Field(min_length=1, max_length=128)
    plan_version: int = Field(ge=1, strict=True)


class PreparationResume(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    plan_id: str = Field(min_length=1, max_length=128)
    plan_version: int = Field(ge=1, strict=True)
    approval_id: str = Field(min_length=1, max_length=256)


class Replan(BaseModel):
    location_context: LocationContext | None = None
    reason: str = "按原需求重新观测并规划"


class Preference(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    polarity: Literal["like", "dislike"] = "like"
    user_id: str = "desktop"


class FeedbackPreference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=1000)
    polarity: Literal["positive", "negative"] = "positive"


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    feedback_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    turn_id: int = Field(ge=1, strict=True)
    rating: Literal["helpful", "unhelpful"]
    text: str = Field(default="", max_length=1000)
    preference: FeedbackPreference | None = None


class Favorite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    poiId: str | None = None
    user_id: str = "desktop"


def create_app(settings=None, *, token=None):
    settings = settings or settings_from_env()
    token = token or os.environ.get("PLANGO_BACKEND_TOKEN", "")
    if not token:
        raise ValueError("PLANGO_BACKEND_TOKEN is required; no unauthenticated desktop control API")
    runtime = DesktopRuntime(settings)

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        await setup_reminders(runtime)
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(title="PlanGo Harness", lifespan=lifespan)
    app.state.runtime = runtime

    async def auth(authorization: str | None = Header(default=None)):
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "invalid backend credential")

    protected = [Depends(auth)]
    install_geo_routes(app, runtime, protected)
    install_health_routes(app, runtime, protected)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": "not found"})

    @app.get("/api/v1/health/live", dependencies=protected)
    @app.get("/health/live")
    async def live():
        return {"status": "ok", "app": "PlanGo", "world_provider": "browser"}

    @app.get("/health/ready")
    async def ready():
        ok = runtime._started and await runtime.database.ping()
        if not ok:
            raise HTTPException(503, "backend not ready")
        return {
            "ready": True,
            "status": "ready",
            "runtime_profile": settings.runtime_profile,
            "world_provider": "browser",
            "model_enabled": settings.model_enabled,
        }

    @app.post("/api/v1/runs", dependencies=protected, status_code=202)
    async def create(body: CreateRun):
        return await runtime.create_run(
            body.user_id,
            body.input_text,
            body.browser_session_id,
            body.image,
            body.enabled_skills,
            body.location_context.model_dump(mode="json") if body.location_context else None,
            selected_poi=body.selected_poi.model_dump(mode="json") if body.selected_poi else None,
            request_id=body.request_id,
            request_fingerprint=body.request_fingerprint,
        )

    @app.get("/api/v1/requests/{request_id}", dependencies=protected)
    async def accepted_input(request_id: str):
        receipt = await runtime.runs.accepted_input(request_id)
        if receipt is None:
            raise HTTPException(404, "request not accepted")
        return receipt

    @app.get("/api/v1/runs", dependencies=protected)
    async def history(user_id: str = "desktop"):
        return {"runs": await runtime.history(user_id)}

    @app.get("/api/v1/runs/{run_id}", dependencies=protected)
    @app.get("/api/v1/runs/{run_id}/snapshot", dependencies=protected)
    async def snapshot(run_id: str):
        value = await runtime.get_run(run_id)
        if not value:
            raise HTTPException(404, "run not found")
        return value

    @app.post("/api/v1/runs/{run_id}/messages", dependencies=protected, status_code=202)
    async def message(run_id: str, body: Message):
        return await runtime.accept_message(
            run_id, body.text, image=body.image, request_id=body.request_id, request_fingerprint=body.request_fingerprint,
            location_context=body.location_context.model_dump(mode="json") if body.location_context else None,
        )

    @app.post("/api/v1/runs/{run_id}/resume", dependencies=protected, status_code=202)
    async def resume(run_id: str, body: Resume):
        return await runtime.enqueue_resume(
            run_id, body.decision, body.text, interrupt_id=body.interrupt_id
        )

    @app.post("/api/v1/runs/{run_id}/requirements", dependencies=protected, status_code=202)
    async def edit_requirements(run_id: str, body: RequirementEdit):
        return await runtime.edit_requirements(run_id, body)

    @app.post("/api/v1/runs/{run_id}/merchant-candidates", dependencies=protected)
    async def merchant_candidates(run_id: str, body: MerchantCandidatesRequest):
        return await runtime.merchant_candidates(run_id, body.source_ref)

    @app.post("/api/v1/runs/{run_id}/offer-selection", dependencies=protected, status_code=202)
    async def select_offer(run_id: str, body: OfferSelection):
        return await runtime.select_offer(run_id, body)

    @app.post(
        "/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume",
        dependencies=protected,
        status_code=202,
    )
    async def resume_interrupt(run_id: str, interrupt_id: str, body: Resume):
        return await runtime.enqueue_resume(
            run_id, body.decision, body.text, interrupt_id=interrupt_id
        )

    @app.post("/api/v1/runs/{run_id}/replan", dependencies=protected, status_code=202)
    async def replan(run_id: str, body: Replan):
        await runtime.bridge.update_location(
            run_id, body.location_context.model_dump(mode="json") if body.location_context else None
        )
        async with runtime.database.session() as session:
            async with session.begin():
                await session.execute(
                    update(bindings).where(bindings.c.run_id == run_id).values(input_image=None)
                )
        return await runtime.replan(run_id, body.reason)

    @app.post("/api/v1/runs/{run_id}/actions/{action_id}/resolve", dependencies=protected)
    async def resolve_action(run_id: str, action_id: str, body: ResolveAction):
        return await runtime.resolve_user_action(
            run_id, action_id, body.status, body.note, body.reference
        )

    @app.post("/api/v1/runs/{run_id}/plans/select", dependencies=protected, status_code=202)
    async def select_plan(run_id: str, body: SelectPlan):
        return await runtime.select_plan(run_id, body.plan_id, body.plan_version)

    @app.post("/api/v1/runs/{run_id}/draft-decision", dependencies=protected, status_code=202)
    async def decide_draft(run_id: str, body: DraftDecision):
        return await runtime.decide_draft(run_id, body.decision, body.interrupt_id, body.plan_id, body.plan_version)

    @app.post("/api/v1/runs/{run_id}/preparation/resume", dependencies=protected, status_code=202)
    async def resume_preparation(run_id: str, body: PreparationResume):
        return await runtime.resume_preparation(run_id, body.plan_id, body.plan_version, body.approval_id)

    @app.post("/api/v1/runs/{run_id}/cancel", dependencies=protected)
    async def cancel(run_id: str):
        return await runtime.cancel(run_id)

    @app.get("/api/v1/runs/{run_id}/events", dependencies=protected)
    async def events(run_id: str, request: Request, after: int = 0):
        if not await runtime.runs.get(run_id):
            raise HTTPException(404, "run not found")
        if "text/event-stream" not in request.headers.get("accept", ""):
            return {"events": await runtime.get_events(run_id, after)}

        async def stream():
            cursor = max(after, int(request.headers.get("last-event-id", "0") or 0))
            while not await request.is_disconnected():
                rows = await runtime.get_events(run_id, cursor)
                for event in rows:
                    cursor = event["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/v1/browser/commands", dependencies=protected)
    async def poll(browser_session_id: str = Query(min_length=1, max_length=128), after: int = 0):
        return await runtime.bridge.poll(browser_session_id, after)

    @app.post("/api/v1/browser/commands/{command_id}/result", dependencies=protected)
    async def observation(command_id: str, body: Observation):
        return await runtime.bridge.accept(command_id, body)

    @app.get("/api/v1/runs/{run_id}/feedback", dependencies=protected)
    async def feedback_history(run_id: str):
        return {"feedback": await runtime.feedback(run_id)}

    @app.post("/api/v1/runs/{run_id}/feedback", dependencies=protected)
    async def save_feedback(run_id: str, body: Feedback):
        return await runtime.save_feedback(run_id, body.model_dump(mode="json"))

    @app.delete("/api/v1/memory/episodes/{episode_id}", dependencies=protected)
    async def forget_episode(episode_id: str, user_id: str = "desktop"):
        await runtime.memory.forget_episode(user_id, episode_id)
        return await profile(user_id)

    @app.get("/api/v1/memory/profile", dependencies=protected)
    async def profile(user_id: str = "desktop"):
        async with runtime.database.session() as session:
            facts = (
                (
                    await session.execute(
                        select(memory_fact).where(
                            memory_fact.c.user_id == user_id,
                            or_(memory_fact.c.valid_from.is_(None), memory_fact.c.valid_from <= datetime.now(timezone.utc)),
                            or_(memory_fact.c.valid_to.is_(None), memory_fact.c.valid_to > datetime.now(timezone.utc))
                        )
                    )
                )
                .mappings()
                .all()
            )
            episodes = (
                (
                    await session.execute(
                        select(memory_episode)
                        .where(memory_episode.c.user_id == user_id, or_(memory_episode.c.valid_until.is_(None), memory_episode.c.valid_until > datetime.now(timezone.utc)))
                        .order_by(memory_episode.c.created_at.desc())
                        .limit(50)
                    )
                )
                .mappings()
                .all()
            )
        preferences = []
        favorites = []
        for fact in facts:
            value = fact["value_json"] or {}
            provenance = {"source": fact["source"], "explicit": str(fact["source"]).startswith(("user:", "user-confirmed:", "user-correction:", "explicit:"))}
            if fact["fact_key"].startswith("preference:"):
                preferences.append({"id": fact["id"], **value, **provenance})
            if fact["fact_key"].startswith("favorite:"):
                favorites.append({"id": fact["id"], **value, **provenance})
        return {
            "preferences": preferences,
            "favorites": favorites,
            "summaries": [
                {"id": r["id"], "text": r["summary"], "createdAt": _utc(r["created_at"]).isoformat(), "scope": (r["payload_json"] or {}).get("scope")}
                for r in episodes
            ],
            "facts": [
                {
                    "id": r["id"],
                    "key": r["fact_key"],
                    "value": r["value_json"],
                    "source": r["source"],
                }
                for r in facts
            ],
        }

    async def remember(user_id, key, value, operation="upsert"):
        return await runtime.memory.commit(
            user_id,
            [
                MemoryProposal(
                    kind="fact",
                    key=key,
                    value=value,
                    operation=operation,
                    source_event_id="user:" + uuid.uuid4().hex,
                    confidence=1,
                    rationale="用户明确编辑",
                )
            ],
        )

    @app.post("/api/v1/memory/preferences", dependencies=protected)
    async def preference(body: Preference):
        await remember(
            body.user_id, "preference:" + body.text, {"text": body.text, "polarity": body.polarity}
        )
        return await profile(body.user_id)

    @app.delete("/api/v1/memory/preferences", dependencies=protected)
    async def forget_preference(text: str, user_id: str = "desktop"):
        await remember(user_id, "preference:" + text, {}, "forget")
        return await profile(user_id)

    @app.post("/api/v1/memory/favorites", dependencies=protected)
    async def favorite(body: Favorite):
        await remember(
            body.user_id, "favorite:" + body.name, {"name": body.name, "poiId": body.poiId}
        )
        return await profile(body.user_id)

    @app.delete("/api/v1/memory/favorites", dependencies=protected)
    async def unfavorite(name: str, user_id: str = "desktop"):
        await remember(user_id, "favorite:" + name, {}, "forget")
        return await profile(user_id)

    @app.delete("/api/v1/memory/profile", dependencies=protected)
    async def clear_profile(user_id: str = "desktop"):
        await runtime.memory.forget_user(user_id)
        return await profile(user_id)

    install_reminder_routes(app, runtime, auth)
    return app


def main():
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("PLANGO_BACKEND_HOST", "127.0.0.1"),
        port=int(os.environ.get("PLANGO_BACKEND_PORT", "8011")),
    )


if __name__ == "__main__":
    main()
