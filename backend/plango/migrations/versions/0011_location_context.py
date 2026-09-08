"""Persist desktop origin defaults per run, separate from user requirements."""

import sqlalchemy as sa
from alembic import op

revision = "0011_location_context"
down_revision = "0010_yoyu_extensions"
branch_labels = None
depends_on = None


def upgrade():
    if "location_context" not in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns("yoyu_browser_binding")
    }:
        op.add_column("yoyu_browser_binding", sa.Column("location_context", sa.JSON()))


def downgrade():
    op.drop_column("yoyu_browser_binding", "location_context")
