"""PlanGo migrations load only PlanGo's explicitly configured database."""

from alembic import context
from plango.browser import bindings, commands  # noqa: F401 - register owned tables
from plango.reminders import reminders  # noqa: F401 - register owned tables
from plango.settings import settings_from_env
from plango_harness.persistence.database import metadata
from sqlalchemy import create_engine, pool

database_url = context.config.attributes.get("database_url") or settings_from_env().database_url
url = database_url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")


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
    with create_engine(url, poolclass=pool.NullPool).begin() as connection:
        if connection.dialect.name == "sqlite":
            # sqlite3's legacy mode does not BEGIN for DDL; include schema and revision in one transaction.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        context.configure(
            connection=connection, target_metadata=metadata, include_object=include_object,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()
