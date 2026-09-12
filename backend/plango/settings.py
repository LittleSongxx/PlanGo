import os
from pathlib import Path
from typing import Literal

from plango_harness.settings import Settings
from pydantic import model_validator


class DesktopSettings(Settings):
    """Desktop runtime. ``agent_mode`` / ``perspective`` is Advocate fan-out, not a second graph."""

    model_config = {**Settings.model_config, "env_file": None}
    app_name: str = "PlanGo"
    event_stream: str = "plango:runs"
    memory_stream: str = "plango:memory-embed"
    worker_group: str = "plango-workers"
    # PlanGo replaces the upstream deployment/provider choices while preserving runtime settings.
    runtime_profile: Literal["desktop", "service"] = "desktop"  # type: ignore[assignment]
    world_provider: Literal["browser"] = "browser"  # type: ignore[assignment]
    embedded_worker: bool = True
    database_url: str = "sqlite+aiosqlite:///./data/plango.sqlite"
    redis_url: str = "local://"
    allow_redis_fallback: bool = False
    allow_sqlite_fallback: bool = False
    browser_vision_enabled: bool = False

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
            raise ValueError("PlanGo requires the real browser provider")
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
    legacy_keys = sorted(key for key in os.environ if key.startswith(("YOYU_", "XIAONIAN_")))
    if legacy_keys:
        raise RuntimeError(
            "Legacy project environment keys require migration to PLANGO_ (preserve their values): "
            + ", ".join(legacy_keys)
        )
    if (
        os.environ.get("PLANGO_RUNTIME_PROFILE", "desktop") == "desktop"
        and "PLANGO_DATA_DIR" not in os.environ
        and Path("./data/yoyu").exists()
    ):
        raise RuntimeError(
            "Legacy desktop data exists at ./data/yoyu. Stop its processes and back it up, "
            "then move the complete directory to ./data/plango before starting; "
            "do not merge or overwrite an existing destination. "
            "To retain the original location explicitly, set PLANGO_DATA_DIR=./data/yoyu."
        )
    data = Path(os.environ.get("PLANGO_DATA_DIR", "./data/plango")).expanduser().resolve()
    allowed = {
        "openai_api_key": "OPENAI_API_KEY",
        "openai_base_url": "OPENAI_BASE_URL",
        "openai_model": "OPENAI_MODEL",
        "openai_timeout_seconds": "PLANGO_MODEL_TIMEOUT_SECONDS",
        "openai_max_retries": "PLANGO_MODEL_MAX_RETRIES",
        "amap_webservice_key": "AMAP_WEBSERVICE_KEY",
        "amap_timeout_seconds": "PLANGO_AMAP_TIMEOUT_SECONDS",
        "embedding_api_key": "PLANGO_EMBEDDING_API_KEY",
        "embedding_base_url": "PLANGO_EMBEDDING_BASE_URL",
        "embedding_model": "PLANGO_EMBEDDING_MODEL",
        "agent_mode": "PLANGO_AGENT_MODE",  # Perspective strategy; topology remains one central workflow.
        "max_turns": "PLANGO_MAX_TURNS",
        "max_tool_calls": "PLANGO_MAX_TOOL_CALLS",
        "max_model_tokens": "PLANGO_MAX_MODEL_TOKENS",
        "max_run_seconds": "PLANGO_MAX_RUN_SECONDS",
        "max_repair_rounds": "PLANGO_MAX_REPAIR_ROUNDS",
        "browser_vision_enabled": "PLANGO_BROWSER_VISION_ENABLED",
    }
    explicit = {field: os.environ[env] for field, env in allowed.items() if env in os.environ}
    return DesktopSettings.model_validate(
        {
            **explicit,
            "runtime_profile": os.environ.get("PLANGO_RUNTIME_PROFILE", "desktop"),
            "world_provider": "browser",
            "database_url": os.environ.get(
                "PLANGO_DATABASE_URL", f"sqlite+aiosqlite:///{data / 'runs.sqlite'}"
            ),
            "redis_url": os.environ.get("PLANGO_REDIS_URL", "local://"),
            "embedded_worker": os.environ.get("PLANGO_RUNTIME_PROFILE", "desktop") == "desktop",
            "data_dir": data,
            "checkpoint_path": data / "checkpoints.sqlite",
        }
    )
