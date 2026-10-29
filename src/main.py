"""Entry point del servidor MCP de Nous."""

import asyncio
import gc
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete
from sqlalchemy.pool import AsyncAdaptedQueuePool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.auth import BearerTokenMiddleware
from src.api.routes import router
from src.api.trazabilidad import TrazabilidadMiddleware
from src.config import get_settings
from src.core import trazas
from src.core.embeddings import activar_offline_hf
from src.core.exceptions import NousError
from src.mcp.server import mcp_server
from src.mcp.tools import _consultar, _drenar_fondo, _recordar, precalentar_modelos
from src.storage.database import get_session, obtener_engine
from src.storage.models import EventoModel


async def _salud(_request: Request) -> JSONResponse:
    """Responde la sonda de vida pública del servidor."""
    return JSONResponse({"status": "ok"})


class SecurityHeadersMiddleware:
    """Añade cabeceras de seguridad HTTP a toda respuesta.

    Envuelve a ``BearerTokenMiddleware`` para que las respuestas 401
    del middleware también las lleven. La API solo sirve JSON, así que
    recibe CSP ``default-src 'none'``; el dashboard estático (mismo
    origen) necesita CSP propia que permita scripts/estilos 'self'.
    """

    _API = ("api", "mcp", "health")
    _HEADERS_API = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"content-security-policy", b"default-src 'none'"),
        # Respuestas autenticadas: nunca cacheables. Sin esto un 200
        # erróneo (p. ej. un proxy devolviendo HTML) puede quedar en
        # caché heurístico del navegador y envenenar fetches futuros.
        (b"cache-control", b"no-store"),
    )
    _HEADERS_DASH = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (
            b"content-security-policy",
            b"default-src 'self'; script-src 'self' 'unsafe-inline'; "
            b"style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            b"connect-src 'self'; worker-src 'self' blob:; "
            b"font-src 'self' data:",
        ),
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
        prefijo = scope.get("path", "").lstrip("/").split("/", 1)[0]
        extras = (
            self._HEADERS_API if prefijo in self._API else self._HEADERS_DASH
        )

        async def _send_con_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(extras)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, _send_con_headers)


def _dir_dashboard_static() -> Path | None:
    """Localiza el build estático del dashboard (wheel o checkout).

    ADR-001: el dashboard vive en el repo ``nous-dashboard``; se sigue
    aceptando ``dashboard_static`` empaquetado y el ``out/`` hermano
    para servirlo single-process en local.
    """
    raiz = Path(__file__).resolve().parent
    for candidato in (
        raiz / "dashboard_static",                    # incluido en el wheel
        raiz.parent / "nous-dashboard" / "out",       # export del repo hermano
        raiz.parent / "dashboard" / "out",            # checkout fuente (legado)
    ):
        if (candidato / "index.html").is_file():
            return candidato
    return None


def _dashboard_asgi(directorio: Path) -> ASGIApp:
    """Sirve el export estático con fallback SPA.

    ``next export`` emite ``/universo`` como ``universo.html`` (el dir
    homónimo solo lleva payloads RSC). StaticFiles no resuelve esa
    forma: al 404 en un path sin extensión reintenta ``<path>.html``.
    """
    from starlette.exceptions import HTTPException
    from starlette.staticfiles import StaticFiles

    class _StaticSPA(StaticFiles):
        async def get_response(
            self, path: str, scope: Scope
        ) -> Any:
            try:
                resp = await super().get_response(path, scope)
            except HTTPException as exc:
                if exc.status_code != 404 or Path(path).suffix:
                    raise
                return await super().get_response(path + ".html", scope)
            # html=True devuelve 404.html como respuesta, no excepción.
            if resp.status_code == 404 and not Path(path).suffix:
                return await super().get_response(path + ".html", scope)
            return resp

    return _StaticSPA(directory=directorio, html=True)


