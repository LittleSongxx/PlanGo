"""store stable provenance for opt-in memory documents"""

import sqlalchemy as sa
from alembic import op

revision = "0008_document_provenance"
down_revision = "0007_reconcile_event_cursor"
branch_labels = None
depends_on = None


def _add_if_missing(name: str, column: sa.Column) -> None:
    names = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("memory_document")}
    if name not in names:
        op.add_column("memory_document", column)


def upgrade() -> None:
    _add_if_missing("source_id", sa.Column("source_id", sa.String(128)))
    _add_if_missing("source_version", sa.Column("source_version", sa.String(64)))
    _add_if_missing("chunk_index", sa.Column("chunk_index", sa.Integer()))
    _add_if_missing("source_hash", sa.Column("source_hash", sa.String(64)))
    op.create_index(
        "ix_memory_document_source",
        "memory_document",
        ["user_id", "namespace", "source_id", "source_version"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_memory_document_source", table_name="memory_document")
    for name in ("source_hash", "chunk_index", "source_version", "source_id"):
        names = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("memory_document")}
        if name in names:
            op.drop_column("memory_document", name)
