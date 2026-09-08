"""Explicit embedding opt-out must prevent client creation, including an existing client."""
from plango.settings import DesktopSettings
from plango_harness.memory.embedding import EmbeddingService
from plango_harness.memory.repository import MemoryRepository


async def test_chat_key_does_not_enable_embedding():
    service = EmbeddingService(DesktopSettings(openai_api_key="fixture-chat-only", embedding_api_key=""))
    assert service._get_client() is None
    assert await service.embed(["explicit user preference"]) == []
    assert not MemoryRepository(None, service).embedding_available
    service._client = object()  # Even a previously constructed connection cannot bypass an opt-out.
    assert service._get_client() is None
    assert await service.embed(["no external call"]) == []
