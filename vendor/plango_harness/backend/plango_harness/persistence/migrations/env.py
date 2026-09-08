from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from plango_harness.persistence.database import metadata
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = metadata

# Compose supplies the service DSN. Alembic uses the synchronous psycopg
# driver even though the application uses asyncpg.
database_url = os.environ.get("PLANGO_DATABASE_URL")
if database_url:
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", ""),
    )


def include_object(object_, name, type_, reflected, compare_to):
    # LangGraph owns its checkpoint tables, while the native vector column and
    # indexes are installed by the dedicated revisions below.
    if type_ == "table" and reflected and str(name).startswith("checkpoint"):
        return False
    if type_ == "column" and reflected and name == "embedding":
        table = getattr(object_, "table", None)
        if getattr(table, "name", None) == "memory_document":
            return False
    if type_ == "index" and name in {
        "ix_memory_document_embedding_hnsw",
        "ix_memory_document_trgm",
    }:
        return False
    if type_ == "unique_constraint" and name == "uq_run_event_seq":
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
