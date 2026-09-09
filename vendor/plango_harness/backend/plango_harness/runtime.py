from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from langchain_core.messages import HumanMessage, messages_from_dict
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import NodeTimeoutError
from langgraph.types import Command
from sqlalchemy.exc import DBAPIError

from plango_harness.agent.contracts import RunPhase
from plango_harness.agent.graph import (
    GraphDeps,
    build_graph,
    checkpoint_serializer,
)
from plango_harness.agent.model_adapter import ModelAdapter
from plango_harness.agent.state import initial_state, planning_reset
from plango_harness.domain.planning import PlanEngine
from plango_harness.memory.embedding import EmbeddingService
from plango_harness.memory.repository import MemoryRepository
from plango_harness.observability import agent_span
from plango_harness.persistence.actions import ActionLedger
from plango_harness.persistence.database import Database, utc_now
from plango_harness.persistence.runs import InputAcceptance, RunRepository
from plango_harness.providers import SandboxActionProvider, WorldService
from plango_harness.queue import QueueItem, RunQueue
from plango_harness.settings import Settings
from plango_harness.tools.registry import ToolRegistry

TERMINAL_PHASES = {
    RunPhase.SUCCEEDED.value,
    RunPhase.CANCELLED.value,
    RunPhase.PARTIAL_FAILED.value,
    RunPhase.INFEASIBLE.value,
    RunPhase.FAILED.value,
}
PAUSED_PHASES = {RunPhase.WAITING_APPROVAL.value, RunPhase.REQUIREMENTS_READY.value}


