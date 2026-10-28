"""Neuro-6 — capa epistémica: procedencia paramétrica y STP coactiva.

``eventos.parametrica`` marca contenido afirmado desde el conocimiento
paramétrico del agente (sus pesos), no observado ni corroborado — la
metamemoria lo techifica a 0.70 hasta que la corroboración lo levante
(refuerzos ≥ 3 o aprobación humana del flujo staged).

``relaciones.ultima_coactiva`` registra el último co-recall que encendió
la arista — la lectura suma una potenciación rápida (STP, media-vida ~6h)
sobre el peso duradero (LTP): lo recién asociado empuja, lo viejo queda.

Revision ID: 0015
Revises: 0014
Create Date: 2026-01-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Agrega la marca paramétrica y el sello temporal coactivo."""
    op.add_column(
        "eventos",
        sa.Column(
            "parametrica",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "relaciones",
        sa.Column(
            "ultima_coactiva",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    # Las coactivas existentes nunca tuvieron sello — se marcan con su
    # fecha de creación para que el STP ya esté apagado en ellas.
    op.execute(
        "UPDATE relaciones SET ultima_coactiva = "
        "CAST(EXTRACT(EPOCH FROM creado_en) AS BIGINT) "
        "WHERE tipo = 'coactiva' AND ultima_coactiva IS NULL"
    )


def downgrade() -> None:
    """Reversa la capa epistémica."""
    op.drop_column("relaciones", "ultima_coactiva")
    op.drop_column("eventos", "parametrica")
