"""Esquema inicial: eventos, trayectorias, proyecciones, configuracion.

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea extensiones y las cuatro tablas del esquema inicial."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "eventos",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("timestamp_unix", sa.BigInteger(), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("fuente", sa.String(50), nullable=False),
        sa.Column("contenido", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("amplitud", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("frecuencia", sa.Float(), nullable=True),
        sa.Column(
            "contexto",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "contenido_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('spanish', contenido)", persisted=True),
        ),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dimension IN ('cognitiva', 'somatica', 'conductual')",
            name="ck_eventos_dimension",
        ),
    )
    op.create_index("ix_eventos_timestamp", "eventos", ["timestamp_unix"])
    op.create_index("ix_eventos_dimension", "eventos", ["dimension"])
    op.create_index("ix_eventos_fuente", "eventos", ["fuente"])
    op.create_index(
        "ix_eventos_tsv",
        "eventos",
        ["contenido_tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_eventos_embedding",
        "eventos",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "trayectorias",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("fecha_inicio", sa.BigInteger(), nullable=False),
        sa.Column("fecha_fin", sa.BigInteger(), nullable=True),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column(
            "eventos_asociados",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("amplitud_promedio", sa.Float(), nullable=True),
        sa.Column("confianza", sa.Float(), nullable=True),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "proyecciones",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("horizonte_dias", sa.Integer(), nullable=False),
        sa.Column("variable_objetivo", sa.String(50), nullable=True),
        sa.Column("probabilidad", sa.Float(), nullable=True),
        sa.Column("vector_proyectado", postgresql.JSONB(), nullable=True),
        sa.Column("rama_decision", sa.Text(), nullable=True),
        sa.Column(
            "basada_en_eventos",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    op.create_table(
        "configuracion",
        sa.Column("clave", sa.String(100), primary_key=True),
        sa.Column(
            "valor",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Elimina las tablas del esquema inicial."""
    op.drop_table("configuracion")
    op.drop_table("proyecciones")
    op.drop_table("trayectorias")
    op.drop_table("eventos")
