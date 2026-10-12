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
