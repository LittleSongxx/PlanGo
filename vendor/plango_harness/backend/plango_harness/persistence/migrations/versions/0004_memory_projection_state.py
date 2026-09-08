"""add explicit episode projection and durable embedding task state"""

import sqlalchemy as sa
from alembic import op

revision = "0004_memory_projection_state"
down_revision = "0003_remove_notification_outbox"
branch_labels = None
depends_on = None


def _add_if_missing(table: str, column: sa.Column) -> None:
    names = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in names:
        op.add_column(table, column)


def upgrade() -> None:
    _add_if_missing("memory_document", sa.Column("episode_id", sa.String(64)))
    _add_if_missing(
        "memory_document",
        sa.Column(
            "embedding_status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    _add_if_missing(
        "memory_document",
        sa.Column("embedding_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_if_missing("memory_document", sa.Column("embedding_error", sa.Text()))
    _add_if_missing(
        "memory_document", sa.Column("embedding_next_attempt_at", sa.DateTime(timezone=True))
    )
    op.execute(
        "UPDATE memory_document SET embedding_status = 'succeeded' "
        "WHERE embedding_json IS NOT NULL AND embedding_status = 'pending'"
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("DROP INDEX IF EXISTS ix_memory_document_fts")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_memory_document_trgm "
            "ON memory_document USING gin (content gin_trgm_ops)"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_memory_document_trgm")
    for name in (
        "embedding_next_attempt_at",
        "embedding_error",
        "embedding_attempts",
        "embedding_status",
        "episode_id",
    ):
        names = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("memory_document")}
        if name in names:
            op.drop_column("memory_document", name)
