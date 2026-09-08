from __future__ import annotations

import hashlib
import json
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from langgraph.config import get_config
from langgraph.types import interrupt
from planora.agent.contracts import RunPhase
from planora.persistence.database import metadata
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import JSON, Column, Float, Integer, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

run_context: ContextVar[dict[str, Any]] = ContextVar("yoyu_browser_run")

bindings = Table(
    "yoyu_browser_binding",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("browser_session_id", String(128), nullable=False),
    Column("input_image", String),
    Column("generation", Integer, nullable=False, default=0),
    Column("enabled_skills", JSON),
    Column("location_context", JSON),
)
commands = Table(
    "yoyu_browser_command",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("command_id", String(64), unique=True, nullable=False),
    Column("run_id", String(64), nullable=False, index=True),
    Column("browser_session_id", String(128), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
    Column("result", JSON),
    Column("created_at", Float, nullable=False),
)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=1, max_length=64)
    browser_session_id: str = Field(min_length=1, max_length=128)
    ok: bool
    outcome: Literal["observed", "executed", "blocked", "unknown", "failed"]
    error_kind: str | None = None
    error: str | None = None
    snapshot_id: str | None = None
    tab_id: str | None = None
    url: str | None = None
    title: str | None = Field(default=None, max_length=4000)
    text: str | None = Field(default=None, max_length=100000)
    elements: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    tables: list[Any] | None = Field(default=None, max_length=100)
    fields: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    observed_at: str | None = None

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        if value and urlsplit(value).scheme not in {"http", "https"}:
            raise ValueError("browser evidence must use an HTTP(S) source")
        return value


