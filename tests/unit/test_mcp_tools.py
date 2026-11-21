"""Tests de registro y descripciones de las tools MCP."""

from src.mcp.server import mcp_server


async def test_tools_de_dominio_registradas_con_descripcion() -> None:
    """Verifica que las tools de dominio existen y se describen claramente."""
    tools = {t.name: t for t in await mcp_server.list_tools()}

    for nombre in (
        "remember",
        "recall",
        "timeline",
        "project_trajectory",
    ):
        assert nombre in tools, f"tool {nombre} no registrada"
        descripcion = tools[nombre].description or ""
        assert len(descripcion) > 100, f"{nombre} con descripción ambigua"
        assert "USE WHEN" in descripcion


async def test_alias_espanoles_siguen_registrados() -> None:
    """Los alias legados conviven con los nombres canónicos ingleses."""
    tools = {t.name: t for t in await mcp_server.list_tools()}

    for nombre in (
        "recordar_evento",
        "consultar_memoria",
        "obtener_linea_de_universo",
        "proyectar_trayectoria",
    ):
        assert nombre in tools, f"alias {nombre} no registrado"
        assert "Deprecated alias" in (tools[nombre].description or "")


async def test_recordar_evento_expone_enum_de_dimension() -> None:
    """Verifica que el schema restringe dimension a las 3 válidas."""
    tools = {t.name: t for t in await mcp_server.list_tools()}
    schema = tools["recordar_evento"].inputSchema

    dimension = schema["properties"]["dimension"]
    variantes = dimension.get("anyOf", [dimension])
    valores = {
        v for variante in variantes for v in variante.get("enum", [])
    }
    assert valores == {"cognitiva", "somatica", "conductual"}


def test_fuente_efectiva_conserva_fuente_explicita() -> None:
    """Una fuente explícita siempre gana sobre el clientInfo."""
    from src.mcp.tools import _fuente_efectiva

    assert _fuente_efectiva("devin") == "devin"


def test_fuente_efectiva_sin_peticion_mcp_devuelve_default() -> None:
    """Fuera de una petición MCP el default genérico se conserva."""
    from src.mcp.tools import _fuente_efectiva

    assert _fuente_efectiva("agent") == "agent"
    assert _fuente_efectiva("agente") == "agente"


def test_cliente_actual_normaliza_nombre() -> None:
    """clientInfo.name se normaliza a slug de fuente."""
    from src.mcp.tools import _cliente_actual

    class _Info:
        name = "Gemini CLI 1.0"

    class _Params:
        clientInfo = _Info()

    class _Session:
        client_params = _Params()

    class _RC:
        session = _Session()

    from mcp.server.lowlevel.server import request_ctx

    token = request_ctx.set(_RC())  # type: ignore[arg-type]
    try:
        assert _cliente_actual() == "gemini-cli-1-0"
    finally:
        request_ctx.reset(token)


def test_catalogo_skills_lista_artefactos() -> None:
    """El catálogo expone skills y agentes con descripción e instalación."""
    import asyncio

    from src.mcp.tools import _catalogo_skills

    catalogo = asyncio.run(_catalogo_skills())
    nombres = {a["name"] for a in catalogo["artifacts"]}
    assert "nous-remember" in nombres
    assert "nous-keeper" in nombres
    assert {"cli", "remote"} <= set(catalogo["install"])
    assert all(a["kind"] in ("skill", "agent") for a in catalogo["artifacts"])
    assert any(a["description"] for a in catalogo["artifacts"])


def test_catalogo_skills_devuelve_contenido_por_nombre() -> None:
    """Con nombre devuelve el SKILL.md completo y su ruta destino."""
    import asyncio

    from src.mcp.tools import _catalogo_skills

    art = asyncio.run(_catalogo_skills("nous-remember"))
    assert art["kind"] == "skill"
    assert art["path_hint"] == "skills/nous-remember/SKILL.md"
    assert art["content"].startswith("---")

    agente = asyncio.run(_catalogo_skills("nous-keeper"))
    assert agente["kind"] == "agent"
    assert agente["path_hint"] == "agents/nous-keeper.md"


def test_catalogo_skills_nombre_invalido_lista_disponibles() -> None:
    """Nombre inexistente → ErrorValidacion con el listado."""
    import asyncio

    import pytest

    from src.core.exceptions import ErrorValidacion
    from src.mcp.tools import _catalogo_skills

    with pytest.raises(ErrorValidacion, match="nous-remember"):
        asyncio.run(_catalogo_skills("no-existe"))
