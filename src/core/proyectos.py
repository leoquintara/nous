"""Dominio de Projects: subdivisión de workspaces con frontera propia.

Reglas (spec 2026-10-30, grill-me Q1-Q22):

- ``project_id NULL`` = memoria del workspace entero — un workspace
  nuevo nace con cero projects.
- Acceso cerrado: solo ``project_members`` ven un project; el owner
  del workspace ve todo por override.
- ``kind='vault'``: archivo minable — el sueño lee pero nunca muta
  sus items; los insights derivados se escriben fuera.
- ``estado='archivado'``: bloquea escritura, sigue en recall.
- Borrar un project: ``ON DELETE SET NULL`` — sus memorias bajan al
  nivel workspace, nada se pierde.
- Membresía ⊆ workspace: nadie entra a un project sin pertenecer
  antes al workspace que lo contiene.
"""

import re
import uuid
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.exceptions import (
    ErrorValidacion,
    EventoNoEncontradoError,
    PermisoDenegadoError,
)
from src.core.identidad import (
    Contexto,
    _slugs_disponibles,
    _verificar_cupo_projects,
)
from src.storage.database import get_session
from src.storage.models import (
    EventoModel,
    MiembroModel,
    ProjectMemberModel,
    ProjectModel,
    UsuarioModel,
)

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9\-]{0,63}")
_KINDS = ("regular", "vault")
_ESTADOS = ("activo", "archivado")
_ROLES = ("owner", "member", "viewer")


async def _proyecto_por_ref(
    session: Any, ctx: Contexto, ref: str
) -> ProjectModel | None:
    """Resuelve un project por slug o UUID en el workspace efectivo."""
    try:
        pid: uuid.UUID | None = uuid.UUID(ref)
    except ValueError:
        pid = None
    return cast(
        "ProjectModel | None",
        (
            await session.execute(
                select(ProjectModel).where(
                    ProjectModel.workspace_id == ctx.workspace_id,
                    (ProjectModel.id == pid)
                    if pid is not None
                    else (ProjectModel.slug == ref),
                )
            )
        ).scalar_one_or_none(),
    )


async def _proyecto_o_error(
    session: Any, ctx: Contexto, ref: str
) -> ProjectModel:
    """Como ``_proyecto_por_ref`` pero falla 422 con los disponibles."""
    proyecto = await _proyecto_por_ref(session, ctx, ref)
    if proyecto is None:
        raise ErrorValidacion(
            f"project not found in workspace: {ref!r} — "
            f"available: {await _slugs_disponibles(session, ctx)}"
        )
    return proyecto


async def _rol_proyecto(
    session: Any, ctx: Contexto, proyecto: ProjectModel
) -> str | None:
    """Rol efectivo del llamante en un project (None = no miembro).

    El owner del workspace actúa como owner en cualquier project —
    override explícito de la decisión de acceso cerrado.
    """
    if ctx.usuario_id is None or ctx.rol == "owner":
        return "owner"
    return cast(
        "str | None",
        (
            await session.execute(
                select(ProjectMemberModel.rol).where(
                    ProjectMemberModel.project_id == proyecto.id,
                    ProjectMemberModel.usuario_id == ctx.usuario_id,
                )
            )
        ).scalar_one_or_none(),
    )


def _requerir_miembro_project(rol: str | None, ref: str) -> str:
    """Exige membresía en el project o falla 403."""
    if rol is None:
        raise PermisoDenegadoError(
            f"no membership in project: {ref!r}"
        )
    return rol


def _requerir_owner_project(rol: str, ref: str) -> None:
    """Exige rol owner del project (o del workspace) o falla 403."""
    if rol != "owner":
        raise PermisoDenegadoError(
            f"project owner required: {ref!r}"
        )


def _requerir_escritura_project(
    proyecto: ProjectModel, rol: str, ref: str
) -> None:
    """Bloquea escritura en projects archivados o con rol viewer.

    Archivado = read-only reconsultable (la memoria se cierra como
    compartimento, no se borra); viewer lee pero no escribe.
    """
    if proyecto.estado == "archivado":
        raise ErrorValidacion(
            f"project archived: {ref!r} — read-only, "
            "reactivate it to write"
        )
    if rol == "viewer":
        raise PermisoDenegadoError(
            f"viewer role cannot write in project: {ref!r}"
        )


