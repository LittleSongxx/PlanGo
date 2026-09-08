from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

_tracer: Any = None
try:
    from opentelemetry import trace

    _tracer = trace.get_tracer("plango")
except Exception:  # optional in the smallest local install
    _tracer = None


@asynccontextmanager
async def agent_span(name: str, **attributes: Any) -> AsyncIterator[Any]:
    """Create a bounded OTel span without recording hidden chain-of-thought."""
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(f"plango.{key}", str(value))
        yield span
