from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import Executable

metadata = MetaData()


class _CancellationSafeSession(AsyncSession):
    """Let an in-flight SQLite operation finish before propagating cancel."""

    async def execute(self, statement: Executable, params=None, **kwargs) -> Result:
        operation = asyncio.create_task(
            super().execute(statement, params, **kwargs)
        )
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(operation)
            except BaseException:
                pass
            raise

memory_fact = Table(
    "user_fact",
    metadata,
    # SQLite and PostgreSQL both support text IDs; UUID is deliberately kept at
    # the application boundary so fixtures remain portable.
    Column("id", String(64), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("namespace", String(128), nullable=False, default="user"),
    Column("fact_key", String(128), nullable=False),
    Column("value_json", JSON, nullable=False),
    Column("source", String(128), nullable=False),
    Column("confidence", Float, nullable=False, default=0.5),
    Column("valid_from", DateTime(timezone=True)),
    Column("valid_to", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "namespace", "fact_key", name="uq_user_fact_key"),
)

memory_episode = Table(
    "trip_episode",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("summary", Text, nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("source_event_id", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True)),
)

procedural_rule = Table(
    "procedural_rule",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("namespace", String(128), nullable=False, default="user"),
    Column("predicate", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("source_event_id", String(128), nullable=False),
    Column("confidence", Float, nullable=False, default=0.5),
    Column("hit_count", Integer, nullable=False, default=1),
    Column("status", String(32), nullable=False, default="active"),
    Column("expires_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "namespace", "predicate", "action", name="uq_procedural_rule"),
)

memory_document = Table(
    "memory_document",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("namespace", String(128), nullable=False, default="user"),
    Column("kind", String(64), nullable=False),
    Column("content", Text, nullable=False),
    Column("metadata_json", JSON, nullable=False),
    Column("episode_id", String(64)),
    # Optional external-document provenance.  The planning runtime does not
    # depend on it, but opt-in document ingestion can expose stable citations.
    Column("source_id", String(128)),
    Column("source_version", String(64)),
    Column("chunk_index", Integer),
    Column("source_hash", String(64)),
    # Portable JSON storage is the fallback. PostgreSQL adds a native vector
    # column at startup (and keeps this JSON copy for provider-independent
    # export/debugging).
    Column("embedding_json", Text),
    Column("embedding_model", String(128)),
    Column("importance", Float, nullable=False, default=0.5),
    Column("confidence", Float, nullable=False, default=0.5),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True)),
    Column(
        "embedding_status",
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
    ),
    Column("embedding_attempts", Integer, nullable=False, default=0, server_default="0"),
    Column("embedding_error", Text),
    Column("embedding_next_attempt_at", DateTime(timezone=True)),
)

memory_event = Table(
    "memory_event",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("event_kind", String(64), nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

agent_run = Table(
    "agent_run",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("thread_id", String(128), nullable=False, index=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("input_text", Text, nullable=False),
    Column("phase", String(64), nullable=False),
    Column("outcome", String(64)),
    Column("version", Integer, nullable=False, default=1),
    Column("last_event_seq", Integer, nullable=False, default=0),
    Column("lease_owner", String(128)),
    Column("lease_until", DateTime(timezone=True)),
    Column("cancel_requested", Integer, nullable=False, default=0),
    Column("state_json", JSON, nullable=False),
    Column("pending_command", JSON(none_as_null=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

run_event = Table(
    "run_event",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("event_type", String(96), nullable=False),
    Column("phase", String(64), nullable=False),
    Column("agent_id", String(96)),
    Column("payload_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

input_acceptance = Table(
    "input_acceptance",
    metadata,
    Column("request_id", String(128), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("request_fingerprint", String(64)),
    Column("run_id", String(64), nullable=False),
    Column("event_seq", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

agent_action = Table(
    "agent_action",
    metadata,
    Column("action_id", String(64), primary_key=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("plan_id", String(64), nullable=False),
    Column("plan_version", Integer, nullable=False),
    Column("tool_name", String(128), nullable=False),
    Column("idempotency_key", String(192), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("status", String(48), nullable=False),
    Column("arguments_json", JSON, nullable=False),
    Column("result_json", JSON),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("run_id", "idempotency_key", name="uq_agent_action_idempotency"),
)

run_plan = Table(
    "run_plan",
    metadata,
    Column("run_id", String(64), nullable=False),
    Column("plan_version", Integer, nullable=False),
    Column("plan_id", String(64), nullable=False),
    Column("plan_json", JSON, nullable=False),
    Column("verifier_json", JSON),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("run_id", "plan_version", name="pk_run_plan"),
)


class Database:
    """Async SQLAlchemy database with fallback disabled in service settings."""

    def __init__(self, url: str, *, allow_fallback: bool = True) -> None:
        self.requested_url = url
        self.url = url
        self.allow_fallback = allow_fallback
        self.engine: AsyncEngine | None = None
        self.sessions: async_sessionmaker[AsyncSession] | None = None
        self.degraded = False
        self.vector_available = False
        self.lexical_available = False
        self._session_close_tasks: set[asyncio.Task[object]] = set()

    async def connect(self) -> None:
        if self.engine is not None:
            return
        if self.url.startswith("sqlite") and ":memory:" not in self.url:
            sqlite_path = self.url.split("///", 1)[-1]
            Path(sqlite_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        connect_options = (
            {"check_same_thread": False, "timeout": 30} if self.url.startswith("sqlite") else {}
        )
        engine_options = {
            "pool_pre_ping": True,
            "pool_recycle": 1800,
            "connect_args": connect_options,
        }
        self.engine = create_async_engine(self.url, **engine_options)
        try:
            if self.url.startswith("sqlite"):
                async with self.engine.begin() as sqlite_conn:
                    await sqlite_conn.execute(text("PRAGMA journal_mode=WAL"))
                    await sqlite_conn.execute(text("PRAGMA busy_timeout=30000"))
            if self.url.startswith("sqlite"):
                # ``create_all`` is intentionally limited to the explicit
                # sandbox/test database. Service PostgreSQL is migrated by the
                # Compose Alembic gate before API/Worker startup.
                async with self.engine.begin() as conn:
                    await conn.run_sync(metadata.create_all)
                    await self._ensure_sqlite_columns(conn)
                self.lexical_available = True
            else:
                async with self.engine.begin() as conn:
                    migrated = await conn.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = 'public' AND table_name = 'agent_run') "
                            "AND EXISTS (SELECT 1 FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'agent_run' "
                            "AND column_name = 'pending_command') "
                            "AND EXISTS (SELECT 1 FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'input_acceptance' "
                            "AND column_name = 'request_fingerprint')"
                        )
                    )
                    if not bool(migrated.scalar()):
                        raise RuntimeError("database schema is not migrated; run alembic upgrade head")
                    extensions = await conn.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') "
                            "AND EXISTS (SELECT 1 FROM information_schema.columns "
                            "WHERE table_name = 'memory_document' AND column_name = 'embedding') AS vector_ok, "
                            "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') AS lexical_ok"
                        )
                    )
                    row = extensions.first()
                    self.vector_available = bool(row[0]) if row else False
                    self.lexical_available = bool(row[1]) if row else False
        except Exception:
            await self.engine.dispose()
            if not self.allow_fallback:
                self.engine = None
                raise
            # A local SQLite store is available only to callers that explicitly
            # opt into fallback (the canonical sandbox/test setting).
            self.url = "sqlite+aiosqlite:///./data/plango-local.sqlite"
            Path("./data").mkdir(parents=True, exist_ok=True)
            self.engine = create_async_engine(
                self.url,
                connect_args={"check_same_thread": False, "timeout": 30},
            )
            async with self.engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
                await self._ensure_sqlite_columns(conn)
            self.degraded = True
            self.vector_available = False
            self.lexical_available = True
        self.sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=_CancellationSafeSession
        )

    @staticmethod
    async def _ensure_sqlite_columns(conn) -> None:
        """Add portable columns to pre-migration sandbox databases."""
        rows = await conn.execute(text("PRAGMA table_info(memory_document)"))
        existing = {str(row[1]) for row in rows.fetchall()}
        definitions = {
            "source_id": "VARCHAR(128)",
            "source_version": "VARCHAR(64)",
            "chunk_index": "INTEGER",
            "source_hash": "VARCHAR(64)",
        }
        for name, definition in definitions.items():
            if name not in existing:
                await conn.execute(
                    text(f"ALTER TABLE memory_document ADD COLUMN {name} {definition}")
                )
        rows = await conn.execute(text("PRAGMA table_info(agent_run)"))
        if "pending_command" not in {str(row[1]) for row in rows.fetchall()}:
            await conn.execute(text("ALTER TABLE agent_run ADD COLUMN pending_command JSON"))

    async def close(self) -> None:
        pending = tuple(task for task in self._session_close_tasks if not task.done())
        if pending:
            cleanup = asyncio.gather(*pending, return_exceptions=True)
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(cleanup)
                except BaseException:
                    pass
                raise
        if self.engine is not None:
            await self.engine.dispose()
        self.engine = None
        self.sessions = None

    async def ping(self) -> bool:
        try:
            async with self.session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self.sessions is None:
            await self.connect()
        assert self.sessions is not None
        session = self.sessions()
        try:
            yield session
        finally:
            # Cancellation can arrive while an SSE poll is awaiting SQLite.
            # Shield the rollback/close so the connection is returned before
            # the request task exits.
            task = asyncio.current_task()
            cancelled = bool(task and task.cancelling())

            async def cleanup_session() -> None:
                try:
                    if cancelled:
                        await session.invalidate()
                    else:
                        await session.rollback()
                finally:
                    await session.close()

            close_task = asyncio.create_task(cleanup_session())
            self._session_close_tasks.add(close_task)
            close_task.add_done_callback(self._session_close_tasks.discard)
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(close_task)
                except BaseException:
                    # The cancelled DB operation may already have invalidated
                    # the connection; consume cleanup errors and preserve the
                    # original request cancellation.
                    pass
                raise


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
