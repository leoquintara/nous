"""Benchmark comparativo: Nous vs gbrain vs memlord con datos reales.

Conecta clientes MCP reales a los tres servidores HTTP y mide:
  A. Latencia de escritura (5 hechos por sistema)
  B. Latencia de consulta (8 queries × 5 iteraciones)
  C. Capacidades exclusivas (proyección, línea temporal, clasificación 4D)

Requisitos: gbrain en :3131, memlord en :8477, Nous HTTP se levanta solo
en :8790. NOUS_API_TOKEN se lee de .env si no está en el entorno.

Uso:
    poetry run python -m scripts.benchmark_vs
"""

import asyncio
import contextlib
import io
import os
import statistics
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from typing import Any, cast

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

HECHOS_PRUEBA = [
    "Nous usa PostgreSQL con pgvector en el puerto 5433 para embeddings",
    "el clasificador de dimensiones mezcla señal semántica y léxica",
    "la búsqueda híbrida fusiona BM25 y vectores con RRF",
    "memlord y gbrain son sistemas de memoria MCP alternativos",
    "el dashboard de Nous usará Next.js con estilo liquid glass",
]

CONSULTAS = [
    "PostgreSQL embeddings",
    "clasificación semántica",
    "búsqueda híbrida",
    "memoria MCP",
    "dashboard",
    "dimensiones",
    "proyección temporal",
    "tokens de autenticación",
]

ITERACIONES = 5


def _percentil(valores: list[float], p: float) -> float:
    """Calcula el percentil p de una lista de latencias en ms."""
    ordenado = sorted(valores)
    idx = max(0, min(len(ordenado) - 1, round(len(ordenado) * p) - 1))
    return ordenado[idx]


def _tabla(cabeceras: list[str], filas: list[list[str]]) -> str:
    """Renderiza una tabla ASCII con bordes correctos."""
    anchos = [len(c) for c in cabeceras]
    for f in filas:
        anchos = [max(a, len(c)) for a, c in zip(anchos, f, strict=True)]

    def bordes(izq: str, medio: str, der: str) -> str:
        return izq + medio.join("─" * (a + 2) for a in anchos) + der

    def fila_txt(celdas: list[str]) -> str:
        return "│ " + " │ ".join(
            c.ljust(a) for c, a in zip(celdas, anchos, strict=True)
        ) + " │"

    lineas = [
        bordes("┌", "┬", "┐"),
        fila_txt(cabeceras),
        bordes("├", "┼", "┤"),
    ]
    lineas += [fila_txt(f) for f in filas]
    lineas.append(bordes("└", "┴", "┘"))
    return "\n".join(lineas)