class PlanGoRuntime:
    """Graph runtime shared by the HTTP API and the standalone worker."""

    def __init__(self, settings: Settings, *, embedded_worker: bool | None = None, world_service: Any | None = None, action_provider: Any | None = None) -> None:
        self.settings = settings
        self.embedded_worker = (
            settings.embedded_worker if embedded_worker is None else embedded_worker
        )
        self.database = Database(
            settings.database_url, allow_fallback=settings.allow_sqlite_fallback
        )
        self.embedding = EmbeddingService(settings)
        self.runs = RunRepository(self.database)
        self.ledger = ActionLedger(self.runs)
        self.memory = MemoryRepository(self.database, self.embedding)
        self.world_service: Any | None = world_service
        self.model = ModelAdapter(settings)
        self.tools = ToolRegistry()
        self.action_provider = action_provider or SandboxActionProvider(settings.seed)
        self.graph: Any | None = None
        self._checkpointer: Any | None = None
        self._stack = AsyncExitStack()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._lease_owners: dict[str, str] = {}
        self._local_worker_task: asyncio.Task[Any] | None = None
        self.queue = RunQueue(
            settings.redis_url,
            settings.event_stream,
            settings.worker_group,
            allow_fallback=settings.allow_redis_fallback,
            max_retries=settings.max_queue_retries,
        )
        self.memory_queue = RunQueue(
            settings.redis_url,
            settings.memory_stream,
            settings.worker_group,
            allow_fallback=settings.allow_redis_fallback,
            max_retries=settings.max_queue_retries,
        )
        self.worker_id = self.queue.consumer
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self.settings.ensure_dirs()
        await self.database.connect()
        await self.queue.connect()
        await self.memory_queue.connect()
        self.world_service = self.world_service or WorldService(self.settings)
        if self.settings.is_sqlite or self.database.degraded:
            # The installed sqlite checkpoint package does not pass a
            # serializer through its connection-string helper, so keep the
            # connection explicit and use the contract allow-list.
            checkpoint_conn = await aiosqlite.connect(str(self.settings.checkpoint_path))
            self._stack.push_async_callback(checkpoint_conn.close)
            checkpointer = AsyncSqliteSaver(checkpoint_conn, serde=checkpoint_serializer())
            await checkpointer.setup()
        else:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            checkpointer = await self._stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(
                    self.database.url.replace("+asyncpg", ""),
                    serde=checkpoint_serializer(),
                )  # type: ignore[arg-type]
            )
            await checkpointer.setup()
        self._checkpointer = checkpointer
        deps = GraphDeps(
            model=self.model,
            world=self.world_service.provider,
            planner=PlanEngine(self.world_service.provider, self.settings.seed),
            memory=self.memory,
            runs=self.runs,
            ledger=self.ledger,
            tools=self.tools,
            action_provider=self.action_provider,
            agent_mode=self.settings.agent_mode,
            max_turns=self.settings.max_turns,
            max_repair_rounds=self.settings.max_repair_rounds,
            max_context_tokens=self.settings.max_context_tokens,
            max_tool_calls=self.settings.max_tool_calls,
            max_run_seconds=self.settings.max_run_seconds,
            max_model_tokens=self.settings.max_model_tokens,
            node_timeout_seconds=max(60, min(180, round(self.settings.openai_timeout_seconds * 3))),
        )
        self.graph = self.build_graph(deps, checkpointer=checkpointer)
        self._started = True
        if self.embedded_worker:
            self._local_worker_task = asyncio.create_task(
                self._local_worker(), name="plango-local-worker"
            )
            await self._requeue_pending(local=True)
            if await self.memory.has_pending_embeddings():
                self._track_background(asyncio.create_task(self.memory.embed_pending()))

    def build_graph(self, deps, *, checkpointer):
        return build_graph(deps, checkpointer=checkpointer)

    async def close(self) -> None:
        if self._local_worker_task:
            self._local_worker_task.cancel()
            await asyncio.gather(self._local_worker_task, return_exceptions=True)
            self._local_worker_task = None
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        for run_id, owner in list(self._lease_owners.items()):
            await self.runs.release(run_id, owner)
        self._tasks.clear()
        self._lease_owners.clear()
        if self.world_service:
            await self.world_service.close()
        await self.model.close()
        await self.embedding.close()
        await self.queue.close()
        await self.memory_queue.close()
        await self._stack.aclose()
        await self.database.close()
        self._started = False

    async def create_run(self, user_id: str, input_text: str) -> dict[str, Any]:
        self._ensure_started()
        input_text = (input_text or "").strip()
        if not input_text:
            raise ValueError("input_text must not be empty")
        run_id = uuid.uuid4().hex
        event = await self.runs.create_with_event(run_id, user_id or "anonymous", input_text)
        await self._enqueue_run(run_id)
        return self._envelope(
            run_id=run_id,
            phase=RunPhase.CREATED.value,
            outcome=None,
            event_seq=event.seq,
            accepted=True,
        )

    async def send_message(self, run_id: str, text: str, *, acceptance: InputAcceptance | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("message text must not be empty")
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        if row.get("phase") in TERMINAL_PHASES:
            raise ValueError("run is already terminal; create a new run")
        state = dict(row.get("state_json") or {})
        if row.get("phase") == RunPhase.REPLANNING.value and state.get("pending_message"):
            raise ValueError("run is busy; wait for the accepted planning turn")
        paused = row.get("phase") == RunPhase.WAITING_APPROVAL.value or (row.get("phase") == RunPhase.REQUIREMENTS_READY.value and bool(state.get("clarification") or state.get("interrupt_id")))
        lease_until = row.get("lease_until")
        if lease_until is not None:
            if getattr(lease_until, "tzinfo", None) is None:
                lease_until = lease_until.replace(tzinfo=utc_now().tzinfo)
            if lease_until > utc_now() and not paused:
                raise ValueError("run is busy; wait for the current Agent turn")
        state = dict(row.get("state_json") or {})
        interrupted = row.get("phase") == RunPhase.WAITING_APPROVAL.value or bool(
            state.get("clarification") or state.get("interrupt_id")
        )
        if acceptance is not None and not interrupted:
            raise ValueError("run is busy; wait for the current Agent turn")
        kind = "resume" if interrupted else "run"
        payload = {
            "decision": "edit" if row.get("phase") == RunPhase.WAITING_APPROVAL.value else "resume",
            "text": text,
        } if interrupted else {}
        event = await self.runs.update_input_with_event(
            run_id=run_id, phase=RunPhase(row["phase"]), event_type="USER_MESSAGE",
            payload={"text": text}, text=text,
            command_payload=payload if interrupted else None,
            expected_version=int(row["version"]),
            acceptance=acceptance,
        )
        queued = await self._enqueue_run(run_id, kind=kind, payload={"command_id": event.payload["command_id"]} if interrupted else {})
        return self._envelope(
            run_id=run_id,
            phase=row["phase"],
            outcome=row.get("outcome"),
            event_seq=event.seq,
            accepted=True,
            queued=queued,
        )

    async def replan(self, run_id: str, reason: str, *, requirement_edit: dict[str, Any] | None = None, expected_version: int | None = None, acceptance: InputAcceptance | None = None) -> dict[str, Any]:
        """Start a fresh planning turn after a world/constraint change."""
        reason = (reason or "世界状态发生变化").strip()
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        if expected_version is not None and row["version"] != expected_version:
            raise ValueError("requirements_changed_reload_before_editing")
        if acceptance is None and row.get("phase") == RunPhase.REPLANNING.value and (row.get("state_json") or {}).get("pending_message") == reason:
            return self.snapshot(row)
        if row.get("phase") == RunPhase.REPLANNING.value and (row.get("state_json") or {}).get("pending_message"):
            raise ValueError("run is busy; wait for the accepted planning turn")
        if row.get("phase") == RunPhase.WAITING_APPROVAL.value and requirement_edit is None:
            await self.send_message(run_id, reason, acceptance=acceptance)
            return self.snapshot(await self.runs.get(run_id))
        lease_until = row.get("lease_until")
        if lease_until is not None:
            if getattr(lease_until, "tzinfo", None) is None:
                lease_until = lease_until.replace(tzinfo=utc_now().tzinfo)
            if lease_until > utc_now():
                raise ValueError("run is busy; wait for the current Agent turn")
        if row.get("cancel_requested") and acceptance is None:
            await self.runs.clear_cancel(run_id)
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        state = dict(row.get("state_json") or {})
        reset = planning_reset(state)
        if requirement_edit is not None:
            lock = requirement_edit.get("stop_lock")
            if lock:
                prior = dict(reset["previous_plan"])
                prior["stops"] = [{**stop, "locked": lock["locked"]} if stop["place_id"] == lock["place_id"] else stop for stop in prior["stops"]]
                reset["previous_plan"] = prior
            reset["structured_requirement_edit"] = {**requirement_edit, "turn_id": int(state.get("turn_id", 1)) + 1}
        messages = list(state.get("messages", []))
        messages.append(HumanMessage(content=reason, id=f"user:{run_id}:{int(state.get('turn_id', 1)) + 1}"))
        state.update(
            {
                "pending_message": reason,
                "messages": messages,
                "outcome": None,
                "reason": "",
                "phase": RunPhase.REPLANNING.value,
                "turn_count": 0,
                "turn_id": int(state.get("turn_id", 1)) + 1,
                "plan_version": int(state.get("plan_version", 0)),
                **reset,
                "clarification": None,
                "requirement_reference_at": utc_now().isoformat(),
                "turn_budget": {"id": "replan:" + uuid.uuid4().hex,
                                "grant_seq": int(row.get("last_event_seq") or 0) + 1,
                                "model_baseline": int(state.get("model_token_count", 0)),
                                "tool_baseline": int(state.get("tool_call_count", 0))},
                "repair_applied": False,
                "started_at": time.time(),
                "last_observation": {
                    "weather_changed": "雨" in reason or "天气" in reason,
                    "refresh_discovery": False,
                },
            }
        )
        await self.runs.save_state_and_events(
            state,
            expected_version=expected_version if expected_version is not None else int(row.get("version") or 1),
            input_text=reason,
            clear_pending_command=True,
            acceptance=acceptance,
            events=[
                {
                    "phase": RunPhase.REPLANNING,
                    "event_type": "REQUIREMENTS_EDITED" if requirement_edit is not None else "REPLAN_REQUESTED",
                    "payload": {"reason": reason, **({"edit": requirement_edit, "previous_plan": state.get("previous_plan")} if requirement_edit is not None else {})},
                    "agent_id": "supervisor",
                }
            ],
        )
        await self._enqueue_run(run_id)
        return self.snapshot(await self.runs.get(run_id))

    async def _requirement_reference(self, row, command=None) -> str:
        """Use the persisted acceptance event, never a retry's wall clock or payload timestamp."""
        seq = int((command or {}).get("event_seq") or 0)
        events = await self.runs.events(row["run_id"], after=max(0, seq - 1) if seq else max(0, int(row.get("last_event_seq") or 0) - 200), limit=1 if seq else 200)
        accepted = next((event for event in reversed(events) if event.event_type in {"RUN_CREATED", "USER_MESSAGE", "REPLAN_REQUESTED", "REQUIREMENTS_EDITED", "RESUME_REQUESTED"}), None)
        value = accepted.created_at if accepted else row.get("created_at")
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    async def enqueue_resume(
        self,
        run_id: str,
        decision: str,
        text: str = "",
        *,
        interrupt_id: str | None = None,
        acceptance: InputAcceptance | None = None,
    ) -> dict[str, Any]:
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        if decision not in {"approve", "reject", "edit", "resume"}:
            raise ValueError("decision must be approve, reject, edit, or resume")
        expected_interrupt = (row.get("state_json") or {}).get("interrupt_id")
        if (
            interrupt_id
            and expected_interrupt
            and interrupt_id not in {expected_interrupt, "approval", "clarification"}
        ):
            raise ValueError("interrupt_id_mismatch")
        if row.get("phase") not in {
            RunPhase.WAITING_APPROVAL.value,
            RunPhase.REQUIREMENTS_READY.value,
        }:
            return self.snapshot(row)
        if row["phase"] == RunPhase.REQUIREMENTS_READY.value and (decision != "resume" or not text.strip()):
            raise ValueError("clarification requires a nonempty resume answer")
        if row["phase"] == RunPhase.WAITING_APPROVAL.value and decision == "resume":
            raise ValueError("approval requires approve, reject, or edit")
        event = await self.runs.update_input_with_event(
            run_id=run_id,
            phase=RunPhase(row["phase"]),
            event_type="RESUME_REQUESTED",
            payload={"decision": decision, "has_text": bool(text)},
            text=text if text else None,
            command_payload={"decision": decision, "text": text},
            expected_version=int(row["version"]),
            acceptance=acceptance,
        )
        queued = await self._enqueue_run(
            run_id,
            kind="resume",
            payload={"command_id": event.payload["command_id"]},
        )
        result = self._envelope(
            run_id=run_id,
            phase=row["phase"],
            outcome=row.get("outcome"),
            event_seq=event.seq,
            accepted=True,
            queued=queued,
        )
        return result

    async def resume(
        self,
        run_id: str,
        decision: str,
        text: str = "",
        *,
        wait: bool = True,
        interrupt_id: str | None = None,
        wait_timeout: float | None = None,
    ) -> dict[str, Any]:
        """Queue a resume; ``wait`` is a compatibility helper for direct callers.

        HTTP always passes ``wait=False`` so a model turn never blocks a
        request. Tests and small embedded scripts may opt into the bounded
        wait to observe the terminal snapshot; ``wait_timeout`` can be
        increased for slower real-model probes.
        """
        before = await self.runs.get(run_id)
        initial_turn = int((before or {}).get("state_json", {}).get("turn_id", 1))
        result = await self.enqueue_resume(
            run_id, decision, text, interrupt_id=interrupt_id
        )
        if not wait or not result.get("accepted"):
            return result
        deadline = time.monotonic() + min(
            max(0.1, float(wait_timeout if wait_timeout is not None else 15.0)),
            float(self.settings.max_run_seconds),
        )
        while time.monotonic() < deadline:
            current = await self.runs.get(run_id)
            if current:
                phase = current.get("phase")
                state = current.get("state_json") or {}
                if decision in {"edit", "resume"}:
                    if (
                        int(state.get("turn_id", initial_turn)) > initial_turn
                        and (
                            phase in PAUSED_PHASES
                            or phase in TERMINAL_PHASES
                        )
                    ):
                        return self.snapshot(current)
                elif phase not in {
                    RunPhase.WAITING_APPROVAL.value,
                    RunPhase.REQUIREMENTS_READY.value,
                }:
                    return self.snapshot(current)
            await asyncio.sleep(0.02)
        return self.snapshot(await self.runs.get(run_id))

    async def cancel(self, run_id: str) -> dict[str, Any]:
        row = await self.runs.get(run_id)
        if not row:
            raise KeyError(run_id)
        event = None
        if row.get("phase") not in TERMINAL_PHASES:
            await self.runs.request_cancel(run_id)
            task = self._tasks.get(run_id)
            if task and not task.done():
                task.cancel()
                # Let LangGraph/SQLite release its checkpoint transaction
                # before the cancellation event is committed.
                await asyncio.gather(task, return_exceptions=True)
            current = await self.runs.get(run_id)
            if current:
                cancelled_state = dict(current.get("state_json") or {})
                cancelled_state.update(
                    {
                        "run_id": run_id,
                        "phase": RunPhase.CANCELLED,
                        "outcome": RunPhase.CANCELLED.value,
                        "reason": "用户取消",
                    }
                )
                events, _ = await self.runs.save_state_and_events(
                    cancelled_state,
                    expected_version=int(current.get("version") or 1),
                    events=[
                        {
                            "phase": RunPhase.CANCELLED,
                            "event_type": "RUN_CANCELLED",
                            "payload": {"reason": "用户取消"},
                            "agent_id": "runtime",
                        }
                    ],
                )
                event = events[0] if events else None
        snapshot = self.snapshot(await self.runs.get(run_id))
        if event:
            snapshot["event_seq"] = event.seq
        return snapshot

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.snapshot(await self.runs.get(run_id))

    async def get_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        return [
            event.model_dump(mode="json") for event in await self.runs.events(run_id, after=after)
        ]

    async def enqueue_embedding(self, source_id: str = "memory") -> None:
        """Kick the optional embedding worker after a document write."""
        if not self.settings.embedding_enabled:
            return
        if self.embedded_worker:
            del source_id
            self._track_background(asyncio.create_task(self.memory.embed_pending()))
        else:
            try:
                await self.memory_queue.enqueue(source_id, kind="memory-embed", local=False)
            except Exception:
                pass  # Pending document rows remain recoverable in PostgreSQL.

    def _track_background(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        """Keep fire-and-forget maintenance tasks inside the runtime lifecycle."""
        self._background_tasks.add(task)

        def finish(done: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(done)
            if not done.cancelled():
                # Consume an unexpected maintenance exception so it does not
                # become an unhandled-task warning during shutdown.
                done.exception()

        task.add_done_callback(finish)
        return task

    async def resolve_action(
        self,
        run_id: str,
        action_id: str,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an explicit resolution for a previously UNKNOWN action."""
        if status not in {"UNKNOWN", "SUCCEEDED", "FAILED", "CANCELLED"}:
            raise ValueError("invalid_action_resolution")
        action = await self.runs.get_action(run_id, action_id)
        if not action:
            raise KeyError(action_id)
        if action.get("status") != "UNKNOWN" and status != action.get("status"):
            raise ValueError("action_not_unknown")
        await self.runs.update_action(action_id, status, result or action.get("result_json"))
        row = await self.runs.get(run_id)
        if row:
            state = dict(row.get("state_json") or {})
            action_results = list(state.get("action_results") or [])
            for index, item in enumerate(action_results):
                if isinstance(item, dict) and item.get("action_id") == action_id:
                    action_results[index] = {
                        **item,
                        "status": status,
                        "result": result or item.get("result", {}),
                        "resolution_required": status == "UNKNOWN",
                    }
            state["action_results"] = action_results
            phase = (
                RunPhase.SUCCEEDED
                if action_results and all(item.get("status") == "SUCCEEDED" for item in action_results if isinstance(item, dict))
                else RunPhase.PARTIAL_FAILED
            )
            state.update({"phase": phase, "outcome": phase.value})
            if result and result.get("resolution_source") == "user_confirmation":
                state["reason"] = "用户在网站核对后确认已完成；此结果来自用户确认。" if phase == RunPhase.SUCCEEDED else "用户在网站核对后确认未完成或仍有其他待处理动作。"
            await self.runs.save_state_and_events(
                state,
                expected_version=int(row.get("version") or 1),
                events=[
                    {
                        "phase": phase,
                        "event_type": "ACTION_RESOLVED",
                        "payload": {"action_id": action_id, "status": status, **({"source": result.get("source"), "resolution_source": result.get("resolution_source"), "automatically_verified": result.get("automatically_verified")} if result else {})},
                        "agent_id": "runtime",
                    }
                ],
            )
        return await self.runs.get_action(run_id, action_id) or {}

    async def consume(self, stop: asyncio.Event | None = None) -> None:
        """Separate worker plus durable-command reconciliation."""
        self._ensure_started()
        stop = stop or asyncio.Event()
        await self._requeue_pending(local=False)
        async def reconcile() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2.0)
                except TimeoutError:
                    try:
                        await self._requeue_pending(local=False)
                        if self.settings.embedding_enabled and await self.memory.has_pending_embeddings():
                            await self.enqueue_embedding()
                    except (DBAPIError, OSError):
                        continue  # Retry after the database is reachable again.
        tasks = [asyncio.create_task(self.queue.consume(self._process_queue_item, stop)),
                 asyncio.create_task(self.memory_queue.consume(self._process_queue_item, stop)),
                 asyncio.create_task(reconcile())]
        try:
            await asyncio.gather(*tasks)
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _requeue_pending(self, *, local: bool) -> None:
        """Recover committed work even when Redis lost its stream entirely."""
        for run_id in await self.runs.pending_work():
            await self._enqueue_run(run_id, local=local)

    async def _enqueue_run(
        self, run_id: str, *, kind: str = "run",
        payload: dict[str, Any] | None = None, local: bool | None = None,
    ) -> bool:
        row = await self.runs.get(run_id)
        command = (row or {}).get("pending_command")
        if command:
            kind = "resume"
            payload = {"command_id": command["id"]}
        try:
            await self.queue.enqueue(run_id, kind=kind, payload=payload,
                                     local=self.embedded_worker if local is None else local)
            return True
        except Exception:
            # The DB already accepted this work. Keep it pending for the
            # worker's periodic reconciler instead of converting it to FAILED.
            return False

    async def _process_queue_item(self, item: QueueItem) -> None:
        if item.kind == "memory-embed":
            await self.memory.embed_pending()
        elif item.run_id:
            row = await self.runs.get(item.run_id)
            command = (row or {}).get("pending_command")
            if item.kind == "resume":
                if not command or item.payload.get("command_id") != command["id"]:
                    return  # Already applied or unbound legacy/stale delivery.
            if command:
                await self._run_graph(item.run_id, resume=command["payload"], command_id=command["id"])
            else:
                await self._run_graph(item.run_id)

    async def _local_worker(self) -> None:
        worker_task = asyncio.current_task()
        while True:
            item = await self.queue.local.get()
            task: asyncio.Task[Any] | None = None
            try:
                if item.run_id in self._tasks and not self._tasks[item.run_id].done():
                    continue
                task = asyncio.create_task(
                    self._process_queue_item(item), name=f"plango_harness-run-{item.run_id[:8]}"
                )
                self._tasks[item.run_id] = task
                await task
            except asyncio.CancelledError:
                # A user cancellation cancels only the run task; shutdown
                # cancellation targets the worker task itself.
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                if worker_task is not None and worker_task.cancelling():
                    raise
            except Exception as exc:
                # ``_run_graph`` owns the fenced failure transition. The
                # local queue has no reclaim cycle, so retain the exception in
                # the run projection and continue serving later messages.
                del exc
            finally:
                await self.queue.ack(item)
                self.queue.local.task_done()
                if task is not None and self._tasks.get(item.run_id) is task and task.done():
                    self._tasks.pop(item.run_id, None)

    async def _execution_budget(self, row, previous_state, *, resume: bool):
        """One durable grant per accepted user command; pauses only restore remaining work time."""
        projection = row.get("state_json") or {}
        now = time.time()
        budget = dict(projection.get("turn_budget") or previous_state.get("turn_budget") or {})
        fresh = bool(budget.get("id") and not budget.get("started_at"))
        if fresh:
            budget.update(started_at=now, deadline_at=now + self.settings.max_run_seconds,
                          model_baseline=max(int(budget.get("model_baseline", 0)), int(previous_state.get("model_token_count", 0))),
                          tool_baseline=max(int(budget.get("tool_baseline", 0)), int(previous_state.get("tool_call_count", 0))))
        elif not budget:
            budget = {"id": "initial", "grant_seq": 1, "model_baseline": 0, "tool_baseline": 0,
                      "started_at": previous_state.get("started_at") or now,
                      "deadline_at": previous_state.get("deadline_at") or (previous_state.get("started_at") or now) + self.settings.max_run_seconds}
        pause = projection.get("budget_pause")
        if resume and not fresh and "budget_pause" not in projection and projection.get("interrupt_id"):
            # Compatibility for already-paused checkpoints, using the durable
            # interrupt event's time instead of granting a new execution window.
            paused_at = (projection.get("browser_wait") or {}).get("paused_at")
            if not paused_at:
                events = await self.runs.events(row["run_id"], after=max(0, int(row.get("last_event_seq") or 0) - 200), limit=200)
                event = next((item for item in reversed(events) if item.event_type == "GRAPH_INTERRUPTED"), None)
                if event:
                    created = event.created_at
                    paused_at = (created if created.tzinfo else created.replace(tzinfo=timezone.utc)).timestamp()
            if paused_at:
                pause = {"remaining_seconds": max(0.0, float(budget["deadline_at"]) - float(paused_at))}
        if resume and not fresh and pause:
            budget["deadline_at"] = now + min(self.settings.max_run_seconds, max(0.0, float(pause["remaining_seconds"])))
            budget["resumed_at"] = now
        return budget, fresh

    async def _run_graph(self, run_id: str, resume: dict[str, Any] | None = None, command_id: str | None = None) -> dict[str, Any]:
        self._ensure_started()
        assert self.graph is not None
        # A unique owner per delivery fences a stale worker even when its
        # process survives past the lease expiry.
        lease_owner = f"{self.worker_id}:{uuid.uuid4().hex}"
        if not await self.runs.claim(run_id, lease_owner):
            snapshot = self.snapshot(await self.runs.get(run_id))
            snapshot["_not_claimed"] = True
            return snapshot
        self._lease_owners[run_id] = lease_owner
        heartbeat = asyncio.create_task(
            self._lease_heartbeat(run_id, lease_owner), name=f"plango_harness-lease-{run_id[:8]}"
        )
        deadline_at = 0.0
        try:
            row = await self.runs.get(run_id)
            if not row:
                raise KeyError(run_id)
            if row.get("cancel_requested"):
                return self.snapshot(row)
            if row.get("phase") in TERMINAL_PHASES and row.get("outcome"):
                # A duplicate resume can already be in Redis while the first
                # approval finishes; terminal runs are immutable no-ops.
                return self.snapshot(row)
            projection = row.get("state_json") or {}
            command = row.get("pending_command") or {}
            if command_id is not None:
                if command.get("id") != command_id:
                    return self.snapshot(row)
                if (int(projection.get("turn_id", 1)), int(projection.get("plan_version", 0))) != (command["turn_id"], command["plan_version"]):
                    raise RuntimeError("run command target changed")
            paused_for_clarification = bool(
                projection.get("clarification")
                or projection.get("interrupt_id")
            )
            if resume is None and (
                row.get("phase") == RunPhase.WAITING_APPROVAL.value
                or (
                    row.get("phase") == RunPhase.REQUIREMENTS_READY.value
                    and paused_for_clarification
                )
            ):
                # A redelivery after an interrupt must not advance a paused
                # checkpoint without an explicit resume command.
                return self.snapshot(row)
            config = {"configurable": {"thread_id": run_id}}
            checkpoint: Any | None = None
            checkpoint_values: dict[str, Any] = {}
            if resume is None or command_id is not None:
                try:
                    checkpoint = await self.graph.aget_state(config)
                    checkpoint_values = dict(getattr(checkpoint, "values", {}) or {})
                except Exception:
                    checkpoint = None
            # An explicit API replan updates the projection before the graph
            # checkpoint can move. Prefer that fresh planning cursor instead
            # of replaying the old terminal checkpoint.
            accepted_replan = bool(
                projection.get("phase") == RunPhase.REPLANNING.value
                and projection.get("pending_message")
            )
            # A matching durable turn already entered the graph, even if its projection lagged.
            consumed_replan = bool(accepted_replan and checkpoint_values.get("turn_id") == projection.get("turn_id")
                                   and (projection.get("turn_budget") or {}).get("id")
                                   and (checkpoint_values.get("turn_budget") or {}).get("id") == projection["turn_budget"]["id"])
            fresh_replan = accepted_replan and not consumed_replan
            previous_state = projection if fresh_replan else (checkpoint_values or projection)
            if int(projection.get("model_call_count", 0)) >= int(previous_state.get("model_call_count", 0)):
                previous_state = {**previous_state, **{k:v for k,v in projection.items() if k.startswith("model_")}}
            previous_state = {**previous_state, **{key: max(int(projection.get(key, 0)), int(previous_state.get(key, 0))) for key in ("tool_call_count", "model_token_count")}}
            budget, fresh_budget = await self._execution_budget(row, previous_state, resume=resume is not None)
            deadline_at = float(budget["deadline_at"])
            budget_updates = {"turn_budget": budget, "deadline_at": deadline_at,
                              "tool_call_count": previous_state["tool_call_count"],
                              **({"browser_steps": 0, "turn_count": 0} if fresh_budget else {})}
            if budget != projection.get("turn_budget") or projection.get("budget_pause") or "budget_pause" not in projection:
                # Persist consumption before invoking any graph node. A crash
                # or duplicate delivery cannot apply the same pause/grant twice.
                await self.runs.save_state_and_events(
                    {**projection, **budget_updates, "budget_pause": None}, expected_version=int(row["version"]), lease_owner=lease_owner,
                    events=[{"phase": RunPhase(row["phase"]), "event_type": "TURN_BUDGET_STARTED" if fresh_budget else "EXECUTION_BUDGET_RESTORED",
                             "payload": {"budget_id": budget["id"], "model_baseline": budget["model_baseline"], "tool_baseline": budget["tool_baseline"], "remaining_seconds": max(0.0, deadline_at - time.time())}}],
                )
                row = await self.runs.get(run_id)
                if row is None:
                    raise KeyError(run_id)
                projection = row.get("state_json") or {}
            self.model.reset_run(
                int(previous_state.get("model_token_count", 0) or 0),
                call_count=int(previous_state.get("model_call_count", 0) or 0),
                fallback_count=int(previous_state.get("model_fallback_count", 0) or 0),
                total_latency_ms=float(previous_state.get("model_total_latency_ms", 0.0) or 0.0),
                last_error=previous_state.get("model_last_error"),
                last_usage=previous_state.get("model_last_usage") or {},
                call_records=previous_state.get("model_calls") or [],
            )
            self.model.set_run_budget(
                deadline_at,
                token_baseline=int(budget["model_baseline"]),
                cleanup_reserve_seconds=min(5.0, max(1.0, self.settings.max_run_seconds / 20)),
            )
            # The projection is the durable cursor for audit rows. If a
            # worker wrote a LangGraph checkpoint and crashed before this
            # transaction, replay the checkpoint's trace from the projection
            # length instead of silently skipping the missing events.
            old_trace_len = len(projection.get("trace", []))
            input_state: Any
            if resume is None:
                # Resume unfinished nodes, or publish the already completed replan checkpoint.
                if (
                    consumed_replan or (checkpoint is not None
                    and not fresh_replan
                    and (getattr(checkpoint, "next", None) or ()))
                ):
                    input_state = None
                elif not previous_state.get("turn_count", 0) and not fresh_replan:
                    input_state = initial_state(
                        run_id=run_id,
                        user_id=row["user_id"],
                        input_text=row["input_text"],
                        thread_id=run_id,
                    )
                    input_state["deadline_at"] = deadline_at
                    input_state["requirement_reference_at"] = await self._requirement_reference(row, command)
                    input_state["selected_poi"] = projection.get("selected_poi")
                else:
                    pending = row["input_text"]
                    turn = int(previous_state.get("turn_id", 1)) + (0 if fresh_replan else 1)
                    messages: list[Any] = [HumanMessage(content=pending, id=f"user:{run_id}:{turn}")]
                    if not checkpoint_values:
                        # Only rebuilding a missing checkpoint needs the persisted history.
                        # A fresh replan projection already ends with its accepted message.
                        history = previous_state.get("messages", [])
                        history = history[:-1] if fresh_replan else history
                        messages = [*messages_from_dict([{"type": message["type"], "data": message} for message in history]), *messages]
                    input_state = {
                        "input_text": pending,
                        "messages": messages,
                        "previous_spec": previous_state.get("previous_spec")
                        or previous_state.get("trip_spec"),
                        "requirement_reference_at": await self._requirement_reference(row, command),
                        "previous_plan": previous_state.get("selected_plan") or previous_state.get("previous_plan") or next(iter(previous_state.get("candidate_plans") or []), None),
                        "selected_poi": previous_state.get("selected_poi") or projection.get("selected_poi"),
                        "requirement_patch": [], "requirement_refresh": {},
                        "structured_requirement_edit": previous_state.get("structured_requirement_edit"),
                        "execution_goal": None, "execution_outcome": None,
                        "preparation_restart": previous_state.get("preparation_restart"),
                        "trip_spec": None,
                        "plan_draft": None,
                        "draft_errors": [],
                        "place_candidates": previous_state.get("place_candidates", []),
                        "candidate_plans": [],
                        "advocate_reports": [],
                        "delegated_roles": [],
                        "selected_plan": None,
                        "verifier": None,
                        "critique": None,
                        "action_proposal": None,
                        "approval_decision": None,
                        "execution_started": False,
                        "reflection_done": False,
                        "action_results": [],
                        "memory_delta": [],
                        "interrupt_id": None,
                        "evidence": previous_state.get("evidence", []),
                        "weather": previous_state.get("weather"),
                        "outcome": None,
                        "reason": "",
                        "last_observation": previous_state.get("last_observation"),
                        "phase": RunPhase.CREATED,
                        "turn_count": 0,
                        "turn_id": turn,
                        "plan_version": int(previous_state.get("plan_version", 0)),
                        "tool_call_count": int(previous_state.get("tool_call_count", 0) or 0),
                        "repair_round": 0,
                        "repair_applied": False,
                        "model_token_count": int(previous_state.get("model_token_count", 0) or 0),
                        "model_call_count": int(previous_state.get("model_call_count", 0) or 0),
                        "model_fallback_count": int(previous_state.get("model_fallback_count", 0) or 0),
                        "model_total_latency_ms": float(
                            previous_state.get("model_total_latency_ms", 0.0) or 0.0
                        ),
                        "model_last_error": previous_state.get("model_last_error"),
                        "model_last_usage": previous_state.get("model_last_usage") or {},
                        "model_calls": previous_state.get("model_calls") or [],
                        "started_at": previous_state.get("started_at") or time.time(),
                        "deadline_at": deadline_at,
                        "timeout_stage": None,
                        "pending_message": None,
                    }
                    if not checkpoint_values:
                        input_state = {**previous_state, **input_state}
            else:
                if command_id and checkpoint_values.get("consumed_command_id") == command_id:
                    input_state = None  # Resume after the already-applied interrupt, never answer it twice.
                else:
                    updates: dict[str, Any] = {}
                    if resume.get("decision") in {"edit", "resume"} and str(resume.get("text") or "").strip():
                        updates["requirement_reference_at"] = await self._requirement_reference(row, command)
                    input_state = Command(resume={**resume, "_command_id": command_id}, update=updates or None)
            if isinstance(input_state, Command):
                input_state = Command(resume=input_state.resume, update={**(input_state.update or {}), **budget_updates})
            elif input_state is None:
                input_state = Command(update=budget_updates)
            else:
                input_state.update(budget_updates)
            async with agent_span(
                "plan", run_id=run_id, phase=row.get("phase"), model=self.model.metadata.model
            ):
                result = await self.graph.ainvoke(input_state, config)
            latest = await self.runs.get(run_id)
            if latest and latest.get("cancel_requested"):
                return self.snapshot(latest)
            state = {key: value for key, value in result.items() if key != "__interrupt__"}
            state.update(
                {
                    "model_token_count": self.model.total_tokens,
                    "model_call_count": self.model.call_count,
                    "model_fallback_count": self.model.fallback_count,
                    "model_total_latency_ms": round(self.model.total_latency_ms, 2),
                    "model_last_error": self.model.last_error,
                    "model_last_usage": dict(self.model.last_usage),
                    "model_calls": list(self.model.call_records),
                }
            )
            state.pop("pending_message", None)
            interrupt_payload = self._jsonable(result.get("__interrupt__"))
            interrupt_phase = None
            state["budget_pause"] = None
            if interrupt_payload:
                first = (
                    interrupt_payload[0]
                    if isinstance(interrupt_payload, list)
                    else interrupt_payload
                )
                interrupt_phase = (
                    RunPhase.WAITING_APPROVAL
                    if isinstance(first, dict) and first.get("type") == "approval"
                    else RunPhase.REQUIREMENTS_READY
                )
                state["phase"] = interrupt_phase
                state["budget_pause"] = {"budget_id": budget["id"], "paused_at": time.time(),
                                         "remaining_seconds": max(0.0, deadline_at - time.time())}
                if isinstance(first, dict):
                    interrupt_id = first.get("id")
                    if interrupt_id:
                        state["interrupt_id"] = str(interrupt_id)
                    if first.get("type") == "browser":
                        state["browser_wait"] = first
                    else:
                        state["browser_wait"] = None
                    if first.get("type") == "clarification":
                        state["clarification"] = {
                            **(state.get("clarification") or {}),
                            "question": str(first.get("question") or "还需要补充哪些约束？")
                        }
            events: list[dict[str, Any]] = []
            trace = state.get("trace", [])
            for item in trace[old_trace_len:]:
                phase_value = item.get("phase") or state.get("phase") or RunPhase.CREATED
                try:
                    phase = RunPhase(phase_value)
                except ValueError:
                    phase = RunPhase.FAILED
                events.append(
                    {
                        "phase": phase,
                        "event_type": str(item.get("event", "STATE_UPDATE")),
                        "payload": item.get("payload") or {},
                        "agent_id": item.get("agent_id"),
                    }
                )
            if interrupt_payload:
                events.append(
                    {
                        "phase": interrupt_phase or RunPhase.REQUIREMENTS_READY,
                        "event_type": "GRAPH_INTERRUPTED",
                        "payload": {"interrupts": interrupt_payload},
                        "agent_id": "supervisor",
                    }
                )
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self.runs.save_state_and_events(
                state,
                expected_version=int(row.get("version") or 1),
                lease_owner=lease_owner,
                events=events,
                command_id=command_id,
                release_lease=True,
            )
            if state.get("memory_delta"):
                await self.enqueue_embedding(run_id)
            return self.snapshot(await self.runs.get(run_id))
        except (Exception, asyncio.CancelledError) as exc:
            row = await self.runs.get(run_id)
            is_cancelled = isinstance(exc, asyncio.CancelledError)
            row_deadline = float(((row or {}).get("state_json") or {}).get("deadline_at") or deadline_at or 0)
            # Worker cancellation is also the deadline signal. Keep it in
            # the timeout path so the persisted failure is auditable.
            timed_out = isinstance(exc, (TimeoutError, asyncio.TimeoutError, NodeTimeoutError)) or (
                is_cancelled and row_deadline > 0 and time.time() >= row_deadline
            )
            if is_cancelled and not (row_deadline and time.time() >= row_deadline):
                raise
            if row and row.get("phase") not in TERMINAL_PHASES and not row.get("cancel_requested"):
                try:
                    failure_state = dict(row.get("state_json") or {})
                    checkpoint_id = ""
                    checkpoint_trace: list[dict[str, Any]] = []
                    try:
                        checkpoint = await self.graph.aget_state({"configurable": {"thread_id": run_id}})
                        values = dict(getattr(checkpoint, "values", {}) or {})
                        if values:
                            failure_state.update(values)
                        checkpoint_config = getattr(checkpoint, "config", {}) or {}
                        checkpoint_id = str(
                            (checkpoint_config.get("configurable") or {}).get("checkpoint_id") or ""
                        )
                        checkpoint_trace = list(failure_state.get("trace") or [])
                    except Exception:
                        pass
                    failed_stage = str(
                        getattr(exc, "node", None)
                        or (getattr(exc, "__dict__", {}) or {}).get("node")
                        or ("runtime" if is_cancelled else "graph")
                    )
                    failure_state.update(
                        {
                            "run_id": run_id,
                            "phase": RunPhase.FAILED,
                            "outcome": RunPhase.FAILED.value,
                            "reason": "超过本次运行时间上限" if timed_out else str(exc),
                            "timeout_stage": failed_stage if timed_out else failure_state.get("timeout_stage"),
                            "model_token_count": self.model.total_tokens,
                            "model_call_count": self.model.call_count,
                            "model_fallback_count": self.model.fallback_count,
                            "model_total_latency_ms": round(self.model.total_latency_ms, 2),
                            "model_last_error": self.model.last_error,
                            "model_last_usage": dict(self.model.last_usage),
                            "model_calls": list(self.model.call_records),
                        }
                    )
                    old_trace_len = len((row.get("state_json") or {}).get("trace", []))
                    failure_events: list[dict[str, Any]] = []
                    for item in checkpoint_trace[old_trace_len:]:
                        try:
                            phase = RunPhase(item.get("phase") or failure_state.get("phase") or RunPhase.FAILED)
                        except ValueError:
                            phase = RunPhase.FAILED
                        failure_events.append(
                            {
                                "phase": phase,
                                "event_type": str(item.get("event", "STATE_UPDATE")),
                                "payload": item.get("payload") or {},
                                "agent_id": item.get("agent_id"),
                            }
                        )
                    failure_events.append(
                        {
                            "phase": RunPhase.FAILED,
                            "event_type": "RUN_TIMEOUT" if timed_out else "RUN_FAILED",
                            "payload": {
                                "error": type(exc).__name__,
                                "detail": str(exc),
                                "timeout_stage": failed_stage if timed_out else None,
                                "failed_stage": failed_stage,
                                "checkpoint_id": checkpoint_id,
                            },
                            "agent_id": "runtime",
                        }
                    )
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                    await self.runs.save_state_and_events(
                        failure_state,
                        expected_version=int(row.get("version") or 1),
                        lease_owner=lease_owner,
                        events=failure_events,
                        command_id=command_id,
                        release_lease=True,
                    )
                except RuntimeError:
                    # A newer owner won the lease; its state is authoritative.
                    pass
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self.runs.release(run_id, lease_owner)
            if self._lease_owners.get(run_id) == lease_owner:
                self._lease_owners.pop(run_id, None)

    async def _lease_heartbeat(self, run_id: str, lease_owner: str) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                if not await self.runs.renew(run_id, lease_owner):
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                # The graph's authoritative state/error path will handle a
                # persistent database outage; keep trying while the run lives.
                continue

    async def _append_event(self, **kwargs: Any):
        return await self.runs.append_event(**kwargs)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [PlanGoRuntime._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): PlanGoRuntime._jsonable(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            return PlanGoRuntime._jsonable(value.model_dump(mode="json"))
        if hasattr(value, "value"):
            return value.value
        return str(value)

    @staticmethod
    def _envelope(**values: Any) -> dict[str, Any]:
        values.setdefault("phase", RunPhase.CREATED.value)
        values.setdefault("outcome", None)
        values.setdefault("event_seq", 0)
        return values

    @staticmethod
    def snapshot(row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {}
        state = row.get("state_json") or {}
        phase = row.get("phase") or state.get("phase") or RunPhase.CREATED.value
        outcome = row.get("outcome") or state.get("outcome")
        interrupt_id = state.get("interrupt_id") or (
            "approval"
            if phase == RunPhase.WAITING_APPROVAL.value
            else ("clarification" if state.get("clarification") else None)
        )
        return {
            "run_id": row.get("run_id"),
            "thread_id": row.get("thread_id"),
            "user_id": row.get("user_id"),
            "input_text": row.get("input_text"),
            "phase": phase,
            "outcome": outcome,
            "event_seq": int(row.get("last_event_seq") or 0),
            "interrupt_id": interrupt_id,
            "version": row.get("version"),
            "state": PlanGoRuntime._jsonable(state),
            "cancel_requested": bool(row.get("cancel_requested")),
            "command_pending": bool(row.get("pending_command")),
            "updated_at": PlanGoRuntime._jsonable(row.get("updated_at")),
        }

    def _ensure_started(self) -> None:
        if not self._started or self.graph is None:
            raise RuntimeError("PlanGoRuntime is not started")
