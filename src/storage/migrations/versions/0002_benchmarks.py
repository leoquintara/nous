"""Tabla benchmarks: corridas de rendimiento persistidas para el dashboard.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea la tabla benchmarks con índices por nombre y fecha."""
    op.create_table(
        "benchmarks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("nombre", sa.String(50), nullable=False),
        sa.Column("escenario", sa.String(200), nullable=True),
        sa.Column(
            "parametros",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metricas",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("git_commit", sa.String(40), nullable=True),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_benchmarks_nombre", "benchmarks", ["nombre"])
    op.create_index("ix_benchmarks_creado_en", "benchmarks", ["creado_en"])


def downgrade() -> None:
    """Elimina la tabla benchmarks."""
    op.drop_table("benchmarks")
