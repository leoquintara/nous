"""Autenticación Bearer para las superficies HTTP de Nous.

Multi-tenant (grill-me Q2/Q10): la credencial es una sola —

- ``NOUS_API_TOKEN`` (``ns_*`` estático) → equivale al owner del
  workspace bootstrap ``personal``; las instalaciones viejas siguen
  funcionando sin migrar nada.
- Keys de usuario ``ns_u_*`` → se resuelven contra la tabla
  ``api_keys`` a su (usuario, workspace default, rol en ``miembros``).

El contexto resuelto queda accesible en ``request.state.contexto``
(REST) y en el ContextVar de ``identidad`` (tools MCP / stdio).

Comportamiento:

- Token configurado → toda petición HTTP a /api o /mcp sin bearer
  válido recibe 401.
- Token ausente → modo desarrollo local: se permite el paso como el
  owner bootstrap y se registra una advertencia UNA vez al arrancar.
  Nunca usar así en una interfaz expuesta a red.
- stdio no pasa por este módulo (es un proceso local, sin red); su
  identidad la resuelve ``identidad.contexto_actual`` vía
  ``NOUS_API_KEY``.
"""

import hmac
import logging

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import get_settings
from src.core.identidad import (
    USUARIO_OWNER_ID,
    WORKSPACE_PERSONAL_ID,
    Contexto,
    fijar_contexto,
    resolver_contexto,
)
from src.storage.database import obtener_engine

logger = logging.getLogger("nous.auth")

_bearer = HTTPBearer(auto_error=False)
_advertencia_emitida = False

# Sondas de vida, el login del dashboard y el canje de invitaciones
# (el token ns_inv_ es la credencial) son públicos por diseño.
RUTAS_PUBLICAS: frozenset[str] = frozenset(
    {
        "/health",
        "/api/health",
        "/api/login",
        "/api/invitations/redeem",
    }
)


def token_configurado() -> str | None:
    """Retorna el API token configurado o None en modo desarrollo."""
    return get_settings().api_token


def _token_valido(token: str, esperado: str) -> bool:
    """Compara dos credenciales en tiempo constante."""
    return hmac.compare_digest(token.encode(), esperado.encode())


def _advertir_modo_desarrollo() -> None:
    """Registra una única advertencia si el servidor corre sin token."""
    global _advertencia_emitida
    if not _advertencia_emitida:
        _advertencia_emitida = True
        logger.warning(
            "NOUS_API_TOKEN no configurado: HTTP sin autenticación "
            "(solo válido para desarrollo local)"
        )


def _contexto_owner() -> Contexto:
    """Contexto del owner bootstrap — IDs fijos de la migración 0009."""
    return Contexto(
        usuario_id=USUARIO_OWNER_ID,
        workspace_id=WORKSPACE_PERSONAL_ID,
        rol="owner",
    )


async def _resolver(token: str | None) -> Contexto | None:
    """Resuelve la credencial a contexto; tolera DB inalcanzable.

    ``None`` (modo desarrollo) y el ``NOUS_API_TOKEN`` estático se
    resuelven sin tocar la base de datos — el owner bootstrap no
    depende de consulta alguna. Las keys ``ns_u_*`` sí consultan; si
    PostgreSQL no responde devuelven ``None`` (401).
    """
    if token is None:
        return _contexto_owner()
    esperado = token_configurado()
    if esperado is not None and _token_valido(token, esperado):
        return _contexto_owner()
    if not token.startswith("ns_u_"):
        return None
    try:
        return await resolver_contexto(obtener_engine(), token)
    except OSError:
        logger.warning("Identidad: DB inalcanzable resolviendo key")
        return None


