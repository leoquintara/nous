"""Neuro-5 — ciclo de sesión completo: bootstrap, guardrails y cierre.

Cuatro cambios de esquema:

- ``eventos.staged``: escrituras en espera de aprobación (patrón
  write-approval — la memoria propone, el humano dispone). Indexados
  pero invisibles al recall hasta ``aprobar_evento``, como la
  cuarentena pero por política de escritura, no por fuente.
- ``eventos.tipo`` gana ``'censor'``: regla-aprendida que veta o
  advierte acciones futuras. Una corrección que madura a prohibición
  — usa toda la maquinaria de eventos (scope, tags, embeddings,
  inmunidad de accionables).
- ``predicciones``: afirmaciones con confianza declarada y resultado
  resoluble — la materia prima del Brier score (la calibración como
  métrica honesta de qué tan confiable es "confiable").
- ``sesiones``: límites de sesión first-class — inicio/fin, resumen y
  handoff curado. La memoria rinde en los boundaries: el bootstrap
  abre, el handoff cierra.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_DEFAULT = "'00000000-0000-0000-0000-000000000002'::uuid"


def upgrade() -> None:
    """Esquema del ciclo de sesión Neuro-5."""
    op.add_column(
        "eventos",
        sa.Column("staged", sa.Boolean, server_default="false", nullable=False),
    )
    # Parcial — staged es la minoría pendiente de aprobación; el
    # índice queda casi vacío y la consulta siempre lo usa.
    op.create_index(
        "ix_eventos_staged",
        "eventos",
        ["workspace_id", "staged"],
        postgresql_where=sa.text("staged"),
    )
    # Los censores conviven con los tipos epistémicos — mismo CHECK.
    op.drop_constraint("ck_eventos_tipo", "eventos", type_="check")
    op.create_check_constraint(
        "ck_eventos_tipo",
        "eventos",
        "tipo IN ('evento','hecho','creencia','compromiso',"
        "'preferencia','censor')",
    )
    op.create_table(
        "predicciones",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            server_default=sa.text(_WORKSPACE_DEFAULT),
            nullable=False,
        ),
        sa.Column(
            "autor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
        ),
        sa.Column("afirmacion", sa.Text, nullable=False),
        sa.Column("confianza", sa.Float, nullable=False),
        sa.Column(
            "evento_id",
            UUID(as_uuid=True),
            sa.ForeignKey("eventos.id", ondelete="SET NULL"),
        ),
        sa.Column("acierto", sa.Boolean),
        sa.Column("creado_en", sa.BigInteger, nullable=False),
        sa.Column("resuelto_en", sa.BigInteger),
        sa.CheckConstraint(
            "confianza >= 0.0 AND confianza <= 1.0",
            name="ck_predicciones_confianza",
        ),
        sa.Index("ix_predicciones_workspace", "workspace_id", "acierto"),
    )
    op.create_table(
        "sesiones",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            server_default=sa.text(_WORKSPACE_DEFAULT),
            nullable=False,
        ),
        sa.Column(
            "autor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
        ),
        sa.Column("agente", sa.String(80), server_default="", nullable=False),
        sa.Column("inicio", sa.BigInteger, nullable=False),
        sa.Column("fin", sa.BigInteger),
        sa.Column("resumen", sa.Text),
        sa.Column("handoff", sa.Text),
        sa.Column("eventos", sa.Integer, server_default="0", nullable=False),
        sa.Index("ix_sesiones_workspace", "workspace_id", "inicio"),
    )


def downgrade() -> None:
    """Revierte el ciclo de sesión Neuro-5."""
    op.drop_table("sesiones")
    op.drop_table("predicciones")
    op.drop_constraint("ck_eventos_tipo", "eventos", type_="check")
    op.create_check_constraint(
        "ck_eventos_tipo",
        "eventos",
        "tipo IN ('evento','hecho','creencia','compromiso','preferencia')",
    )
    op.drop_index("ix_eventos_staged", table_name="eventos")
    op.drop_column("eventos", "staged")