def crear_app_http() -> ASGIApp:
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
        """Traduce errores de dominio a 422/403/402 según el tipo."""
        from src.core.exceptions import (
            CupoExcedidoError,
            PermisoDenegadoError,
        )

        if isinstance(exc, CupoExcedidoError):
            status = 402
        elif isinstance(exc, PermisoDenegadoError):
            status = 403
        else:
            status = 422
        return JSONResponse(
            status_code=status, content={"detail": str(exc)}
        )

    # El dashboard (Next.js) es un origen distinto al API — configurable
    # vía NOUS_CORS_ORIGINS para self-hosters en otro puerto/dominio.
    settings = get_settings()
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origen.strip()
            for origen in settings.cors_origins.split(",")
            if origen.strip()
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.include_router(router)
    mcp_app = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def _lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with mcp_app.router.lifespan_context(mcp_app):
            # Calienta el path real de escritura/consulta — incluido el
            # dispatch MCP (call_tool compila la validación pydantic de
            # cada tool en su primer uso). Sin esto, las primeras
            # requests de cada proceso pagan ~30ms extra por tool.
            engine = obtener_engine()
            parar_trazas = asyncio.Event()
            tarea_trazas: asyncio.Task[None] | None = None
            try:
                # Owner+workspace personal garantizados antes de que
                # cualquier escritura los referencie (multi-tenant).
                from src.core.identidad import asegurar_bootstrap

                await asegurar_bootstrap(engine)
                try:
                    await mcp_server.call_tool(
                        "recordar_evento",
                        {
                            "contenido": "__warmup__",
                            "dimension": "cognitiva",
                            "fuente": "warmup",
                        },
                    )
                    await mcp_server.call_tool(
                        "consultar_memoria",
                        {"consulta": "__warmup__", "top_n": 1},
                    )
                except Exception:  # noqa: BLE001 — fallback al path directo
                    logging.getLogger("nous").debug(
                        "warmup via call_tool fallo, uso directo"
                    )
                    await _recordar(
                        "__warmup__", "cognitiva", "warmup", 0.5,
                        None, None,
                    )
                    await _consultar("__warmup__", 1)
                await _drenar_fondo()
                async with get_session(engine) as session:
                    await session.execute(
                        delete(EventoModel).where(
                            EventoModel.fuente == "warmup"
                        )
                    )
                    await session.commit()
                # Llena el pool: las tareas write-behind compiten por
                # conexiones; si el pool está frío, una escritura real
                # puede pagar el handshake TCP+auth (~80ms).
                pool = cast(AsyncAdaptedQueuePool, engine.pool)
                conexiones = await asyncio.gather(
                    *(engine.connect() for _ in range(pool.size()))
                )
                await asyncio.gather(*(c.close() for c in conexiones))
                tarea_trazas = asyncio.create_task(
                    trazas.vaciador(engine, parar_trazas)
                )
            except Exception:
                # DB inalcanzable: el servidor igual arranca — /health
                # y el dashboard responden; cada request reintentará.
                logging.getLogger("nous").error(
                    "PostgreSQL no respondió al arrancar; "
                    "los endpoints reintentarán por request"
                )
            # Congela los objetos calientes (modelos ONNX, caches de
            # HF): las colecciones gen2 del GC dejan de recorrerlos y
            # las pausas de GC dejan de producir outliers de ~40ms.
            gc.collect()
            gc.freeze()
            # Umbrales altos: el GC casi nunca dispara dentro de una
            # request; el worker de fondo recolecta en la quietud.
            gc.set_threshold(50_000, 50, 50)
            try:
                yield
            finally:
                parar_trazas.set()
                if tarea_trazas is not None:
                    await tarea_trazas

    from starlette.routing import BaseRoute

    rutas: list[BaseRoute] = [
        Route("/health", _salud),
        Mount("/api", app=api),
        # Las rutas internas del MCP (Mount("/mcp")) se empalmam
        # directas: Mount("/") anterior las convertía en catch-all
        # y bloqueaba el dashboard estático.
        *mcp_app.routes,
    ]
    if (dash := _dir_dashboard_static()) is not None:
        # Último: catch-all solo para paths que /api y /mcp no tomaron.
        rutas.append(Mount("/", app=_dashboard_asgi(dash)))
    app = Starlette(routes=rutas, lifespan=_lifespan)
    return TrazabilidadMiddleware(
        SecurityHeadersMiddleware(BearerTokenMiddleware(app))
    )


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
