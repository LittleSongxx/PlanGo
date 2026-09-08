from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import struct
import time
import zlib
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from langgraph.config import get_config
from langgraph.types import interrupt
from plango_harness.agent.contracts import RunPhase
from plango_harness.persistence.database import agent_action, agent_run, metadata, run_event
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import JSON, Column, Float, Integer, String, Table, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .outcomes import preparation_click_forbidden

run_context: ContextVar[dict[str, Any]] = ContextVar("plango_browser_run")

bindings = Table(
    "plango_browser_binding",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("browser_session_id", String(128), nullable=False),
    Column("input_image", String),
    Column("generation", Integer, nullable=False, default=0),
    Column("enabled_skills", JSON),
    Column("location_context", JSON),
)
commands = Table(
    "plango_browser_command",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("command_id", String(64), unique=True, nullable=False),
    Column("run_id", String(64), nullable=False, index=True),
    Column("browser_session_id", String(128), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
    Column("result", JSON),
    Column("created_at", Float, nullable=False),
)


class BrowserScreenshot(BaseModel):
    """A native PNG capture, with viewport/clip in CSS pixels and dpr including zoom."""

    model_config = ConfigDict(extra="forbid", strict=True)
    screenshot_id: str = Field(min_length=1, max_length=128)
    snapshot_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=8192)
    captured_at: AwareDatetime
    viewport: dict[str, float]
    image: dict[str, int]
    dpr: float = Field(ge=0.25, le=8, allow_inf_nan=False)
    zoom: float = Field(ge=0.25, le=5, allow_inf_nan=False)
    clip: dict[str, float]
    scroll: dict[str, float]
    page_version: str = Field(min_length=1, max_length=200)
    data_url: str = Field(max_length=10_666_700)
    view_bounds: dict[str, float] | None = None

    @field_validator("captured_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @model_validator(mode="after")
    def validate_geometry_and_png(self):
        for value, keys in ((self.viewport, {"width", "height"}), (self.image, {"width", "height"}),
                            (self.clip, {"x", "y", "width", "height"}), (self.scroll, {"x", "y"})):
            if set(value) != keys or any(not math.isfinite(v) or abs(v) > 10_000_000 for v in value.values()):
                raise ValueError("invalid_screenshot_geometry")
        if self.view_bounds is not None and (set(self.view_bounds) != {"x", "y", "width", "height"}
                or any(not math.isfinite(v) for v in self.view_bounds.values())
                or self.view_bounds["width"] <= 0 or self.view_bounds["height"] <= 0):
            raise ValueError("invalid_screenshot_view_bounds")
        for axis, size in (("x", "width"), ("y", "height")):
            if not 0 < self.viewport[size] <= 8192 or not 0 < self.image[size] <= 16384:
                raise ValueError("screenshot_dimensions_out_of_bounds")
            if self.clip[axis] < 0 or self.clip[size] <= 0 or self.clip[axis] + self.clip[size] > self.viewport[size] + 0.01:
                raise ValueError("screenshot_clip_outside_viewport")
            # devicePixelRatio already includes browser zoom; do not multiply zoom twice.
            if abs(self.image[size] - self.clip[size] * self.dpr) > 2:
                raise ValueError("screenshot_pixel_ratio_mismatch")
        if self.image["width"] * self.image["height"] > 16_000_000:
            raise ValueError("screenshot_pixel_budget_exceeded")
        url = urlsplit(self.url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("invalid_screenshot_url")
        if not re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/=]+", self.data_url):
            raise ValueError("screenshot_requires_native_png")
        raw = base64.b64decode(self.data_url.split(",", 1)[1], validate=True)
        if not 45 <= len(raw) <= 8_000_000 or raw[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
            raise ValueError("invalid_or_oversize_screenshot_png")
        if zlib.crc32(raw[12:29]) != int.from_bytes(raw[29:33], "big") or raw[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82":
            raise ValueError("invalid_screenshot_png_header")
        if struct.unpack(">II", raw[16:24]) != (self.image["width"], self.image["height"]):
            raise ValueError("screenshot_image_dimensions_mismatch")
        return self

    def check_binding(self, observation, command, before, *, created_at=0, now=None, max_age_seconds=30):
        captured = self.captured_at.timestamp()
        current = time.time() if now is None else now
        if captured > current + 2 or captured < created_at - 2 or (
            max_age_seconds is not None and current - captured > max_age_seconds
        ):
            raise ValueError("stale_screenshot")
        if (command.get("operation") != "screenshot" or not command.get("expected_snapshot_id")
                or self.snapshot_id != command["expected_snapshot_id"]
                or self.snapshot_id != observation.get("snapshot_id") or self.snapshot_id != before.get("snapshot_id")
                or self.url != observation.get("url") or self.url != before.get("url")
                or not command.get("tab_id") or observation.get("tab_id") != command["tab_id"]
                or before.get("tab_id") != command["tab_id"]
                or self.page_version != before.get("page_version") or self.page_version != observation.get("page_version")
                or (command.get("command_id") and command["command_id"] != observation.get("command_id"))):
            raise ValueError("screenshot_page_binding_mismatch")


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=1, max_length=64)
    browser_session_id: str = Field(min_length=1, max_length=128)
    ok: bool
    outcome: Literal["observed", "executed", "blocked", "unknown", "failed"]
    interaction_kind: Literal["navigation"] | None = None
    error_kind: str | None = None
    error: str | None = None
    snapshot_id: str | None = None
    page_version: str | None = Field(default=None, min_length=1, max_length=200)
    screenshot: BrowserScreenshot | None = None
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
        expected_page=None,
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
        if tab_id and not approved_action_id and operation in {"extract", "snapshot", "read_page", "extract_tables"} and not str(slot or "").startswith("receipt:"):
            # A user retry advances generation. Preserve that choice across checkpoint replay:
            # a closed tab from this exact read is replaced by the current visible session,
            # while writes, screenshots and business-receipt checks keep their original binding.
            async with self.database.session() as session:
                closed = (await session.execute(select(commands.c.command_id).where(
                    commands.c.run_id == run_id, commands.c.browser_session_id == binding["browser_session_id"],
                    commands.c.payload["_interrupt_scope"].as_string() == scope,
                    commands.c.payload["_turn_key"].as_string() == context["turn_key"],
                    commands.c.payload["operation"].as_string() == operation,
                    commands.c.payload["tab_id"].as_string() == tab_id,
                    commands.c.payload["approved_action_id"].as_string().is_(None),
                    func.coalesce(commands.c.payload["_generation"].as_integer(), 0) < binding["generation"],
                    commands.c.result["outcome"].as_string().in_(["blocked", "failed"]),
                    commands.c.result["error_kind"].as_string() == "tab_closed",
                ).limit(1))).first()
            if closed:
                tab_id, expected_snapshot_id = None, None
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
                *([expected_page] if operation == "screenshot" else []),
            ],
            sort_keys=True,
            ensure_ascii=False,
        )
        command_id = hashlib.sha256(key.encode()).hexdigest()[:40]
        row = await self.get(command_id)
        if operation == "screenshot":
            if not expected_page or expected_page.get("snapshot_id") != expected_snapshot_id:
                raise ValueError("screenshot_requires_prior_dom_snapshot")
            async with self.database.session() as session:
                unresolved = (await session.execute(select(agent_action.c.action_id).where(
                    agent_action.c.run_id == run_id, agent_action.c.status.in_(["UNKNOWN", "RUNNING"]),
                ).limit(1))).first()
                if unresolved:
                    raise ValueError("vision_forbidden_for_unresolved_submission")
                prior = (await session.execute(select(commands.c.command_id).where(
                    commands.c.run_id == run_id, commands.c.payload["operation"].as_string() == "screenshot",
                    commands.c.payload["_turn_id"].as_integer() == context.get("turn_id", 1),
                ))).scalars().all()
            if prior and command_id not in prior:
                raise ValueError("vision_capture_already_requested_this_turn")
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
                _budget_id=context.get("budget_id", "initial"),
                _generation=binding["generation"],
            )
            if operation == "screenshot":
                payload["_expected_page"] = expected_page
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
            state = run.get("state_json") or {}
            approved = await self.runtime.runs.get_action(row["run_id"], payload["approved_action_id"]) if payload.get("approved_action_id") else None
            target = ((approved or {}).get("arguments_json") or {}).get("target")
            if preparation_click_forbidden(state, payload.get("operation"), target):
                # The command may already have executed before its receipt was lost. Only stop dispatch;
                # never fabricate a result or change the ledger while awaiting the real receipt.
                async with self.runtime.runs.event_transaction() as session:
                    await session.execute(select(agent_run.c.run_id).where(agent_run.c.run_id == row["run_id"]).with_for_update())
                    present = (await session.execute(select(run_event.c.seq).where(
                        run_event.c.run_id == row["run_id"], run_event.c.event_type == "BROWSER_COMMAND_BLOCKED",
                        run_event.c.payload_json["command_id"].as_string() == row["command_id"],
                    ).limit(1))).first()
                    result = (await session.execute(select(commands.c.result).where(commands.c.command_id == row["command_id"]))).scalar_one()
                    if not present and result is None:
                        await self.runtime.runs.append_event_in_transaction(session, run_id=row["run_id"], phase=RunPhase(run["phase"]),
                            event_type="BROWSER_COMMAND_BLOCKED", agent_id="browser",
                            payload={"command_id": row["command_id"], "operation": "click", "scope": "preparation_only",
                                     "reason": "准备范围不允许自动提交，请人工核对"})
                continue
            if payload.get("approved_action_id"):
                proposal = state.get("action_proposal") or {}
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
        if observation.screenshot is not None or row["payload"]["operation"] == "screenshot":
            if observation.ok:
                if observation.outcome != "observed" or observation.screenshot is None:
                    raise ValueError("screenshot_requires_readonly_capture")
                if row["result"] is None:
                    # Receipt delivery can lag capture. Store the immutable
                    # observation; Vision independently requires a fresh image.
                    observation.screenshot.check_binding(result, row["payload"], row["payload"].get("_expected_page") or {}, created_at=row["created_at"], max_age_seconds=None)
            elif observation.screenshot is not None:
                raise ValueError("failed_observation_must_not_supply_screenshot")
        if observation.ok and observation.outcome == "observed" and not observation.url:
            raise ValueError("successful_observation_requires_source")
        if (
            observation.ok
            and row["payload"]["operation"]
            in {"snapshot", "read_page", "extract", "extract_tables"}
            and not observation.snapshot_id
        ):
            raise ValueError("page_observation_requires_snapshot")
        async with self.runtime.runs.event_transaction() as session:
            written = (await session.execute(update(commands)
                .where(commands.c.command_id == command_id, commands.c.result.is_(None))
                .values(result=result).returning(commands.c.command_id))).scalar_one_or_none()
            if written is None:
                saved = (await session.execute(select(commands.c.result).where(commands.c.command_id == command_id))).scalar_one()
                if {**result, "observed_at": saved.get("observed_at")} != saved:
                    raise ValueError("browser_result_already_recorded")
            # A legacy receipt may predate atomic event writes. Repair only its missing audit fact.
            present = (await session.execute(select(run_event.c.seq).where(
                run_event.c.run_id == row["run_id"], run_event.c.event_type == "BROWSER_OBSERVATION",
                run_event.c.payload_json["command_id"].as_string() == command_id).limit(1))).first()
            if not present:
                await self.runtime.runs.append_event_in_transaction(session, run_id=row["run_id"],
                    phase=RunPhase.REQUIREMENTS_READY, event_type="BROWSER_OBSERVATION",
                    payload={"command_id": command_id, "outcome": observation.outcome, "url": observation.url}, agent_id="browser")
        await self.runtime.resume_browser(row["run_id"], command_id)
        return {"accepted": True, "replayed": written is None}

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
