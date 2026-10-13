"""Tests de registro y descripciones de las tools MCP."""

from src.mcp.server import mcp_server


async def test_tools_de_dominio_registradas_con_descripcion() -> None:
    """Verifica que las tools de dominio existen y se describen claramente."""
    tools = {t.name: t for t in await mcp_server.list_tools()}

    for nombre in (
        "recordar_evento",
        "consultar_memoria",
        "obtener_linea_de_universo",
    ):
        assert nombre in tools, f"tool {nombre} no registrada"
        descripcion = tools[nombre].description or ""
        assert len(descripcion) > 100, f"{nombre} con descripción ambigua"
        assert "USAR CUANDO" in descripcion
        assert "NO usar" in descripcion


async def test_recordar_evento_expone_enum_de_dimension() -> None:
    """Verifica que el schema restringe dimension a las 3 válidas."""
    tools = {t.name: t for t in await mcp_server.list_tools()}
    schema = tools["recordar_evento"].inputSchema

    dimension = schema["properties"]["dimension"]
    assert set(dimension["enum"]) == {"cognitiva", "somatica", "conductual"}
