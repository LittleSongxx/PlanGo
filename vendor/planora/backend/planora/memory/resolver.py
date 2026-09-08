from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from planora.agent.contracts import MemoryProposal


class MemoryResolver:
    """Small deterministic resolver for model-proposed long-term memories."""

    @staticmethod
    def source_priority(source: str) -> int:
        if source.startswith(("user:", "user-confirmed:", "user-correction:")):
            return 100
        if source.startswith("system:"):
            return 80
        if source.startswith("explicit:"):
            return 95
        if source.startswith("fixture:"):
            return 40
        return 60

    def resolve(self, proposals: Iterable[MemoryProposal]) -> list[MemoryProposal]:
        chosen: dict[tuple[str, str], MemoryProposal] = {}
        now = datetime.now(timezone.utc)
        for proposal in proposals:
            expiry = proposal.valid_until
            if expiry and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry and expiry <= now and proposal.operation != "forget":
                continue
            key = (proposal.kind, proposal.key)
            previous = chosen.get(key)
            proposal_rank = (
                110 if proposal.operation == "forget" else self.source_priority(proposal.source_event_id),
                proposal.confidence,
            )
            if previous is None:
                chosen[key] = proposal
                continue
            previous_rank = (
                110 if previous.operation == "forget" else self.source_priority(previous.source_event_id),
                previous.confidence,
            )
            if proposal_rank >= previous_rank:
                chosen[key] = proposal
        return list(chosen.values())
