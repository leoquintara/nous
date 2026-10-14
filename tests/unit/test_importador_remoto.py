"""Tests del importador remoto vía MCP streamable-HTTP.

El transporte se inyecta con httpx.MockTransport: no hay red real —
se simula el handshake MCP, tools/list y tools/call por nombre.
"""

import json
from typing import Any

import httpx
import pytest

from src.cli.importador_remoto import (
    ClienteMCP,
    ErrorRemoto,
    leer_fuente_remota,
)


def _result(datos: Any) -> dict[str, Any]:
    """Envuelve un payload en el content[] de un result MCP."""
    return {
        "content": [{"type": "text", "text": json.dumps(datos)}],
    }


def servidor_falso(herramientas: dict[str, Any]) -> httpx.MockTransport:
    """Servidor MCP falso: responde según método/tool invocado."""

    def handler(request: httpx.Request) -> httpx.Response:
        mensaje = json.loads(request.content)
        metodo = mensaje.get("method")
        if metodo == "notifications/initialized":
            return httpx.Response(202)
        resultado = herramientas.get(metodo)
        if metodo == "tools/call":
            nombre = mensaje["params"]["name"]
            args = mensaje["params"].get("arguments", {})
            fn = herramientas.get(f"tool:{nombre}")
            if fn is None:
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": mensaje.get("id"),
                        "result": {
                            "isError": True,
                            "content": [
                                {"type": "text", "text": "tool desconocida"}
                            ],
                        },
                    },
                )
            valor = fn(args) if callable(fn) else fn
            resultado = {"result": _result(valor)}
        elif resultado is None:
            resultado = {"result": {}}
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": mensaje.get("id"), **resultado},
            headers={"mcp-session-id": "sesion-test"},
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_gbrain_importa_paginas_paginadas() -> None:
    """gbrain: list_pages (paginado) + get_page por slug → filas."""
    llamadas: list[dict[str, Any]] = []

    def list_pages(args: dict[str, Any]) -> list[dict[str, Any]]:
        llamadas.append(args)
        if args.get("offset", 0) == 0:
            return [{"slug": "proyecto-a"}, {"slug": "proyecto-b"}]
        return []

    herramientas = {
        "initialize": {"result": {"protocolVersion": "2025-03-26"}},
        "tool:list_pages": list_pages,
        "tool:get_page": lambda args: {
            "slug": args["slug"],
            "content": f"contenido de {args['slug']}",
            "updated_at": "2026-01-10T12:00:00Z",
        },
        "tool:recall": {"facts": []},
    }
    transporte = servidor_falso(herramientas)

    filas, _, errores = await leer_fuente_remota(
        "http://gbrain.test/mcp", "tok", "gbrain", "gbrain", transporte
    )

    assert errores == 0
    assert {f["contenido"].split("\n")[0] for f in filas} == {
        "proyecto-a",
        "proyecto-b",
    }
    # Ids deterministas por slug: reimportar no duplica.
    assert len({f["id"] for f in filas}) == 2
    assert all(f["timestamp_unix"] > 0 for f in filas)


@pytest.mark.asyncio
async def test_gbrain_importa_facts_globales() -> None:
    """recall sin entidad aporta los facts como filas propias."""
    herramientas = {
        "initialize": {"result": {}},
        "tool:list_pages": [],
        "tool:recall": {
            "facts": [
                {"fact_id": "f1", "fact": "a Leo le gusta el café"},
                {"fact": "sin id usa texto", "created_at": 1770000000},
            ]
        },
    }

    filas, _, _ = await leer_fuente_remota(
        "http://gbrain.test/mcp",
        "tok",
        "gbrain",
        "gbrain",
        servidor_falso(herramientas),
    )

    assert len(filas) == 2
    assert filas[0]["contenido"] == "a Leo le gusta el café"
    assert filas[1]["timestamp_unix"] == 1770000000


@pytest.mark.asyncio
async def test_memlord_importa_por_workspace() -> None:
    """memlord: list_workspaces → tool de listado con workspace como arg."""
    pedidos: list[dict[str, Any]] = []

    def retrieve(args: dict[str, Any]) -> dict[str, Any]:
        pedidos.append(args)
        if args.get("workspace") == "personal":
            return {
                "memories": [
                    {
                        "name": "preferencia-editor",
                        "content": "usa neovim",
                        "memory_type": "preference",
                        "tags": ["dev"],
                    }
                ]
            }
        return {"memories": []}

    herramientas = {
        "initialize": {"result": {}},
        "tools/list": {
            "result": {
                "tools": [
                    {
                        "name": "retrieve_memory",
                        "inputSchema": {
                            "properties": {
                                "workspace": {"type": "string"},
                                "limit": {"type": "number"},
                                "query": {"type": "string"},
                            }
                        },
                    },
                    {"name": "list_workspaces", "inputSchema": {}},
                ]
            }
        },
        "tool:list_workspaces": {
            "workspaces": [{"name": "personal"}, {"name": "trabajo"}]
        },
        "tool:retrieve_memory": retrieve,
    }

    filas, _, errores = await leer_fuente_remota(
        "http://memlord.test/mcp",
        "tok",
        "memlord",
        "memlord",
        servidor_falso(herramientas),
    )

    assert errores == 0
    assert len(filas) == 1
    assert "usa neovim" in filas[0]["contenido"]
    # Se pidió una vez por workspace, con limit/query rellenos del schema.
    assert {p.get("workspace") for p in pedidos} == {"personal", "trabajo"}
    assert all(p.get("limit") == 2000 for p in pedidos)


@pytest.mark.asyncio
async def test_memlord_sin_tool_de_listado_falla_claro() -> None:
    herramientas = {
        "initialize": {"result": {}},
        "tools/list": {
            "result": {"tools": [{"name": "store_memory", "inputSchema": {}}]}
        },
    }
    with pytest.raises(ErrorRemoto, match="listado"):
        await leer_fuente_remota(
            "http://memlord.test/mcp",
            "tok",
            "memlord",
            "memlord",
            servidor_falso(herramientas),
        )


@pytest.mark.asyncio
async def test_respuesta_sse_se_parsea() -> None:
    """Un servidor que responde event-stream también funciona."""

    def handler(request: httpx.Request) -> httpx.Response:
        mensaje = json.loads(request.content)
        if mensaje.get("method") == "notifications/initialized":
            return httpx.Response(202)
        cuerpo = {
            "jsonrpc": "2.0",
            "id": mensaje.get("id"),
            "result": {"ok": True},
        }
        return httpx.Response(
            200,
            content=f"event: message\ndata: {json.dumps(cuerpo)}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    async with ClienteMCP(
        "http://mcp.test/mcp", transporte=httpx.MockTransport(handler)
    ) as cli:
        datos = await cli.llamar_metodo("ping", {})
    assert datos == {"ok": True}


@pytest.mark.asyncio
async def test_error_http_se_traduce() -> None:
    # 401 en initialize → ErrorRemoto, no excepción httpx cruda.
    transporte = httpx.MockTransport(
        lambda req: httpx.Response(401, text="no autorizado")
    )
    with pytest.raises(ErrorRemoto, match="401"):
        async with ClienteMCP("http://x.test/mcp", transporte=transporte):
            pass
