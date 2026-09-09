"""Keep input acceptance identities across responses, worker turns and restarts."""

import sqlalchemy as sa
from alembic import op

revision = "0013_input_acceptance"
down_revision = "0012_plango_rename"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "input_acceptance",
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64)),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("input_acceptance")
