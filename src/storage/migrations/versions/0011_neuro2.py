"""Neuro-2 — segunda tanda de patrones neurocognitivos.

``eventos`` gana ``inaugura_epoca`` (efecto primacía: el primer
evento tras un hueco de inactividad persiste más) y ``labil_hasta``
(ventana de reconsolidación post-evocación — un supersede dentro de
la ventana es una reconsolidación, arista ``reconsolida``).

``relaciones.tipo`` admite tres tipos nuevos: ``coactiva`` (hebbiana
— eventos que se evocan juntos), ``reconsolida`` (supersede dentro
de la ventana lábil) y ``gist`` (evento semántico destilado de un
cluster de episodios durante el sueño).

Tabla nueva: ``borradores`` — memoria de trabajo (buffer de
Baddeley): pensamientos volátiles con TTL que expiran sin ensayo y
promueven a la línea de universo si se repiten lo bastante.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)
_WS_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000002'::uuid")

_TIPOS_VIEJOS = (
    "'explicita','semantica','reemplaza','cierra','contradice'"
)
_TIPOS_NUEVOS = _TIPOS_VIEJOS + ",'coactiva','reconsolida','gist'"


def _col_ws() -> sa.Column[Any]:
    return sa.Column(
        "workspace_id",
        _UUID,
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=_WS_DEFAULT,
        nullable=False,
    )


def _col_autor() -> sa.Column[Any]:
    return sa.Column(
        "autor_id",
        _UUID,
        sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    """Campos de primacía/labilidad, tabla borradores y nuevos tipos."""
    op.add_column(
        "eventos",
        sa.Column(
            "inaugura_epoca",
            sa.Boolean,
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "eventos",
        sa.Column("labil_hasta", sa.BigInteger, nullable=True),
    )
    op.create_table(
        "borradores",
        sa.Column(
            "id",
            _UUID,
            server_default=sa.func.gen_random_uuid(),
            primary_key=True,
        ),
        _col_ws(),
        _col_autor(),
        sa.Column("contenido", sa.Text, nullable=False),
        sa.Column(
            "dimension", sa.String(20), server_default="cognitiva",
            nullable=False,
        ),
        sa.Column(
            "ensayos", sa.Integer, server_default="0", nullable=False
        ),
        sa.Column("creado_en", sa.BigInteger, nullable=False),
        sa.Column("expira_en", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "dimension IN ('cognitiva', 'somatica', 'conductual')",
            name="ck_borradores_dimension",
        ),
        sa.Index("ix_borradores_workspace", "workspace_id", "expira_en"),
    )
    op.drop_constraint("ck_relaciones_tipo", "relaciones")
    op.create_check_constraint(
        "ck_relaciones_tipo",
        "relaciones",
        f"tipo IN ({_TIPOS_NUEVOS})",
    )


def downgrade() -> None:
    """Revierte la segunda tanda neurocognitiva."""
    op.drop_constraint("ck_relaciones_tipo", "relaciones")
    op.create_check_constraint(
        "ck_relaciones_tipo",
        "relaciones",
        f"tipo IN ({_TIPOS_VIEJOS})",
    )
    op.drop_table("borradores")
    op.drop_column("eventos", "labil_hasta")
    op.drop_column("eventos", "inaugura_epoca")
