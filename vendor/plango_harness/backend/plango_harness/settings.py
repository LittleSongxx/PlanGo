from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the PlanGo application.

    ``service`` is the Compose profile. ``sandbox`` is the explicit offline
    profile used by tests and demos; it never appears as an implicit fallback.
    """

    model_config = SettingsConfigDict(
        env_prefix="PLANGO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Keep constructor-friendly field names for tests and scripts while
        # accepting the public environment-variable aliases below.
        populate_by_name=True,
    )

    app_name: str = "PlanGo"
    app_version: str = "0.1.0"
    host: str = Field("127.0.0.1", validation_alias="PLANGO_HOST")
    port: int = Field(8000, validation_alias="PLANGO_PORT")
    runtime_profile: Literal["service", "sandbox"] = Field(
        "service", validation_alias="PLANGO_RUNTIME_PROFILE"
    )
    world_provider: Literal["amap", "sandbox"] = Field(
        "amap", validation_alias="PLANGO_WORLD_PROVIDER"
    )
    seed: int = Field(20260903, validation_alias="PLANGO_SEED")

    # OpenAI-compatible model endpoint. DashScope is the default, while any
    # compatible provider can be selected without code changes.
    openai_api_key: str = Field("", validation_alias="OPENAI_API_KEY", repr=False)
    openai_base_url: str = Field(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="OPENAI_BASE_URL",
    )
    openai_model: str = Field("qwen3.7-plus-2026-05-26", validation_alias="OPENAI_MODEL")
    # Sandbox stays offline by default; real LLM calls require an explicit
    # opt-in for a local integration smoke test.
    sandbox_use_model: bool = Field(False, validation_alias="PLANGO_SANDBOX_USE_MODEL")
    openai_timeout_seconds: float = Field(45.0, validation_alias="OPENAI_TIMEOUT_SECONDS")
    openai_max_retries: int = Field(1, validation_alias="OPENAI_MAX_RETRIES")

    embedding_api_key: str = Field("", validation_alias="PLANGO_EMBEDDING_API_KEY", repr=False)
    embedding_base_url: str = Field(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="PLANGO_EMBEDDING_BASE_URL",
    )
    embedding_model: str = Field("text-embedding-v4", validation_alias="PLANGO_EMBEDDING_MODEL")
    embedding_dimensions: int = Field(1024, validation_alias="PLANGO_EMBEDDING_DIMENSIONS")

    # PostgreSQL/Redis are the service defaults. Sandbox settings select their
    # SQLite/local queue endpoints explicitly (see .env.sandbox.example).
    database_url: str = Field(
        "postgresql+asyncpg://plango:plango@127.0.0.1:5432/plango",
        validation_alias="PLANGO_DATABASE_URL",
    )
    redis_url: str = Field("redis://127.0.0.1:6379/4", validation_alias="PLANGO_REDIS_URL")
    data_dir: Path = Field(Path("./data"), validation_alias="PLANGO_DATA_DIR")
    checkpoint_path: Path = Field(
        Path("./data/plango-checkpoints.sqlite"),
        validation_alias="PLANGO_CHECKPOINT_PATH",
    )

    amap_webservice_key: str = Field("", validation_alias="AMAP_WEBSERVICE_KEY", repr=False)
    amap_timeout_seconds: float = Field(8.0, validation_alias="AMAP_TIMEOUT_SECONDS")

    # Perspective strategy: whether Advocate fans out. Not a second orchestration.
    agent_mode: Literal["multi", "single"] = "multi"

    @property
    def perspective(self) -> Literal["multi", "single"]:
        return self.agent_mode

    max_turns: int = Field(12, validation_alias="PLANGO_MAX_TURNS", ge=1, le=100)
    # A run may include an edit/replan before approval; keep one bounded
    # budget that still leaves room for the final write-tool pass.
    max_tool_calls: int = Field(48, validation_alias="PLANGO_MAX_TOOL_CALLS", ge=1, le=1000)
    # Browser steps one accepted command may spend. Paging through a listing needs more
    # than reading a single page, so this is a knob rather than a constant.
    max_browser_steps: int = Field(12, validation_alias="PLANGO_MAX_BROWSER_STEPS", ge=1, le=200)
    max_queue_retries: int = Field(3, validation_alias="PLANGO_MAX_QUEUE_RETRIES", ge=1, le=20)
    max_repair_rounds: int = Field(2, validation_alias="PLANGO_MAX_REPAIR_ROUNDS", ge=0, le=20)
    max_context_tokens: int = Field(6000, validation_alias="PLANGO_MAX_CONTEXT_TOKENS")
    max_model_tokens: int = Field(
        200000, validation_alias="PLANGO_MAX_MODEL_TOKENS", ge=256, le=200000
    )
    event_stream: str = Field("plango:runs", validation_alias="PLANGO_EVENT_STREAM")
    memory_stream: str = Field("plango:memory-embed", validation_alias="PLANGO_MEMORY_STREAM")
    worker_group: str = Field("plango-workers", validation_alias="PLANGO_WORKER_GROUP")
    embedded_worker: bool = Field(False, validation_alias="PLANGO_EMBEDDED_WORKER")
    allow_sqlite_fallback: bool = Field(False, validation_alias="PLANGO_ALLOW_SQLITE_FALLBACK")
    allow_redis_fallback: bool = Field(False, validation_alias="PLANGO_ALLOW_REDIS_FALLBACK")
    max_run_seconds: int = Field(300, validation_alias="PLANGO_MAX_RUN_SECONDS", ge=30, le=3600)

    @model_validator(mode="before")
    @classmethod
    def canonicalize_legacy_config(cls, values):
        """Map pre-convergence local env values to one canonical profile."""
        if not isinstance(values, dict):
            return values
        def set_value(name: str, alias: str, value) -> None:
            values[alias if alias in values else name] = value

        profile_key = "PLANGO_RUNTIME_PROFILE" if "PLANGO_RUNTIME_PROFILE" in values else "runtime_profile"
        provider_key = "PLANGO_WORLD_PROVIDER" if "PLANGO_WORLD_PROVIDER" in values else "world_provider"
        profile_explicit = profile_key in values
        profile = values.get(profile_key, "service")
        if profile not in {"service", "sandbox"}:
            values[profile_key] = "service"
            profile = "service"
        provider = values.get(provider_key, "amap")
        if provider not in {"amap", "sandbox"}:
            values[provider_key] = "sandbox" if profile == "sandbox" else "amap"
        elif profile == "sandbox" and provider_key not in values:
            values[provider_key] = "sandbox"
        elif provider == "sandbox" and not profile_explicit:
            set_value("runtime_profile", "PLANGO_RUNTIME_PROFILE", "sandbox")
            profile = "sandbox"
        if profile == "sandbox":
            if "PLANGO_DATABASE_URL" not in values and "database_url" not in values:
                set_value("database_url", "PLANGO_DATABASE_URL", "sqlite+aiosqlite:///./data/plango-sandbox.sqlite")
            if "PLANGO_REDIS_URL" not in values and "redis_url" not in values:
                set_value("redis_url", "PLANGO_REDIS_URL", "redis://127.0.0.1:6399/4")
            if "PLANGO_EMBEDDED_WORKER" not in values and "embedded_worker" not in values:
                set_value("embedded_worker", "PLANGO_EMBEDDED_WORKER", True)
            if "PLANGO_ALLOW_REDIS_FALLBACK" not in values and "allow_redis_fallback" not in values:
                set_value("allow_redis_fallback", "PLANGO_ALLOW_REDIS_FALLBACK", True)
        else:
            # Service always has one external worker and strict dependencies;
            # ignore stale pre-convergence local env toggles.
            set_value("embedded_worker", "PLANGO_EMBEDDED_WORKER", False)
            set_value("allow_sqlite_fallback", "PLANGO_ALLOW_SQLITE_FALLBACK", False)
            set_value("allow_redis_fallback", "PLANGO_ALLOW_REDIS_FALLBACK", False)
        return values

    @model_validator(mode="after")
    def validate_profile_boundary(self):
        if self.runtime_profile == "sandbox" and self.world_provider != "sandbox":
            raise ValueError("sandbox profile requires world_provider=sandbox")
        if self.runtime_profile == "service" and self.world_provider != "amap":
            raise ValueError("service profile requires world_provider=amap")
        if self.runtime_profile == "service" and self.database_url.startswith("sqlite"):
            raise ValueError("service profile requires PostgreSQL")
        if self.runtime_profile == "sandbox" and not self.database_url.startswith("sqlite"):
            raise ValueError("sandbox profile requires SQLite")
        return self

    @field_validator("openai_base_url", "embedding_base_url")
    @classmethod
    def strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def model_enabled(self) -> bool:
        return bool(self.openai_api_key) and (
            self.runtime_profile == "service"
            or (self.runtime_profile == "sandbox" and self.sandbox_use_model)
        )

    @property
    def embedding_enabled(self) -> bool:
        return self.runtime_profile != "sandbox" and bool(
            self.embedding_api_key or self.openai_api_key
        )

    @property
    def resolved_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.openai_api_key

    @property
    def is_sandbox(self) -> bool:
        return self.runtime_profile == "sandbox"

    @property
    def postgres_dsn(self) -> str:
        value = self.database_url
        if value.startswith("postgresql+asyncpg://"):
            return "postgresql://" + value.split("://", 1)[1]
        if value.startswith("postgres+asyncpg://"):
            return "postgresql://" + value.split("//", 1)[1]
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.ensure_dirs()
    return settings
