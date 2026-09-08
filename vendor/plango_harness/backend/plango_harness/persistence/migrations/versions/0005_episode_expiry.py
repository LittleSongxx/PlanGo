"""store expiry on canonical episodic records"""

import sqlalchemy as sa
from alembic import op

revision = "0005_episode_expiry"
down_revision = "0004_memory_projection_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("trip_episode")}
    if "valid_until" not in columns:
        op.add_column("trip_episode", sa.Column("valid_until", sa.DateTime(timezone=True)))


def downgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("trip_episode")}
    if "valid_until" in columns:
        op.drop_column("trip_episode", "valid_until")
