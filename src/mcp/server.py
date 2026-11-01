"""Servidor MCP de Nous (FastMCP).

Soporta transporte stdio y streamable-http según configuración.
"""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from src.config import get_settings

_settings = get_settings()


def _lista_csv(valor: str) -> list[str]:
    """Divide una lista separada por comas descartando entradas vacías."""
    return [item.strip() for item in valor.split(",") if item.strip()]


# FastMCP habilita protección anti-DNS-rebinding con allowlist solo
# localhost. Conservamos esos hosts y añadimos los del despliegue
# (IP tailnet, MagicDNS, subdominio propio) vía NOUS_MCP_ALLOWED_*.
_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        *_lista_csv(_settings.mcp_allowed_hosts),
    ],
    allowed_origins=[
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        "https://127.0.0.1:*",
        "https://localhost:*",
        "https://[::1]:*",
        *_lista_csv(_settings.mcp_allowed_origins),
    ],
)

mcp_server = FastMCP(
    "nous",
    # Respuestas JSON directas en vez de frames SSE: menos trabajo de
    # serialización por llamada en el transporte streamable-http.
    json_response=True,
    transport_security=_transport_security,
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
