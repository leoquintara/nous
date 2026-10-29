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
    users = sub.add_parser("users", help="User management.")
    u_sub = users.add_subparsers(dest="accion", required=True)
    u_crear = u_sub.add_parser("create", help="Creates a user + their key.")
    u_crear.add_argument("--handle", required=True, help="Unique handle.")
    u_crear.add_argument("--name", "--nombre", required=True, help="Display name.")
    u_crear.add_argument("--email", default=None, help="Optional email.")
    u_crear.add_argument(
        "--workspace",
        default="personal",
        help="Initial workspace (default: personal).",
    )
    u_crear.add_argument(
        "--role",
        "--rol",
        choices=["member", "viewer"],
        default="member",
        help="Role in the initial workspace (default: member).",
    )
    u_list = u_sub.add_parser("list", help="Lists users.")
    u_list.add_argument("--json", action="store_true", help="JSON output.")

    wss = sub.add_parser("workspaces", help="Workspace management.")
    w_sub = wss.add_subparsers(dest="accion", required=True)
    w_crear = w_sub.add_parser("create", help="Creates a workspace.")
    w_crear.add_argument("--slug", required=True, help="Unique slug.")
    w_crear.add_argument("--name", "--nombre", required=True, help="Display name.")
    w_list = w_sub.add_parser("list", help="Lists workspaces.")
    w_list.add_argument("--json", action="store_true", help="JSON output.")
    w_inv = w_sub.add_parser("invite", help="Issues a redeemable invitation to the workspace.")
    w_inv.add_argument("slug", metavar="slug", help="Target workspace slug.")
    w_inv.add_argument(
        "--role",
        "--rol",
        choices=["member", "viewer"],
        default="member",
        help="Guest role (default: member).",
    )
    w_inv.add_argument(
        "--days",
        "--dias",
        type=int,
        default=7,
        help="Days until expiry (1-30, default 7).",
    )
    w_canj = w_sub.add_parser(
        "redeem", aliases=["canjear"], help="Redeems an invitation: creates user + key."
    )
    w_canj.add_argument("token", metavar="token", help="Received ns_inv_* token.")
    w_canj.add_argument("--handle", required=True, help="Chosen handle.")
    w_canj.add_argument("--name", "--nombre", required=True, help="Display name.")
    w_canj.add_argument("--email", default=None, help="Optional email.")

    keys = sub.add_parser("keys", help="User API key management.")
    k_sub = keys.add_subparsers(dest="accion", required=True)
    k_crear = k_sub.add_parser("create", help="Issues an ns_u_* key.")
    k_crear.add_argument("--user", "--usuario", required=True, help="User handle.")
    k_crear.add_argument(
        "--workspace",
        default=None,
        help="Default workspace for the key (slug or UUID).",
    )
    k_crear.add_argument("--name", "--nombre", default=None, help="Label (laptop, cursor…).")
    k_rev = k_sub.add_parser("revoke", help="Revokes a key by UUID or prefix.")
    k_rev.add_argument("ref", metavar="ref", help="UUID or ns_u_xxxx prefix of the key.")
    k_list = k_sub.add_parser("list", help="Lists keys (no hash).")
    k_list.add_argument("--user", "--usuario", default=None, help="Filter by handle.")
    k_list.add_argument("--json", action="store_true", help="JSON output.")


def cmd_identidad(comando: str, args: argparse.Namespace) -> int:
    """Enruta users/workspaces/keys a su implementación async."""
    tabla = {
        ("users", "create"): _users_create,
        ("users", "list"): _users_list,
        ("workspaces", "create"): _ws_create,
        ("workspaces", "list"): _ws_list,
        ("workspaces", "invite"): _ws_invite,
        ("workspaces", "redeem"): _ws_canjear,
        ("keys", "create"): _keys_create,
        ("keys", "revoke"): _keys_revoke,
        ("keys", "list"): _keys_list,
    }
    accion = {"canjear": "redeem"}.get(args.accion, args.accion)
    fn = tabla.get((comando, accion))
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
            await session.execute(select(UsuarioModel).where(UsuarioModel.handle == handle))
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
        usuario = await crear_usuario(engine, args.handle, args.name, args.email)
        ws = await _workspace_por_slug(engine, args.workspace)
        async with get_session(engine) as session:
            session.add(
                MiembroModel(
                    workspace_id=ws.id,
                    usuario_id=usuario.id,
                    rol=args.role,
                )
            )
            await session.commit()
        key = await emitir_key_usuario(engine, usuario.id, ws.id, nombre="first")
    finally:
        await engine.dispose()
    sys.stdout.write(
        ui.ok(f"Usuario {args.handle} creado en {args.workspace} ({args.role}).")
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
                f"{ui.negrita(u.handle)} — {u.nombre}" f"  {ui.suave(u.email or '')}\n"
            )
    return 0