async def crear_proyecto(
    engine: AsyncEngine,
    ctx: Contexto,
    slug: str,
    nombre: str,
    descripcion: str | None = None,
    kind: str = "regular",
) -> ProjectModel:
    """Crea un project en el workspace efectivo.

    Cualquier miembro ``member+`` del workspace puede crear; el
    creador queda ``owner`` del project. El cupo libre (5 por
    workspace) no aplica a owner ni instalaciones licenciadas.

    Raises:
        ErrorValidacion: Slug inválido/ocupado o kind inválido.
        PermisoDenegadoError: Rol viewer en el workspace.
        CupoExcedidoError: Cupo de la edición libre alcanzado.
    """
    if not _SLUG_RE.fullmatch(slug):
        raise ErrorValidacion(
            f"invalid project slug: {slug!r} — lowercase, numbers, "
            "dashes"
        )
    if kind not in _KINDS:
        raise ErrorValidacion(
            f"invalid project kind: {kind!r} — {_KINDS}"
        )
    if ctx.rol == "viewer":
        raise PermisoDenegadoError(
            "viewer role cannot create projects"
        )
    await _verificar_cupo_projects(engine, ctx)
    async with get_session(engine) as session:
        existe = (
            await session.execute(
                select(ProjectModel.id).where(
                    ProjectModel.workspace_id == ctx.workspace_id,
                    ProjectModel.slug == slug,
                )
            )
        ).scalar_one_or_none()
        if existe is not None:
            raise ErrorValidacion(f"project slug already taken: {slug!r}")
        proyecto = ProjectModel(
            workspace_id=ctx.workspace_id,
            slug=slug,
            nombre=nombre,
            descripcion=descripcion,
            kind=kind,
            creado_por=ctx.usuario_id,
        )
        session.add(proyecto)
        await session.flush()
        if ctx.usuario_id is not None:
            session.add(
                ProjectMemberModel(
                    project_id=proyecto.id,
                    usuario_id=ctx.usuario_id,
                    rol="owner",
                )
            )
        await session.commit()
        return proyecto


async def obtener_proyecto(
    engine: AsyncEngine, ctx: Contexto, ref: str
) -> ProjectModel:
    """Devuelve un project por slug/UUID del workspace efectivo.

    Exige membresía en el project (el owner del workspace entra por
    override) — un no-miembro recibe 403, no un 404 ambiguo.
    """
    async with get_session(engine) as session:
        proyecto = await _proyecto_o_error(session, ctx, ref)
        _requerir_miembro_project(
            await _rol_proyecto(session, ctx, proyecto), ref
        )
        return proyecto


async def listar_proyectos(
    engine: AsyncEngine, ctx: Contexto
) -> list[ProjectModel]:
    """Roster de projects accesibles para routing semántico.

    El owner del workspace ve todos; el resto solo sus membresías;
    una key con scope estricto ve únicamente su project.
    """
    async with get_session(engine) as session:
        stmt = select(ProjectModel).where(
            ProjectModel.workspace_id == ctx.workspace_id
        )
        if ctx.key_proyecto_id is not None:
            stmt = stmt.where(ProjectModel.id == ctx.key_proyecto_id)
        elif ctx.usuario_id is not None and ctx.rol != "owner":
            stmt = stmt.join(
                ProjectMemberModel,
                ProjectMemberModel.project_id == ProjectModel.id,
            ).where(
                ProjectMemberModel.usuario_id == ctx.usuario_id
            )
        return list(
            (
                await session.execute(stmt.order_by(ProjectModel.slug))
            )
            .scalars()
            .all()
        )


