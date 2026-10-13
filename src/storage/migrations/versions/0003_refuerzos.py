"""Columna refuerzos: consolidación por evocación (reconsolidación).

Cada vez que una búsqueda devuelve un evento, su contador de
refuerzos crece; el decaimiento por recencia se frena en función de
los refuerzos acumulados (las huellas consolidadas se olvidan más
despacio — Nader et al., 2000; McGaugh, 2000).

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade eventos.refuerzos (int, 0) a las filas existentes."""
    op.add_column(
        "eventos",
        sa.Column(
            "refuerzos", sa.Integer(), server_default="0", nullable=False
        ),
    )


def downgrade() -> None:
    """Elimina la columna refuerzos."""
    op.drop_column("eventos", "refuerzos")
