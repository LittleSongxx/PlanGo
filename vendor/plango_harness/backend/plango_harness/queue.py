from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, cast


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
    return value if isinstance(value, dict) else {}


def _field(fields: dict[Any, Any], key: str, default: Any = "") -> Any:
    return fields.get(key, fields.get(key.encode(), default))


@dataclass(frozen=True)
class QueueItem:
    stream_id: str
    run_id: str
    kind: str = "run"
    stream: str = "plango:runs"
    payload: dict[str, Any] = field(default_factory=dict)


class RunQueue:
    """Redis Streams transport with an explicit sandbox local queue option."""

    def __init__(
        self,
        url: str,
        stream: str,
        group: str,
        *,
        allow_fallback: bool = True,
        max_retries: int = 3,
    ) -> None:
        self.url = url
        self.stream = stream
        self.group = group
        self.allow_fallback = allow_fallback
        self.max_retries = max(1, max_retries)
        self._failures: dict[str, int] = {}
        self._attempt_key = f"{stream}:attempts:{group}"
        self.consumer = f"{socket.gethostname()}-{id(self):x}"
        self.client: Any | None = None
        self.available = False
        self.local: asyncio.Queue[QueueItem] = asyncio.Queue()

    async def connect(self) -> None:
        if self.url == "local://":
            self.available = False
            return
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        try:
            import redis.asyncio as redis

            self.client = redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                # XREADGROUP blocks for one second; the socket timeout must be
                # longer or every idle poll looks like a worker failure.
                socket_timeout=5.0,
            )
            await cast(Any, self.client.ping())
            try:
                await self.client.xgroup_create(self.stream, self.group, id="0-0", mkstream=True)
            except Exception as exc:
                # BUSYGROUP means the desired group already exists.
                if "BUSYGROUP" not in str(exc):
                    raise
            self.available = True
        except Exception:
            self.available = False
            if self.client is not None:
                await self.client.aclose()
            self.client = None
            if not self.allow_fallback:
                raise

    async def enqueue(
        self,
        run_id: str,
        kind: str = "run",
        *,
        local: bool = True,
        stream: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> QueueItem:
        target_stream = stream or self.stream
        payload = payload or {}
        if not local and (not self.available or self.client is None):
            raise RuntimeError("queue_unavailable")
        stream_id = "local"
        if self.available and self.client is not None:
            stream_id = await self.client.xadd(
                target_stream,
                {"run_id": run_id, "kind": kind, "payload": json.dumps(payload, ensure_ascii=False)},
                maxlen=10000,
                approximate=True,
            )
        item = QueueItem(
            stream_id=str(stream_id),
            run_id=run_id,
            kind=kind,
            stream=target_stream,
            payload=payload,
        )
        if local:
            await self.local.put(item)
        return item

    async def ack(self, item: QueueItem) -> None:
        if self.available and self.client is not None and item.stream_id != "local":
            try:
                await self.client.xack(item.stream, self.group, item.stream_id)
            except Exception:
                pass

    async def publish(self, stream: str, fields: dict[str, Any]) -> str | None:
        if not self.available or self.client is None:
            return None
        try:
            value = {
                str(key): str(item)
                if not isinstance(item, (dict, list))
                else json.dumps(item, ensure_ascii=False)
                for key, item in fields.items()
            }
            return str(await self.client.xadd(stream, value, maxlen=20000, approximate=True))
        except Exception:
            return None

    async def consume(
        self,
        handler: Callable[[QueueItem], Awaitable[None]],
        stop: asyncio.Event | None = None,
    ) -> None:
        """Consume Redis messages, reclaiming abandoned pending entries."""
        if not self.available or self.client is None:
            return
        stop = stop or asyncio.Event()

        async def increment_attempt(item: QueueItem) -> int:
            if self.available and self.client is not None and item.stream_id != "local":
                try:
                    attempts = int(await self.client.hincrby(self._attempt_key, item.stream_id, 1))
                    await self.client.expire(self._attempt_key, 7 * 24 * 3600)
                    return attempts
                except Exception:
                    pass
            attempts = self._failures.get(item.stream_id, 0) + 1
            self._failures[item.stream_id] = attempts
            return attempts

        async def clear_attempt(item: QueueItem) -> None:
            self._failures.pop(item.stream_id, None)
            if self.available and self.client is not None and item.stream_id != "local":
                try:
                    await self.client.hdel(self._attempt_key, item.stream_id)
                except Exception:
                    pass

        async def deliver(item: QueueItem) -> None:
            attempts = await increment_attempt(item)
            try:
                await handler(item)
            except Exception as exc:
                if attempts >= self.max_retries:
                    await self.publish(
                        f"{self.stream}:dead-letter",
                        {
                            "run_id": item.run_id,
                            "kind": item.kind,
                            "stream_id": item.stream_id,
                            "attempts": attempts,
                            "error": type(exc).__name__,
                        },
                    )
                    await self.ack(item)
                    await clear_attempt(item)
                # Otherwise leave the message pending for XAUTOCLAIM.
                return
            await clear_attempt(item)
            await self.ack(item)

        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import ResponseError
        from redis.exceptions import TimeoutError as RedisTimeoutError

        while not stop.is_set():
            if not self.available or self.client is None:
                try:
                    await self.connect()
                except (RedisConnectionError, RedisTimeoutError):
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=1.0)
                    except TimeoutError:
                        pass
                    continue
            if self.client is None:
                continue
            try:
                # Reclaim messages left pending by a crashed worker. The durable
                # run lease/idempotency checks still decide whether execution is
                # allowed after reclamation.
                try:
                    reclaimed = await self.client.xautoclaim(
                        self.stream,
                        self.group,
                        self.consumer,
                        min_idle_time=90_000,
                        start_id="0-0",
                        count=10,
                    )
                    pending = reclaimed[1] if reclaimed else []
                    for stream_id, fields in pending:
                        payload = _decode_payload(fields.get("payload", fields.get(b"payload", "{}")))
                        item = QueueItem(
                            stream_id=str(stream_id),
                            run_id=str(_field(fields, "run_id")),
                            kind=str(_field(fields, "kind", "run")),
                            stream=self.stream,
                            payload=payload,
                        )
                        await deliver(item)
                except Exception:
                    pass
                rows = await self.client.xreadgroup(
                    self.group,
                    self.consumer,
                    {self.stream: ">"},
                    count=1,
                    block=1000,
                )
                for _stream, messages in rows or []:
                    for stream_id, fields in messages:
                        payload = _decode_payload(fields.get("payload", fields.get(b"payload", "{}")))
                        item = QueueItem(
                            stream_id=str(stream_id),
                            run_id=str(_field(fields, "run_id")),
                            kind=str(_field(fields, "kind", "run")),
                            stream=self.stream,
                            payload=payload,
                        )
                        await deliver(item)
            except (RedisConnectionError, RedisTimeoutError):
                self.available = False
            except ResponseError as exc:
                if "NOGROUP" not in str(exc):
                    raise
                self.available = False  # Recreate the group after an empty Redis restart.

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
        self.client = None
        self.available = False

    async def ping(self) -> bool:
        if not self.available or self.client is None:
            return False
        try:
            return bool(await cast(Any, self.client.ping()))
        except Exception:
            self.available = False
            return False
