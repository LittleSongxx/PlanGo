from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol


class ActionProvider(Protocol):
    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class SandboxActionProvider:
    """Deterministic action adapter used only for Sandbox execution."""

    seed: int = 20260903

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        digest = hashlib.sha256(
            f"{self.seed}:{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}".encode(
                "utf-8"
            )
        ).hexdigest()
        action_ref = f"sandbox-{digest[:16]}"
        # The sandbox never contacts or charges an external service. It models
        # the observable result that the Agent/Executor must handle.
        return {
            "ok": True,
            "action_ref": action_ref,
            "tool_name": tool_name,
            "status": "SUCCEEDED",
            "source": "simulated",
            "arguments": arguments,
        }
