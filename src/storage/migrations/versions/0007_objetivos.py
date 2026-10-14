"""Objetivos del usuario sobre la línea de universo.

``objetivos``: metas administrables que anclan el futuro — las
proyecciones fantasma del río temporal convergen hacia ellos. Un
objetivo tiene fecha límite, dimensión opcional y un ciclo de vida
auditable (activo → cumplido/abandonado, nunca borrado en duro salvo
petición explícita).

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea la tabla de objetivos administrables."""
    op.create_table(
        "objetivos",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("dimension", sa.String(20), nullable=True),
        sa.Column("fecha_objetivo", sa.BigInteger(), nullable=False),
        sa.Column(
            "estado",
            sa.String(20),
            server_default="activo",
            nullable=False,
        ),
        sa.Column("rama", sa.String(64), server_default="main", nullable=False),
        sa.Column(
            "evento_cierre_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eventos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint(
            "dimension IS NULL OR dimension IN "
            "('cognitiva','somatica','conductual')",
            name="ck_objetivos_dimension",
        ),
        sa.CheckConstraint(
            "estado IN ('activo','cumplido','abandonado')",
            name="ck_objetivos_estado",
        ),
        sa.Index("ix_objetivos_estado", "estado"),
        sa.Index("ix_objetivos_fecha", "fecha_objetivo"),
    )


def downgrade() -> None:
    """Elimina la tabla de objetivos."""
    op.drop_table("objetivos")
