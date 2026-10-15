"""Multi-tenant: usuarios, workspaces, miembros, api_keys, invitaciones.

Toda entidad de dominio queda acotada a un ``workspace_id``; las
entidades de memoria además llevan ``scope`` (personal|workspace) y
``autor_id``. Datos existentes se absorben a un workspace bootstrap
``personal`` propiedad del usuario ``owner``, junto a un usuario de
sistema (UUID cero) que actúa de sentinel para config de workspace.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)
_SISTEMA_ID = "00000000-0000-0000-0000-000000000000"
_OWNER_ID = "00000000-0000-0000-0000-000000000001"
_WS_ID = "00000000-0000-0000-0000-000000000002"


def _fk(tabla: str, ondelete: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{tabla}.id", ondelete=ondelete)


_WS_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000002'::uuid")


def _col_ws(
    nullable: bool = False, con_default: bool = True
) -> sa.Column[Any]:
    return sa.Column(
        "workspace_id", _UUID, _fk("workspaces", "CASCADE"),
        server_default=_WS_DEFAULT if con_default else None,
        nullable=nullable,
    )


def _col_scope() -> sa.Column[Any]:
    return sa.Column(
        "scope", sa.String(20), server_default="personal", nullable=False
    )


def _col_autor() -> sa.Column[Any]:
    return sa.Column(
        "autor_id", _UUID, _fk("usuarios", "SET NULL"), nullable=True
    )


def upgrade() -> None:
    """Crea la capa de identidad y acota las entidades de dominio."""
    op.create_table(
        "usuarios",
        sa.Column(
            "id", _UUID, primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("handle", sa.String(64), unique=True, nullable=False),
        sa.Column("nombre", sa.String(200), nullable=True),
        sa.Column("email", sa.String(320), unique=True, nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "workspaces",
        sa.Column(
            "id", _UUID, primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("slug", sa.String(80), unique=True, nullable=False),
        sa.Column("nombre", sa.String(200), nullable=False),
        sa.Column(
            "owner_id", _UUID, _fk("usuarios", "RESTRICT"), nullable=False
        ),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "miembros",
        sa.Column(
            "workspace_id", _UUID, _fk("workspaces", "CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "usuario_id", _UUID, _fk("usuarios", "CASCADE"),
            primary_key=True,
        ),
        sa.Column("rol", sa.String(20), server_default="member"),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "rol IN ('owner','member','viewer')", name="ck_miembros_rol"
        ),
    )
    op.create_table(
        "api_keys",
        sa.Column(
            "id", _UUID, primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("hash", sa.String(64), unique=True, nullable=False),
        sa.Column("prefijo", sa.String(16), nullable=False),
        sa.Column(
            "usuario_id", _UUID, _fk("usuarios", "CASCADE"), nullable=False
        ),
        sa.Column(
            "workspace_default_id", _UUID,
            _fk("workspaces", "SET NULL"), nullable=True,
        ),
        sa.Column("nombre", sa.String(100), server_default=""),
        sa.Column("ultimo_uso", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocada", sa.Boolean(), server_default="false"),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "invitaciones",
        sa.Column(
            "id", _UUID, primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "workspace_id", _UUID, _fk("workspaces", "CASCADE"),
            nullable=False,
        ),
        sa.Column("rol", sa.String(20), server_default="member"),
        sa.Column(
            "creado_por", _UUID, _fk("usuarios", "SET NULL"), nullable=True
        ),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "canjeada_por", _UUID, _fk("usuarios", "SET NULL"), nullable=True
        ),
        sa.Column(
            "canjeada_en", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "rol IN ('member','viewer')", name="ck_invitaciones_rol"
        ),
    )

    # Bootstrap: sistema (sentinel), owner inicial y su workspace.
    op.execute(
        sa.text(
            "INSERT INTO usuarios (id, handle, nombre) VALUES "
            f"('{_SISTEMA_ID}', 'sistema', 'Nous (sistema)'), "
            f"('{_OWNER_ID}', 'owner', 'Owner')"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO workspaces (id, slug, nombre, owner_id) VALUES "
            f"('{_WS_ID}', 'personal', 'Personal', '{_OWNER_ID}')"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO miembros (workspace_id, usuario_id, rol) VALUES "
            f"('{_WS_ID}', '{_OWNER_ID}', 'owner')"
        )
    )

    # Entidades con scope completo: memoria, metas, patrones, futuros.
    for tabla in ("eventos", "trayectorias", "proyecciones", "objetivos"):
        op.add_column(tabla, _col_ws(nullable=True))
        op.add_column(tabla, _col_scope())
        op.add_column(tabla, _col_autor())
        op.execute(
            sa.text(
                f"UPDATE {tabla} SET workspace_id = '{_WS_ID}', "
                f"autor_id = '{_OWNER_ID}'"
            )
        )
        op.alter_column(tabla, "workspace_id", nullable=False)

    # Atribución sin scope: vínculos y fuentes (NOT NULL), auditoría
    # y telemetría (NULLable — una traza puede no resolver workspace).
    for tabla in ("relaciones", "fuentes"):
        op.add_column(tabla, _col_ws(nullable=True))
        op.add_column(tabla, _col_autor())
        op.execute(
            sa.text(
                f"UPDATE {tabla} SET workspace_id = '{_WS_ID}', "
                f"autor_id = '{_OWNER_ID}'"
            )
        )
        op.alter_column(tabla, "workspace_id", nullable=False)
    for tabla in ("benchmarks", "llamadas"):
        op.add_column(
            tabla, _col_ws(nullable=True, con_default=False)
        )
        op.add_column(tabla, _col_autor())
        op.execute(
            sa.text(
                f"UPDATE {tabla} SET workspace_id = '{_WS_ID}', "
                f"autor_id = '{_OWNER_ID}'"
            )
        )

    # PKs compuestas: misma fuente/clave puede existir por workspace.
    op.drop_constraint("fuentes_pkey", "fuentes", type_="primary")
    op.create_primary_key(
        "fuentes_pkey", "fuentes", ["nombre", "workspace_id"]
    )
    op.drop_constraint("configuracion_pkey", "configuracion", type_="primary")
    op.add_column(
        "configuracion", _col_ws(nullable=True),
    )
    op.add_column(
        "configuracion",
        sa.Column(
            "usuario_id", _UUID, _fk("usuarios", "CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE configuracion SET workspace_id = "
            f"'{_WS_ID}', usuario_id = '{_SISTEMA_ID}'"
        )
    )
    op.alter_column("configuracion", "workspace_id", nullable=False)
    op.alter_column("configuracion", "usuario_id", nullable=False)
    op.create_primary_key(
        "configuracion_pkey", "configuracion",
        ["clave", "workspace_id", "usuario_id"],
    )

    op.execute(
        sa.text(
            "ALTER TABLE eventos ADD CONSTRAINT ck_eventos_scope "
            "CHECK (scope IN ('personal','workspace'))"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE trayectorias ADD CONSTRAINT "
            "ck_trayectorias_scope "
            "CHECK (scope IN ('personal','workspace'))"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE proyecciones ADD CONSTRAINT "
            "ck_proyecciones_scope "
            "CHECK (scope IN ('personal','workspace'))"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE objetivos ADD CONSTRAINT ck_objetivos_scope "
            "CHECK (scope IN ('personal','workspace'))"
        )
    )
    op.create_index("ix_eventos_workspace", "eventos", ["workspace_id"])
    op.create_index("ix_eventos_scope", "eventos", ["scope"])
    op.create_index("ix_eventos_autor", "eventos", ["autor_id"])
    op.create_index(
        "ix_trayectorias_workspace", "trayectorias", ["workspace_id"]
    )
    op.create_index(
        "ix_proyecciones_workspace", "proyecciones", ["workspace_id"]
    )
    op.create_index("ix_objetivos_workspace", "objetivos", ["workspace_id"])


def downgrade() -> None:
    """Revierte la capa multi-tenant (destructivo para datos nuevos)."""
    # Índices y checks añadidos por esta revisión (los que referencian
    # columnas a borrar se eliminan implícitamente al drop_column,
    # pero los nombramos para que el plan de Alembic sea explícito).
    for nombre, tabla in (
        ("ix_eventos_workspace", "eventos"),
        ("ix_eventos_scope", "eventos"),
        ("ix_eventos_autor", "eventos"),
        ("ix_trayectorias_workspace", "trayectorias"),
        ("ix_proyecciones_workspace", "proyecciones"),
        ("ix_objetivos_workspace", "objetivos"),
    ):
        op.drop_index(nombre, table_name=tabla)
    for nombre, tabla in (
        ("ck_eventos_scope", "eventos"),
        ("ck_trayectorias_scope", "trayectorias"),
        ("ck_proyecciones_scope", "proyecciones"),
        ("ck_objetivos_scope", "objetivos"),
    ):
        op.drop_constraint(nombre, tabla, type_="check")

    op.drop_constraint("configuracion_pkey", "configuracion", type_="primary")
    op.create_primary_key("configuracion_pkey", "configuracion", ["clave"])
    op.drop_constraint("fuentes_pkey", "fuentes", type_="primary")
    op.create_primary_key("fuentes_pkey", "fuentes", ["nombre"])

    # Columnas exactas por tabla — en PostgreSQL una sentencia
    # fallida aborta toda la transacción de migración, así que no
    # cabe el drop defensivo con try/except.
    cols_por_tabla: dict[str, tuple[str, ...]] = {
        "eventos": ("autor_id", "scope", "workspace_id"),
        "trayectorias": ("autor_id", "scope", "workspace_id"),
        "proyecciones": ("autor_id", "scope", "workspace_id"),
        "objetivos": ("autor_id", "scope", "workspace_id"),
        "relaciones": ("autor_id", "workspace_id"),
        "fuentes": ("autor_id", "workspace_id"),
        "benchmarks": ("autor_id", "workspace_id"),
        "llamadas": ("autor_id", "workspace_id"),
        "configuracion": ("usuario_id", "workspace_id"),
    }
    for tabla, cols in cols_por_tabla.items():
        for col in cols:
            op.drop_column(tabla, col)
    for tabla in (
        "invitaciones", "api_keys", "miembros", "workspaces", "usuarios"
    ):
        op.drop_table(tabla)
