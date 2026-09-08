"""Standalone Redis Streams worker: ``python -m plango_harness.worker``."""

from __future__ import annotations

import asyncio

from plango_harness.runtime import PlanGoRuntime
from plango_harness.settings import get_settings


async def main() -> None:
    runtime = PlanGoRuntime(get_settings(), embedded_worker=False)
    await runtime.start()
    try:
        await runtime.consume()
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
