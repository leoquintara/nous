"""Patrones de memoria avanzados (ADR-003).

``eventos`` gana los campos que los 7 patrones neurocognitivos necesitan:
``tipo_memoria`` (episódica/semántica/procedimental), ``estado_emocional``,
``weight`` (consolidación), ``ultimo_acceso``, archivado reversible
(``archivado``/``archivado_en``/``razon_archivo``) y ``curado`` (tier
protegido que nunca decae — equivalente al MEMORY.md curado de OpenClaw).

Tablas nuevas: ``intenciones_prospectivas`` (memoria prospectiva
time/event-based), ``scripts_detectados`` (minería de secuencias) y
``reportes_consolidacion`` (transparencia del proceso de sueño —
el equivalente del dream diary de OpenClaw).

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)
_WS_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000002'::uuid")


def _fk(tabla: str, ondelete: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{tabla}.id", ondelete=ondelete)


def _col_ws() -> sa.Column[Any]:
    return sa.Column(
        "workspace_id", _UUID, _fk("workspaces", "CASCADE"),
        server_default=_WS_DEFAULT, nullable=False,
    )


def _col_autor() -> sa.Column[Any]:
    return sa.Column(
        "autor_id", _UUID, _fk("usuarios", "SET NULL"), nullable=True
    )


def upgrade() -> None:
    """Agrega campos neurocognitivos a eventos y crea tablas nuevas."""
    op.add_column(
        "eventos",
        sa.Column("tipo_memoria", sa.String(20), nullable=True),
    )
    op.add_column(
        "eventos", sa.Column("estado_emocional", sa.Float, nullable=True)
    )
    op.add_column(
        "eventos",
        sa.Column(
            "weight", sa.Float, server_default="1.0", nullable=False
        ),
    )
    op.add_column(
        "eventos", sa.Column("ultimo_acceso", sa.BigInteger, nullable=True)
    )
    op.add_column(
        "eventos",
        sa.Column(
            "archivado", sa.Boolean, server_default="false", nullable=False
        ),
    )
    op.add_column(
        "eventos", sa.Column("archivado_en", sa.BigInteger, nullable=True)
    )
    op.add_column(
        "eventos", sa.Column("razon_archivo", sa.Text, nullable=True)
    )
    op.add_column(
        "eventos",
        sa.Column(
            "curado", sa.Boolean, server_default="false", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_eventos_tipo_memoria",
        "eventos",
        "tipo_memoria IS NULL OR tipo_memoria IN "
        "('episodica','semantica','procedimental')",
    )
    op.create_index("ix_eventos_tipo_memoria", "eventos", ["tipo_memoria"])
    op.create_index("ix_eventos_archivado", "eventos", ["archivado"])
    op.create_index("ix_eventos_curado", "eventos", ["curado"])
    op.create_index(
        "ix_eventos_weight_arch",
        "eventos",
        ["workspace_id", "archivado", "weight"],
    )

    op.create_table(
        "intenciones_prospectivas",
        sa.Column(
            "id", _UUID,
            server_default=sa.func.gen_random_uuid(), primary_key=True,
        ),
        _col_ws(),
        _col_autor(),
        sa.Column("creada_en", sa.BigInteger, nullable=False),
        sa.Column("intencion", sa.Text, nullable=False),
        sa.Column("trigger_tipo", sa.String(10), nullable=False),
        sa.Column(
            "trigger_valor",
            postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "ejecutada", sa.Boolean, server_default="false", nullable=False
        ),
        sa.Column(
            "cancelada", sa.Boolean, server_default="false", nullable=False
        ),
        sa.Column("ejecutada_en", sa.BigInteger, nullable=True),
        sa.Column("cancelada_en", sa.BigInteger, nullable=True),
        sa.CheckConstraint(
            "trigger_tipo IN ('time','event')",
            name="ck_intenciones_trigger_tipo",
        ),
        sa.Index(
            "ix_intenciones_workspace", "workspace_id", "ejecutada",
            "cancelada",
        ),
        sa.Index("ix_intenciones_trigger", "trigger_tipo"),
    )

    op.create_table(
        "scripts_detectados",
        sa.Column(
            "id", _UUID,
            server_default=sa.func.gen_random_uuid(), primary_key=True,
        ),
        _col_ws(),
        _col_autor(),
        sa.Column("nombre", sa.String(160), nullable=False),
        sa.Column(
            "pasos",
            postgresql.JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("frecuencia", sa.Float, nullable=False),
        sa.Column("confianza", sa.Float, nullable=False),
        sa.Column("creado_en", sa.BigInteger, nullable=False),
        sa.Column("actualizado_en", sa.BigInteger, nullable=False),
        sa.Index("ix_scripts_workspace", "workspace_id"),
        sa.Index("ix_scripts_confianza", "confianza"),
    )

    op.create_table(
        "reportes_consolidacion",
        sa.Column(
            "id", _UUID,
            server_default=sa.func.gen_random_uuid(), primary_key=True,
        ),
        _col_ws(),
        sa.Column("ejecutado_en", sa.BigInteger, nullable=False),
        sa.Column(
            "fortalecidos", sa.Integer, server_default="0", nullable=False
        ),
        sa.Column(
            "archivados", sa.Integer, server_default="0", nullable=False
        ),
        sa.Column(
            "promovidos", sa.Integer, server_default="0", nullable=False
        ),
        sa.Column(
            "detalle",
            postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Index("ix_reportes_workspace", "workspace_id", "ejecutado_en"),
    )

    # La detección de interferencia (ADR-003) vincula el evento nuevo
    # con el contradicho vía relación tipo 'contradice'.
    op.drop_constraint("ck_relaciones_tipo", "relaciones")
    op.create_check_constraint(
        "ck_relaciones_tipo",
        "relaciones",
        "tipo IN ('explicita','semantica','reemplaza','cierra','contradice')",
    )


def downgrade() -> None:
    """Revierte el esquema neurocognitivo."""
    op.drop_constraint("ck_relaciones_tipo", "relaciones")
    op.create_check_constraint(
        "ck_relaciones_tipo",
        "relaciones",
        "tipo IN ('explicita','semantica','reemplaza','cierra')",
    )
    op.drop_table("reportes_consolidacion")
    op.drop_table("scripts_detectados")
    op.drop_table("intenciones_prospectivas")
    op.drop_index("ix_eventos_weight_arch", table_name="eventos")
    op.drop_index("ix_eventos_curado", table_name="eventos")
    op.drop_index("ix_eventos_archivado", table_name="eventos")
    op.drop_index("ix_eventos_tipo_memoria", table_name="eventos")
    op.drop_constraint("ck_eventos_tipo_memoria", "eventos")
    for col in (
        "curado", "razon_archivo", "archivado_en", "archivado",
        "ultimo_acceso", "weight", "estado_emocional", "tipo_memoria",
    ):
        op.drop_column("eventos", col)
