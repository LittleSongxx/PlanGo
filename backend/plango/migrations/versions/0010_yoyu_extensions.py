"""Own browser command ledger, session binding, and explicit reminders."""

import sqlalchemy as sa
from alembic import op

revision = "0010_yoyu_extensions"
down_revision = "0009_pending_command"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("yoyu_browser_binding"):
        op.create_table(
            "yoyu_browser_binding",
            sa.Column("run_id", sa.String(64), primary_key=True),
            sa.Column("browser_session_id", sa.String(128), nullable=False),
            sa.Column("input_image", sa.String()),
            sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("enabled_skills", sa.JSON()),
        )
    if not sa.inspect(bind).has_table("yoyu_browser_command"):
        op.create_table(
            "yoyu_browser_command",
            sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("command_id", sa.String(64), unique=True, nullable=False),
            sa.Column("run_id", sa.String(64), nullable=False, index=True),
            sa.Column("browser_session_id", sa.String(128), nullable=False, index=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON()),
            sa.Column("created_at", sa.Float(), nullable=False),
        )
    if not sa.inspect(bind).has_table("yoyu_reminder"):
        op.create_table(
            "yoyu_reminder",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("text", sa.String(2000), nullable=False),
            sa.Column("at_ms", sa.BigInteger(), nullable=False, index=True),
            sa.Column("fired_at_ms", sa.BigInteger()),
        )


def downgrade():
    for name in ("yoyu_reminder", "yoyu_browser_command", "yoyu_browser_binding"):
        op.drop_table(name)
