from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import and_, delete, desc, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from plango_harness.agent.contracts import MemoryProposal
from plango_harness.memory.resolver import MemoryResolver
from plango_harness.persistence.database import (
    Database,
    memory_document,
    memory_episode,
    memory_event,
    memory_fact,
    procedural_rule,
    utc_now,
)


def _id(prefix: str, *parts: str) -> str:
    raw = ":".join((prefix, *parts))
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def _age_days(value: Any, now: datetime) -> float:
    if not isinstance(value, datetime):
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value).total_seconds() / 86400)


class MemoryRepository:
    """Structured long-term memory with portable lexical fallback.

    Facts and rules are always read relationally. Free-form episodes/documents
    can later use the PostgreSQL vector column; service lexical retrieval is
    pg_trgm and SQLite keeps a small deterministic fallback.
    """

    def __init__(self, database: Database, embedding_service: Any | None = None) -> None:
        self.database = database
        self.embedding_service = embedding_service
        self.resolver = MemoryResolver()

    @property
    def embedding_available(self) -> bool:
        if self.embedding_service is None:
            return False
        settings = getattr(self.embedding_service, "settings", None)
        return bool(getattr(settings, "embedding_enabled", True))

    async def retrieve(
        self,
        user_id: str,
        query: str = "",
        limit: int = 8,
        namespace: str | None = None,
        token_budget: int | None = None,
    ) -> list[dict[str, Any]]:
        terms = [t.lower() for t in (query or "").split() if t]
        now = utc_now()
        async with self.database.session() as session:
            facts = (
                (
                    await session.execute(
                        select(memory_fact)
                        .where(
                            and_(
                                memory_fact.c.user_id == user_id,
                                *([memory_fact.c.namespace == namespace] if namespace else []),
                                or_(
                                    memory_fact.c.valid_from.is_(None),
                                    memory_fact.c.valid_from <= now,
                                ),
                                or_(memory_fact.c.valid_to.is_(None), memory_fact.c.valid_to > now),
                            )
                        )
                        .order_by(desc(memory_fact.c.updated_at))
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            rules = (
                (
                    await session.execute(
                        select(procedural_rule)
                        .where(
                            and_(
                                procedural_rule.c.user_id == user_id,
                                *([procedural_rule.c.namespace == namespace] if namespace else []),
                                procedural_rule.c.status == "active",
                                or_(
                                    procedural_rule.c.expires_at.is_(None),
                                    procedural_rule.c.expires_at > now,
                                ),
                            )
                        )
                        .order_by(
                            desc(procedural_rule.c.hit_count), desc(procedural_rule.c.updated_at)
                        )
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            episodes = (
                (
                    await session.execute(
                        select(memory_episode)
                        .where(
                            memory_episode.c.user_id == user_id,
                            or_(
                                memory_episode.c.valid_until.is_(None),
                                memory_episode.c.valid_until > now,
                            ),
                        )
                        .order_by(desc(memory_episode.c.created_at))
                        .limit(max(limit * 2, 10))
                    )
                )
                .mappings()
                .all()
            )
            document_limit = max(limit * 2, 10)
            documents: list[dict[str, Any]] = []
            if (
                query
                and self.database.url.startswith("postgres")
                and getattr(self.database, "lexical_available", True)
            ):
                # pg_trgm is the single service lexical path. It works for
                # Chinese text without relying on locale-specific tokenizers;
                # the migration gate installs the extension and
                # index before the API starts.
                namespace_clause = "AND namespace = :namespace" if namespace else ""
                try:
                    documents = [
                        dict(row)
                        for row in (
                            await session.execute(
                                text(
                                    "SELECT id, user_id, namespace, kind, content, metadata_json, "
                                    "episode_id, source_id, source_version, chunk_index, "
                                    "importance, confidence, created_at, "
                                    "similarity(content, :query) AS lexical_score "
                                    "FROM memory_document WHERE user_id = :user_id "
                                    + namespace_clause
                                    + " AND (valid_until IS NULL OR valid_until > :now) "
                                    "AND content % :query "
                                    "ORDER BY similarity(content, :query) DESC, importance DESC, "
                                    "created_at DESC LIMIT :limit"
                                ),
                                {
                                    "query": query,
                                    "user_id": user_id,
                                    "now": now,
                                    "limit": document_limit,
                                    **({"namespace": namespace} if namespace else {}),
                                },
                            )
                        )
                        .mappings()
                        .all()
                    ]
                except Exception:
                    # A missing extension/index is a degraded service state;
                    # do not silently introduce a second PostgreSQL search
                    # algorithm. Readiness and migrations expose the issue.
                    documents = []
            if not documents and not self.database.url.startswith("postgres"):
                documents = [
                    dict(row)
                    for row in (
                        await session.execute(
                            select(memory_document)
                            .where(
                                memory_document.c.user_id == user_id,
                                *([memory_document.c.namespace == namespace] if namespace else []),
                                or_(
                                    memory_document.c.valid_until.is_(None),
                                    memory_document.c.valid_until > now,
                                ),
                            )
                            .order_by(
                                desc(memory_document.c.importance),
                                desc(memory_document.c.created_at),
                            )
                            .limit(document_limit)
                        )
                    )
                    .mappings()
                    .all()
                ]
            vector_rows: list[dict[str, Any]] = []
            # Native pgvector retrieval is an optional acceleration. The JSON
            # copy and lexical path below keep the same behavior on SQLite or
            # when the extension/provider is unavailable.
            if query and self.database.vector_available and self.embedding_available and self.embedding_service is not None:
                try:
                    vectors = await self.embedding_service.embed([query])
                    if vectors:
                        literal = "[" + ",".join(str(float(x)) for x in vectors[0]) + "]"
                        vector_rows = [
                            dict(row)
                            for row in (
                                await session.execute(
                                    text(
                                        "SELECT id, kind, content, metadata_json, episode_id, "
                                        "source_id, source_version, chunk_index, "
                                        "1 - (embedding <=> CAST(:embedding AS vector)) AS similarity "
                                        "FROM memory_document WHERE user_id = :user_id "
                                        + ("AND namespace = :namespace " if namespace else "")
                                        + "AND (valid_until IS NULL OR valid_until > :now) "
                                        + "AND embedding_status = 'succeeded' "
                                        + "AND embedding IS NOT NULL "
                                        + "ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT :limit"
                                    ),
                                    {
                                        "embedding": literal,
                                        "user_id": user_id,
                                        "now": now,
                                        "limit": max(limit * 2, 10),
                                        **({"namespace": namespace} if namespace else {}),
                                    },
                                )
                            )
                            .mappings()
                            .all()
                        ]
                except Exception:
                    vector_rows = []
        items: list[dict[str, Any]] = []
        items.extend(
            {
                "kind": "fact",
                "key": row["fact_key"],
                "value": row["value_json"],
                "source": row["source"],
                "confidence": row["confidence"],
                "relevance": 1.0,
            }
            for row in facts
        )
        items.extend(
            {
                "kind": "rule",
                "predicate": row["predicate"],
                "action": row["action"],
                "source": row["source_event_id"],
                "confidence": row["confidence"],
                "hit_count": row["hit_count"],
                "relevance": 0.9,
            }
            for row in rules
        )
        for row in episodes:
            content = row["summary"].lower()
            episode_relevance = sum(1 for term in terms if term in content) if terms else 0
            age_days = _age_days(row["created_at"], now)
            items.append(
                {
                    "kind": "episode",
                    "id": row["id"],
                    "content": row["summary"],
                    "payload": row["payload_json"],
                    "source": row["source_event_id"],
                    "relevance": episode_relevance,
                    "recency": 1.0 / (1.0 + age_days),
                }
        )
        for document_row in documents:
            content = document_row["content"].lower()
            lexical_score = float(document_row.get("lexical_score") or 0)
            term_score = (
                sum(1 for term in terms if term in content) / max(1, len(terms))
                if terms
                else 0.0
            )
            document_relevance = max(
                term_score,
                min(1.0, max(0.0, lexical_score)),
            )
            if any(
                item.get("kind") == "episode" and item.get("content") == document_row["content"]
                for item in items
            ):
                continue
            items.append(
                {
                    "kind": document_row["kind"],
                    "id": document_row["id"],
                    "episode_id": document_row.get("episode_id"),
                    "source_id": document_row.get("source_id"),
                    "source_version": document_row.get("source_version"),
                    "chunk_index": document_row.get("chunk_index"),
                    "content": document_row["content"],
                    "metadata": document_row["metadata_json"],
                    "source": "memory_document",
                    "relevance": document_relevance,
                    "lexical_score": min(1.0, max(0.0, lexical_score)),
                    "recency": 1.0 / (1.0 + _age_days(document_row["created_at"], now)),
                    "importance": document_row["importance"],
                }
            )
        for vector_row in vector_rows:
            # Avoid returning the same document twice when lexical and vector
            # retrieval overlap; the vector score is retained for reranking.
            if any(item.get("id") == vector_row.get("id") for item in items):
                for item in items:
                    if item.get("id") == vector_row.get("id"):
                        vector_score = min(
                            1.0, max(0.0, float(vector_row.get("similarity") or 0))
                        )
                        lexical_score = min(1.0, max(0.0, float(item.get("relevance") or 0)))
                        item["vector_score"] = vector_score
                        item["relevance"] = 0.45 * lexical_score + 0.55 * vector_score
                        break
            else:
                items.append(
                    {
                        "kind": vector_row.get("kind", "document"),
                        "id": vector_row.get("id"),
                        "episode_id": vector_row.get("episode_id"),
                        "source_id": vector_row.get("source_id"),
                        "source_version": vector_row.get("source_version"),
                        "chunk_index": vector_row.get("chunk_index"),
                        "content": vector_row.get("content", ""),
                        "metadata": vector_row.get("metadata_json") or {},
                        "source": "memory_document",
                        "relevance": float(vector_row.get("similarity") or 0),
                        "vector_score": float(vector_row.get("similarity") or 0),
                    }
                )
        items.sort(
            key=lambda item: (
                item.get("relevance", 0),
                item.get("recency", 0) * item.get("importance", 1),
                item.get("confidence", 0),
            ),
            reverse=True,
        )
        selected = items[:limit]
        if token_budget is None:
            return selected
        result: list[dict[str, Any]] = []
        used = 0
        for item in selected:
            cost = max(1, len(str(item.get("content") or item.get("value") or item)) // 4)
            if result and used + cost > token_budget:
                break
            result.append(item)
            used += cost
        return result

    async def _user_control(self, session, user_id, now):
        """Serialize memory writes and erasure so a late projector cannot resurrect erased data."""
        assert self.database.engine is not None
        insert_row = pg_insert if self.database.engine.dialect.name == "postgresql" else sqlite_insert
        identity = _id("memory-control", user_id)
        # ponytail: one write gate per user; split by namespace only if memory write throughput requires it.
        await session.execute(insert_row(memory_event).values(
            id=identity, user_id=user_id, event_kind="control", payload_json={}, created_at=now,
        ).on_conflict_do_nothing(index_elements=["id"]))
        row = (await session.execute(select(memory_event.c.payload_json).where(memory_event.c.id == identity).with_for_update())).scalar_one()
        return identity, row or {}

    async def _scrub_events(self, session, user_id, *conditions):
        rows = (await session.execute(select(memory_event.c.id, memory_event.c.payload_json).where(
            memory_event.c.user_id == user_id, memory_event.c.event_kind != "control", *conditions,
        ))).all()
        for identity, payload in rows:
            # Keep only an opaque source receipt; erase the remembered text/value itself.
            await session.execute(update(memory_event).where(memory_event.c.id == identity).values(
                payload_json={"forgotten": True, "source_event_id": (payload or {}).get("source_event_id")},
            ))

    async def events_for_source(self, user_id: str, source_id: str) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            rows = (await session.execute(select(memory_event).where(
                memory_event.c.user_id == user_id, memory_event.c.payload_json["source_event_id"].as_string() == source_id,
            ).order_by(memory_event.c.created_at))).mappings().all()
        return [dict(row) for row in rows]

    async def feedback_events(self, user_id: str, run_id: str) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            rows = (await session.execute(select(memory_event).where(
                memory_event.c.user_id == user_id, memory_event.c.event_kind == "episode",
                memory_event.c.payload_json["value"]["scope"].as_string() == "user_feedback",
                memory_event.c.payload_json["value"]["run_id"].as_string() == run_id,
            ).order_by(memory_event.c.created_at.desc()))).mappings().all()
        return [dict(row) for row in rows]

    async def forget_episode(self, user_id: str, episode_id: str) -> bool:
        async with self.database.session() as session:
            async with session.begin():
                await self._user_control(session, user_id, utc_now())
                row = (await session.execute(select(memory_episode.c.source_event_id).where(
                    memory_episode.c.id == episode_id, memory_episode.c.user_id == user_id,
                ))).first()
                if row is None:
                    return False
                await session.execute(delete(memory_document).where(memory_document.c.user_id == user_id, memory_document.c.episode_id == episode_id))
                await session.execute(delete(memory_episode).where(memory_episode.c.id == episode_id, memory_episode.c.user_id == user_id))
                await self._scrub_events(session, user_id, memory_event.c.event_kind == "episode", memory_event.c.payload_json["source_event_id"].as_string() == row[0])
        return True

    async def commit(
        self, user_id: str, proposals: Iterable[MemoryProposal]
    ) -> list[dict[str, Any]]:
        proposals = self.resolver.resolve(proposals)
        if not proposals:
            return []
        now = utc_now()
        committed: list[dict[str, Any]] = []
        async with self.database.session() as session:
            async with session.begin():
                _, control = await self._user_control(session, user_id, now)
                for proposal in proposals:
                    if control.get("forget_before") and not proposal.source_event_id.startswith(("user:", "explicit:", "user-confirmed:", "user-correction:")):
                        try:
                            observed = datetime.fromisoformat(str(proposal.value.get("observed_at") or "").replace("Z", "+00:00"))
                            if observed.tzinfo is None or observed <= datetime.fromisoformat(control["forget_before"]):
                                continue
                        except ValueError:
                            continue
                    event_id = _id(
                        "event", user_id, proposal.kind, proposal.key, proposal.source_event_id
                    )
                    existing_event = (
                        await session.execute(
                            select(memory_event.c.payload_json).where(memory_event.c.id == event_id)
                        )
                    ).first()
                    if existing_event:
                        previous = existing_event[0] or {}
                        if not previous.get("forgotten") and previous != proposal.model_dump(mode="json"):
                            raise ValueError("memory_event_payload_conflict")
                        continue
                    if not existing_event:
                        try:
                            async with session.begin_nested():
                                await session.execute(
                                    memory_event.insert().values(
                                        id=event_id,
                                        user_id=user_id,
                                        event_kind=proposal.kind,
                                        payload_json=proposal.model_dump(mode="json"),
                                        created_at=now,
                                    )
                                )
                        except IntegrityError:
                            continue
                    if proposal.operation == "forget" or proposal.value.get("forget"):
                        await self._scrub_events(session, user_id, memory_event.c.event_kind == proposal.kind, memory_event.c.payload_json["key"].as_string() == proposal.key)
                    if proposal.kind == "fact":
                        if proposal.operation == "forget" or proposal.value.get("forget"):
                            await session.execute(
                                memory_fact.delete().where(
                                    and_(
                                        memory_fact.c.user_id == user_id,
                                        memory_fact.c.namespace == "user",
                                        memory_fact.c.fact_key == proposal.key,
                                    )
                                )
                            )
                            committed.append(proposal.model_dump(mode="json"))
                            continue
                        existing = (
                            await session.execute(
                                select(memory_fact.c.id).where(
                                    and_(
                                        memory_fact.c.user_id == user_id,
                                        memory_fact.c.namespace == "user",
                                        memory_fact.c.fact_key == proposal.key,
                                    )
                                )
                            )
                        ).first()
                        values = {
                            "user_id": user_id,
                            "namespace": "user",
                            "fact_key": proposal.key,
                            "value_json": proposal.value,
                            "source": proposal.source_event_id,
                            "confidence": proposal.confidence,
                            "valid_from": now,
                            "valid_to": proposal.valid_until,
                            "updated_at": now,
                        }
                        if existing:
                            # Explicit/user-confirmed facts may replace an
                            # inferred fact; a weaker inference never erases a
                            # higher-confidence value.
                            current_row = (
                                await session.execute(
                                    select(memory_fact.c.confidence, memory_fact.c.source).where(
                                        memory_fact.c.id == existing[0]
                                    )
                                )
                            ).first()
                            current_conf = float(current_row[0] or 0) if current_row else 0.0
                            current_source = str(current_row[1]) if current_row else ""
                            if (
                                (
                                    self.resolver.source_priority(proposal.source_event_id),
                                    proposal.confidence,
                                )
                                >= (
                                    self.resolver.source_priority(current_source),
                                    current_conf,
                                )
                            ):
                                await session.execute(
                                    update(memory_fact)
                                    .where(memory_fact.c.id == existing[0])
                                    .values(**values)
                                )
                        else:
                            values["id"] = _id("fact", user_id, proposal.key)
                            await session.execute(memory_fact.insert().values(**values))
                    elif proposal.kind == "episode":
                        summary = str(proposal.value.get("summary") or proposal.key)
                        if proposal.operation == "forget" or proposal.value.get("forget"):
                            episode_ids = [
                                row[0]
                                for row in (
                                    await session.execute(
                                        select(memory_episode.c.id).where(
                                            memory_episode.c.user_id == user_id,
                                            memory_episode.c.summary == summary,
                                        )
                                    )
                                ).all()
                            ]
                            if episode_ids:
                                await session.execute(
                                    delete(memory_document).where(
                                        memory_document.c.episode_id.in_(episode_ids)
                                    )
                                )
                                await session.execute(
                                    delete(memory_episode).where(
                                        memory_episode.c.id.in_(episode_ids)
                                    )
                                )
                            committed.append(proposal.model_dump(mode="json"))
                            continue
                        episode_id = _id("episode", user_id, proposal.source_event_id, summary)
                        if not (
                            await session.execute(
                                select(memory_episode.c.id).where(memory_episode.c.id == episode_id)
                            )
                        ).first():
                            await session.execute(
                                memory_episode.insert().values(
                                    id=episode_id,
                                    user_id=user_id,
                                    summary=summary,
                                    payload_json=proposal.value,
                                    source_event_id=proposal.source_event_id,
                                    created_at=now,
                                    valid_until=proposal.valid_until,
                                )
                            )
                        document_id = _id("document", user_id, proposal.source_event_id, summary)
                        if not (
                            await session.execute(
                                select(memory_document.c.id).where(
                                    memory_document.c.id == document_id
                                )
                            )
                        ).first():
                            await session.execute(
                                memory_document.insert().values(
                                    id=document_id,
                                    user_id=user_id,
                                    namespace="user",
                                    kind="episode",
                                    content=summary,
                                    metadata_json={"source_event_id": proposal.source_event_id},
                                    episode_id=episode_id,
                                    source_id=proposal.source_event_id,
                                    source_version="1",
                                    chunk_index=0,
                                    source_hash=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
                                    importance=proposal.confidence,
                                    confidence=proposal.confidence,
                                    created_at=now,
                                    valid_until=proposal.valid_until,
                                    embedding_status=(
                                        "pending" if self.embedding_available else "disabled"
                                    ),
                                    embedding_attempts=0,
                                )
                            )
                    elif proposal.kind == "rule":
                        if proposal.operation == "forget" or proposal.value.get("forget"):
                            predicate = str(proposal.value.get("predicate") or proposal.key)
                            action = str(proposal.value.get("action") or "prefer")
                            await session.execute(
                                procedural_rule.delete().where(
                                    and_(
                                        procedural_rule.c.user_id == user_id,
                                        procedural_rule.c.namespace == "user",
                                        procedural_rule.c.predicate == predicate,
                                        procedural_rule.c.action == action,
                                    )
                                )
                            )
                            committed.append(proposal.model_dump(mode="json"))
                            continue
                        predicate = str(proposal.value.get("predicate") or proposal.key)
                        action = str(proposal.value.get("action") or "prefer")
                        existing = (
                            await session.execute(
                                select(procedural_rule.c.id, procedural_rule.c.hit_count).where(
                                    and_(
                                        procedural_rule.c.user_id == user_id,
                                        procedural_rule.c.namespace == "user",
                                        procedural_rule.c.predicate == predicate,
                                        procedural_rule.c.action == action,
                                    )
                                )
                            )
                        ).first()
                        values = {
                            "user_id": user_id,
                            "namespace": "user",
                            "predicate": predicate,
                            "action": action,
                            "source_event_id": proposal.source_event_id,
                            "confidence": proposal.confidence,
                            "updated_at": now,
                            "expires_at": proposal.valid_until,
                            "status": "active",
                        }
                        if existing:
                            await session.execute(
                                update(procedural_rule)
                                .where(procedural_rule.c.id == existing[0])
                                .values(hit_count=int(existing[1] or 0) + 1, **values)
                            )
                        else:
                            values["id"] = _id("rule", user_id, predicate, action)
                            values["hit_count"] = 1
                            await session.execute(procedural_rule.insert().values(**values))
                    committed.append(proposal.model_dump(mode="json"))
        return committed

    async def add_document(
        self,
        *,
        user_id: str,
        namespace: str,
        kind: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
        episode_id: str | None = None,
        source_id: str | None = None,
        source_version: str | None = None,
        chunk_index: int | None = None,
        source_hash: str | None = None,
    ) -> str:
        identity = [user_id, namespace, kind, content]
        if source_id or source_version:
            identity.extend((source_id or "", source_version or ""))
        document_id = _id("document", *identity)
        async with self.database.session() as session:
            async with session.begin():
                exists = (
                    await session.execute(
                        select(memory_document.c.id).where(memory_document.c.id == document_id)
                    )
                ).first()
                if not exists:
                    await session.execute(
                        memory_document.insert().values(
                            id=document_id,
                            user_id=user_id,
                            namespace=namespace,
                            kind=kind,
                            content=content,
                            metadata_json=metadata or {},
                            episode_id=episode_id,
                            source_id=source_id,
                            source_version=source_version,
                            chunk_index=chunk_index,
                            source_hash=source_hash,
                            embedding_json=json.dumps(embedding) if embedding else None,
                            embedding_model=embedding_model,
                            importance=0.5,
                            confidence=0.5,
                            created_at=datetime.now(timezone.utc),
                            embedding_status=(
                                "succeeded"
                                if embedding
                                else ("pending" if self.embedding_available else "disabled")
                            ),
                            embedding_attempts=0,
                        )
                    )
                    if embedding and self.database.vector_available:
                        try:
                            async with session.begin_nested():
                                literal = "[" + ",".join(str(float(x)) for x in embedding) + "]"
                                await session.execute(
                                    text(
                                        "UPDATE memory_document SET embedding = CAST(:embedding AS vector) "
                                        "WHERE id = :id"
                                    ),
                                    {"embedding": literal, "id": document_id},
                                )
                        except Exception:
                            pass
        return document_id

    @staticmethod
    def split_document(content: str, max_chars: int = 800, overlap: int = 120) -> list[str]:
        """Small heading/paragraph-aware splitter for opt-in text documents."""
        if max_chars < 32 or overlap < 0 or overlap >= max_chars:
            raise ValueError("invalid_chunk_limits")
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content.replace("\r\n", "\n")) if item.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            pieces: list[str] = []
            start = 0
            step = max(1, max_chars - overlap)
            while start < len(paragraph):
                pieces.append(paragraph[start : start + max_chars])
                if start + max_chars >= len(paragraph):
                    break
                start += step
            for piece in pieces:
                if current and len(current) + 2 + len(piece) > max_chars:
                    chunks.append(current)
                    current = piece
                else:
                    current = f"{current}\n\n{piece}" if current else piece
        if current:
            chunks.append(current)
        return chunks

    async def ingest_document(
        self,
        *,
        user_id: str,
        namespace: str = "user",
        source_id: str,
        content: str,
        source_version: str = "1",
        metadata: dict[str, Any] | None = None,
        max_chars: int = 800,
        overlap: int = 120,
    ) -> list[str]:
        """Store a versioned, citeable document as rebuildable chunks."""
        normalized = content.replace("\r\n", "\n").strip()
        if not user_id or not source_id or not normalized:
            raise ValueError("document identity and content are required")
        chunks = self.split_document(normalized, max_chars=max_chars, overlap=overlap)
        source_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        now = utc_now()
        ids: list[str] = []
        async with self.database.session() as session:
            async with session.begin():
                # A newly published version makes older versions invisible;
                # their rows remain available for audit/rebuild.
                existing_hashes = {
                    str(row[0])
                    for row in (
                        await session.execute(
                            select(memory_document.c.source_hash).where(
                                memory_document.c.user_id == user_id,
                                memory_document.c.namespace == namespace,
                                memory_document.c.source_id == source_id,
                                memory_document.c.source_version == source_version,
                                memory_document.c.source_hash.is_not(None),
                            )
                        )
                    ).all()
                }
                await session.execute(
                    update(memory_document)
                    .where(
                        memory_document.c.user_id == user_id,
                        memory_document.c.namespace == namespace,
                        memory_document.c.source_id == source_id,
                        memory_document.c.source_version != source_version,
                    )
                    .values(valid_until=now)
                )
                if existing_hashes and existing_hashes != {source_hash}:
                    await session.execute(
                        update(memory_document)
                        .where(
                            memory_document.c.user_id == user_id,
                            memory_document.c.namespace == namespace,
                            memory_document.c.source_id == source_id,
                            memory_document.c.source_version == source_version,
                        )
                        .values(valid_until=now)
                    )
                for index, chunk in enumerate(chunks):
                    document_id = _id(
                        "document-chunk",
                        user_id,
                        namespace,
                        source_id,
                        source_version,
                        str(index),
                        source_hash,
                    )
                    ids.append(document_id)
                    exists = (
                        await session.execute(
                            select(memory_document.c.id).where(memory_document.c.id == document_id)
                        )
                    ).first()
                    if exists:
                        continue
                    await session.execute(
                        memory_document.insert().values(
                            id=document_id,
                            user_id=user_id,
                            namespace=namespace,
                            kind="document",
                            content=chunk,
                            metadata_json={
                                **(metadata or {}),
                                "source_id": source_id,
                                "source_version": source_version,
                                "chunk_index": index,
                                "citation": {"source_id": source_id, "chunk_index": index},
                            },
                            source_id=source_id,
                            source_version=source_version,
                            chunk_index=index,
                            source_hash=source_hash,
                            importance=0.5,
                            confidence=0.8,
                            created_at=now,
                            embedding_status=(
                                "pending" if self.embedding_available else "disabled"
                            ),
                            embedding_attempts=0,
                        )
                    )
        return ids

    async def forget_document(
        self, user_id: str, source_id: str, namespace: str = "user"
    ) -> int:
        """Remove all versions/chunks for a source within one user namespace."""
        async with self.database.session() as session:
            async with session.begin():
                result = await session.execute(
                    delete(memory_document).where(
                        memory_document.c.user_id == user_id,
                        memory_document.c.namespace == namespace,
                        memory_document.c.source_id == source_id,
                    )
                )
                return int(getattr(result, "rowcount", 0) or 0)

    async def load_fixture(self, user_id: str, path: str) -> int:
        """Explicitly load a test fixture; runtime never calls this implicitly."""
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        proposals: list[MemoryProposal] = []
        for item in payload.get("facts", []):
            proposals.append(
                MemoryProposal(
                    kind="fact",
                    key=str(item["key"]),
                    value=item.get("value") or {},
                    source_event_id=f"fixture:{path}:{item['key']}",
                    confidence=float(item.get("confidence", 0.5)),
                )
            )
        for item in payload.get("rules", []):
            predicate = str(item.get("predicate", ""))
            proposals.append(
                MemoryProposal(
                    kind="rule",
                    key=predicate,
                    value={"predicate": predicate, "action": item.get("action", "")},
                    source_event_id=f"fixture:{path}:{predicate}",
                    confidence=float(item.get("confidence", 0.5)),
                )
            )
        await self.commit(user_id, proposals)
        return len(proposals)

    async def embed_pending(self, limit: int = 16, max_attempts: int = 3) -> int:
        """Claim and process embedding tasks with durable retry state."""
        max_attempts = max(1, max_attempts)
        now = utc_now()
        service = self.embedding_service
        if not self.embedding_available or service is None:
            async with self.database.session() as session:
                async with session.begin():
                    await session.execute(
                        update(memory_document)
                        .where(memory_document.c.embedding_status == "pending")
                        .values(embedding_status="disabled", embedding_error="embedding provider unavailable")
                    )
            return 0
        async with self.database.session() as session:
            async with session.begin():
                statement = (
                    select(
                        memory_document.c.id,
                        memory_document.c.content,
                        memory_document.c.embedding_attempts,
                    )
                    .where(
                        memory_document.c.embedding_status.in_(("pending", "failed", "running")),
                        memory_document.c.embedding_attempts < max_attempts,
                        or_(
                            memory_document.c.embedding_next_attempt_at.is_(None),
                            memory_document.c.embedding_next_attempt_at <= now,
                        ),
                    )
                    .order_by(memory_document.c.created_at)
                    .limit(limit)
                )
                if self.database.url.startswith("postgres"):
                    statement = statement.with_for_update(skip_locked=True)
                rows = (await session.execute(statement)).mappings().all()
                for row in rows:
                    await session.execute(
                        update(memory_document)
                        .where(memory_document.c.id == row["id"])
                        .values(
                            embedding_status="running",
                            embedding_attempts=int(row["embedding_attempts"] or 0) + 1,
                            embedding_error=None,
                            embedding_next_attempt_at=now + timedelta(minutes=1),
                        )
                    )
        if not rows:
            return 0
        try:
            vectors = await service.embed([row["content"] for row in rows]) or []
        except Exception as exc:
            await self._finish_embedding_failures(rows, max_attempts, type(exc).__name__)
            return 0
        count = 0
        async with self.database.session() as session:
            async with session.begin():
                for index, row in enumerate(rows):
                    vector = vectors[index] if index < len(vectors) else []
                    if not vector:
                        attempts = int(row["embedding_attempts"] or 0) + 1
                        status = "failed" if attempts >= max_attempts else "pending"
                        await session.execute(
                            update(memory_document)
                            .where(memory_document.c.id == row["id"])
                            .values(
                                embedding_status=status,
                                embedding_attempts=attempts,
                                embedding_error="empty_embedding",
                                embedding_next_attempt_at=(
                                    None if status == "failed" else now
                                ),
                            )
                        )
                        continue
                    encoded = json.dumps(vector)
                    await session.execute(
                        update(memory_document)
                        .where(memory_document.c.id == row["id"])
                        .values(
                            embedding_json=encoded,
                            embedding_model=getattr(
                                getattr(self.embedding_service, "settings", None),
                                "embedding_model",
                                None,
                            ),
                            embedding_status="succeeded",
                            embedding_error=None,
                            embedding_next_attempt_at=None,
                        )
                    )
                    if self.database.vector_available:
                        try:
                            async with session.begin_nested():
                                literal = "[" + ",".join(str(float(x)) for x in vector) + "]"
                                await session.execute(
                                    text(
                                        "UPDATE memory_document SET embedding = CAST(:embedding AS vector) "
                                        "WHERE id = :id"
                                    ),
                                    {"embedding": literal, "id": row["id"]},
                                )
                        except Exception:
                            # The structured row stays committed; native vector
                            # storage can be repaired without losing the task.
                            pass
                    count += 1
        return count

    async def has_pending_embeddings(self, max_attempts: int = 3) -> bool:
        now = utc_now()
        async with self.database.session() as session:
            row = (
                await session.execute(
                    select(memory_document.c.id)
                    .where(
                        memory_document.c.embedding_status.in_(
                            ("pending", "failed", "running")
                        ),
                        memory_document.c.embedding_attempts < max(1, max_attempts),
                        or_(
                            memory_document.c.embedding_next_attempt_at.is_(None),
                            memory_document.c.embedding_next_attempt_at <= now,
                        ),
                    )
                    .limit(1)
                )
            ).first()
        return row is not None

    async def _finish_embedding_failures(
        self, rows: Iterable[Any], max_attempts: int, error: str
    ) -> None:
        now = utc_now()
        async with self.database.session() as session:
            async with session.begin():
                for row in rows:
                    attempts = int(row["embedding_attempts"] or 0) + 1
                    status = "failed" if attempts >= max_attempts else "pending"
                    await session.execute(
                        update(memory_document)
                        .where(memory_document.c.id == row["id"])
                        .values(
                            embedding_status=status,
                            embedding_attempts=attempts,
                            embedding_error=error,
                            embedding_next_attempt_at=(
                                None if status == "failed" else now
                            ),
                        )
                    )

    async def forget_user(self, user_id: str) -> None:
        async with self.database.session() as session:
            async with session.begin():
                now = utc_now()
                identity, _ = await self._user_control(session, user_id, now)
                for table in (memory_fact, memory_episode, procedural_rule, memory_document):
                    await session.execute(table.delete().where(table.c.user_id == user_id))
                await self._scrub_events(session, user_id)
                await session.execute(update(memory_event).where(memory_event.c.id == identity).values(payload_json={"forget_before": now.isoformat()}))
