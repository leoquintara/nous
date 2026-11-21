"""Aprobación por key: escrituras staged por credencial.

El flag global ``NOUS_ESCRITURA_APROBACION`` volvía staged TODAS las
escrituras — impracticable para uso mixto humano/agente. El caso real:
agentes externos (muse, qwen) deberían proponer memorias y un humano
aprobarlas, sin poner staged al dueño. ``api_keys.aprobacion`` marca
la credencial: sus escrituras caen ``staged=true`` aunque el flag
global esté apagado; el resto de claves escribe directo.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agrega ``aprobacion`` a api_keys."""
    op.add_column(
        "api_keys",
        sa.Column(
            "aprobacion",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    """Quita ``aprobacion`` de api_keys."""
    op.drop_column("api_keys", "aprobacion")
