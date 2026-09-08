"""Persist accepted interrupt commands independently from graph projections."""

import sqlalchemy as sa
from alembic import op

revision = "0009_pending_command"
down_revision = "0008_document_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    names = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("agent_run")}
    if "pending_command" not in names:
        op.add_column("agent_run", sa.Column("pending_command", sa.JSON(none_as_null=True)))


def downgrade() -> None:
    op.drop_column("agent_run", "pending_command")
