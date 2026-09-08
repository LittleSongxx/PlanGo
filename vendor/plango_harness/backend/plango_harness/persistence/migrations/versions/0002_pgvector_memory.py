"""add pgvector and the service pg_trgm lexical index"""

from alembic import op

revision = "0002_pgvector_memory"
down_revision = "0001_planora_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("ALTER TABLE memory_document ADD COLUMN IF NOT EXISTS embedding vector(1024)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_document_embedding_hnsw "
        "ON memory_document USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_document_trgm "
        "ON memory_document USING gin (content gin_trgm_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_memory_document_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_memory_document_trgm")
    op.execute("ALTER TABLE memory_document DROP COLUMN IF EXISTS embedding")
