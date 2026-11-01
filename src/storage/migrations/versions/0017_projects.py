"""Projects: subdivisión de workspaces con membresía propia.

El workspace se usaba como si fuera proyecto (``ruba``, ``memlord``
eran workspaces-proyecto). El modelo correcto: workspace = espacio de
equipo; project = compartimento semántico con frontera propia de
membresía. ``project_id NULL`` = memoria del workspace entero;
``kind='vault'`` = archivo minable que el sueño lee pero nunca muta.

La columna se añade a todas las entidades de memoria (``ON DELETE SET
NULL``: borrar un project baja sus memorias al nivel workspace —
archivar/cerrar nunca destruye). ``api_keys.project_id`` habilita keys
de scope estricto a un solo project.

Sin columna: ``relaciones`` (la arista vive bajo el project de su
evento origen), ``evento_chunks`` (hereda por join) y las tablas
operativas (llamadas, benchmarks, configuracion, fuentes,
reportes_consolidacion, scripts_detectados) que son del workspace.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Tablas de memoria que ganan project_id (spec §3). Operativas y
# relaciones/chunks quedan a nivel workspace o heredan por join.
_TABLAS_MEMORIA = (
    "eventos",
    "objetivos",
    "intenciones_prospectivas",
    "trayectorias",
    "sesiones",
    "borradores",
    "predicciones",
    "proyecciones",
)


def _columna_project() -> sa.Column:
    """Columna project_id nullable con SET NULL al borrar el project."""
    return sa.Column(
        "project_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    """Crea projects + project_members y propaga project_id."""
    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("nombre", sa.String(200), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column(
            "kind",
            sa.String(20),
            nullable=False,
            server_default="regular",
        ),
        sa.Column(
            "estado",
            sa.String(20),
            nullable=False,
            server_default="activo",
        ),
        sa.Column(
            "creado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('regular','vault')", name="ck_projects_kind"
        ),
        sa.CheckConstraint(
            "estado IN ('activo','archivado')",
            name="ck_projects_estado",
        ),
        sa.UniqueConstraint(
            "workspace_id", "slug", name="uq_projects_workspace_slug"
        ),
    )
    op.create_index("ix_projects_workspace", "projects", ["workspace_id"])

    op.create_table(
        "project_members",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "usuario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("usuarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rol", sa.String(20), server_default="member"),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rol IN ('owner','member','viewer')",
            name="ck_project_members_rol",
        ),
    )
    op.create_index(
        "ix_project_members_usuario", "project_members", ["usuario_id"]
    )

    op.add_column("api_keys", _columna_project())
    for tabla in _TABLAS_MEMORIA:
        op.add_column(tabla, _columna_project())
        op.create_index(f"ix_{tabla}_project", tabla, ["project_id"])


def downgrade() -> None:
    """Elimina project_id y las tablas de projects."""
    for tabla in _TABLAS_MEMORIA:
        op.drop_index(f"ix_{tabla}_project", table_name=tabla)
        op.drop_column(tabla, "project_id")
    op.drop_column("api_keys", "project_id")
    op.drop_index(
        "ix_project_members_usuario", table_name="project_members"
    )
    op.drop_table("project_members")
    op.drop_index("ix_projects_workspace", table_name="projects")
    op.drop_table("projects")
