"""Tipos epistémicos, TTL, supersede y contraparte de compromisos.

``eventos.tipo``: clase epistémica del recuerdo (evento/hecho/creencia/
compromiso/preferencia) — ortogonal a la dimensión. Los compromisos
alimentan el motor de open loops.

``eventos.expira_en``: timestamp Unix opcional — el evento caduca solo
(TTL declarado al recordar); la búsqueda lo excluye y el grafo lo
muestra como huella muerta, pero la línea de universo lo conserva.

``eventos.reemplazado_por``: UUID del evento que lo sustituye
(supersede): la huella vieja queda para arqueología, la nueva es la
cabeza de la cadena.

``eventos.con_quien``: contraparte del compromiso — a quién se le debe
la respuesta (motor de open loops estilo gbrain).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade tipo/TTL/supersede/contraparte a eventos."""
    op.add_column(
        "eventos",
        sa.Column(
            "tipo",
            sa.String(20),
            server_default="evento",
            nullable=False,
        ),
    )
    op.add_column(
        "eventos",
        sa.Column("expira_en", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "eventos",
        sa.Column(
            "reemplazado_por",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eventos.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "eventos",
        sa.Column("con_quien", sa.String(128), nullable=True),
    )
    op.create_index("ix_eventos_tipo", "eventos", ["tipo"])
    op.create_check_constraint(
        "ck_eventos_tipo",
        "eventos",
        "tipo IN ('evento','hecho','creencia','compromiso','preferencia')",
    )
    # Nuevos tipos de vínculo: 'reemplaza' (supersede) y 'cierra'
    # (resolución de compromiso / open loop).
    op.drop_constraint("ck_relaciones_tipo", "relaciones", type_="check")
    op.create_check_constraint(
        "ck_relaciones_tipo",
        "relaciones",
        "tipo IN ('explicita', 'semantica', 'reemplaza', 'cierra')",
    )


def downgrade() -> None:
    """Elimina las columnas epistémicas de eventos."""
    op.drop_constraint("ck_relaciones_tipo", "relaciones", type_="check")
    op.create_check_constraint(
        "ck_relaciones_tipo",
        "relaciones",
        "tipo IN ('explicita', 'semantica')",
    )
    op.drop_constraint("ck_eventos_tipo", "eventos", type_="check")
    op.drop_index("ix_eventos_tipo", table_name="eventos")
    op.drop_column("eventos", "con_quien")
    op.drop_column("eventos", "reemplazado_por")
    op.drop_column("eventos", "expira_en")
    op.drop_column("eventos", "tipo")