@contextlib.asynccontextmanager
async def _sesion(
    url: str, token: str | None = None
) -> AsyncIterator[ClientSession]:
    """Abre una sesión MCP streamable-http con auth opcional."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with streamablehttp_client(url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            yield s


async def _medir(coro: Any) -> float:
    """Ejecuta una corutina y devuelve su latencia en ms."""
    inicio = time.perf_counter()
    await coro
    return (time.perf_counter() - inicio) * 1000


async def _benchmark_sistema(
    nombre: str,
    url: str,
    escribir: Any,
    consultar: Any,
    token: str | None = None,
) -> dict[str, Any]:
    """Corre escritura y consulta contra un servidor MCP."""
    res: dict[str, Any] = {"nombre": nombre}
    try:
        async with _sesion(url, token) as s:
            escrituras = []
            for i, hecho in enumerate(HECHOS_PRUEBA):
                escrituras.append(await _medir(escribir(s, hecho, i)))
            res["escritura_ms"] = escrituras

            consultas = []
            for _ in range(ITERACIONES):
                for q in CONSULTAS:
                    consultas.append(await _medir(consultar(s, q)))
            res["consulta_ms"] = consultas
            res["ok"] = True
    except Exception as exc:  # noqa: BLE001 — reportar el fallo, no ocultarlo
        res["ok"] = False
        res["error"] = f"{type(exc).__name__}: {exc}"
    return res


def _arrancar_nous(puerto: int) -> subprocess.Popen[bytes]:
    """Levanta el servidor HTTP de Nous como subproceso."""
    env = {
        **os.environ,
        "NOUS_MCP_TRANSPORT": "http",
        "NOUS_API_PORT": str(puerto),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "src.main"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _esperar_puerto(puerto: int, intentos: int = 60) -> bool:
    """Espera hasta que el puerto HTTP responda (cualquier status)."""
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


def _leer_var(nombre: str, archivos: tuple[str, ...] = (".env", ".env.bench")) -> str:
    """Obtiene una variable del entorno o de archivos .env locales."""
    valor = os.environ.get(nombre, "")
    if valor:
        return valor
    for archivo in archivos:
        with contextlib.suppress(OSError):
            for linea in open(archivo, encoding="utf-8"):  # noqa: SIM115
                if linea.startswith(f"{nombre}="):
                    return linea.split("=", 1)[1].strip()
    return ""


async def main() -> None:
    """Ejecuta la comparativa completa e imprime el reporte."""
    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )
    puerto_nous = 8790
    token = _leer_var("NOUS_API_TOKEN")
    token_gbrain = _leer_var("GBRAIN_TOKEN")
    token_memlord = _leer_var("MEMLORD_TOKEN")

    servidor = _arrancar_nous(puerto_nous)
    if not _esperar_puerto(puerto_nous):
        servidor.terminate()
        raise SystemExit("Nous HTTP no arrancó")

    try:
        resultados = await asyncio.gather(
            _benchmark_sistema(
                "nous",
                f"http://127.0.0.1:{puerto_nous}/mcp/",
                escribir=lambda s, h, i: s.call_tool(
                    "recordar_evento",
                    {
                        "contenido": f"[bench-vs] {h}",
                        "fuente": "benchmark_vs",
                        "dimension": "cognitiva",
                    },
                ),
                consultar=lambda s, q: s.call_tool(
                    "consultar_memoria", {"consulta": q, "top_n": 5}
                ),
                token=token,
            ),
            _benchmark_sistema(
                "gbrain",
                "http://127.0.0.1:3131/mcp",
                escribir=lambda s, h, i: s.call_tool(
                    "remember",
                    {
                        "fact": f"[bench-vs] {h}",
                        "entity": "nous-benchmark",
                        "provenance": "benchmark_vs.py",
                        "request_id": f"bench-vs-{i}-{int(time.time())}",
                    },
                ),
                consultar=lambda s, q: s.call_tool("recall", {"query": q}),
                token=token_gbrain,
            ),
            _benchmark_sistema(
                "memlord",
                "http://127.0.0.1:8477/mcp",
                escribir=lambda s, h, i: s.call_tool(
                    "store_memory",
                    {
                        "content": f"[bench-vs] {h}",
                        "memory_type": "fact",
                        "name": f"bench-vs-{i}-{int(time.time() * 1000)}",
                        "workspace": "nous-benchmark",
                    },
                ),
                consultar=lambda s, q: s.call_tool(
                    "retrieve_memory", {"query": q, "limit": 5}
                ),
                token=token_memlord,
            ),
        )

        # Capacidades exclusivas de Nous: proyección sobre datos reales
        async with _sesion(
            f"http://127.0.0.1:{puerto_nous}/mcp/", token
        ) as s:
            t_proy = await _medir(
                s.call_tool(
                    "proyectar_trayectoria",
                    {"horizonte_dias": 30, "dias_historial": 30},
                )
            )
    finally:
        servidor.terminate()
        servidor.wait(timeout=10)

    print("\n🚀 Nous vs gbrain vs memlord — comparativa real\n")

    filas_escritura = []
    filas_consulta = []
    for r in resultados:
        if not r["ok"]:
            filas_escritura.append([r["nombre"], "ERROR", "-", "-"])
            filas_consulta.append([r["nombre"], f"ERROR: {r['error'][:40]}", "-", "-"])
            continue
        esc = r["escritura_ms"]
        con = r["consulta_ms"]
        filas_escritura.append(
            [
                r["nombre"],
                f"{_percentil(esc, 0.5):.0f}ms",
                f"{_percentil(esc, 0.95):.0f}ms",
                f"{max(esc):.0f}ms",
            ]
        )
        filas_consulta.append(
            [
                r["nombre"],
                f"{_percentil(con, 0.5):.0f}ms",
                f"{_percentil(con, 0.95):.0f}ms",
                f"{statistics.mean(con):.0f}ms",
            ]
        )

    print("📝 A. Escritura (5 hechos reales c/u)")
    print(_tabla(["Sistema", "p50", "p95", "max"], filas_escritura))
    print("\n🔍 B. Consulta (8 queries × 5 iteraciones = 40 medidas c/u)")
    print(_tabla(["Sistema", "p50", "p95", "promedio"], filas_consulta))

    print("\n🧠 C. Capacidades exclusivas")
    print(
        _tabla(
            ["Capacidad", "nous", "gbrain", "memlord"],
            [
                ["Clasificación 4D auto", "✅", "❌", "❌"],
                ["Proyección temporal", f"✅ {t_proy:.0f}ms", "❌", "❌"],
                ["Línea de universo", "✅", "✅ timeline", "❌"],
                ["Dimensiones somáticas", "✅", "❌", "❌"],
                ["Embeddings 100% locales", "✅ MiniLM", "✅ ollama", "✅ ONNX"],
                ["Reranking cross-encoder", "✅", "❌", "❌"],
            ],
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
