"""Entry point del servidor MCP de Nous."""

import logging

import uvicorn

from src.api.auth import BearerTokenMiddleware
from src.config import get_settings
from src.mcp.server import mcp_server


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
    app = BearerTokenMiddleware(mcp_server.streamable_http_app())
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
