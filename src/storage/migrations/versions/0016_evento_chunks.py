"""Retrieval a nivel fragmento: ``evento_chunks``.

Un documento largo embebido como un solo vector diluye la señal de
cada sección — una consulta precisa pierde contra textos cortos
densos en el término. Los chunks conservan la topicalidad por
fragmento (misma unidad que los ``content_chunks`` de gbrain); la
visibilidad/archivo se hereda del evento padre por join, nunca se
consulta un chunk cuyo evento no sea evocable.

Revision ID: 0016
Revises: 0015
Create Date: 2026-01-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Crea la tabla de fragmentos indexables con sus índices."""
    op.create_table(
        "evento_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "evento_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eventos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("indice", sa.SmallInteger(), nullable=False),
        sa.Column("contenido", sa.Text(), nullable=False),
        sa.Column(
            "contenido_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('spanish', contenido)", persisted=True
            ),
            nullable=True,
        ),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.UniqueConstraint(
            "evento_id", "indice", name="uq_chunks_evento_indice"
        ),
    )
    op.create_index("ix_chunks_evento", "evento_chunks", ["evento_id"])
    op.create_index(
        "ix_chunks_tsv",
        "evento_chunks",
        ["contenido_tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_chunks_embedding",
        "evento_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Elimina la tabla de fragmentos."""
    op.drop_index("ix_chunks_embedding", table_name="evento_chunks")
    op.drop_index("ix_chunks_tsv", table_name="evento_chunks")
    op.drop_index("ix_chunks_evento", table_name="evento_chunks")
    op.drop_table("evento_chunks")
