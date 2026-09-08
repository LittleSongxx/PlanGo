import os
from pathlib import Path
from typing import Literal

from planora.settings import Settings
from pydantic import model_validator


class DesktopSettings(Settings):
    model_config = {**Settings.model_config, "env_file": None}
    app_name: str = "YOYU"
    event_stream: str = "yoyu:runs"
    memory_stream: str = "yoyu:memory-embed"
    worker_group: str = "yoyu-workers"
    # YOYU replaces the upstream deployment/provider choices while preserving runtime settings.
    runtime_profile: Literal["desktop", "service"] = "desktop"  # type: ignore[assignment]
    world_provider: Literal["browser"] = "browser"  # type: ignore[assignment]
    embedded_worker: bool = True
    database_url: str = "sqlite+aiosqlite:///./data/yoyu.sqlite"
    redis_url: str = "local://"
    allow_redis_fallback: bool = False
    allow_sqlite_fallback: bool = False

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)

    @model_validator(mode="before")
    @classmethod
    def canonicalize_legacy_config(cls, values):
        return values

    @model_validator(mode="after")
    def validate_profile_boundary(self):
        if self.world_provider != "browser":
            raise ValueError("YOYU requires the real browser provider")
        if self.runtime_profile == "desktop" and not self.is_sqlite:
            raise ValueError("desktop profile requires SQLite")
        if self.runtime_profile == "service" and self.is_sqlite:
            raise ValueError("service profile requires PostgreSQL")
        return self

    @property
    def model_enabled(self):
        return bool(self.openai_api_key)

    @property
    def embedding_enabled(self):
        return bool(self.embedding_api_key)  # Embedding is an explicit opt-in.


def settings_from_env():
    data = Path(os.environ.get("YOYU_DATA_DIR", "./data/yoyu")).expanduser().resolve()
    allowed = {
        "openai_api_key": "OPENAI_API_KEY",
        "openai_base_url": "OPENAI_BASE_URL",
        "openai_model": "OPENAI_MODEL",
        "openai_timeout_seconds": "YOYU_MODEL_TIMEOUT_SECONDS",
        "openai_max_retries": "YOYU_MODEL_MAX_RETRIES",
        "amap_webservice_key": "AMAP_WEBSERVICE_KEY",
        "amap_timeout_seconds": "YOYU_AMAP_TIMEOUT_SECONDS",
        "embedding_api_key": "YOYU_EMBEDDING_API_KEY",
        "embedding_base_url": "YOYU_EMBEDDING_BASE_URL",
        "embedding_model": "YOYU_EMBEDDING_MODEL",
        "max_turns": "YOYU_MAX_TURNS",
        "max_tool_calls": "YOYU_MAX_TOOL_CALLS",
        "max_model_tokens": "YOYU_MAX_MODEL_TOKENS",
        "max_run_seconds": "YOYU_MAX_RUN_SECONDS",
        "max_repair_rounds": "YOYU_MAX_REPAIR_ROUNDS",
    }
    explicit = {field: os.environ[env] for field, env in allowed.items() if env in os.environ}
    return DesktopSettings.model_validate(
        {
            **explicit,
            "runtime_profile": os.environ.get("YOYU_RUNTIME_PROFILE", "desktop"),
            "world_provider": "browser",
            "database_url": os.environ.get(
                "YOYU_DATABASE_URL", f"sqlite+aiosqlite:///{data / 'runs.sqlite'}"
            ),
            "redis_url": os.environ.get("YOYU_REDIS_URL", "local://"),
            "embedded_worker": os.environ.get("YOYU_RUNTIME_PROFILE", "desktop") == "desktop",
            "data_dir": data,
            "checkpoint_path": data / "checkpoints.sqlite",
        }
    )
