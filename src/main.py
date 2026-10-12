"""Entry point del servidor MCP de Nous."""

import logging

from src.config import get_settings
from src.mcp.server import mcp_server


def main() -> None:
    """Arranca el servidor MCP con el transporte configurado."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logging.getLogger("nous").info(
        "Arrancando Nous (transporte=%s)", settings.mcp_transport
    )
    mcp_server.run(
        transport="stdio" if settings.mcp_transport == "stdio" else "streamable-http"
    )


if __name__ == "__main__":
    main()
