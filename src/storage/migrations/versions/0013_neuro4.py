"""Neuro-4 — cuarta tanda de patrones neurocognitivos.

``eventos`` gana ``debilitados``: contador de debilitamientos por
competencia — cuántas veces la memoria perdió un slot de recall:

- **RIF** (retrieval-induced forgetting): fue candidata de la
  consulta pero no quedó en la selección final — la evocación del
  ganador la suprime un poco.
- **Interferencia proactiva**: un evento nuevo similar-pero-distinto
  se codificó encima — la huella vieja cede el slot a la nueva.

Una sola columna para los dos mecanismos: ambos son "esta memoria
perdió una competencia", auditable igual que ``refuerzos`` cuenta
las victorias.

Los demás patrones Neuro-4 no requieren esquema: el efecto
Zeigarnik reusa ``tipo='compromiso'`` + relaciones ``cierra``, la
flashbulb reusa ``tags`` + ``inaugura_epoca``, y la amnesia de
fuente reusa ``fuentes.confiable``.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Contador de debilitamientos competitivos en ``eventos``."""
    op.add_column(
        "eventos",
        sa.Column(
            "debilitados", sa.Integer, server_default="0", nullable=False
        ),
    )


def downgrade() -> None:
    """Revierte la cuarta tanda neurocognitiva."""
    op.drop_column("eventos", "debilitados")
