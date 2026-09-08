"""Explicit local reminders; listing due rows never sends external messages."""

import time
import uuid
from datetime import datetime

from fastapi import Depends, HTTPException
from planora.persistence.database import metadata
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import BigInteger, Column, String, Table, delete, insert, select, update

reminders = Table(
    "yoyu_reminder",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("text", String(2000), nullable=False),
    Column("at_ms", BigInteger, nullable=False, index=True),
    Column("fired_at_ms", BigInteger),
)


def now_ms():
    return int(time.time() * 1000)


class NewReminder(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=2000)
    at: datetime

    @field_validator("at", mode="before")
    @classmethod
    def require_iso(cls, value):
        if not isinstance(value, str):
            raise ValueError("at must be an ISO 8601 string with timezone")
        return value

    @field_validator("at")
    @classmethod
    def require_future(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("at must include a timezone")
        if int(value.timestamp() * 1000) <= now_ms():
            raise ValueError("reminder time must be in the future")
        return value


async def setup_reminders(runtime):
    await runtime.database.connect()
    async with runtime.database.engine.begin() as connection:
        await connection.run_sync(lambda conn: reminders.create(conn, checkfirst=True))


def install_reminder_routes(app, runtime, auth):
    protected = [Depends(auth)]

    async def listing(*, due=False):
        query = select(reminders).order_by(reminders.c.at_ms, reminders.c.id)
        if due:
            query = query.where(reminders.c.fired_at_ms.is_(None), reminders.c.at_ms <= now_ms())
        async with runtime.database.session() as session:
            rows = (await session.execute(query)).mappings().all()
        return {
            "reminders": [
                {
                    "id": r["id"],
                    "text": r["text"],
                    "at": r["at_ms"],
                    "fired": r["fired_at_ms"] is not None,
                }
                for r in rows
            ],
            "history": [
                {"id": r["id"], "text": r["text"], "ts": r["fired_at_ms"], "kind": "reminder"}
                for r in sorted(rows, key=lambda r: r["fired_at_ms"] or 0, reverse=True)
                if r["fired_at_ms"] is not None
            ],
        }

    @app.get("/api/v1/reminders", dependencies=protected)
    async def list_reminders():
        return await listing()

    @app.get("/api/v1/reminders/due", dependencies=protected)
    async def due_reminders():
        return await listing(due=True)

    @app.post("/api/v1/reminders", dependencies=protected, status_code=201)
    async def create_reminder(body: NewReminder):
        async with runtime.database.session() as session:
            async with session.begin():
                await session.execute(
                    insert(reminders).values(
                        id=uuid.uuid4().hex, text=body.text, at_ms=int(body.at.timestamp() * 1000)
                    )
                )
        return await listing()

    @app.delete("/api/v1/reminders/{reminder_id}", dependencies=protected)
    async def remove_reminder(reminder_id: str):
        async with runtime.database.session() as session:
            async with session.begin():
                result = await session.execute(
                    delete(reminders).where(reminders.c.id == reminder_id)
                )
                if not result.rowcount:
                    raise HTTPException(404, "reminder not found")
        return await listing()

    @app.post("/api/v1/reminders/{reminder_id}/ack", dependencies=protected)
    async def acknowledge_reminder(reminder_id: str):
        # Duplicate delivery acknowledgements preserve the original firing time.
        async with runtime.database.session() as session:
            async with session.begin():
                row = (
                    (await session.execute(select(reminders).where(reminders.c.id == reminder_id)))
                    .mappings()
                    .first()
                )
                if row is None:
                    raise HTTPException(404, "reminder not found")
                timestamp = now_ms()
                if row["at_ms"] > timestamp:
                    raise HTTPException(409, "reminder is not due")
                await session.execute(
                    update(reminders)
                    .where(reminders.c.id == reminder_id, reminders.c.fired_at_ms.is_(None))
                    .values(fired_at_ms=timestamp)
                )
        return await listing()