async def _ws_create(args: argparse.Namespace) -> int:
    """``nous workspaces create`` — workspace con el owner local."""
    engine = crear_engine()
    try:
        ws = await crear_workspace(engine, args.slug, args.name, USUARIO_OWNER_ID)
    finally:
        await engine.dispose()
    sys.stdout.write(ui.ok(f"Workspace {ws.slug} creado (owner: local).") + "\n")
    return 0


async def _ws_list(args: argparse.Namespace) -> int:
    """``nous workspaces list``."""
    engine = crear_engine()
    try:
        workspaces = await listar_workspaces(engine)
    finally:
        await engine.dispose()
    if getattr(args, "json", False):
        _json([{"id": str(w.id), "slug": w.slug, "nombre": w.nombre} for w in workspaces])
    else:
        for w in workspaces:
            sys.stdout.write(f"{ui.negrita(w.slug)} — {w.nombre}\n")
    return 0


async def _ws_invite(args: argparse.Namespace) -> int:
    """``nous workspaces invite`` — token canjeable."""
    engine = crear_engine()
    try:
        ws = await _workspace_por_slug(engine, args.slug)
        token = await crear_invitacion(engine, ws.id, args.role, args.days, USUARIO_OWNER_ID)
    finally:
        await engine.dispose()
    sys.stdout.write(
        ui.ok(f"Invitación a {args.slug} ({args.role}, {args.days}d):")
        + "\n"
        + ui.negrita(token)
        + "\n"
        + ui.suave("The guest redeems it in the dashboard or with ")
        + ui.info("nous workspaces canjear")
        + "\n"
    )
    return 0


async def _ws_canjear(args: argparse.Namespace) -> int:
    """``nous workspaces canjear`` — invitación → usuario + key."""
    engine = crear_engine()
    try:
        usuario, key = await canjear_invitacion(
            engine, args.token, args.handle, args.name, args.email
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
        usuario = await _usuario_por_handle(engine, args.user)
        ws = await _workspace_por_slug(engine, args.workspace) if args.workspace else None
        key = await emitir_key_usuario(
            engine,
            usuario.id,
            ws.id if ws else None,
            nombre=args.name,
        )
    finally:
        await engine.dispose()
    sys.stdout.write(ui.negrita(f"Key (una sola vez): {key}") + "\n")
    return 0


async def _keys_revoke(args: argparse.Namespace) -> int:
    """``nous keys revoke`` — invalida una key."""
    engine = crear_engine()
    try:
        ok = await revocar_key_usuario(engine, args.ref)
    finally:
        await engine.dispose()
    if not ok:
        sys.stderr.write(ui.error(f"key no encontrada: {args.ref}") + "\n")
        return 1
    sys.stdout.write(ui.ok(f"Key revocada: {args.ref}") + "\n")
    return 0


async def _keys_list(args: argparse.Namespace) -> int:
    """``nous keys list``."""
    engine = crear_engine()
    try:
        usuario = await _usuario_por_handle(engine, args.user) if args.user else None
        filas = await listar_keys(engine, usuario.id if usuario else None)
    finally:
        await engine.dispose()
    salida = [
        {
            "id": str(k.id),
            "prefijo": k.prefijo,
            "usuario_id": str(k.usuario_id),
            "workspace_default_id": (
                str(k.workspace_default_id) if k.workspace_default_id else None
            ),
            "nombre": k.nombre,
            "revocada": k.revocada,
            "ultimo_uso": (k.ultimo_uso.isoformat() if k.ultimo_uso else None),
        }
        for k in filas
    ]
    if getattr(args, "json", False):
        _json(salida)
    else:
        for k in salida:
            marca = ui.error("revocada") if k["revocada"] else ui.ok("activa")
            sys.stdout.write(f"{k['prefijo']}… {marca} {ui.suave(str(k['nombre'] or ''))}\n")
    return 0
