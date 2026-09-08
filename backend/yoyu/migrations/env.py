"""YOYU migrations load only YOYU's explicitly configured database."""

from alembic import context
from planora.persistence.database import metadata
from sqlalchemy import create_engine, pool
from yoyu.browser import bindings, commands  # noqa: F401 - register owned tables
from yoyu.reminders import reminders  # noqa: F401 - register owned tables
from yoyu.settings import settings_from_env

settings = settings_from_env()
url = settings.database_url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")


def include_object(object_, name, type_, reflected, compare_to):
    # LangGraph owns checkpoint migrations; pgvector indexes use explicit revisions.
    if reflected and type_ == "table" and str(name).startswith("checkpoint"):
        return False
    if reflected and type_ == "column" and name == "embedding":
        return False
    if type_ == "index" and name in {
        "ix_memory_document_embedding_hnsw",
        "ix_memory_document_trgm",
    }:
        return False
    return True


if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url, poolclass=pool.NullPool).connect() as connection:
        context.configure(
            connection=connection, target_metadata=metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()
