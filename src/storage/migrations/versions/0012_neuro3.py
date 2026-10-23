"""Neuro-3 — tercera tanda de patrones neurocognitivos.

``eventos`` gana:

- ``olvido_dirigido``: el usuario pide olvidar — decay acelerado
  (≠ ``archivado``, que es decisión del sueño y reversible por
  restauración; el olvido dirigido acelera la curva natural).
- ``proxima_revision``: siguiente repaso óptimo calculado desde la
  curva de retención (spaced repetition — el SRS vive en la memoria,
  no en una app aparte).
- ``cuarentena``: el evento vino de una fuente no confiable — queda
  fuera del recall hasta que la fuente se apruebe o el evento se
  corrobore (sistema inmune contra inyección por fuentes externas).

``fuentes`` gana ``confiable``: fuente aprobada por el operador
(default true — retrocompatible; solo las marcadas no confiables
mandan sus eventos a cuarentena).

``intenciones_prospectivas`` gana:

- ``aplazada_hasta``: snooze — la intención existe pero no dispara
  hasta ese timestamp (ni por tiempo ni por evento).
- ``preparacion``: prep pack — memorias relacionadas adjuntadas en
  el momento del disparo (JSONB, lista de ``{id, contenido}``).

``relaciones.tipo`` admite dos tipos nuevos: ``rem`` (asociación
descubierta en el sueño — similar pero distante: lo que el cerebro
"hace clic" dormido) y ``encadena`` (evento temporalmente consecutivo
del mismo origen — el "¿y luego qué?" navegable).

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIPOS_VIEJOS = (
    "'explicita','semantica','reemplaza','cierra','contradice',"
    "'coactiva','reconsolida','gist'"
)
_TIPOS_NUEVOS = _TIPOS_VIEJOS + ",'rem','encadena'"


def _col_bool(nombre: str) -> sa.Column[Any]:
    return sa.Column(
        nombre, sa.Boolean, server_default="false", nullable=False
    )


def upgrade() -> None:
    """Columnas de olvido dirigido, SRS y cuarentena; tipos rem/encadena."""
    op.add_column("eventos", _col_bool("olvido_dirigido"))
    op.add_column("eventos", _col_bool("cuarentena"))
    op.add_column(
        "eventos",
        sa.Column("proxima_revision", sa.BigInteger, nullable=True),
    )
    op.add_column(
        "fuentes",
        sa.Column(
            "confiable", sa.Boolean, server_default="true", nullable=False
        ),
    )
    op.create_index(
        "ix_eventos_proxima_revision",
        "eventos",
        ["workspace_id", "proxima_revision"],
        postgresql_where=sa.text("proxima_revision IS NOT NULL"),
    )
    op.create_index(
        "ix_eventos_cuarentena",
        "eventos",
        ["workspace_id", "cuarentena"],
        postgresql_where=sa.text("cuarentena"),
    )
    op.add_column(
        "intenciones_prospectivas",
        sa.Column("aplazada_hasta", sa.BigInteger, nullable=True),
    )
    op.add_column(
        "intenciones_prospectivas",
        sa.Column(
            "preparacion",
            sa.dialects.postgresql.JSONB,
            server_default="[]",
            nullable=False,
        ),
    )
    op.drop_constraint("ck_relaciones_tipo", "relaciones")
    op.create_check_constraint(
        "ck_relaciones_tipo",
        "relaciones",
        f"tipo IN ({_TIPOS_NUEVOS})",
    )


def downgrade() -> None:
    """Revierte la tercera tanda neurocognitiva."""
    op.drop_constraint("ck_relaciones_tipo", "relaciones")
    op.create_check_constraint(
        "ck_relaciones_tipo", "relaciones", f"tipo IN ({_TIPOS_VIEJOS})"
    )
    op.drop_index("ix_eventos_cuarentena", table_name="eventos")
    op.drop_index("ix_eventos_proxima_revision", table_name="eventos")
    op.drop_column("intenciones_prospectivas", "preparacion")
    op.drop_column("intenciones_prospectivas", "aplazada_hasta")
    op.drop_column("fuentes", "confiable")
    op.drop_column("eventos", "proxima_revision")
    op.drop_column("eventos", "cuarentena")
    op.drop_column("eventos", "olvido_dirigido")
