"""Entry point del servidor MCP de Nous."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from src.api.auth import BearerTokenMiddleware
from src.api.routes import router
from src.config import get_settings
from src.core.exceptions import NousError
from src.mcp.server import mcp_server


async def _salud(_request: Request) -> JSONResponse:
    """Responde la sonda de vida pública del servidor."""
    return JSONResponse({"status": "ok"})


def crear_app_http() -> BearerTokenMiddleware:
    """Compone la app ASGI combinada: REST bajo /api y MCP en /mcp.

    El lifespan del transporte MCP se propaga manualmente porque
    Starlette no lo reenvía a sub-aplicaciones montadas.

    Returns:
        Aplicación ASGI lista para uvicorn, con auth Bearer aplicada.
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
    return BearerTokenMiddleware(app)


def main() -> None:
    """Arranca el servidor MCP con el transporte configurado."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("nous")
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
