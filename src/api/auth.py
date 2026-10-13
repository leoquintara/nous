"""Autenticación por API token (Bearer) para las superficies HTTP de Nous.

Modelo idéntico al de gbrain/memlord: un token estático configurable
con ``NOUS_API_TOKEN`` que los clientes envían como
``Authorization: Bearer <token>``.

Comportamiento:

- Token configurado → toda petición HTTP sin bearer válido recibe 401.
- Token ausente → modo desarrollo local: se permite el paso y se
  registra una advertencia UNA vez al arrancar. Nunca usar así en una
  interfaz expuesta a red.
- stdio no pasa por este módulo (es un proceso local, sin red).
"""

import hmac
import logging

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import get_settings

logger = logging.getLogger("nous.auth")

_bearer = HTTPBearer(auto_error=False)
_advertencia_emitida = False


def token_configurado() -> str | None:
    """Retorna el API token configurado o None en modo desarrollo."""
    return get_settings().api_token


def _advertir_modo_desarrollo() -> None:
    """Registra una única advertencia si el servidor corre sin token."""
    global _advertencia_emitida
    if not _advertencia_emitida:
        _advertencia_emitida = True
        logger.warning(
            "NOUS_API_TOKEN no configurado: HTTP sin autenticación "
            "(solo válido para desarrollo local)"
        )


def _token_valido(token: str, esperado: str) -> bool:
    """Compara el token recibido con el configurado en tiempo constante."""
    return hmac.compare_digest(token.encode(), esperado.encode())


async def verificar_token(request: Request) -> None:
    """Dependencia FastAPI que exige bearer token en rutas protegidas.

    Args:
        request: Petición entrante.

    Raises:
        HTTPException: 401 si falta el header o el token no coincide.
    """
    esperado = token_configurado()
    if esperado is None:
        _advertir_modo_desarrollo()
        return
    credenciales: HTTPAuthorizationCredentials | None = await _bearer(request)
    if credenciales is None or not _token_valido(
        credenciales.credentials, esperado
    ):
        raise HTTPException(
            status_code=401,
            detail="Token de acceso inválido o ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )


class BearerTokenMiddleware:
    """Middleware ASGI que exige bearer token en toda la app envuelta.

    Se usa para proteger el transporte streamable-http del servidor
    MCP, que no pasa por dependencias FastAPI.
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
        # Sondas de vida, el login del dashboard y los preflight CORS
        # son públicos por diseño.
        if scope.get("method") == "OPTIONS" or scope.get("path") in (
            "/health",
            "/api/health",
            "/api/login",
        ):
            await self._app(scope, receive, send)
            return
        esperado = token_configurado()
        if esperado is None:
            _advertir_modo_desarrollo()
            await self._app(scope, receive, send)
            return
        cabeceras = {
            clave.decode(): valor.decode()
            for clave, valor in scope.get("headers", [])
        }
        autorizacion = cabeceras.get("authorization", "")
        esquema, _, token = autorizacion.partition(" ")
        if esquema.lower() != "bearer" or not _token_valido(token, esperado):
            await self._responder_401(send)
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _responder_401(
        send: Send,
    ) -> None:
        """Emite una respuesta 401 JSON sin tocar la app interior."""
        cuerpo = b'{"error":"Token de acceso invalido o ausente"}'
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
