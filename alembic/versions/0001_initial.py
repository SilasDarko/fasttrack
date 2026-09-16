"""initial schema: evidence tables, workflow tables, pgvector extension + HNSW indexes

Revision ID: 0001
Revises:
Create Date: 2026-01-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models import Base

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VECTOR_TABLES = [
    "telemetry_events",
    "deployments",
    "source_changes",
    "runbooks",
    "prior_incidents",
]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    # All tables are defined once, on the ORM models in app/models.py; this keeps
    # the schema and the application code from drifting apart for a project at
    # this stage. Later schema changes would be expressed as normal incremental
    # Alembic revisions.
    Base.metadata.create_all(bind)

    for table in _VECTOR_TABLES:
        bind.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_embedding_hnsw "
                f"ON {table} USING hnsw (embedding vector_cosine_ops)"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in _VECTOR_TABLES:
        bind.execute(sa.text(f"DROP INDEX IF EXISTS ix_{table}_embedding_hnsw"))
    Base.metadata.drop_all(bind)
