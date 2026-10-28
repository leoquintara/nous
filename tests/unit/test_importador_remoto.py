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
async def test_memlord_importa_con_paginacion_y_detalle() -> None:
    """memlord: list_memories paginado (slim) → get_memory por nombre."""
    pedidos: list[dict[str, Any]] = []

    def list_memories(args: dict[str, Any]) -> dict[str, Any]:
        pedidos.append(args)
        if args.get("page") == 1:
            return {
                "items": [
                    {
                        "name": "preferencia-editor",
                        "memory_type": "preference",
                        "workspace": "personal",
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 100,
            }
        return {"items": [], "total": 1, "page": 2, "page_size": 100}

    herramientas = {
        "initialize": {"result": {}},
        "tools/list": {
            "result": {
                "tools": [
                    {"name": "list_memories", "inputSchema": {}},
                    {"name": "get_memory", "inputSchema": {}},
                ]
            }
        },
        "tool:list_memories": list_memories,
        "tool:get_memory": lambda args: {
            "name": args["name"],
            "content": "usa neovim",
            "memory_type": "preference",
            "workspace_name": "personal",
            "tags": ["dev"],
            "created_at": "2026-01-05T10:00:00+00:00",
        },
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
    assert filas[0]["timestamp_unix"] == 1767607200
    # "dev" + nombre del workspace como tag de procedencia.
    assert set(filas[0]["tags"]) >= {"dev", "personal"}


def test_fila_memlord_preserva_remitente_en_contenido_y_tags() -> None:
    """metadata.sender queda consultable: «mensaje DE samay» existe."""
    from src.cli.importador_remoto import _fila_memlord

    fila = _fila_memlord(
        {
            "name": "memory-137",
            "content": "Hola @Samay Reyes buenos dias",
            "memory_type": "fact",
            "workspace": "group-atencion-db",
            "metadata": {
                "sender": "Leonardo Quintana",
                "source": "signal",
            },
            "created_at": "2026-05-19T15:44:00+00:00",
        },
        "memlord",
    )

    assert "Leonardo Quintana: Hola @Samay Reyes" in fila["contenido"]
    assert "sender:leonardo-quintana" in fila["tags"]
    assert "src:signal" in fila["tags"]
    assert "group-atencion-db" in fila["tags"]


def test_fila_memlord_sin_remitente_conserva_contenido_plano() -> None:
    from src.cli.importador_remoto import _fila_memlord

    fila = _fila_memlord(
        {"name": "regla-1", "content": "usa tabs", "memory_type": "fact"},
        "memlord",
    )
    assert fila["contenido"].endswith("usa tabs")
    assert not any(t.startswith("sender:") for t in fila["tags"])


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
