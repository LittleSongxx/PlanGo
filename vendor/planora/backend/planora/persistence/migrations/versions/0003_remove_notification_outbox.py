"""remove the unused notification outbox path"""

import sqlalchemy as sa
from alembic import op

revision = "0003_remove_notification_outbox"
down_revision = "0002_pgvector_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("outbox_event"):
        op.drop_table("outbox_event")


def downgrade() -> None:
    op.create_table(
        "outbox_event",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("stream", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
