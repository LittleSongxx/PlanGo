"""Planora HTTP entry point.

The API is deliberately thin: PostgreSQL-backed run events and projections are
the query surface, while the LangGraph Worker owns execution/checkpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from planora.agent.contracts import RunPhase
from planora.runtime import PlanoraRuntime
from planora.settings import Settings
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger("planora")

TERMINAL_PHASE_VALUES = {
    RunPhase.SUCCEEDED.value,
    RunPhase.PARTIAL_FAILED.value,
    RunPhase.FAILED.value,
    RunPhase.CANCELLED.value,
    RunPhase.INFEASIBLE.value,
}


def _event_stream(
    runtime: PlanoraRuntime, run_id: str, request: Request, after: int
) -> StreamingResponse:
    async def generator() -> AsyncIterator[str]:
        cursor = after
        terminal_idle = 0
        while not await request.is_disconnected():
            events = await runtime.get_events(run_id, after=cursor)
            for event in events:
                cursor = max(cursor, int(event["seq"]))
                # The payload carries event_type; using the default SSE
                # message event keeps generic EventSource clients compatible.
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            snapshot = await runtime.get_run(run_id)
            if snapshot and snapshot.get("phase") in TERMINAL_PHASE_VALUES:
                terminal_idle += 1
                if terminal_idle >= 2 and not events:
                    break
            else:
                terminal_idle = 0
            await asyncio.sleep(0.4)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class CreateRunRequest(BaseModel):
    user_id: str = Field(default="anonymous", min_length=1, max_length=128)
    input_text: str | None = Field(default=None, min_length=1, max_length=4000)
    message: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_input(self):
        if not (self.input_text or self.message):
            raise ValueError("input_text is required")
        return self


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ReplanRequest(BaseModel):
    reason: str = Field(default="世界状态发生变化", max_length=4000)


class ResumeRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject|edit|resume)$")
    text: str = Field(default="", max_length=4000)


class AcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    phase: str
    outcome: str | None = None
    event_seq: int = 0
    accepted: bool = True
    queued: bool | None = None
    interrupt_id: str | None = None


class RunSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    thread_id: str
    user_id: str
    input_text: str
    phase: str
    outcome: str | None = None
    event_seq: int = 0
    interrupt_id: str | None = None
    version: int | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    cancel_requested: bool = False
    command_pending: bool = False
    updated_at: str | None = None


class EventResponse(BaseModel):
    seq: int
    event_type: str
    phase: str
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class EventReplayResponse(BaseModel):
    run_id: str
    phase: str
    outcome: str | None = None
    event_seq: int = 0
    events: list[EventResponse] = Field(default_factory=list)
    after: int = 0


class HealthResponse(BaseModel):
    status: str
    service: str | None = None
    database_ok: bool | None = None
    database: str | None = None
    vector: bool | None = None
    lexical: bool | None = None
    redis: bool | None = None
    model: bool | None = None
    world: str | None = None


class DocumentIngestRequest(BaseModel):
    user_id: str = Field(default="anonymous", min_length=1, max_length=128)
    namespace: str = Field(default="user", min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    source_version: str = Field(default="1", min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=200_000)
    max_chars: int = Field(default=800, ge=32, le=10_000)
    overlap: int = Field(default=120, ge=0, le=2_000)

    @model_validator(mode="after")
    def validate_chunk_limits(self):
        if self.overlap >= self.max_chars:
            raise ValueError("overlap must be smaller than max_chars")
        return self


class DocumentIngestResponse(BaseModel):
    source_id: str
    source_version: str
    chunk_ids: list[str]


class MemorySearchResponse(BaseModel):
    user_id: str
    query: str
    items: list[dict[str, Any]] = Field(default_factory=list)


class ActionResolutionRequest(BaseModel):
    status: str = Field(pattern="^(UNKNOWN|SUCCEEDED|FAILED|CANCELLED)$")
    result: dict[str, Any] = Field(default_factory=dict)


class HistoryResponse(BaseModel):
    run_id: str
    items: list[dict[str, Any]] = Field(default_factory=list)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    runtime = PlanoraRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="Planora — Multi-Agent Local-Life Planner",
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_runtime() -> PlanoraRuntime:
        current = app.state.runtime
        if not current._started:
            raise HTTPException(status_code=503, detail="runtime_not_started")
        return current

    @app.exception_handler(KeyError)
    async def missing_resource(_request: Request, exc: KeyError):
        return JSONResponse({"error": "not_found", "detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_request(_request: Request, exc: ValueError):
        return JSONResponse({"error": "invalid_request", "detail": str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception):
        logger.exception("unhandled Planora request error", exc_info=exc)
        return JSONResponse(
            {"error": "internal_error", "detail": type(exc).__name__}, status_code=500
        )

    @app.post("/api/v1/runs", status_code=202, response_model=AcceptedResponse)
    async def create_run(body: CreateRunRequest):
        return await get_runtime().create_run(body.user_id, body.input_text or body.message or "")

    @app.post(
        "/api/v1/runs/{run_id}/messages", status_code=202, response_model=AcceptedResponse
    )
    async def send_message(run_id: str, body: MessageRequest):
        return await get_runtime().send_message(run_id, body.text)

    @app.get("/api/v1/runs/{run_id}", response_model=RunSnapshotResponse)
    async def get_run(run_id: str):
        result = await get_runtime().get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        return result

    @app.get("/api/v1/runs/{run_id}/events", response_model=EventReplayResponse)
    async def get_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
    ):
        result = await get_runtime().get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        runtime = get_runtime()
        return {
            "run_id": run_id,
            "phase": result.get("phase"),
            "outcome": result.get("outcome"),
            "event_seq": result.get("event_seq", 0),
            "events": await runtime.get_events(run_id, after=after),
            "after": after,
        }

    @app.post(
        "/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume",
        status_code=202,
        response_model=AcceptedResponse,
    )
    async def resume_interrupt(run_id: str, interrupt_id: str, body: ResumeRequest):
        # LangGraph owns the opaque interrupt checkpoint. The URL id is kept
        # for a stable public contract and is echoed in the audit event.
        result = await get_runtime().resume(
            run_id,
            body.decision,
            body.text,
            wait=False,
            interrupt_id=interrupt_id,
        )
        result["interrupt_id"] = interrupt_id
        return result

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunSnapshotResponse)
    async def cancel_run(run_id: str):
        return await get_runtime().cancel(run_id)

    @app.get("/api/v1/runs/{run_id}/plans", response_model=HistoryResponse)
    async def list_plans(run_id: str):
        runtime = get_runtime()
        if await runtime.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        return {"run_id": run_id, "items": await runtime.runs.plans(run_id)}

    @app.get("/api/v1/runs/{run_id}/actions", response_model=HistoryResponse)
    async def list_actions(run_id: str):
        runtime = get_runtime()
        if await runtime.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        return {"run_id": run_id, "items": await runtime.runs.actions(run_id)}

    @app.post("/api/v1/runs/{run_id}/actions/{action_id}/resolve")
    async def resolve_action(
        run_id: str, action_id: str, body: ActionResolutionRequest
    ):
        return await get_runtime().resolve_action(
            run_id, action_id, body.status, body.result
        )

    @app.post(
        "/api/v1/runs/{run_id}/replan", status_code=202, response_model=RunSnapshotResponse
    )
    async def replan_run(run_id: str, body: ReplanRequest):
        return await get_runtime().replan(run_id, body.reason)

    @app.get("/api/v1/health/live", response_model=HealthResponse)
    async def health_live():
        return {"status": "ok", "service": "planora"}

    @app.get("/api/v1/health/ready", response_model=HealthResponse)
    async def health_ready():
        rt = get_runtime()
        database_kind = "sqlite" if rt.database.url.startswith("sqlite") else "postgresql"
        database_ok = await rt.database.ping()
        redis_ok = await rt.queue.ping()
        lexical_ok = getattr(rt.database, "lexical_available", True)
        return {
            "status": "ok"
            if database_ok and redis_ok and lexical_ok and not rt.database.degraded
            else "degraded",
            "database_ok": database_ok,
            "database": "fallback-sqlite" if rt.database.degraded else database_kind,
            "vector": rt.database.vector_available,
            "lexical": lexical_ok,
            "redis": redis_ok,
            "model": rt.model.available,
            "world": type(rt.world_service.provider).__name__ if rt.world_service else None,
        }

    @app.post(
        "/api/v1/memory/documents",
        status_code=201,
        response_model=DocumentIngestResponse,
    )
    async def ingest_memory_document(body: DocumentIngestRequest):
        runtime = get_runtime()
        chunk_ids = await runtime.memory.ingest_document(
            user_id=body.user_id,
            namespace=body.namespace,
            source_id=body.source_id,
            source_version=body.source_version,
            content=body.content,
            max_chars=body.max_chars,
            overlap=body.overlap,
        )
        await runtime.enqueue_embedding(body.source_id)
        return {
            "source_id": body.source_id,
            "source_version": body.source_version,
            "chunk_ids": chunk_ids,
        }

    @app.get("/api/v1/memory/search", response_model=MemorySearchResponse)
    async def search_memory(
        user_id: str = Query(default="anonymous", min_length=1, max_length=128),
        query: str = Query(default="", max_length=4000),
        namespace: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=8, ge=1, le=50),
    ):
        runtime = get_runtime()
        return {
            "user_id": user_id,
            "query": query,
            "items": await runtime.memory.retrieve(
                user_id, query, limit=limit, namespace=namespace
            ),
        }

    @app.delete("/api/v1/memory/documents/{source_id}")
    async def forget_memory_document(
        source_id: str,
        user_id: str = Query(default="anonymous", min_length=1, max_length=128),
        namespace: str = Query(default="user", min_length=1, max_length=128),
    ):
        count = await get_runtime().memory.forget_document(user_id, source_id, namespace)
        return {"source_id": source_id, "deleted": count}

    @app.get("/api/v1/runs/{run_id}/events/stream")
    async def stream_events(run_id: str, request: Request, after: int = Query(default=0, ge=0)):
        # SSE is a separate transport endpoint over the same durable rows.
        if await get_runtime().get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run_not_found")

        return _event_stream(get_runtime(), run_id, request, after)

    # Serve the compiled Vue app when present; a source fallback keeps the
    # backend useful before ``npm run build``.
    root = Path(__file__).resolve().parents[1]
    frontend_dist = root / "frontend" / "dist"
    frontend_source = root / "frontend" / "index.html"
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/")
    async def index():
        path = (
            frontend_dist / "index.html"
            if (frontend_dist / "index.html").exists()
            else frontend_source
        )
        if path.exists():
            return FileResponse(path)
        return {"service": "planora", "docs": "/docs"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=Settings().host, port=Settings().port, reload=False)
