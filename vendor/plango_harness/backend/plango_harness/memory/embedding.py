from __future__ import annotations

from typing import Any

from plango_harness.settings import Settings


class EmbeddingService:
    """OpenAI-compatible embedding adapter.

    It is intentionally optional: structured memory is usable without an
    embedding provider, and callers can fall back to lexical retrieval.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def _get_client(self):
        if not self.settings.embedding_enabled:
            return None
        if (
            self._client is None
            and self.settings.runtime_profile != "sandbox"
            and self.settings.resolved_embedding_api_key
        ):
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.settings.resolved_embedding_api_key,
                base_url=self.settings.embedding_base_url,
                timeout=self.settings.openai_timeout_seconds,
                max_retries=self.settings.openai_max_retries,
            )
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        if not client or not texts:
            return []
        response = await client.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
            dimensions=self.settings.embedding_dimensions,
        )
        rows = sorted(response.data, key=lambda item: item.index)
        return [list(row.embedding) for row in rows]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
