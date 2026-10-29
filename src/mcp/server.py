"""Servidor MCP de Nous (FastMCP).

Soporta transporte stdio y streamable-http según configuración.
"""

from mcp.server.fastmcp import FastMCP

mcp_server = FastMCP(
    "nous",
    # Respuestas JSON directas en vez de frames SSE: menos trabajo de
    # serialización por llamada en el transporte streamable-http.
    json_response=True,
)


@mcp_server.tool()
def ping() -> str:
    """Liveness check of the server.

    Returns:
        The string 'pong'.
    """
    return "pong"


from src.mcp import prompts as _prompts  # noqa: E402,F401  # registra los prompts
from src.mcp import resources as _resources  # noqa: E402,F401  # registra los resources
from src.mcp import tools as _tools  # noqa: E402,F401  # alias en español
from src.mcp import tools_en as _tools_en  # noqa: E402,F401  # tools canónicas EN