async def actualizar_proyecto(
    engine: AsyncEngine,
    ctx: Contexto,
    ref: str,
    nombre: str | None = None,
    descripcion: str | None = None,
    estado: str | None = None,
) -> ProjectModel:
    """Actualiza nombre/descripción/estado de un project.

    Solo el owner del project o el owner del workspace. ``estado``
    admite ``archivado`` (cierra escritura, sigue en recall) y
    ``activo`` (reactiva).
    """
    if estado is not None and estado not in _ESTADOS:
        raise ErrorValidacion(
            f"invalid project estado: {estado!r} — {_ESTADOS}"
        )
    async with get_session(engine) as session:
        proyecto = await _proyecto_o_error(session, ctx, ref)
        rol = _requerir_miembro_project(
            await _rol_proyecto(session, ctx, proyecto), ref
        )
        _requerir_owner_project(rol, ref)
        if nombre is not None:
            proyecto.nombre = nombre
        if descripcion is not None:
            proyecto.descripcion = descripcion
        if estado is not None:
            proyecto.estado = estado
        await session.commit()
        return proyecto


async def eliminar_proyecto(
    engine: AsyncEngine, ctx: Contexto, ref: str
) -> None:
    """Elimina un project — sus memorias bajan a nivel workspace.

    ``ON DELETE SET NULL`` en todas las FK de memoria: borrar es
    barato y reversible en contenido (las memorias quedan en NULL);
    la organización es lo que se pierde. Members caen por CASCADE.
    """
    async with get_session(engine) as session:
        proyecto = await _proyecto_o_error(session, ctx, ref)
        rol = _requerir_miembro_project(
            await _rol_proyecto(session, ctx, proyecto), ref
        )
        _requerir_owner_project(rol, ref)
        await session.delete(proyecto)
        await session.commit()


async def agregar_miembro(
    engine: AsyncEngine,
    ctx: Contexto,
    ref: str,
    handle: str,
    rol: str = "member",
) -> None:
    """Añade un miembro del workspace al project.

    Membresía ⊆ workspace: el handle debe ser miembro del workspace
    contenedor — no existe membresía de project sin la del espacio.
    """
    if rol not in _ROLES:
        raise ErrorValidacion(f"invalid member role: {rol!r}")
    async with get_session(engine) as session:
        proyecto = await _proyecto_o_error(session, ctx, ref)
        _requerir_owner_project(
            _requerir_miembro_project(
                await _rol_proyecto(session, ctx, proyecto), ref
            ),
            ref,
        )
        usuario = (
            await session.execute(
                select(UsuarioModel).where(UsuarioModel.handle == handle)
            )
        ).scalar_one_or_none()
        if usuario is None:
            raise ErrorValidacion(f"user not found: {handle!r}")
        en_workspace = (
            await session.execute(
                select(MiembroModel.rol).where(
                    MiembroModel.workspace_id == ctx.workspace_id,
                    MiembroModel.usuario_id == usuario.id,
                )
            )
        ).scalar_one_or_none()
        if en_workspace is None:
            raise PermisoDenegadoError(
                f"{handle!r} is not a member of the workspace — "
                "invite them to the workspace first"
            )
        await session.merge(
            ProjectMemberModel(
                project_id=proyecto.id, usuario_id=usuario.id, rol=rol
            )
        )
        await session.commit()


async def quitar_miembro(
    engine: AsyncEngine, ctx: Contexto, ref: str, handle: str
) -> None:
    """Quita un miembro del project (su membresía de workspace sigue)."""
    async with get_session(engine) as session:
        proyecto = await _proyecto_o_error(session, ctx, ref)
        _requerir_owner_project(
            _requerir_miembro_project(
                await _rol_proyecto(session, ctx, proyecto), ref
            ),
            ref,
        )
        usuario = (
            await session.execute(
                select(UsuarioModel).where(UsuarioModel.handle == handle)
            )
        ).scalar_one_or_none()
        if usuario is None:
            raise ErrorValidacion(f"user not found: {handle!r}")
        fila = await session.get(
            ProjectMemberModel, (proyecto.id, usuario.id)
        )
        if fila is None:
            raise EventoNoEncontradoError(
                f"{handle!r} is not a member of project: {ref!r}"
            )
        await session.delete(fila)
        await session.commit()


