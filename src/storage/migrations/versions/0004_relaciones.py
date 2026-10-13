"""Tabla relaciones: vínculos explícitos y semánticos entre eventos.

``explicita``: declarada por el agente/usuario al registrar un evento.
``semantica``: inferida al indexarse (similitud coseno del embedding
por encima del umbral configurado). El par se almacena en orden
canónico (origen_id < destino_id) — la relación es no dirigida.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea la tabla relaciones con sus índices y restricciones."""
    op.create_table(
        "relaciones",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("origen_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destino_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("peso", sa.Float(), server_default="1.0"),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["origen_id"], ["eventos.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["destino_id"], ["eventos.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "origen_id < destino_id", name="ck_relaciones_orden"
        ),
        sa.CheckConstraint(
            "tipo IN ('explicita', 'semantica')", name="ck_relaciones_tipo"
        ),
        sa.UniqueConstraint(
            "origen_id", "destino_id", "tipo", name="uq_relaciones_par_tipo"
        ),
    )
    op.create_index("ix_relaciones_origen", "relaciones", ["origen_id"])
    op.create_index("ix_relaciones_destino", "relaciones", ["destino_id"])


def downgrade() -> None:
    """Elimina la tabla relaciones."""
    op.drop_table("relaciones")
