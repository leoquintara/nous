"""Prueba end-to-end del servidor MCP de Nous vía streamable-http.

Arranca el transporte HTTP real (con token Bearer) y ejercita con un
cliente MCP genuino las tools, resources y prompts registrados.

Uso:
    poetry run python -m scripts.e2e_mcp [--puerto N]
"""

import argparse
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, cast

from pydantic import AnyUrl

from mcp.types import CallToolResult, TextContent, TextResourceContents

logger = logging.getLogger("nous.scripts.e2e_mcp")


def _texto(resultado: CallToolResult) -> str:
    """Extrae el payload de texto del primer bloque de contenido."""
    bloque = resultado.content[0]
    if isinstance(bloque, TextContent):
        return bloque.text
    raise TypeError(f"bloque de contenido inesperado: {type(bloque)}")


async def _ejecutar_cliente(puerto: int, token: str) -> dict[str, Any]:
    """Conecta un cliente MCP real y ejercita todo el surface."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    # Sin slash final: /mcp/ redirige (307) y el cliente pierde el POST.
    url = f"http://127.0.0.1:{puerto}/mcp"
    resultados: dict[str, Any] = {}
    async with streamablehttp_client(
        url, headers={"Authorization": f"Bearer {token}"}
    ) as (leer, escribir, _):
        async with ClientSession(leer, escribir) as sesion:
            await sesion.initialize()

            herramientas = await sesion.list_tools()
            resultados["tools"] = sorted(t.name for t in herramientas.tools)
            recursos = await sesion.list_resource_templates()
            uris = sorted(t.uriTemplate for t in recursos.resourceTemplates)
            estaticos = await sesion.list_resources()
            uris += sorted(str(r.uri) for r in estaticos.resources)
            resultados["resources"] = uris
            prompts = await sesion.list_prompts()
            resultados["prompts"] = sorted(p.name for p in prompts.prompts)

            # recordar → consultar → linea → proyectar
            resultado_ev = await sesion.call_tool(
                "recordar_evento",
                {
                    "contenido": "validé el flujo E2E del servidor MCP",
                    "dimension": "conductual",
                    "fuente": "e2e_mcp",
                },
            )
            evento = json.loads(_texto(resultado_ev))
            resultados["recordar_evento_id"] = evento["id"]

            resultado_hits = await sesion.call_tool(
                "consultar_memoria", {"consulta": "flujo E2E", "top_n": 3}
            )
            resultados["consultar_hits"] = len(json.loads(_texto(resultado_hits)))

            ahora = int(time.time())
            resultado_linea = await sesion.call_tool(
                "obtener_linea_de_universo",
                {"desde_unix": ahora - 3600, "hasta_unix": ahora + 60},
            )
            resultados["linea_eventos"] = len(json.loads(_texto(resultado_linea)))

            resultado_proy = await sesion.call_tool(
                "proyectar_trayectoria",
                {"horizonte_dias": 7, "dias_historial": 30},
            )
            proyeccion = json.loads(_texto(resultado_proy))
            resultados["proyeccion"] = proyeccion["dimension_probable"]

            resultado_res = await sesion.read_resource(
                AnyUrl("memoria://resumen/diario")
            )
            resumen = resultado_res.contents[0]
            resultados["resource_resumen"] = json.loads(
                resumen.text if isinstance(resumen, TextResourceContents) else "{}"
            )["total_eventos"]
            resultado_prompt = await sesion.get_prompt(
                "preparar_decision", {"tema": "e2e"}
            )
            resultados["prompt_ok"] = len(resultado_prompt.messages) > 0
    return resultados


def _leer_token_env() -> str:
    """Obtiene NOUS_API_TOKEN del entorno o del .env del proyecto."""
    token = os.environ.get("NOUS_API_TOKEN", "")
    if token:
        return token
    try:
        for linea in open(".env", encoding="utf-8"):  # noqa: SIM115
            if linea.startswith("NOUS_API_TOKEN="):
                return linea.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _esperar_puerto(puerto: int, intentos: int = 200) -> bool:
    """Espera a que el puerto responda (401 = vivo, la auth ya manda).

    El warmup de ONNX en frío tarda ~20s; 200×0.3s ≈ 60s de margen
    sobrado para hosts lentos sin penalizar el caso rápido.
    """
    import urllib.error
    import urllib.request

    for _ in range(intentos):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{puerto}/mcp/", timeout=1)
            return True
        except urllib.error.HTTPError:
            return True
        except OSError:
            time.sleep(0.3)
    return False


def main() -> None:
    """Levanta el servidor HTTP, corre el cliente y reporta."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puerto", type=int, default=8790)
    args = parser.parse_args()
    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )

    token = _leer_token_env()
    if not token:
        raise SystemExit("Define NOUS_API_TOKEN (formato ns_...) en .env")

    env = {**os.environ, "NOUS_MCP_TRANSPORT": "http", "NOUS_API_PORT": str(args.puerto)}
    servidor = subprocess.Popen(
        [sys.executable, "-m", "src.main"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _esperar_puerto(args.puerto):
            raise SystemExit("El servidor no respondió a tiempo")
        resultados = asyncio.run(_ejecutar_cliente(args.puerto, token))
    finally:
        servidor.terminate()
        servidor.wait(timeout=10)

    print("\n✅ E2E MCP completado")
    print(json.dumps(resultados, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
