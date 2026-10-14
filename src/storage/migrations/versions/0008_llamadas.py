"""Registro de llamadas a Nous (trazabilidad).

``llamadas``: cada petición HTTP que llega a Nous — del dashboard, de
un agente MCP, de un importador — con método, ruta, origen, latencia y
status. Las sondas de vida y pings explícitos se marcan con
``es_ping`` para el registro de conectividad. Solo metadata: NUNCA se
registra el contenido de las peticiones ni de las memorias.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea la tabla de llamadas con índices de consulta frecuente."""
    op.create_table(
        "llamadas",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("ts", sa.BigInteger(), nullable=False),
        sa.Column("metodo", sa.String(10), nullable=False),
        sa.Column("ruta", sa.String(160), nullable=False),
        sa.Column("origen", sa.String(80), server_default="", nullable=False),
        sa.Column("agente", sa.String(200), server_default="", nullable=False),
        sa.Column("latencia_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column(
            "es_ping", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Index("ix_llamadas_ts", "ts"),
        sa.Index("ix_llamadas_ruta", "ruta"),
        sa.Index("ix_llamadas_ping", "es_ping"),
    )


def downgrade() -> None:
    """Elimina la tabla de llamadas."""
    op.drop_table("llamadas")
