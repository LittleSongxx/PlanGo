"""Optional PlanGo-owned Redis worker; desktop mode embeds this runtime in the API."""

import asyncio

from .runtime import DesktopRuntime
from .settings import settings_from_env


async def main():
    settings = settings_from_env()
    if settings.runtime_profile != "service":
        raise ValueError("standalone worker requires PLANGO_RUNTIME_PROFILE=service")
    runtime = DesktopRuntime(settings)
    try:
        await runtime.start()
        await runtime.consume()
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
