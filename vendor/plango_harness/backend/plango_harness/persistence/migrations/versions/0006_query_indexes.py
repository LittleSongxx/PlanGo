"""add relational query indexes declared by the application metadata"""

import sqlalchemy as sa
from alembic import op

revision = "0006_query_indexes"
down_revision = "0005_episode_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {
        (table, index["name"])
        for table in (
            "user_fact",
            "trip_episode",
            "procedural_rule",
            "memory_document",
            "memory_event",
            "agent_run",
            "agent_action",
        )
        for index in inspector.get_indexes(table)
    }
    definitions = (
        ("user_fact", "ix_user_fact_user_id", ["user_id"]),
        ("trip_episode", "ix_trip_episode_user_id", ["user_id"]),
        ("procedural_rule", "ix_procedural_rule_user_id", ["user_id"]),
        ("memory_document", "ix_memory_document_user_id", ["user_id"]),
        ("memory_event", "ix_memory_event_user_id", ["user_id"]),
        ("agent_run", "ix_agent_run_user_id", ["user_id"]),
        ("agent_run", "ix_agent_run_thread_id", ["thread_id"]),
        ("agent_action", "ix_agent_action_run_id", ["run_id"]),
    )
    for table, name, columns in definitions:
        if (table, name) not in indexes:
            op.create_index(name, table, columns)


def downgrade() -> None:
    for name in (
        "ix_agent_action_run_id",
        "ix_agent_run_thread_id",
        "ix_agent_run_user_id",
        "ix_memory_event_user_id",
        "ix_memory_document_user_id",
        "ix_procedural_rule_user_id",
        "ix_trip_episode_user_id",
        "ix_user_fact_user_id",
    ):
        op.drop_index(name)
