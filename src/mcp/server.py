"""Servidor MCP de Nous (FastMCP).

Soporta transporte stdio y streamable-http según configuración.
"""

from mcp.server.fastmcp import FastMCP

mcp_server = FastMCP("nous")


@mcp_server.tool()
def ping() -> str:
    """Verificación de vida del servidor.

    Returns:
        La cadena 'pong'.
    """
    return "pong"


from src.mcp import prompts as _prompts  # noqa: E402,F401  # registra los prompts
from src.mcp import resources as _resources  # noqa: E402,F401  # registra los resources
from src.mcp import tools as _tools  # noqa: E402,F401  # registra las tools de dominio