class BrowserBridge:
    def __init__(self, runtime):
        self.runtime = runtime
        self.database = runtime.database

    async def bind(
        self, run_id, browser_session_id, image=None, enabled_skills=None, location_context=None
    ):
        async with self.database.session() as session:
            async with session.begin():
                await session.execute(
                    insert(bindings).values(
                        run_id=run_id,
                        browser_session_id=browser_session_id,
                        input_image=image,
                        enabled_skills=enabled_skills,
                        location_context=location_context,
                    )
                )

    async def binding(self, run_id):
        async with self.database.session() as session:
            row = (
                (await session.execute(select(bindings).where(bindings.c.run_id == run_id)))
                .mappings()
                .first()
            )
        if not row:
            raise ValueError("browser_session_not_bound")
        return dict(row)

    async def update_location(self, run_id, context):
        if context is None:
            return
        await self.binding(run_id)
        async with self.database.session() as session:
            async with session.begin():
                await session.execute(
                    update(bindings)
                    .where(bindings.c.run_id == run_id)
                    .values(location_context=context)
                )

    async def get(self, command_id):
        async with self.database.session() as session:
            row = (
                (await session.execute(select(commands).where(commands.c.command_id == command_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def request(
        self,
        operation,
        arguments=None,
        *,
        approved_action_id=None,
        expected_snapshot_id=None,
        tab_id=None,
        slot=None,
        expires_at=None,
    ):
        context = run_context.get()
        run_id = context["run_id"]
        binding = await self.binding(run_id)
        config = get_config()
        meta = config.get("metadata") or {}
        scope = str(
            (
                meta.get("langgraph_checkpoint_ns"),
                meta.get("langgraph_node"),
                meta.get("langgraph_step"),
            )
        )
        # A replay requests the same durable operation. A new user turn changes its scope.
        key = json.dumps(
            [
                run_id,
                context["turn_key"],
                context.get("turn_id", 1),
                binding["generation"] if not approved_action_id else 0,
                operation,
                arguments,
                approved_action_id,
                slot,
                tab_id,
                expected_snapshot_id,
            ],
            sort_keys=True,
            ensure_ascii=False,
        )
        command_id = hashlib.sha256(key.encode()).hexdigest()[:40]
        row = await self.get(command_id)
        if not row:
            if context["browser_calls"] >= self.runtime.settings.max_tool_calls:
                raise ValueError("browser_tool_budget_exhausted")
            payload = dict(
                command_id=command_id,
                run_id=run_id,
                browser_session_id=binding["browser_session_id"],
                operation=operation,
                arguments=arguments or {},
                tab_id=tab_id,
                expected_snapshot_id=expected_snapshot_id,
                approved_action_id=approved_action_id,
                expires_at=expires_at
                or datetime.fromtimestamp(time.time() + 900, timezone.utc).isoformat(),
                _interrupt_scope=scope,
                _turn_key=context["turn_key"],
                _turn_id=context.get("turn_id", 1),
                _generation=binding["generation"],
            )
            try:
                async with self.database.session() as session:
                    async with session.begin():
                        await session.execute(
                            insert(commands).values(
                                command_id=command_id,
                                run_id=run_id,
                                browser_session_id=binding["browser_session_id"],
                                payload=payload,
                                created_at=time.time(),
                            )
                        )
                context["browser_calls"] += 1
            except IntegrityError:
                pass
            row = await self.get(command_id)
        if row["payload"].get("_interrupt_scope") == scope:
            interrupt(
                {
                    "type": "browser",
                    "id": f"browser:{command_id}",
                    "command_id": command_id,
                    "operation": operation,
                    "message": "等待桌面浏览器结果。",
                    "paused_at": time.time(),
                }
            )
            row = await self.get(command_id)
        while not row["result"]:
            interrupt(
                {
                    "type": "browser",
                    "id": f"browser:{command_id}",
                    "command_id": command_id,
                    "operation": operation,
                    "message": "等待桌面浏览器；请保持应用开启，登录或接管后继续。",
                    "paused_at": time.time(),
                }
            )
            row = await self.get(command_id)
        return row["result"]

    async def poll(self, browser_session_id, after=0):
        # Pending rows are always returned even when after advanced; SSE/cursors are notifications, not ownership.
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(commands)
                        .where(
                            commands.c.browser_session_id == browser_session_id,
                            commands.c.result.is_(None),
                        )
                        .order_by(commands.c.seq)
                    )
                )
                .mappings()
                .all()
            )
        out = []
        for row in rows:
            run = await self.runtime.runs.get(row["run_id"])
            if (
                not run
                or run["cancel_requested"]
                or run["phase"]
                in {"CANCELLED", "SUCCEEDED", "FAILED", "INFEASIBLE", "PARTIAL_FAILED"}
            ):
                continue
            payload = row["payload"]
            if run.get("pending_command") or payload.get("_turn_id", 1) != (
                run.get("state_json") or {}
            ).get("turn_id", 1):
                continue
            if (
                payload.get("_turn_key")
                != hashlib.sha256(str(run["input_text"]).encode()).hexdigest()
            ):
                continue
            binding = await self.binding(row["run_id"])
            if (
                not payload.get("approved_action_id")
                and payload.get("_generation") != binding["generation"]
            ):
                continue
            if payload.get("approved_action_id"):
                state = run.get("state_json") or {}
                proposal = state.get("action_proposal") or {}
                approved = await self.runtime.runs.get_action(
                    row["run_id"], payload["approved_action_id"]
                )
                if (
                    not approved
                    or approved["status"] != "RUNNING"
                    or state.get("approval_decision") != "approve"
                    or not any(
                        a.get("action_id") == payload["approved_action_id"]
                        for a in proposal.get("actions", [])
                    )
                ):
                    continue
            out.append({k: v for k, v in payload.items() if not k.startswith("_")})
            if len(out) >= 30:
                break
        return {"commands": out, "cursor": max([after, *[r["seq"] for r in rows]])}

    async def accept(self, command_id, observation: Observation):
        row = await self.get(command_id)
        if not row:
            raise KeyError(command_id)
        if (
            command_id != observation.command_id
            or row["browser_session_id"] != observation.browser_session_id
        ):
            raise ValueError("browser_command_ownership_mismatch")
        result = observation.model_dump(mode="json", exclude_none=True)
        result["observed_at"] = (row.get("result") or {}).get("observed_at") or datetime.now(
            timezone.utc
        ).isoformat()
        if row["result"] is not None:
            if row["result"] != result:
                raise ValueError("browser_result_already_recorded")
            await self.runtime.resume_browser(row["run_id"], command_id)
            return {"accepted": True, "replayed": True}
        if observation.ok and observation.outcome == "observed" and not observation.url:
            raise ValueError("successful_observation_requires_source")
        if (
            observation.ok
            and row["payload"]["operation"]
            in {"snapshot", "read_page", "extract", "extract_tables"}
            and not observation.snapshot_id
        ):
            raise ValueError("page_observation_requires_snapshot")
        async with self.database.session() as session:
            async with session.begin():
                await session.execute(
                    update(commands)
                    .where(commands.c.command_id == command_id, commands.c.result.is_(None))
                    .values(result=result)
                )
        await self.runtime.runs.append_event(
            run_id=row["run_id"],
            phase=RunPhase.REQUIREMENTS_READY,
            event_type="BROWSER_OBSERVATION",
            payload={
                "command_id": command_id,
                "outcome": observation.outcome,
                "url": observation.url,
            },
            agent_id="browser",
        )
        await self.runtime.resume_browser(row["run_id"], command_id)
        return {"accepted": True, "replayed": False}

    async def observations(self, run_id):
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(commands).where(commands.c.run_id == run_id).order_by(commands.c.seq)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]
