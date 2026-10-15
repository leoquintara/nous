"""Subcomandos de identidad multi-tenant — ``nous users|workspaces|keys``.

Capa de presentación: parsea, delega en ``src.core.identidad`` y
formatea la salida. Las keys en claro se imprimen una única vez;
solo sus hashes se persisten.
"""

import argparse
import asyncio
import sys
import uuid
from typing import Any

from src.cli import ui
from src.cli.main import _json
from src.core.exceptions import ErrorValidacion
from src.core.identidad import (
    USUARIO_OWNER_ID,
    canjear_invitacion,
    crear_invitacion,
    crear_usuario,
    crear_workspace,
    emitir_key_usuario,
    listar_keys,
    listar_usuarios,
    listar_workspaces,
    revocar_key_usuario,
)
from src.storage.database import crear_engine


def registrar_subparsers(sub: "argparse._SubParsersAction") -> None:  # type: ignore[type-arg]
    """Añade ``users``, ``workspaces`` y ``keys`` al parser raíz."""
    users = sub.add_parser("users", help="Gestión de usuarios.")
    u_sub = users.add_subparsers(dest="accion", required=True)
    u_crear = u_sub.add_parser("create", help="Crea un usuario + su key.")
    u_crear.add_argument("--handle", required=True, help="Handle único.")
    u_crear.add_argument("--nombre", required=True, help="Nombre legible.")
    u_crear.add_argument("--email", default=None, help="Email opcional.")
    u_crear.add_argument(
        "--workspace", default="personal",
        help="Workspace inicial (default: personal).",
    )
    u_crear.add_argument(
        "--rol", choices=["member", "viewer"], default="member",
        help="Rol en el workspace inicial (default: member).",
    )
    u_list = u_sub.add_parser("list", help="Lista usuarios.")
    u_list.add_argument("--json", action="store_true", help="Salida JSON.")

    wss = sub.add_parser("workspaces", help="Gestión de workspaces.")
    w_sub = wss.add_subparsers(dest="accion", required=True)
    w_crear = w_sub.add_parser("create", help="Crea un workspace.")
    w_crear.add_argument("--slug", required=True, help="Slug único.")
    w_crear.add_argument("--nombre", required=True, help="Nombre legible.")
    w_list = w_sub.add_parser("list", help="Lista workspaces.")
    w_list.add_argument("--json", action="store_true", help="Salida JSON.")
    w_inv = w_sub.add_parser(
        "invite", help="Emite una invitación canjeable al workspace."
    )
    w_inv.add_argument("slug", help="Slug del workspace destino.")
    w_inv.add_argument(
        "--rol", choices=["member", "viewer"], default="member",
        help="Rol del invitado (default: member).",
    )
    w_inv.add_argument(
        "--dias", type=int, default=7,
        help="Días hasta expirar (1-30, default 7).",
    )
    w_canj = w_sub.add_parser(
        "canjear", help="Canjea una invitación: crea usuario + key."
    )
    w_canj.add_argument("token", help="Token ns_inv_* recibido.")
    w_canj.add_argument("--handle", required=True, help="Handle elegido.")
    w_canj.add_argument("--nombre", required=True, help="Nombre legible.")
    w_canj.add_argument("--email", default=None, help="Email opcional.")

    keys = sub.add_parser("keys", help="Gestión de API keys de usuario.")
    k_sub = keys.add_subparsers(dest="accion", required=True)
    k_crear = k_sub.add_parser("create", help="Emite una key ns_u_*.")
    k_crear.add_argument("--usuario", required=True, help="Handle del usuario.")
    k_crear.add_argument(
        "--workspace", default=None,
        help="Workspace default de la key (slug o UUID).",
    )
    k_crear.add_argument(
        "--nombre", default=None, help="Etiqueta (laptop, cursor…)."
    )
    k_rev = k_sub.add_parser("revoke", help="Revoca una key por UUID o prefijo.")
    k_rev.add_argument("ref", help="UUID o prefijo ns_u_xxxx de la key.")
    k_list = k_sub.add_parser("list", help="Lista keys (sin hash).")
    k_list.add_argument("--usuario", default=None, help="Filtrar por handle.")
    k_list.add_argument("--json", action="store_true", help="Salida JSON.")


