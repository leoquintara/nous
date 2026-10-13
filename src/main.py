"""Entry point del servidor MCP de Nous."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.auth import BearerTokenMiddleware
from src.api.routes import router
from src.config import get_settings
from src.core.embeddings import activar_offline_hf
from src.core.exceptions import NousError
from src.mcp.server import mcp_server
from src.mcp.tools import precalentar_modelos


async def _salud(_request: Request) -> JSONResponse:
    """Responde la sonda de vida pública del servidor."""
    return JSONResponse({"status": "ok"})


class SecurityHeadersMiddleware:
    """Añade cabeceras de seguridad HTTP a toda respuesta.

    Envuelve a ``BearerTokenMiddleware`` para que las respuestas 401
    del middleware también las lleven. La API solo sirve JSON, así que
    una CSP de ``default-src 'none'`` es correcta y estricta.
    """

    _HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"content-security-policy", b"default-src 'none'"),
    )

    def __init__(self, app: ASGIApp) -> None:
        """Envuelve la app ASGI interior."""
        self._app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Inyecta las cabeceras en el inicio de la respuesta."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def _send_con_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, _send_con_headers)


def crear_app_http() -> SecurityHeadersMiddleware:
    """Compone la app ASGI combinada: REST bajo /api y MCP en /mcp.

    El lifespan del transporte MCP se propaga manualmente porque
    Starlette no lo reenvía a sub-aplicaciones montadas.

    Returns:
        Aplicación ASGI lista para uvicorn: security headers →
        Bearer token → rutas.
    """
    api = FastAPI(title="Nous API")

    @api.exception_handler(NousError)
    async def _error_dominio(
        _request: Request, exc: NousError
    ) -> JSONResponse:
        """Traduce errores de dominio a 422 con detalle legible."""
        return JSONResponse(
            status_code=422, content={"detail": str(exc)}
        )

    # El dashboard (Next.js en :3000) es un origen distinto al API (:8720).
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.include_router(router)
    mcp_app = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def _lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = Starlette(
        routes=[
            Route("/health", _salud),
            Mount("/api", app=api),
            Mount("/", app=mcp_app),
        ],
        lifespan=_lifespan,
    )
    return SecurityHeadersMiddleware(BearerTokenMiddleware(app))


def _precalentar() -> None:
    """Ejecuta el warm-up de modelos tolerando cualquier fallo.

    Debe correr en el hilo principal: los imports nativos de
    torch/scipy/transformers (LoadLibrary + DllMain) pueden hacer
    deadlock con el loader lock de Windows si se ejecutan en un
    hilo secundario. Verificado: un warm-up en daemon thread dejaba
    bloqueada la primera consulta indefinidamente.

    El warm-up es una optimización: si falla (modelo no cacheado,
    disco lento, etc.) el servidor sigue operativo y la carga se
    hará perezosamente en la primera consulta.
    """
    logger = logging.getLogger("nous")
    try:
        precalentar_modelos()
    except Exception:  # noqa: BLE001 — el warm-up nunca debe tumbar el arranque
        logger.warning(
            "Warm-up de modelos falló; se cargarán en la primera consulta",
            exc_info=True,
        )
    else:
        logger.info("Modelos locales precalentados")


def main() -> None:
    """Arranca el servidor MCP con el transporte configurado."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("nous")
    activar_offline_hf()
    if os.environ.get("NOUS_FAULTHANDLER_SEGS"):
        import faulthandler

        faulthandler.dump_traceback_later(
            float(os.environ["NOUS_FAULTHANDLER_SEGS"]), repeat=True
        )
    if settings.precalentar_modelos:
        _precalentar()
    if settings.mcp_transport == "stdio":
        logger.info("Arrancando Nous (transporte=stdio)")
        mcp_server.run(transport="stdio")
        return
    logger.info(
        "Arrancando Nous (transporte=http, auth=%s)",
        "token" if settings.api_token else "SIN TOKEN (solo desarrollo)",
    )
    uvicorn.run(
        crear_app_http(), host=settings.api_host, port=settings.api_port
    )


if __name__ == "__main__":
    main()
