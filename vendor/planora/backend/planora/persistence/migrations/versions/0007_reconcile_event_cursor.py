"""reconcile the event cursor from durable audit rows"""

from alembic import op

revision = "0007_reconcile_event_cursor"
down_revision = "0006_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE agent_run SET last_event_seq = COALESCE(("
        "SELECT MAX(seq) FROM run_event WHERE run_event.run_id = agent_run.run_id"
        "), 0) WHERE last_event_seq < COALESCE(("
        "SELECT MAX(seq) FROM run_event WHERE run_event.run_id = agent_run.run_id"
        "), 0)"
    )


def downgrade() -> None:
    pass
