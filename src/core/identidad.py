"""Identidad multi-tenant: bootstrap del owner y resolución de keys.

Modelo (decisiones grill-me Q1-Q13): el workspace es el límite de
aislamiento; cada entidad de dominio lleva ``workspace_id``, y las
entidades de memoria además ``scope`` (personal|workspace) y
``autor_id``. La key de API es la credencial completa — el estático
``NOUS_API_TOKEN`` equivale al owner del workspace bootstrap.
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import (
    ApiKeyModel,
    MiembroModel,
    UsuarioModel,
    WorkspaceModel,
)

logger = logging.getLogger(__name__)

USUARIO_SISTEMA_ID = uuid.UUID(int=0)
USUARIO_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
WORKSPACE_PERSONAL_ID = uuid.UUID(
    "00000000-0000-0000-0000-000000000002"
)
HANDLE_OWNER = "owner"
SLUG_WS_PERSONAL = "personal"
PREFIJO_KEY_USUARIO = "ns_u_"
PREFIJO_KEY_INVITACION = "ns_inv_"


@dataclass(frozen=True, slots=True)
class Contexto:
    """Identidad resuelta de una llamada autenticada.

    ``usuario_id=None`` indica modo desarrollo sin token — el acceso
    queda sin atribuir (solo válido en local, ver auth.py).
    """

    usuario_id: uuid.UUID | None
    workspace_id: uuid.UUID
    rol: str  # 'owner' | 'member' | 'viewer'
    key_id: uuid.UUID | None = None

    @property
    def puede_escribir(self) -> bool:
        """True si el rol permite crear/modificar memorias."""
        return self.rol in ("owner", "member")

    @property
    def es_owner(self) -> bool:
        """True si el rol es owner del workspace actual."""
        return self.rol == "owner"


# Contexto de la request HTTP en curso (lo fija el middleware Bearer);
# en transporte stdio queda a None y ``contexto_actual()`` resuelve el
# contexto del proceso (NOUS_API_KEY o el owner local).
_contexto_request: ContextVar["Contexto | None"] = ContextVar(
    "nous_contexto", default=None
)


def fijar_contexto(ctx: "Contexto | None") -> None:
    """Fija el contexto autenticado de la request en curso."""
    _contexto_request.set(ctx)


def leer_contexto_fijado() -> "Contexto | None":
    """Devuelve el contexto fijado por el middleware, si existe."""
    return _contexto_request.get()


def hash_key(key: str) -> str:
    """SHA-256 hex de la key — lo único que se persiste."""
    return hashlib.sha256(key.encode()).hexdigest()


def generar_key_usuario() -> str:
    """Emite una key de usuario ``ns_u_`` + 48 hex."""
    return PREFIJO_KEY_USUARIO + secrets.token_hex(24)


def generar_token_invitacion() -> str:
    """Emite un token de invitación ``ns_inv_`` + 32 hex."""
    return PREFIJO_KEY_INVITACION + secrets.token_hex(16)


async def asegurar_bootstrap(engine: AsyncEngine) -> None:
    """Garantiza sistema+owner+workspace personal+membresía.

    Idempotente (``ON CONFLICT DO NOTHING`` vía merges): la migración
    0009 ya los crea con IDs fijos, pero este camino cubre bases
    creadas por ``create_all`` (tests) o seeds manuales.
    """
    async with get_session(engine) as session:
        sistema = await session.get(UsuarioModel, USUARIO_SISTEMA_ID)
        if sistema is None:
            session.add(
                UsuarioModel(
                    id=USUARIO_SISTEMA_ID, handle="sistema",
                    nombre="Nous (sistema)",
                )
            )
        owner = (
            await session.execute(
                select(UsuarioModel).where(
                    UsuarioModel.handle == HANDLE_OWNER
                )
            )
        ).scalar_one_or_none()
        if owner is None:
            owner = UsuarioModel(handle=HANDLE_OWNER, nombre="Owner")
            session.add(owner)
            await session.flush()
        ws = (
            await session.execute(
                select(WorkspaceModel).where(
                    WorkspaceModel.slug == SLUG_WS_PERSONAL
                )
            )
        ).scalar_one_or_none()
        if ws is None:
            ws = WorkspaceModel(
                slug=SLUG_WS_PERSONAL, nombre="Personal",
                owner_id=owner.id,
            )
            session.add(ws)
            await session.flush()
        membresia = await session.get(
            MiembroModel, (ws.id, owner.id)
        )
        if membresia is None:
            session.add(
                MiembroModel(
                    workspace_id=ws.id, usuario_id=owner.id, rol="owner"
                )
            )
        await session.commit()


_TTL_CACHE_CTX = 30.0
_cache_ctx: dict[str, tuple[Contexto, float]] = {}
_contexto_proceso: Contexto | None = None


def _contexto_owner() -> Contexto:
    """Contexto del owner bootstrap — IDs fijos de la migración 0009."""
    return Contexto(
        usuario_id=USUARIO_OWNER_ID,
        workspace_id=WORKSPACE_PERSONAL_ID,
        rol="owner",
    )


async def resolver_contexto(
    engine: AsyncEngine, token: str | None
) -> Contexto | None:
    """Resuelve una credencial Bearer a su contexto de identidad.

    Orden de resolución:

    1. ``None`` → contexto owner (modo desarrollo local sin token).
    2. Token == ``NOUS_API_TOKEN`` → contexto owner bootstrap.
    3. Prefijo ``ns_u_`` → key de usuario: se busca su hash, su
       workspace default y su rol en ``miembros``.
    4. Cualquier otra cosa → ``None`` (401).

    La resolución de keys se cachea ``_TTL_CACHE_CTX`` segundos: el
    ``ultimo_uso`` se actualiza en la misma consulta de resolución,
    no una por request.
    """
    if token is None:
        return _contexto_owner()
    from src.config import get_settings

    esperado = get_settings().api_token
    if esperado is not None and hmac.compare_digest(token, esperado):
        return _contexto_owner()
    if not token.startswith(PREFIJO_KEY_USUARIO):
        return None
    ahora = time.monotonic()
    if (cached := _cache_ctx.get(token)) and cached[1] > ahora:
        return cached[0]
    ctx = await _resolver_key_usuario(engine, token)
    if ctx is not None:
        _cache_ctx[token] = (ctx, ahora + _TTL_CACHE_CTX)
    return ctx


async def _resolver_key_usuario(
    engine: AsyncEngine, key: str
) -> Contexto | None:
    """Resuelve una key ``ns_u_*`` a su (usuario, workspace, rol)."""
    async with get_session(engine) as session:
        fila = (
            await session.execute(
                select(ApiKeyModel).where(
                    ApiKeyModel.hash == hash_key(key),
                    ApiKeyModel.revocada.is_(False),
                )
            )
        ).scalar_one_or_none()
        if fila is None:
            return None
        workspace_id = fila.workspace_default_id
        if workspace_id is None:
            # Key sin workspace por defecto: usa el de su primera
            # membresía; si el usuario no pertenece a ninguno, la
            # key no resuelve contexto alguno.
            workspace_id = (
                await session.execute(
                    select(MiembroModel.workspace_id)
                    .where(MiembroModel.usuario_id == fila.usuario_id)
                    .order_by(MiembroModel.creado_en)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if workspace_id is None:
                return None
        rol = (
            await session.execute(
                select(MiembroModel.rol).where(
                    MiembroModel.workspace_id == workspace_id,
                    MiembroModel.usuario_id == fila.usuario_id,
                )
            )
        ).scalar_one_or_none()
        if rol is None:
            return None
        fila.ultimo_uso = (
            await session.execute(select(func.now()))
        ).scalar_one()
        await session.commit()
        return Contexto(
            usuario_id=fila.usuario_id,
            workspace_id=workspace_id,
            rol=rol,
            key_id=fila.id,
        )


async def resolver_workspace(
    engine: AsyncEngine, ctx: Contexto, ref: str
) -> Contexto:
    """Resuelve un override de workspace a un contexto equivalente.

    ``ref`` acepta slug o UUID de workspace. La membresía del
    llamante en el destino fija el rol resultante — un viewer en el
    destino sigue siendo viewer aunque sea owner en el origen.

    Args:
        engine: Engine async para abrir la sesión de resolución.
        ctx: Contexto autenticado actual.
        ref: Slug o UUID del workspace destino.

    Returns:
        Contexto con ``workspace_id``/``rol`` del destino.

    Raises:
        ErrorValidacion: Workspace inexistente.
        PermisoDenegadoError: Sin membresía en el destino.
    """
    from src.core.exceptions import ErrorValidacion, PermisoDenegadoError

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
        if ctx.usuario_id is None:
            # Modo desarrollo sin token: acceso abierto al ws.
            return Contexto(
                usuario_id=None, workspace_id=ws.id, rol="owner"
            )
        rol = (
            await session.execute(
                select(MiembroModel.rol).where(
                    MiembroModel.workspace_id == ws.id,
                    MiembroModel.usuario_id == ctx.usuario_id,
                )
            )
        ).scalar_one_or_none()
        if rol is None:
            raise PermisoDenegadoError(
                f"sin membresía en workspace: {ref!r}"
            )
        return Contexto(
            usuario_id=ctx.usuario_id,
            workspace_id=ws.id,
            rol=rol,
            key_id=ctx.key_id,
        )


async def contexto_actual(engine: AsyncEngine) -> Contexto:
    """Contexto efectivo del proceso: request HTTP o identidad stdio.

    En HTTP el middleware fija ``_contexto_request`` por request. En
    stdio no hay middleware: se resuelve ``NOUS_API_KEY`` (key de
    usuario) una sola vez y se cachea a nivel de proceso; sin ella,
    el proceso actúa como el owner local — comportamiento previo al
    multi-tenant.
    """
    if (ctx := _contexto_request.get()) is not None:
        return ctx
    global _contexto_proceso
    if _contexto_proceso is None:
        key = os.environ.get("NOUS_API_KEY")
        if key:
            ctx = await resolver_contexto(engine, key)
            if ctx is None:
                logger.warning(
                    "NOUS_API_KEY no resolvió a ningún usuario; "
                    "cayendo al owner local"
                )
                ctx = _contexto_owner()
            _contexto_proceso = ctx
        else:
            _contexto_proceso = _contexto_owner()
    return _contexto_proceso


def reiniciar_contexto_proceso() -> None:
    """Resetea la identidad stdio cacheada (solo tests)."""
    global _contexto_proceso
    _contexto_proceso = None


# ── Gestión de identidad (CLI y dashboard) ──────────────────────

from src.core.exceptions import (  # noqa: E402
    CupoExcedidoError,
    ErrorValidacion,
)
from src.storage.models import InvitacionModel  # noqa: E402

# ── Feature gate de la edición libre (grill-me Q11) ─────────────


def licencia_activa() -> bool:
    """True si ``NOUS_LICENSE_KEY`` está configurada.

    El gate es sistema de honor: el código AGPL permite quitarlo,
    pero el self-hosted honesto queda limitado a 1 usuario y 3
    workspaces — el uso de equipo real pasa por cloud o licencia.
    """
    from src.config import get_settings

    return bool(get_settings().license_key)


async def _contar_usuarios(engine: AsyncEngine) -> int:
    """Cuenta usuarios reales (excluye el sistema sentinel)."""
    async with get_session(engine) as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(UsuarioModel)
                    .where(UsuarioModel.id != USUARIO_SISTEMA_ID)
                )
            ).scalar_one()
        )


async def _contar_workspaces(engine: AsyncEngine) -> int:
    """Cuenta workspaces existentes (incluye ``personal``)."""
    async with get_session(engine) as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(WorkspaceModel)
                )
            ).scalar_one()
        )


async def _verificar_cupo_usuarios(engine: AsyncEngine) -> None:
    """Exige cupo para un usuario nuevo en la edición libre.

    Raises:
        CupoExcedidoError: Sin licencia y ``max_usuarios_libres``
            alcanzado.
    """
    if licencia_activa():
        return
    from src.config import get_settings

    maximo = get_settings().max_usuarios_libres
    if await _contar_usuarios(engine) >= maximo:
        raise CupoExcedidoError(
            f"edición libre limitada a {maximo} usuario(s) — "
            "activa NOUS_LICENSE_KEY para desbloquear el equipo"
        )


async def _verificar_cupo_workspaces(engine: AsyncEngine) -> None:
    """Exige cupo para un workspace nuevo en la edición libre.

    Raises:
        CupoExcedidoError: Sin licencia y ``max_workspaces_libres``
            alcanzado.
    """
    if licencia_activa():
        return
    from src.config import get_settings

    maximo = get_settings().max_workspaces_libres
    if await _contar_workspaces(engine) >= maximo:
        raise CupoExcedidoError(
            f"edición libre limitada a {maximo} workspaces — "
            "activa NOUS_LICENSE_KEY para crear más"
        )


@dataclass(frozen=True, slots=True)
class EstadoLicencia:
    """Estado del feature gate para exponer en la API/dashboard."""

    licenciado: bool
    usuarios: int
    max_usuarios: int | None
    workspaces: int
    max_workspaces: int | None


async def estado_licencia(engine: AsyncEngine) -> EstadoLicencia:
    """Resume el uso actual contra los límites del plan."""
    from src.config import get_settings

    licenciado = licencia_activa()
    s = get_settings()
    return EstadoLicencia(
        licenciado=licenciado,
        usuarios=await _contar_usuarios(engine),
        max_usuarios=(
            None if licenciado else s.max_usuarios_libres
        ),
        workspaces=await _contar_workspaces(engine),
        max_workspaces=(
            None if licenciado else s.max_workspaces_libres
        ),
    )


async def crear_usuario(
    engine: AsyncEngine,
    handle: str,
    nombre: str,
    email: str | None = None,
) -> UsuarioModel:
    """Crea un usuario. El handle es único e inmutable.

    Args:
        engine: Engine async.
        handle: Identificador público único (slug de persona).
        nombre: Nombre legible.
        email: Opcional — para invitaciones SMTP futuras.

    Returns:
        El usuario persistido.

    Raises:
        ErrorValidacion: Handle ya ocupado o inválido.
    """
    import re as _re

    if not _re.fullmatch(r"[a-z0-9][a-z0-9_.\-]{0,63}", handle):
        raise ErrorValidacion(
            f"handle inválido: {handle!r} — minúsculas, números, ._-"
        )
    await _verificar_cupo_usuarios(engine)
    async with get_session(engine) as session:
        existe = (
            await session.execute(
                select(UsuarioModel.id).where(
                    UsuarioModel.handle == handle
                )
            )
        ).scalar_one_or_none()
        if existe is not None:
            raise ErrorValidacion(f"handle ya registrado: {handle!r}")
        usuario = UsuarioModel(
            handle=handle, nombre=nombre, email=email
        )
        session.add(usuario)
        await session.commit()
        return usuario


async def listar_usuarios(engine: AsyncEngine) -> list[UsuarioModel]:
    """Lista todos los usuarios registrados."""
    async with get_session(engine) as session:
        return list(
            (
                await session.execute(
                    select(UsuarioModel).order_by(UsuarioModel.creado_en)
                )
            ).scalars().all()
        )


async def crear_workspace(
    engine: AsyncEngine,
    slug: str,
    nombre: str,
    owner_id: uuid.UUID,
) -> WorkspaceModel:
    """Crea un workspace con su membresía owner.

    Args:
        engine: Engine async.
        slug: Identificador único del workspace.
        nombre: Nombre legible.
        owner_id: Usuario que queda como owner.

    Returns:
        El workspace persistido.

    Raises:
        ErrorValidacion: Slug ocupado o inválido.
    """
    import re as _re

    if not _re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,63}", slug):
        raise ErrorValidacion(
            f"slug inválido: {slug!r} — minúsculas, números y guiones"
        )
    await _verificar_cupo_workspaces(engine)
    async with get_session(engine) as session:
        existe = (
            await session.execute(
                select(WorkspaceModel.id).where(
                    WorkspaceModel.slug == slug
                )
            )
        ).scalar_one_or_none()
        if existe is not None:
            raise ErrorValidacion(f"slug ya registrado: {slug!r}")
        ws = WorkspaceModel(
            slug=slug, nombre=nombre, owner_id=owner_id
        )
        session.add(ws)
        await session.flush()
        session.add(
            MiembroModel(
                workspace_id=ws.id, usuario_id=owner_id, rol="owner"
            )
        )
        await session.commit()
        return ws


async def listar_workspaces(
    engine: AsyncEngine, usuario_id: uuid.UUID | None = None
) -> list[WorkspaceModel]:
    """Lista workspaces; con usuario_id solo los de sus membresías."""
    async with get_session(engine) as session:
        if usuario_id is None:
            stmt = select(WorkspaceModel).order_by(WorkspaceModel.creado_en)
        else:
            stmt = (
                select(WorkspaceModel)
                .join(
                    MiembroModel,
                    MiembroModel.workspace_id == WorkspaceModel.id,
                )
                .where(MiembroModel.usuario_id == usuario_id)
                .order_by(WorkspaceModel.creado_en)
            )
        return list((await session.execute(stmt)).scalars().all())


async def emitir_key_usuario(
    engine: AsyncEngine,
    usuario_id: uuid.UUID,
    workspace_default_id: uuid.UUID | None = None,
    nombre: str | None = None,
) -> str:
    """Emite una key ``ns_u_*`` para un usuario.

    Solo el hash SHA-256 se persiste — la key en claro se devuelve
    una única vez al emisor.

    Args:
        engine: Engine async.
        usuario_id: Dueño de la key.
        workspace_default_id: Workspace activo por defecto (None =
            primera membresía del usuario al resolver).
        nombre: Etiqueta opcional ("laptop", "cursor").

    Returns:
        La key en claro — mostrar al usuario una sola vez.
    """
    key = generar_key_usuario()
    async with get_session(engine) as session:
        session.add(
            ApiKeyModel(
                hash=hash_key(key),
                prefijo=key[:11],
                usuario_id=usuario_id,
                workspace_default_id=workspace_default_id,
                nombre=nombre,
            )
        )
        await session.commit()
    return key


async def revocar_key_usuario(engine: AsyncEngine, ref: str) -> bool:
    """Revoca una key por UUID o prefijo.

    Args:
        engine: Engine async.
        ref: UUID de la key o su prefijo visible (``ns_u_xxxx``).

    Returns:
        True si se revocó alguna key.
    """
    global _cache_ctx
    try:
        key_id = uuid.UUID(ref)
    except ValueError:
        key_id = None
    async with get_session(engine) as session:
        stmt = select(ApiKeyModel).where(
            (ApiKeyModel.id == key_id)
            if key_id is not None
            else (ApiKeyModel.prefijo == ref)
        )
        filas = (await session.execute(stmt)).scalars().all()
        if not filas:
            return False
        for fila in filas:
            fila.revocada = True
        await session.commit()
    _cache_ctx.clear()
    return True


async def listar_keys(
    engine: AsyncEngine, usuario_id: uuid.UUID | None = None
) -> list[ApiKeyModel]:
    """Lista keys de API (sin hash); con usuario_id solo las suyas."""
    async with get_session(engine) as session:
        stmt = select(ApiKeyModel).order_by(ApiKeyModel.creado_en)
        if usuario_id is not None:
            stmt = stmt.where(ApiKeyModel.usuario_id == usuario_id)
        return list((await session.execute(stmt)).scalars().all())


async def crear_invitacion(
    engine: AsyncEngine,
    workspace_id: uuid.UUID,
    rol: str,
    dias: int = 7,
    creada_por: uuid.UUID | None = None,
) -> str:
    """Emite un token de invitación ``ns_inv_*`` canjeable una vez.

    Args:
        engine: Engine async.
        workspace_id: Workspace al que se invita.
        rol: Rol que recibirá el invitado (member|viewer).
        dias: Días hasta expirar (1-30).
        creada_por: Usuario que emite la invitación.

    Returns:
        El token en claro — el mecanismo de transporte (link,
        email futuro) es externo.

    Raises:
        ErrorValidacion: Rol inválido u owner (no invitable).
    """
    if rol not in ("member", "viewer"):
        raise ErrorValidacion(
            f"rol invitable: {rol!r} — solo member|viewer"
        )
    # La invitación implica un usuario nuevo al canjearse: fallar
    # aquí ahorra emitir un token condenado a expirar sin uso.
    await _verificar_cupo_usuarios(engine)
    token = generar_token_invitacion()
    expira = datetime.now(UTC) + timedelta(
        days=min(max(dias, 1), 30)
    )
    async with get_session(engine) as session:
        session.add(
            InvitacionModel(
                token_hash=hash_key(token),
                workspace_id=workspace_id,
                rol=rol,
                expira_en=expira,
                creado_por=creada_por,
            )
        )
        await session.commit()
    return token


async def canjear_invitacion(
    engine: AsyncEngine,
    token: str,
    handle: str,
    nombre: str,
    email: str | None = None,
) -> tuple[UsuarioModel, str]:
    """Canjea una invitación: crea usuario + membresía + key.

    La invitación queda marcada ``canjeada_por`` — un solo uso.

    Args:
        engine: Engine async.
        token: Token ``ns_inv_*`` recibido.
        handle: Handle elegido por el invitado.
        nombre: Nombre legible.
        email: Opcional.

    Returns:
        (usuario creado, key ``ns_u_*`` en claro).

    Raises:
        ErrorValidacion: Token inválido, vencido, canjeado o handle
            ocupado.
        CupoExcedidoError: Sin licencia y cupo de usuarios lleno.
    """
    await _verificar_cupo_usuarios(engine)
    async with get_session(engine) as session:
        inv = (
            await session.execute(
                select(InvitacionModel).where(
                    InvitacionModel.token_hash == hash_key(token)
                )
            )
        ).scalar_one_or_none()
        ahora = (
            await session.execute(select(func.now()))
        ).scalar_one()
        if inv is None or inv.canjeada_por is not None or (
            inv.expira_en is not None and inv.expira_en <= ahora
        ):
            raise ErrorValidacion(
                "invitación inválida, vencida o ya canjeada"
            )
        existe = (
            await session.execute(
                select(UsuarioModel.id).where(
                    UsuarioModel.handle == handle
                )
            )
        ).scalar_one_or_none()
        if existe is not None:
            raise ErrorValidacion(f"handle ya registrado: {handle!r}")
        usuario = UsuarioModel(
            handle=handle, nombre=nombre, email=email
        )
        session.add(usuario)
        await session.flush()
        session.add(
            MiembroModel(
                workspace_id=inv.workspace_id,
                usuario_id=usuario.id,
                rol=inv.rol,
            )
        )
        inv.canjeada_por = usuario.id
        inv.canjeada_en = ahora
        key = generar_key_usuario()
        session.add(
            ApiKeyModel(
                hash=hash_key(key),
                prefijo=key[:11],
                usuario_id=usuario.id,
                workspace_default_id=inv.workspace_id,
                nombre="invitación",
            )
        )
        await session.commit()
        return usuario, key


@dataclass(frozen=True, slots=True)
class WorkspaceConRol:
    """Workspace del usuario con el rol que tiene en él."""

    id: uuid.UUID
    slug: str
    nombre: str
    rol: str


async def listar_workspaces_con_rol(
    engine: AsyncEngine, usuario_id: uuid.UUID | None
) -> list[WorkspaceConRol]:
    """Lista workspaces del usuario junto a su rol en cada uno.

    ``usuario_id=None`` (modo desarrollo sin token) devuelve todos
    los workspaces con rol owner.

    Args:
        engine: Engine async.
        usuario_id: Usuario cuyas membresías se listan.

    Returns:
        Workspaces ordenados por fecha de creación.
    """
    async with get_session(engine) as session:
        if usuario_id is None:
            filas = (
                await session.execute(
                    select(WorkspaceModel).order_by(
                        WorkspaceModel.creado_en
                    )
                )
            ).scalars().all()
            return [
                WorkspaceConRol(
                    id=w.id, slug=w.slug, nombre=w.nombre, rol="owner"
                )
                for w in filas
            ]
        filas2 = (
            await session.execute(
                select(WorkspaceModel, MiembroModel.rol)
                .join(
                    MiembroModel,
                    MiembroModel.workspace_id == WorkspaceModel.id,
                )
                .where(MiembroModel.usuario_id == usuario_id)
                .order_by(WorkspaceModel.creado_en)
            )
        ).all()
        return [
            WorkspaceConRol(
                id=w.id, slug=w.slug, nombre=w.nombre, rol=rol
            )
            for w, rol in filas2
        ]


@dataclass(frozen=True, slots=True)
class MiembroInfo:
    """Fila de membresía enriquecida con datos del usuario."""

    usuario_id: uuid.UUID
    handle: str
    nombre: str | None
    rol: str


async def listar_miembros(
    engine: AsyncEngine, workspace_id: uuid.UUID
) -> list[MiembroInfo]:
    """Lista los miembros de un workspace con su handle y nombre.

    Args:
        engine: Engine async.
        workspace_id: Workspace cuyo roster se lee.

    Returns:
        Miembros ordenados por fecha de alta.
    """
    async with get_session(engine) as session:
        filas = (
            await session.execute(
                select(MiembroModel, UsuarioModel)
                .join(
                    UsuarioModel,
                    UsuarioModel.id == MiembroModel.usuario_id,
                )
                .where(MiembroModel.workspace_id == workspace_id)
                .order_by(MiembroModel.creado_en)
            )
        ).all()
    return [
        MiembroInfo(
            usuario_id=u.id, handle=u.handle, nombre=u.nombre, rol=m.rol
        )
        for m, u in filas
    ]


async def _es_ultimo_owner(
    engine: AsyncEngine, workspace_id: uuid.UUID, usuario_id: uuid.UUID
) -> bool:
    """True si quitar/degradar a este usuario deja el ws sin owner."""
    async with get_session(engine) as session:
        owners = (
            await session.execute(
                select(MiembroModel.usuario_id).where(
                    MiembroModel.workspace_id == workspace_id,
                    MiembroModel.rol == "owner",
                )
            )
        ).scalars().all()
    return list(owners) == [usuario_id]


async def quitar_miembro(
    engine: AsyncEngine, workspace_id: uuid.UUID, usuario_id: uuid.UUID
) -> None:
    """Quita un miembro del workspace (sus memorias quedan intactas).

    Args:
        engine: Engine async.
        workspace_id: Workspace afectado.
        usuario_id: Miembro a expulsar.

    Raises:
        ErrorValidacion: El usuario no es miembro, o es el último
            owner (el workspace no puede quedar sin dueño).
    """
    async with get_session(engine) as session:
        fila = (
            await session.execute(
                select(MiembroModel).where(
                    MiembroModel.workspace_id == workspace_id,
                    MiembroModel.usuario_id == usuario_id,
                )
            )
        ).scalar_one_or_none()
        if fila is None:
            raise ErrorValidacion("el usuario no es miembro")
    if await _es_ultimo_owner(engine, workspace_id, usuario_id):
        raise ErrorValidacion(
            "no se puede quitar al único owner del workspace"
        )
    async with get_session(engine) as session:
        fila = (
            await session.execute(
                select(MiembroModel).where(
                    MiembroModel.workspace_id == workspace_id,
                    MiembroModel.usuario_id == usuario_id,
                )
            )
        ).scalar_one()
        await session.delete(fila)
        await session.commit()
    _cache_ctx.clear()


async def cambiar_rol(
    engine: AsyncEngine,
    workspace_id: uuid.UUID,
    usuario_id: uuid.UUID,
    rol: str,
) -> None:
    """Cambia el rol de un miembro (member|viewer; owner no asignable).

    Args:
        engine: Engine async.
        workspace_id: Workspace afectado.
        usuario_id: Miembro a modificar.
        rol: Nuevo rol — ``member`` o ``viewer``.

    Raises:
        ErrorValidacion: Rol inválido, usuario no miembro o último
            owner degradado.
    """
    if rol not in ("member", "viewer"):
        raise ErrorValidacion(
            f"rol no asignable: {rol!r} — solo member|viewer"
        )
    async with get_session(engine) as session:
        fila = (
            await session.execute(
                select(MiembroModel).where(
                    MiembroModel.workspace_id == workspace_id,
                    MiembroModel.usuario_id == usuario_id,
                )
            )
        ).scalar_one_or_none()
        if fila is None:
            raise ErrorValidacion("el usuario no es miembro")
        rol_actual = fila.rol
    if rol_actual == rol:
        return
    if rol_actual == "owner" and await _es_ultimo_owner(
        engine, workspace_id, usuario_id
    ):
        raise ErrorValidacion(
            "no se puede degradar al único owner del workspace"
        )
    async with get_session(engine) as session:
        fila = (
            await session.execute(
                select(MiembroModel).where(
                    MiembroModel.workspace_id == workspace_id,
                    MiembroModel.usuario_id == usuario_id,
                )
            )
        ).scalar_one()
        fila.rol = rol
        await session.commit()
    _cache_ctx.clear()