async def listar_miembros(
    engine: AsyncEngine, ctx: Contexto, ref: str
) -> list[dict[str, Any]]:
    """Miembros del project con su rol (handle + nombre + rol)."""
    async with get_session(engine) as session:
        proyecto = await _proyecto_o_error(session, ctx, ref)
        _requerir_miembro_project(
            await _rol_proyecto(session, ctx, proyecto), ref
        )
        filas = (
            await session.execute(
                select(UsuarioModel.handle, UsuarioModel.nombre,
                       ProjectMemberModel.rol)
                .join(
                    UsuarioModel,
                    UsuarioModel.id == ProjectMemberModel.usuario_id,
                )
                .where(ProjectMemberModel.project_id == proyecto.id)
                .order_by(UsuarioModel.handle)
            )
        ).all()
        return [
            {"handle": f.handle, "nombre": f.nombre, "rol": f.rol}
            for f in filas
        ]


async def asignar_proyecto(
    engine: AsyncEngine,
    ctx: Contexto,
    destino: str | None,
    evento_ids: list[str] | None = None,
    query: str | None = None,
    desde: int | None = None,
    hasta: int | None = None,
) -> dict[str, Any]:
    """Mueve eventos al project destino (o a NULL = nivel workspace).

    Requiere escritura en el destino (member+ del project, o member+
    del workspace si el destino es NULL) y visibilidad sobre los
    eventos origen — el ``UPDATE`` filtra por el predicado de
    visibilidad del llamante, nunca toca lo que no puede ver.

    Args:
        destino: Slug/UUID del project, o ``None``/``"general"`` para
            bajar los eventos al nivel workspace (project_id NULL).
        evento_ids: Selección explícita de UUIDs.
        query: Filtro de texto (tsv español) — para lotes semánticos.
        desde/hasta: Ventana ``timestamp_unix`` opcional.

    Returns:
        ``{"moved": n, "destino": slug|None}``.
    """
    destino_id: uuid.UUID | None = None
    destino_slug: str | None = None
    async with get_session(engine) as session:
        if destino and destino != "general":
            proyecto = await _proyecto_o_error(session, ctx, destino)
            rol = _requerir_miembro_project(
                await _rol_proyecto(session, ctx, proyecto), destino
            )
            _requerir_escritura_project(proyecto, rol, destino)
            if (
                ctx.key_proyecto_id is not None
                and ctx.key_proyecto_id != proyecto.id
            ):
                raise PermisoDenegadoError(
                    "project-scoped key cannot move events to other "
                    "projects"
                )
            destino_id = proyecto.id
            destino_slug = proyecto.slug
        else:
            if ctx.key_proyecto_id is not None:
                raise PermisoDenegadoError(
                    "project-scoped key cannot move events to the "
                    "workspace level"
                )
            if ctx.rol == "viewer":
                raise PermisoDenegadoError(
                    "viewer role cannot reassign events"
                )
        condiciones = [EventoModel.workspace_id == ctx.workspace_id]
        if ctx.usuario_id is not None:
            condiciones.append(
                or_(
                    EventoModel.scope == "workspace",
                    EventoModel.autor_id == ctx.usuario_id,
                    EventoModel.autor_id.is_(None),
                )
            )
        if ctx.key_proyecto_id is not None:
            condiciones.append(
                EventoModel.project_id == ctx.key_proyecto_id
            )
        elif ctx.projects_visibles is not None:
            condiciones.append(
                or_(
                    EventoModel.project_id.is_(None),
                    EventoModel.project_id.in_(ctx.projects_visibles),
                )
            )
        if evento_ids:
            ids = [uuid.UUID(e) for e in evento_ids]
            condiciones.append(EventoModel.id.in_(ids))
        if query:
            condiciones.append(
                EventoModel.contenido_tsv.op("@@")(
                    func.plainto_tsquery("spanish", query)
                )
            )
        if desde is not None:
            condiciones.append(EventoModel.timestamp_unix >= desde)
        if hasta is not None:
            condiciones.append(EventoModel.timestamp_unix <= hasta)
        if not (evento_ids or query or desde is not None or hasta):
            raise ErrorValidacion(
                "assign_project needs a selector: event_ids, query "
                "or a time range"
            )
        resultado = cast(
            CursorResult[EventoModel],
            await session.execute(
                update(EventoModel)
                .where(*condiciones)
                .values(project_id=destino_id)
            ),
        )
        await session.commit()
        return {
            "moved": int(resultado.rowcount or 0),
            "destino": destino_slug,
        }
