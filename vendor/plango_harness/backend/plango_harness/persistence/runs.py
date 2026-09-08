from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Mapping, Sequence

from sqlalchemy import and_, desc, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plango_harness.agent.contracts import RunEvent, RunPhase
from plango_harness.persistence.database import (
    Database,
    agent_action,
    agent_run,
    run_event,
    run_plan,
    utc_now,
)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "content"):
        return {"type": type(value).__name__, "content": _jsonable(value.content)}
    return str(value)


class RunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        # SQLite ignores SELECT FOR UPDATE, so serialize event allocation in
        # this process as well as using the database row lock on PostgreSQL.
        # ponytail: one process-wide lock; use per-run locks if event throughput matters.
        self._event_lock = asyncio.Lock()

    async def create(self, run_id: str, user_id: str, input_text: str) -> None:
        now = utc_now()
        state = {
            "run_id": run_id,
            "thread_id": run_id,
            "user_id": user_id,
            "input_text": input_text,
            "phase": RunPhase.CREATED.value,
            "turn_count": 0,
            "turn_id": 1,
            "plan_version": 0,
            "tool_call_count": 0,
            "repair_round": 0,
            "interrupt_id": None,
            "evidence": [],
            "weather": None,
            "model_token_count": 0,
            "model_call_count": 0,
            "model_fallback_count": 0,
            "model_total_latency_ms": 0.0,
            "model_last_error": None,
            "model_last_usage": {},
            "model_calls": [],
        }
        async with self.database.session() as session:
            async with session.begin():
                await session.execute(
                    agent_run.insert().values(
                        run_id=run_id,
                        thread_id=run_id,
                        user_id=user_id,
                        input_text=input_text,
                        phase=RunPhase.CREATED.value,
                        outcome=None,
                        version=1,
                        last_event_seq=0,
                        cancel_requested=0,
                        state_json=state,
                        created_at=now,
                        updated_at=now,
                    )
                )

    async def create_with_event(
        self, run_id: str, user_id: str, input_text: str, *, selected_poi: dict[str, Any] | None = None
    ) -> RunEvent:
        """Create a run and its first audit fact in one transaction."""
        now = utc_now()
        state = {
            "run_id": run_id,
            "thread_id": run_id,
            "user_id": user_id,
            "input_text": input_text,
            "phase": RunPhase.CREATED.value,
            "turn_count": 0,
            "turn_id": 1,
            "plan_version": 0,
            "tool_call_count": 0,
            "repair_round": 0,
            "interrupt_id": None,
            "evidence": [],
            "weather": None,
            "model_token_count": 0,
            "model_call_count": 0,
            "model_fallback_count": 0,
            "model_total_latency_ms": 0.0,
            "model_last_error": None,
            "model_last_usage": {},
            "model_calls": [],
        }
        if selected_poi is not None:
            state["selected_poi"] = selected_poi
        initial_payload = {"input_text": input_text, **({"selected_poi": selected_poi} if selected_poi is not None else {})}
        async with self._event_lock:
            async with self.database.session() as session:
                async with session.begin():
                    await session.execute(
                        agent_run.insert().values(
                            run_id=run_id,
                            thread_id=run_id,
                            user_id=user_id,
                            input_text=input_text,
                            phase=RunPhase.CREATED.value,
                            outcome=None,
                            version=1,
                            last_event_seq=1,
                            cancel_requested=0,
                            state_json=state,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    await session.execute(
                        run_event.insert().values(
                            run_id=run_id,
                            seq=1,
                            event_type="RUN_CREATED",
                            phase=RunPhase.CREATED.value,
                            agent_id="runtime",
                            payload_json=initial_payload,
                            created_at=now,
                        )
                    )
        return RunEvent(
            run_id=run_id,
            seq=1,
            event_type="RUN_CREATED",
            phase=RunPhase.CREATED,
            agent_id="runtime",
            payload=initial_payload,
            created_at=now,
        )

    async def get(self, run_id: str) -> dict[str, Any] | None:
        async with self.database.session() as session:
            row = (
                (await session.execute(select(agent_run).where(agent_run.c.run_id == run_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def pending_work(self, limit: int = 100) -> list[str]:
        """Return resumable runs whose queue delivery may have been lost."""
        now = utc_now()
        terminal = {
            RunPhase.SUCCEEDED.value,
            RunPhase.CANCELLED.value,
            RunPhase.PARTIAL_FAILED.value,
            RunPhase.INFEASIBLE.value,
            RunPhase.FAILED.value,
        }
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            agent_run.c.run_id,
                            agent_run.c.phase,
                            agent_run.c.state_json,
                            agent_run.c.lease_until,
                            agent_run.c.pending_command,
                        )
                        .where(
                            agent_run.c.phase.not_in(terminal),
                            agent_run.c.cancel_requested == 0,
                            or_(
                                agent_run.c.lease_until.is_(None),
                                agent_run.c.lease_until < now,
                            ),
                            or_(
                                agent_run.c.pending_command.is_not(None),
                                agent_run.c.phase.not_in({RunPhase.WAITING_APPROVAL.value, RunPhase.REQUIREMENTS_READY.value}),
                                and_(
                                    agent_run.c.phase == RunPhase.REQUIREMENTS_READY.value,
                                    agent_run.c.state_json["interrupt_id"].as_string().is_(None),
                                    agent_run.c.state_json["clarification"].as_string().is_(None),
                                ),
                            ),
                        )
                        .order_by(agent_run.c.pending_command.is_(None), agent_run.c.updated_at)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        result: list[str] = []
        for row in rows:
            state = row.get("state_json") or {}
            if row.get("pending_command"):
                result.append(str(row["run_id"]))
                continue
            # Never auto-resume an approval/clarification interrupt. It needs
            # an explicit user command; all other non-terminal checkpoints can
            # safely be handed back to the worker.
            if row["phase"] == RunPhase.WAITING_APPROVAL.value:
                continue
            if row["phase"] == RunPhase.REQUIREMENTS_READY.value and (
                state.get("clarification") or state.get("interrupt_id")
            ):
                continue
            result.append(str(row["run_id"]))
        return result

    async def save_state(
        self,
        state: Mapping[str, Any],
        *,
        expected_version: int | None = None,
        lease_owner: str | None = None,
    ) -> int:
        _, version = await self.save_state_and_events(
            state,
            expected_version=expected_version,
            lease_owner=lease_owner,
            events=(),
        )
        return version

    async def save_state_and_events(
        self,
        state: Mapping[str, Any],
        *,
        expected_version: int | None = None,
        lease_owner: str | None = None,
        events: Sequence[Mapping[str, Any]],
        input_text: str | None = None,
        command_id: str | None = None,
        clear_pending_command: bool = False,
        release_lease: bool = False,
    ) -> tuple[list[RunEvent], int]:
        """Atomically persist the projection and its audit events.

        The LangGraph checkpoint remains authoritative for graph execution;
        ``agent_run.state_json`` is only a query/UI projection. A lease owner
        is checked in the same transaction to fence a worker whose lease has
        expired.
        """
        run_id = str(state["run_id"])
        now = utc_now()
        payload = _jsonable(dict(state))
        raw_phase = state.get("phase") or RunPhase.CREATED
        phase = raw_phase.value if isinstance(raw_phase, RunPhase) else str(raw_phase)
        outcome = state.get("outcome")
        created_events: list[RunEvent] = []
        async with self._event_lock:
            async with self.database.session() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(
                                agent_run.c.version,
                                agent_run.c.last_event_seq,
                                agent_run.c.lease_owner,
                                agent_run.c.cancel_requested,
                                agent_run.c.pending_command,
                            )
                            .where(agent_run.c.run_id == run_id)
                            .with_for_update()
                        )
                    ).first()
                    if not row:
                        raise KeyError(f"run not found: {run_id}")
                    if expected_version is not None and int(row[0]) != expected_version:
                        raise RuntimeError("stale run state version")
                    if lease_owner is not None and row[2] != lease_owner:
                        raise RuntimeError("run lease fenced")
                    if lease_owner is not None and row[3]:
                        raise RuntimeError("run cancellation requested")
                    if command_id is not None and (row[4] or {}).get("id") != command_id:
                        raise RuntimeError("run command fenced")
                    if release_lease and not lease_owner:
                        raise ValueError("lease owner required for boundary release")
                    next_version = int(row[0]) + 1
                    await session.execute(
                        update(agent_run)
                        .where(agent_run.c.run_id == run_id)
                        .values(
                            phase=phase,
                            outcome=str(outcome) if outcome else None,
                            version=next_version,
                            state_json=payload,
                            updated_at=now,
                            **({"input_text": input_text} if input_text is not None else {}),
                            **({"pending_command": None} if command_id is not None or clear_pending_command else {}),
                            **({"lease_owner": None, "lease_until": None} if release_lease else {}),
                        )
                    )
                    seq = int(row[1] or 0)
                    for item in events:
                        seq += 1
                        event_phase = item.get("phase") or phase
                        event_phase = (
                            event_phase.value
                            if isinstance(event_phase, RunPhase)
                            else str(event_phase)
                        )
                        event_payload = _jsonable(item.get("payload") or {})
                        event_type = str(item.get("event_type") or item.get("event") or "STATE_UPDATE")
                        agent_id = item.get("agent_id")
                        await session.execute(
                            run_event.insert().values(
                                run_id=run_id,
                                seq=seq,
                                event_type=event_type,
                                phase=event_phase,
                                agent_id=str(agent_id) if agent_id else None,
                                payload_json=event_payload,
                                created_at=now,
                            )
                        )
                        created_events.append(
                            RunEvent(
                                run_id=run_id,
                                seq=seq,
                                event_type=event_type,
                                phase=RunPhase(event_phase),
                                agent_id=str(agent_id) if agent_id else None,
                                payload=event_payload,
                                created_at=now,
                            )
                        )
                    if created_events:
                        await session.execute(
                            update(agent_run)
                            .where(agent_run.c.run_id == run_id)
                            .values(last_event_seq=seq, updated_at=now)
                        )
        return created_events, next_version

    async def update_input(self, run_id: str, text: str) -> None:
        async with self.database.session() as session:
            async with session.begin():
                await session.execute(
                    update(agent_run)
                    .where(agent_run.c.run_id == run_id)
                    .values(input_text=text, updated_at=utc_now())
                )

    async def update_input_with_event(
        self,
        run_id: str,
        text: str | None,
        *,
        phase: RunPhase,
        event_type: str,
        payload: dict[str, Any] | None = None,
        command_payload: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> RunEvent:
        """Persist a user command and its audit fact atomically."""
        now = utc_now()
        phase_value = phase.value if isinstance(phase, RunPhase) else str(phase)
        event_payload = _jsonable(payload or {})
        async with self._event_lock:
            async with self.database.session() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(agent_run.c.last_event_seq, agent_run.c.version, agent_run.c.state_json, agent_run.c.pending_command)
                            .where(agent_run.c.run_id == run_id)
                            .with_for_update()
                        )
                    ).first()
                    if not row:
                        raise KeyError(f"run not found: {run_id}")
                    if expected_version is not None and row[1] != expected_version:
                        raise ValueError("run changed before command acceptance")
                    if command_payload is not None and row[3]:
                        pending = row[3]
                        if pending.get("payload") != command_payload:
                            raise ValueError("another command is pending for this run")
                        return RunEvent(run_id=run_id, seq=pending["event_seq"], phase=phase, event_type=event_type, payload={**event_payload, "command_id": pending["id"]}, agent_id="runtime", created_at=now)
                    seq = int(row[0] or 0) + 1
                    values: dict[str, Any] = {"last_event_seq": seq, "updated_at": now}
                    if command_payload is not None:
                        command_id = uuid.uuid4().hex
                        projection = row[2] or {}
                        values["pending_command"] = {
                            "id": command_id, "payload": command_payload, "event_seq": seq,
                            "turn_id": int(projection.get("turn_id", 1)),
                            "plan_version": int(projection.get("plan_version", 0)),
                            "interrupt_id": projection.get("interrupt_id"),
                        }
                        event_payload["command_id"] = command_id
                    if text is not None:
                        values["input_text"] = text
                    if text and text.strip() and event_type in {"USER_MESSAGE", "RESUME_REQUESTED"} and (
                        command_payload is None or command_payload.get("decision") in {"edit", "resume"}
                    ):
                        projection = dict(row[2] or {})
                        grant = {"id": f"user:{seq}", "grant_seq": seq, "model_baseline": int(projection.get("model_token_count", 0)),
                                 "tool_baseline": int(projection.get("tool_call_count", 0))}
                        values["state_json"] = {**projection, "turn_budget": grant}
                        event_payload["budget_grant_id"] = grant["id"]
                    await session.execute(
                        update(agent_run).where(agent_run.c.run_id == run_id).values(**values)
                    )
                    await session.execute(
                        run_event.insert().values(
                            run_id=run_id,
                            seq=seq,
                            event_type=event_type,
                            phase=phase_value,
                            agent_id="runtime",
                            payload_json=event_payload,
                            created_at=now,
                        )
                    )
        return RunEvent(
            run_id=run_id,
            seq=seq,
            event_type=event_type,
            phase=RunPhase(phase_value),
            agent_id="runtime",
            payload=event_payload,
            created_at=now,
        )

    async def save_plan(self, run_id: str, plan: Any, verifier: Any | None = None) -> None:
        """Persist an immutable plan version for stale-action detection."""
        payload = _jsonable(plan)
        version = int(payload.get("version", 1))
        async with self.database.session() as session:
            async with session.begin():
                latest = (
                    await session.execute(
                        select(run_plan.c.plan_version)
                        .where(run_plan.c.run_id == run_id)
                        .order_by(desc(run_plan.c.plan_version))
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if latest is not None and version < int(latest):
                    raise RuntimeError("plan version must be monotonic")
                existing = (
                    await session.execute(
                        select(run_plan.c.plan_id, run_plan.c.plan_json).where(
                            run_plan.c.run_id == run_id,
                            run_plan.c.plan_version == version,
                        )
                    )
                ).first()
                values = {
                    "run_id": run_id,
                    "plan_version": version,
                    "plan_id": str(payload.get("plan_id", "")),
                    "plan_json": payload,
                    "verifier_json": _jsonable(verifier) if verifier is not None else None,
                    "created_at": utc_now(),
                }
                if existing:
                    old_json = existing[1] if isinstance(existing[1], dict) else {}
                    def stable_stops(value: Any) -> Any:
                        if not isinstance(value, list):
                            return value
                        return [
                            {
                                key: item_value
                                for key, item_value in stop.items()
                                if key not in {"supply_observed_at", "supply_expires_at"}
                            }
                            if isinstance(stop, dict)
                            else stop
                            for stop in value
                        ]
                    if (
                        str(existing[0]) != str(values["plan_id"])
                        or stable_stops(old_json.get("stops"))
                        != stable_stops(values["plan_json"].get("stops"))
                    ):
                        raise RuntimeError("plan version is immutable")
                    await session.execute(
                        update(run_plan)
                        .where(
                            run_plan.c.run_id == run_id,
                            run_plan.c.plan_version == version,
                        )
                        .values(**values)
                    )
                else:
                    await session.execute(run_plan.insert().values(**values))

    async def get_plan(self, run_id: str, version: int) -> dict[str, Any] | None:
        async with self.database.session() as session:
            row = (
                (
                    await session.execute(
                        select(run_plan).where(
                            run_plan.c.run_id == run_id,
                            run_plan.c.plan_version == version,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def latest_plan_version(self, run_id: str) -> int | None:
        async with self.database.session() as session:
            return (
                await session.execute(
                    select(run_plan.c.plan_version)
                    .where(run_plan.c.run_id == run_id)
                    .order_by(desc(run_plan.c.plan_version))
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def plans(self, run_id: str) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(run_plan)
                        .where(run_plan.c.run_id == run_id)
                        .order_by(run_plan.c.plan_version)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def actions(self, run_id: str) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(agent_action)
                        .where(agent_action.c.run_id == run_id)
                        .order_by(agent_action.c.created_at)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def get_action(self, run_id: str, action_id: str) -> dict[str, Any] | None:
        async with self.database.session() as session:
            row = (
                (
                    await session.execute(
                        select(agent_action).where(
                            agent_action.c.run_id == run_id,
                            agent_action.c.action_id == action_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def mark_terminal(
        self,
        run_id: str,
        phase: RunPhase,
        reason: str = "",
        *,
        lease_owner: str | None = None,
    ) -> None:
        row = await self.get(run_id)
        if not row:
            raise KeyError(f"run not found: {run_id}")
        state = dict(row.get("state_json") or {})
        state.update({"phase": phase.value, "outcome": phase.value, "reason": reason})
        await self.save_state(
            state,
            expected_version=int(row.get("version") or 1),
            lease_owner=lease_owner,
        )

    async def claim(self, run_id: str, owner: str, lease_seconds: int = 90) -> bool:
        """Claim a run briefly so Redis redelivery cannot execute it twice."""
        now = utc_now()
        until = now + timedelta(seconds=max(1, lease_seconds))
        async with self.database.session() as session:
            async with session.begin():
                result = await session.execute(
                    update(agent_run)
                    .where(
                        and_(
                            agent_run.c.run_id == run_id,
                            agent_run.c.cancel_requested == 0,
                            agent_run.c.lease_until.is_(None) | (agent_run.c.lease_until < now),
                        )
                    )
                    .values(lease_owner=owner, lease_until=until)
                )
                return bool(getattr(result, "rowcount", 0))

    async def release(self, run_id: str, owner: str) -> None:
        async with self.database.session() as session:
            async with session.begin():
                await session.execute(
                    update(agent_run)
                    .where(agent_run.c.run_id == run_id, agent_run.c.lease_owner == owner)
                    .values(lease_owner=None, lease_until=None)
                )

    async def renew(self, run_id: str, owner: str, lease_seconds: int = 90) -> bool:
        async with self.database.session() as session:
            async with session.begin():
                result = await session.execute(
                    update(agent_run)
                    .where(
                        agent_run.c.run_id == run_id,
                        agent_run.c.lease_owner == owner,
                        agent_run.c.lease_until > utc_now(),
                    )
                    .values(lease_until=utc_now() + timedelta(seconds=max(1, lease_seconds)))
                )
                return bool(getattr(result, "rowcount", 0))

    async def request_cancel(self, run_id: str) -> bool:
        async with self.database.session() as session:
            async with session.begin():
                result = await session.execute(
                    update(agent_run).where(agent_run.c.run_id == run_id).values(cancel_requested=1, pending_command=None)
                )
                return bool(getattr(result, "rowcount", 0))

    async def clear_cancel(self, run_id: str) -> None:
        async with self.database.session() as session:
            async with session.begin():
                await session.execute(
                    update(agent_run)
                    .where(agent_run.c.run_id == run_id)
                    .values(cancel_requested=0, outcome=None, updated_at=utc_now())
                )

    async def append_event(
        self,
        *,
        run_id: str,
        phase: RunPhase,
        event_type: str,
        payload: dict[str, Any] | None = None,
        agent_id: str | None = None,
        lease_owner: str | None = None,
    ) -> RunEvent:
        async with self._event_lock:
            for attempt in range(3):
                try:
                    return await self._append_event_unlocked(
                        run_id=run_id,
                        phase=phase,
                        event_type=event_type,
                        payload=payload,
                        agent_id=agent_id,
                        lease_owner=lease_owner,
                    )
                except IntegrityError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0)
        raise RuntimeError("event allocation failed")

    @asynccontextmanager
    async def event_transaction(self):
        """One local sequencing lock and database transaction for a fact and its audit event."""
        async with self._event_lock:
            async with self.database.session() as session:
                async with session.begin():
                    yield session

    async def _append_event_unlocked(self, **kwargs: Any) -> RunEvent:
        async with self.database.session() as session:
            async with session.begin():
                return await self.append_event_in_transaction(session, **kwargs)

    async def append_event_in_transaction(
        self, session: AsyncSession, *, run_id: str, phase: RunPhase, event_type: str,
        payload: dict[str, Any] | None = None, agent_id: str | None = None,
        lease_owner: str | None = None,
    ) -> RunEvent:
        """Caller holds the sequencing lock and a database transaction; result data and event commit together."""
        now = utc_now()
        payload = _jsonable(payload or {})
        phase_value = phase.value if isinstance(phase, RunPhase) else str(phase)
        # Lock the run row before allocating the sequence. This is
        # safe under concurrent workers; SQLite's single writer still
        # provides serial local behavior.
        current_row = (
            await session.execute(
                select(agent_run.c.last_event_seq, agent_run.c.lease_owner)
                .where(agent_run.c.run_id == run_id)
                .with_for_update()
            )
        ).first()
        if not current_row:
            raise KeyError(f"run not found: {run_id}")
        if lease_owner is not None and current_row[1] != lease_owner:
            raise RuntimeError("run lease fenced")
        seq = int(current_row[0] or 0) + 1
        await session.execute(
            run_event.insert().values(
                run_id=run_id,
                seq=seq,
                event_type=event_type,
                phase=phase_value,
                agent_id=agent_id,
                payload_json=payload,
                created_at=now,
            )
        )
        await session.execute(
            update(agent_run)
            .where(agent_run.c.run_id == run_id)
            .values(last_event_seq=seq, updated_at=now)
        )
        return RunEvent(
            run_id=run_id,
            seq=seq,
            event_type=event_type,
            phase=RunPhase(phase_value),
            agent_id=agent_id,
            payload=payload,
            created_at=now,
        )

    async def events(self, run_id: str, after: int = 0, limit: int = 200) -> list[RunEvent]:
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(run_event)
                        .where(run_event.c.run_id == run_id, run_event.c.seq > after)
                        .order_by(run_event.c.seq)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [
            RunEvent(
                run_id=row["run_id"],
                seq=row["seq"],
                event_type=row["event_type"],
                phase=RunPhase(row["phase"]),
                agent_id=row["agent_id"],
                payload=row["payload_json"] or {},
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def record_action(
        self,
        *,
        action_id: str,
        run_id: str,
        plan_id: str,
        plan_version: int,
        tool_name: str,
        idempotency_key: str,
        request_hash: str,
        arguments: dict[str, Any],
        status: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        async with self.database.session() as session:
            async with session.begin():
                existing = (
                    (
                        await session.execute(
                            select(agent_action).where(
                                agent_action.c.run_id == run_id,
                                agent_action.c.idempotency_key == idempotency_key,
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise ValueError("idempotency key reused with different arguments")
                    row = dict(existing)
                    row["_created"] = False
                    return row
                try:
                    # A savepoint lets us recover from a concurrent unique-key
                    # race without invalidating the surrounding transaction.
                    async with session.begin_nested():
                        await session.execute(
                            agent_action.insert().values(
                                action_id=action_id,
                                run_id=run_id,
                                plan_id=plan_id,
                                plan_version=plan_version,
                                tool_name=tool_name,
                                idempotency_key=idempotency_key,
                                request_hash=request_hash,
                                status=status,
                                arguments_json=arguments,
                                result_json=result,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                except IntegrityError:
                    # Another worker won the idempotency race. Re-read its
                    # durable result rather than executing a side effect twice.
                    raced = (
                        (
                            await session.execute(
                                select(agent_action).where(
                                    agent_action.c.run_id == run_id,
                                    agent_action.c.idempotency_key == idempotency_key,
                                )
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if raced:
                        if raced["request_hash"] != request_hash:
                            raise ValueError("idempotency key reused with different arguments")
                        row = dict(raced)
                        row["_created"] = False
                        return row
                    raise
        return {
            "action_id": action_id,
            "run_id": run_id,
            "tool_name": tool_name,
            "idempotency_key": idempotency_key,
            "status": status,
            "result_json": result,
            "_created": True,
        }

    async def update_action(
        self, action_id: str, status: str, result: dict[str, Any] | None = None
    ) -> None:
        async with self.database.session() as session:
            async with session.begin():
                current = (
                    await session.execute(
                        select(agent_action.c.status).where(agent_action.c.action_id == action_id)
                    )
                ).scalar_one_or_none()
                if current is None:
                    raise KeyError(f"action not found: {action_id}")
                if current in {"SUCCEEDED", "FAILED", "CANCELLED"} and current != status:
                    return
                if current == "UNKNOWN" and status not in {
                    "UNKNOWN",
                    "SUCCEEDED",
                    "FAILED",
                    "CANCELLED",
                }:
                    return
                await session.execute(
                    update(agent_action)
                    .where(agent_action.c.action_id == action_id)
                    .values(status=status, result_json=result, updated_at=utc_now())
                )
