"""Standalone Redis Streams worker: ``python -m planora.worker``."""

from __future__ import annotations

import asyncio

from planora.runtime import PlanoraRuntime
from planora.settings import get_settings


async def main() -> None:
    runtime = PlanoraRuntime(get_settings(), embedded_worker=False)
    await runtime.start()
    try:
        await runtime.consume()
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