def cmd_identidad(comando: str, args: argparse.Namespace) -> int:
    """Enruta users/workspaces/keys a su implementación async."""
    tabla = {
        ("users", "create"): _users_create,
        ("users", "list"): _users_list,
        ("workspaces", "create"): _ws_create,
        ("workspaces", "list"): _ws_list,
        ("workspaces", "invite"): _ws_invite,
        ("workspaces", "canjear"): _ws_canjear,
        ("keys", "create"): _keys_create,
        ("keys", "revoke"): _keys_revoke,
        ("keys", "list"): _keys_list,
    }
    fn = tabla.get((comando, args.accion))
    if fn is None:
        return 2
    try:
        return asyncio.run(fn(args))
    except ErrorValidacion as e:
        sys.stderr.write(ui.error(f"error: {e}") + "\n")
        return 1


async def _workspace_por_slug(engine: Any, ref: str) -> Any:
    """Resuelve un workspace por slug o UUID o aborta con mensaje."""
    from sqlalchemy import select

    from src.storage.database import get_session
    from src.storage.models import WorkspaceModel

    try:
        ws_id: uuid.UUID | None = uuid.UUID(ref)
    except ValueError:
        ws_id = None
    async with get_session(engine) as session:
        ws = (
            await session.execute(
                select(WorkspaceModel).where(
                    (WorkspaceModel.id == ws_id)
                    if ws_id is not None
                    else (WorkspaceModel.slug == ref)
                )
            )
        ).scalar_one_or_none()
    if ws is None:
        raise ErrorValidacion(f"workspace no encontrado: {ref!r}")
    return ws


async def _usuario_por_handle(engine: Any, handle: str) -> Any:
    """Resuelve un usuario por handle o aborta con mensaje."""
    from sqlalchemy import select

    from src.storage.database import get_session
    from src.storage.models import UsuarioModel

    async with get_session(engine) as session:
        usuario = (
            await session.execute(
                select(UsuarioModel).where(UsuarioModel.handle == handle)
            )
        ).scalar_one_or_none()
    if usuario is None:
        raise ErrorValidacion(f"usuario no encontrado: {handle!r}")
    return usuario


async def _users_create(args: argparse.Namespace) -> int:
    """``nous users create`` — usuario + membresía + primera key."""

    from src.storage.database import get_session
    from src.storage.models import MiembroModel

    engine = crear_engine()
    try:
        usuario = await crear_usuario(
            engine, args.handle, args.nombre, args.email
        )
        ws = await _workspace_por_slug(engine, args.workspace)
        async with get_session(engine) as session:
            session.add(
                MiembroModel(
                    workspace_id=ws.id,
                    usuario_id=usuario.id,
                    rol=args.rol,
                )
            )
            await session.commit()
        key = await emitir_key_usuario(
            engine, usuario.id, ws.id, nombre="primera"
        )
    finally:
        await engine.dispose()
    sys.stdout.write(
        ui.ok(f"Usuario {args.handle} creado en {args.workspace} ({args.rol}).")
        + "\n"
        + ui.negrita(f"Key (una sola vez): {key}")
        + "\n"
    )
    return 0


async def _users_list(args: argparse.Namespace) -> int:
    """``nous users list``."""
    engine = crear_engine()
    try:
        usuarios = await listar_usuarios(engine)
    finally:
        await engine.dispose()
    if getattr(args, "json", False):
        _json(
            [
                {
                    "id": str(u.id),
                    "handle": u.handle,
                    "nombre": u.nombre,
                    "email": u.email,
                }
                for u in usuarios
            ]
        )
    else:
        for u in usuarios:
            sys.stdout.write(
                f"{ui.negrita(u.handle)} — {u.nombre}"
                f"  {ui.suave(u.email or '')}\n"
            )
    return 0


async def _ws_create(args: argparse.Namespace) -> int:
    """``nous workspaces create`` — workspace con el owner local."""
    engine = crear_engine()
    try:
        ws = await crear_workspace(
            engine, args.slug, args.nombre, USUARIO_OWNER_ID
        )
    finally:
        await engine.dispose()
    sys.stdout.write(
        ui.ok(f"Workspace {ws.slug} creado (owner: local).") + "\n"
    )
    return 0