async def verificar_token(request: Request) -> Contexto:
    """Dependencia FastAPI: exige bearer válido y devuelve el contexto.

    Args:
        request: Petición entrante.

    Returns:
        Contexto de identidad (usuario, workspace, rol) del llamante.

    Raises:
        HTTPException: 401 si la credencial no resuelve.
    """
    if token_configurado() is None:
        _advertir_modo_desarrollo()
        ctx = await _resolver(None)
        if ctx is None:  # pragma: no cover — _resolver(None) nunca falla
            raise HTTPException(status_code=401, detail="Sin contexto")
        request.state.contexto = ctx
        return ctx
    credenciales: HTTPAuthorizationCredentials | None = await _bearer(request)
    ctx = await _resolver(
        credenciales.credentials if credenciales else None
    )
    if credenciales is None or ctx is None:
        raise HTTPException(
            status_code=401,
            detail="Token de acceso inválido o ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.contexto = ctx
    return ctx


async def requerir_escritura(
    request: Request,
) -> Contexto:
    """Dependencia FastAPI: exige rol con permiso de escritura.

    Encadena ``verificar_token`` (401 sin credencial válida) y
    responde 403 a viewers. Usar en lugar de ``verificar_token``
    en cualquier endpoint que modifique datos.

    Args:
        request: Petición entrante.

    Returns:
        El contexto autenticado con permiso de escritura.

    Raises:
        HTTPException: 401 sin credencial; 403 si el rol es viewer.
    """
    ctx = await verificar_token(request)
    if not ctx.puede_escribir:
        raise HTTPException(
            status_code=403,
            detail="Rol de solo lectura: sin permiso de escritura",
        )
    return ctx


async def requerir_owner(request: Request) -> Contexto:
    """Dependencia FastAPI: exige rol owner (acciones del servidor).

    Para operaciones de ámbito global — licencia, límites del plan —
    que no pertenecen a ningún workspace concreto.

    Args:
        request: Petición entrante.

    Returns:
        El contexto autenticado del owner.

    Raises:
        HTTPException: 401 sin credencial; 403 si no es owner.
    """
    ctx = await verificar_token(request)
    if ctx.rol != "owner":
        raise HTTPException(
            status_code=403,
            detail="Solo el owner del servidor puede hacer esto",
        )
    return ctx


class BearerTokenMiddleware:
    """Middleware ASGI que exige bearer token en las superficies de datos.

    Resuelve el token a un :class:`Contexto` y lo deja en
    ``scope["state"]["contexto"]`` y en el ContextVar de identidad,
    para que el transporte streamable-http del MCP (que no pasa por
    dependencias FastAPI) conozca quién llama.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Envuelve una aplicación ASGI con la verificación de token.

        Args:
            app: Aplicación ASGI interior (p. ej. la app HTTP de FastMCP).
        """
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Verifica el bearer token antes de delegar en la app interior."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        # Los preflight CORS son públicos por diseño.
        path = scope.get("path", "")
        if scope.get("method") == "OPTIONS" or path in RUTAS_PUBLICAS:
            await self._app(scope, receive, send)
            return
        # El dashboard estático (HTML/JS/CSS) es UI pública: no
        # contiene datos; toda superficie de datos (/api, /mcp) sigue
        # protegida por token.
        prefijo = path.lstrip("/").split("/", 1)[0]
        if prefijo not in ("api", "mcp"):
            await self._app(scope, receive, send)
            return
        if token_configurado() is None:
            _advertir_modo_desarrollo()
            ctx = await _resolver(None)
            if ctx is not None:
                scope.setdefault("state", {})["contexto"] = ctx
                fijar_contexto(ctx)
            await self._app(scope, receive, send)
            return
        cabeceras = {
            clave.decode(): valor.decode()
            for clave, valor in scope.get("headers", [])
        }
        autorizacion = cabeceras.get("authorization", "")
        esquema, _, token = autorizacion.partition(" ")
        if esquema.lower() != "bearer":
            await self._responder_401(send)
            return
        ctx = await _resolver(token)
        if ctx is None:
            await self._responder_401(send)
            return
        scope.setdefault("state", {})["contexto"] = ctx
        fijar_contexto(ctx)
        try:
            await self._app(scope, receive, send)
        finally:
            fijar_contexto(None)

    @staticmethod
    async def _responder_401(
        send: Send,
    ) -> None:
        """Emite una respuesta 401 JSON sin tocar la app interior."""
        cuerpo = b'{"error":"Invalid or missing access token"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send(
            {"type": "http.response.body", "body": cuerpo, "more_body": False}
        )
