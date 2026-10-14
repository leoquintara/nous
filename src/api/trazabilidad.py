"""Middleware ASGI de trazabilidad: registra cada request con latencia.

Va por fuera de ``BearerTokenMiddleware`` para capturar también los
401 — un intento rechazado es una llamada a Nous igual que una
aceptada. Marca ``es_ping`` en las sondas (``/health``, ``/api/ping``).
"""

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core import trazas

_RUTAS_PING = frozenset({"/health", "/api/health", "/api/ping"})


class TrazabilidadMiddleware:
    """Registra método, ruta, origen, latencia y status de cada request."""

    def __init__(self, app: ASGIApp) -> None:
        """Envuelve la app ASGI interior."""
        self._app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Cronometra la request y encola su registro al terminar."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        inicio = time.perf_counter()
        status = 500

        async def send_observador(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, send_observador)
        finally:
            latencia_ms = int((time.perf_counter() - inicio) * 1000)
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers") or []
            }
            origen, agente = trazas.clasificar_origen(
                headers.get("x-nous-origen"), headers.get("user-agent", "")
            )
            ruta = str(scope.get("path", "/"))
            trazas.registrar(
                str(scope.get("method", "?")),
                ruta,
                origen,
                agente,
                latencia_ms,
                status,
                ruta in _RUTAS_PING,
            )
