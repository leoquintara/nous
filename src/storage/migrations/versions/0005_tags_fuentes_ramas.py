"""Tags híbridos, ramas de línea de universo y fuentes gestionables.

``eventos.tags``: etiquetas que relacionan contenido (explícitas del
agente + #hashtags extraídos). Los eventos que comparten tag forman
aristas semánticas en el grafo.

``eventos.rama``: línea de universo alternativa ("main" por defecto).
Ramificar una fuente crea una línea paralela que diverge desde un
punto — los eventos anteriores quedan compartidos.

``fuentes``: registro administrable de orígenes de datos unificados
(obsidian, gbrain, memlord, agentes, imports). Permite archivar y
ramificar fuentes sin separar la base de datos.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade tags/rama a eventos y crea la tabla fuentes."""
    op.add_column(
        "eventos",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(64)),
            server_default=sa.text("'{}'::varchar(64)[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "eventos",
        sa.Column(
            "rama",
            sa.String(64),
            server_default="main",
            nullable=False,
        ),
    )
    op.create_index("ix_eventos_tags", "eventos", ["tags"], postgresql_using="gin")
    op.create_index("ix_eventos_rama", "eventos", ["rama"])
    op.create_table(
        "fuentes",
        sa.Column("nombre", sa.String(64), primary_key=True),
        sa.Column("tipo", sa.String(20), server_default="manual", nullable=False),
        sa.Column(
            "estado",
            sa.String(20),
            server_default="activa",
            nullable=False,
        ),
        sa.Column(
            "namespace",
            sa.String(128),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "rama_origen",
            sa.String(64),
            server_default="main",
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "tipo IN ('manual','obsidian','gbrain','memlord','agente','import')",
            name="ck_fuentes_tipo",
        ),
        sa.CheckConstraint(
            "estado IN ('activa','archivada')", name="ck_fuentes_estado"
        ),
    )
    # Registra las fuentes ya presentes en la línea de universo.
    op.execute(
        """
        INSERT INTO fuentes (nombre, tipo, metadata)
        SELECT DISTINCT fuente, 'manual', '{"descubierta": true}'::jsonb
        FROM eventos
        ON CONFLICT (nombre) DO NOTHING
        """
    )


def downgrade() -> None:
    """Elimina fuentes y las columnas nuevas de eventos."""
    op.drop_table("fuentes")
    op.drop_index("ix_eventos_rama", table_name="eventos")
    op.drop_index("ix_eventos_tags", table_name="eventos")
    op.drop_column("eventos", "rama")
    op.drop_column("eventos", "tags")
