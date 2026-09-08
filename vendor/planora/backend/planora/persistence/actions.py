from __future__ import annotations

from typing import Any

from planora.persistence.runs import RunRepository


class ActionLedger:
    """Named facade for durable action reservation and state transitions."""

    def __init__(self, runs: RunRepository) -> None:
        self.runs = runs

    async def reserve(self, **kwargs: Any) -> dict[str, Any]:
        return await self.runs.record_action(**kwargs)

    async def complete(
        self, action_id: str, status: str, result: dict[str, Any] | None = None
    ) -> None:
        await self.runs.update_action(action_id, status, result)