async def _ws_list(args: argparse.Namespace) -> int:
    """``nous workspaces list``."""
    engine = crear_engine()
    try:
        workspaces = await listar_workspaces(engine)
    finally:
        await engine.dispose()
    if getattr(args, "json", False):
        _json(
            [
                {"id": str(w.id), "slug": w.slug, "nombre": w.nombre}
                for w in workspaces
            ]
        )
    else:
        for w in workspaces:
            sys.stdout.write(f"{ui.negrita(w.slug)} — {w.nombre}\n")
    return 0


async def _ws_invite(args: argparse.Namespace) -> int:
    """``nous workspaces invite`` — token canjeable."""
    engine = crear_engine()
    try:
        ws = await _workspace_por_slug(engine, args.slug)
        token = await crear_invitacion(
            engine, ws.id, args.rol, args.dias, USUARIO_OWNER_ID
        )
    finally:
        await engine.dispose()
    sys.stdout.write(
        ui.ok(f"Invitación a {args.slug} ({args.rol}, {args.dias}d):")
        + "\n"
        + ui.negrita(token)
        + "\n"
        + ui.suave("El invitado la canjea en el dashboard o con ")
        + ui.info("nous workspaces canjear")
        + "\n"
    )
    return 0


async def _ws_canjear(args: argparse.Namespace) -> int:
    """``nous workspaces canjear`` — invitación → usuario + key."""
    engine = crear_engine()
    try:
        usuario, key = await canjear_invitacion(
            engine, args.token, args.handle, args.nombre, args.email
        )
    finally:
        await engine.dispose()
    sys.stdout.write(
        ui.ok(f"Bienvenido, {usuario.handle}.")
        + "\n"
        + ui.negrita(f"Key (una sola vez): {key}")
        + "\n"
    )
    return 0


async def _keys_create(args: argparse.Namespace) -> int:
    """``nous keys create`` — key ns_u_* para un usuario."""
    engine = crear_engine()
    try:
        usuario = await _usuario_por_handle(engine, args.usuario)
        ws = (
            await _workspace_por_slug(engine, args.workspace)
            if args.workspace
            else None
        )
        key = await emitir_key_usuario(
            engine,
            usuario.id,
            ws.id if ws else None,
            nombre=args.nombre,
        )
    finally:
        await engine.dispose()
    sys.stdout.write(
        ui.negrita(f"Key (una sola vez): {key}") + "\n"
    )
    return 0


async def _keys_revoke(args: argparse.Namespace) -> int:
    """``nous keys revoke`` — invalida una key."""
    engine = crear_engine()
    try:
        ok = await revocar_key_usuario(engine, args.ref)
    finally:
        await engine.dispose()
    if not ok:
        sys.stderr.write(
            ui.error(f"key no encontrada: {args.ref}") + "\n"
        )
        return 1
    sys.stdout.write(ui.ok(f"Key revocada: {args.ref}") + "\n")
    return 0


async def _keys_list(args: argparse.Namespace) -> int:
    """``nous keys list``."""
    engine = crear_engine()
    try:
        usuario = (
            await _usuario_por_handle(engine, args.usuario)
            if args.usuario
            else None
        )
        filas = await listar_keys(
            engine, usuario.id if usuario else None
        )
    finally:
        await engine.dispose()
    salida = [
        {
            "id": str(k.id),
            "prefijo": k.prefijo,
            "usuario_id": str(k.usuario_id),
            "workspace_default_id": (
                str(k.workspace_default_id)
                if k.workspace_default_id
                else None
            ),
            "nombre": k.nombre,
            "revocada": k.revocada,
            "ultimo_uso": (
                k.ultimo_uso.isoformat() if k.ultimo_uso else None
            ),
        }
        for k in filas
    ]
    if getattr(args, "json", False):
        _json(salida)
    else:
        for k in salida:
            marca = ui.error("revocada") if k["revocada"] else ui.ok("activa")
            sys.stdout.write(
                f"{k['prefijo']}… {marca} {ui.suave(str(k['nombre'] or ''))}\n"
            )
    return 0
