"""Benchmark de retrieval: Nous vs gbrain vs memlord sobre datos reales.

22 preguntas construidas desde un muestreo ALEATORIO del contenido
migrado de Nous (workspaces memlord y gbrain), no seleccionadas a
mano. Cada pregunta lista sus "marcadores oro": un acierto (hit) es
que alguno aparezca en el top-k del sistema evaluado.

Regla de corpus honesto: un sistema solo se evalúa en corpus que
realmente contiene — gbrain no tiene mensajes de memlord ni memlord
tiene páginas de gbrain; ambos quedan como ``sin_datos`` en esas
preguntas en vez de contar como fallo. Solo Nous tiene ambos corpus,
por lo que compite contra el dueño original de cada corpus en su
propio terreno.

Corpus:
  memlord  -> elegibles {nous, memlord}
  gbrain   -> elegibles {nous, gbrain}
  cruzado  -> nous necesita marcadores de AMBOS lados; los demás
              puntúan parcial con su lado (solo Nous puede cubrirlo)

Requisitos: Nous en :8720, gbrain en :3131, memlord en :8477.
Tokens de .env / .env.bench (NOUS_API_TOKEN, GBRAIN_TOKEN, MEMLORD_TOKEN).

Uso:
    python -m scripts.benchmark_retrieval [--persist]
"""

import argparse
import asyncio
import contextlib
import gc
import io
import json
import os
import statistics
import sys
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, cast

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

TOP_K = 8

# Cada pregunta: categoria, consulta, corpus y marcadores oro por lado.
# marcadores = gol único; marcadores_a/b = cruzados (dos corpus).
PREGUNTAS: list[dict[str, Any]] = [
    # ---- detalle exacto (corpus memlord) ----
    {"cat": "detalle", "corpus": "memlord", "ws": "memlord",
     "q": "¿Qué fecha de entrega mencionó Samay que ya se había entregado?",
     "marcadores": ["8 de julio", "memory-5767"]},
    {"cat": "detalle", "corpus": "memlord", "ws": "memlord",
     "q": "¿Qué enlace de tiktok pidieron poner en el footer?",
     "marcadores": ["ruba_", "memory-206"]},
    {"cat": "detalle", "corpus": "memlord", "ws": "memlord",
     "q": "¿Qué tamaño de font pidieron para los legales?",
     "marcadores": ["font 10", "memory-4537"]},
    {"cat": "detalle", "corpus": "memlord", "ws": "memlord",
     "q": "¿Qué título sugirió Oscar para el dropdown del PDF?",
     "marcadores": ["elige tu ciudad", "memory-310"]},
    {"cat": "detalle", "corpus": "memlord", "ws": "memlord",
     "q": "¿Qué porcentaje de entrega reportó Oscar ya cumplido?",
     "marcadores": ["109", "memory-6534"]},
    {"cat": "detalle", "corpus": "memlord", "ws": "memlord",
     "q": "¿Cuál es el correo de Alexander?",
     "marcadores": ["isaizander09", "memory-48", "memory-49"]},
    {"cat": "detalle", "corpus": "memlord", "ws": "memlord",
     "q": "¿Qué iban a cambiar por 5 desarrollos con visalto?",
     "marcadores": ["visalto", "memory-6383"]},
    # ---- temático (corpus memlord) ----
    {"cat": "tematico", "corpus": "memlord", "ws": "memlord",
     "q": "migración de landing de angular a react con deadline",
     "marcadores": ["angular", "memory-5231"]},
    {"cat": "tematico", "corpus": "memlord", "ws": "memlord",
     "q": "guardrail para consumo de tokens en audios",
     "marcadores": ["guardrail", "memory-3409"]},
    {"cat": "tematico", "corpus": "memlord", "ws": "memlord",
     "q": "circuito tenerife para crear en zoho",
     "marcadores": ["tenerife", "memory-858"]},
    {"cat": "tematico", "corpus": "memlord", "ws": "memlord",
     "q": "landings pendientes en puebla de automatización de precios",
     "marcadores": ["puebla", "memory-2124"]},
    # ---- entidades (corpus gbrain) ----
    {"cat": "entidad", "corpus": "gbrain", "ws": "gbrain",
     "q": "¿Quién es Alexander en el equipo?",
     "marcadores": ["dev junior", "alexander"]},
    {"cat": "entidad", "corpus": "gbrain", "ws": "gbrain",
     "q": "¿Quién es Samay Reyes y qué rol tiene?",
     "marcadores": ["qa", "coordinadora"]},
    {"cat": "entidad", "corpus": "gbrain", "ws": "gbrain",
     "q": "¿Qué rol tiene Eduardo?",
     "marcadores": ["dev mid", "onboarding", "eduardo"]},
    {"cat": "entidad", "corpus": "gbrain", "ws": "gbrain",
     "q": "¿Qué es el componente InputHelper?",
     "marcadores": ["inputhelper"]},
    {"cat": "entidad", "corpus": "gbrain", "ws": "gbrain",
     "q": "¿Quién es Leonardo Quintana?",
     "marcadores": ["fundador", "dev lead"]},
    {"cat": "tematico", "corpus": "gbrain", "ws": "gbrain",
     "q": "protocolo de inteligencia emocional del equipo",
     "marcadores": ["emotional intelligence", "emocional"]},
    # ---- temporal (corpus memlord) ----
    {"cat": "temporal", "corpus": "memlord", "ws": "memlord",
     "q": "¿Qué se habló entre el 20 y el 26 de julio?",
     "marcadores": ["chihuahua", "kinam", "ale wong", "visalto",
                    "pr validator", "whatsapp intent", "memory-6350",
                    "memory-6383", "memory-6501", "memory-6503",
                    "memory-6642"]},
    {"cat": "temporal", "corpus": "memlord", "ws": "memlord",
     "q": "¿Qué pidió Leo sobre el checklist de MR de gitlab?",
     "marcadores": ["pr validator", "checklist", "memory-6501"]},
    {"cat": "temporal", "corpus": "memlord", "ws": "memlord",
     "q": "¿Cuál fue el primer mensaje de signal?",
     "marcadores": ["memory-15", "banner y footer"]},
    # ---- cruzados (solo Nous puede cubrir ambos lados) ----
    {"cat": "cruzado", "corpus": "cruzado", "ws": None,
     "q": "Leo: su rol en el equipo y qué pidió sobre los crons migrables",
     "marcadores_a": ["fundador", "dev lead"],   # lado gbrain
     "marcadores_b": ["crons", "memory-1721"]},  # lado memlord
    {"cat": "cruzado", "corpus": "cruzado", "ws": None,
     "q": "Samay: su rol en el equipo y qué pedía actualizado para la junta",
     "marcadores_a": ["qa", "coordinadora"],
     "marcadores_b": ["junta", "memory-1791"]},
]

CORPUS_ELEGIBLES: dict[str, set[str]] = {
    "memlord": {"nous", "memlord"},
    "gbrain": {"nous", "gbrain"},
    "cruzado": {"nous", "memlord", "gbrain"},
}


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


def _texto_resultado(resultado: Any) -> str:
    """Concatena el texto de todos los content items de un CallToolResult."""
    partes = []
    for item in getattr(resultado, "content", []) or []:
        texto = getattr(item, "text", None)
        if texto:
            partes.append(texto)
    estructurado = getattr(resultado, "structuredContent", None)
    if estructurado:
        partes.append(json.dumps(estructurado, ensure_ascii=False))
    return "\n".join(partes)


def _hit(texto: str, marcadores: list[str]) -> str | None:
    """Devuelve el primer marcador encontrado (case-insensitive) o None."""
    bajo = texto.lower()
    for m in marcadores:
        if m.lower() in bajo:
            return m
    return None


async def _consulta_nous(
    s: ClientSession, p: dict[str, Any], scoped: bool = True
) -> str:
    """Consulta Nous con su entry point canónico recall.

    ``scoped`` (default): cada corpus se consulta en su workspace —
    el equivalente honesto a "memlord buscando su propia data". Las
    preguntas cruzadas necesitan una llamada por corpus (Nous no
    tiene cross-workspace search por diseño multi-tenant).

    ``scoped=False``: medición del default-scope — lo que un agente
    ve sin saber que el historial migrado vive en otros workspaces.
    """
    if p["corpus"] == "cruzado" and scoped:
        textos = []
        for ws in ("gbrain", "memlord"):
            r = await s.call_tool(
                "recall", {"query": p["q"], "top_n": TOP_K, "workspace": ws}
            )
            textos.append(_texto_resultado(r))
        return "\n".join(textos)
    args: dict[str, Any] = {"query": p["q"], "top_n": TOP_K}
    if scoped and p.get("ws"):
        args["workspace"] = p["ws"]
    r = await s.call_tool("recall", args)
    return _texto_resultado(r)


async def _consulta_memlord(s: ClientSession, p: dict[str, Any]) -> str:
    """Consulta memlord con su tool según el tipo de pregunta.

    retrieve_memory es el search híbrido general; recall_memory es el
    tool que memlord diseñó para expresiones temporales — usarlo en la
    categoría temporal es su respuesta honesta, no trampa.
    """
    if p["cat"] == "temporal":
        r = await s.call_tool(
            "recall_memory", {"query": p["q"], "n_results": TOP_K}
        )
    else:
        r = await s.call_tool(
            "retrieve_memory", {"query": p["q"], "limit": TOP_K}
        )
    return _texto_resultado(r)


async def _consulta_gbrain(s: ClientSession, p: dict[str, Any]) -> str:
    """Consulta gbrain con search (su analogía directa a retrieve/recall)."""
    r = await s.call_tool("search", {"query": p["q"], "limit": TOP_K})
    return _texto_resultado(r)


def _evaluar(p: dict[str, Any], sistema: str, texto: str) -> dict[str, Any]:
    """Puntúa una respuesta: hit/parcial/miss/sin_datos + marcador."""
    if sistema not in CORPUS_ELEGIBLES[p["corpus"]]:
        return {"veredicto": "sin_datos"}
    if p["corpus"] == "cruzado":
        lado_a = _hit(texto, p["marcadores_a"])
        lado_b = _hit(texto, p["marcadores_b"])
        if lado_a and lado_b:
            return {"veredicto": "hit", "m": f"{lado_a}+{lado_b}"}
        parcial = lado_a or lado_b
        return {"veredicto": "parcial", "m": parcial} if parcial else {
            "veredicto": "miss"}
    m = _hit(texto, p["marcadores"])
    return {"veredicto": "hit", "m": m} if m else {"veredicto": "miss"}


async def main() -> None:
    """Ejecuta la comparativa de retrieval e imprime el reporte."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--persist", action="store_true",
        help="persiste la corrida en la tabla benchmarks",
    )
    args = parser.parse_args()

    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )
    gc.collect()
    gc.freeze()

    token = _leer_var("NOUS_API_TOKEN")
    token_gbrain = _leer_var("GBRAIN_TOKEN")
    token_memlord = _leer_var("MEMLORD_TOKEN")

    ConsultaFn = Callable[
        [ClientSession, dict[str, Any]], Coroutine[Any, Any, str]
    ]
    sistemas: dict[str, tuple[str, str, ConsultaFn]] = {
        "nous": ("http://127.0.0.1:8720/mcp/", token,
                 lambda s, p: _consulta_nous(s, p, scoped=True)),
        "gbrain": ("http://127.0.0.1:3131/mcp", token_gbrain, _consulta_gbrain),
        "memlord": ("http://127.0.0.1:8477/mcp", token_memlord, _consulta_memlord),
    }

    # resultados[pregunta_idx][sistema] = evaluación; latencias[sistema]
    resultados: list[dict[str, dict[str, Any]]] = []
    latencias: dict[str, list[float]] = {k: [] for k in sistemas}
    default_scope: dict[int, str] = {}  # veredicto nous sin workspace
    errores: dict[str, str] = {}

    for sistema, (url, tok, fn) in sistemas.items():
        try:
            async with _sesion(url, tok) as s:
                await fn(s, {"q": "warmup", "cat": "detalle",
                             "corpus": "memlord", "ws": None})
        except Exception as exc:  # noqa: BLE001
            errores[sistema] = f"{type(exc).__name__}: {exc}"
        else:
            continue

    for i, p in enumerate(PREGUNTAS):
        fila: dict[str, dict[str, Any]] = {}
        for sistema, (url, tok, fn) in sistemas.items():
            if sistema in errores:
                fila[sistema] = {"veredicto": "error"}
                continue
            try:
                async with _sesion(url, tok) as s:
                    inicio = time.perf_counter()
                    texto = await fn(s, p)
                    ms = (time.perf_counter() - inicio) * 1000
                latencias[sistema].append(ms)
                fila[sistema] = _evaluar(p, sistema, texto)
            except Exception as exc:  # noqa: BLE001
                fila[sistema] = {"veredicto": "error",
                                 "err": f"{type(exc).__name__}"}
        # Hallazgo aparte: qué ve el workspace por defecto (personal)
        # — documenta la brecha de visibilidad de los corpus migrados.
        if "nous" not in errores:
            try:
                async with _sesion(sistemas["nous"][0],
                                   sistemas["nous"][1]) as s:
                    texto_d = await _consulta_nous(s, p, scoped=False)
                default_scope[i] = _evaluar(p, "nous", texto_d)["veredicto"]
            except Exception:  # noqa: BLE001,S110 — hallazgo secundario
                pass
        resultados.append(fila)

    # ---------- marcador ----------
    print("\n🧠 Nous vs gbrain vs memlord — retrieval sobre datos reales\n")
    filas_detalle = []
    victorias = {"nous": 0, "gbrain": 0, "memlord": 0, "empate": 0}
    for i, p in enumerate(PREGUNTAS):
        fila = resultados[i]
        elegibles = [s for s in sistemas
                     if fila[s]["veredicto"] not in ("sin_datos", "error")]
        hits = [s for s in elegibles if fila[s]["veredicto"] in ("hit", "parcial")]
        completos = [s for s in elegibles if fila[s]["veredicto"] == "hit"]
        if len(completos) == len(elegibles) and elegibles:
            ganador = "empate"
            victorias["empate"] += 1
        elif completos:
            ganador = "+".join(completos)
            for sis in completos:
                victorias[sis] += 1
        elif hits:
            ganador = "parcial:" + "+".join(hits)
        else:
            ganador = "nadie"
        detalle = [f"{s}:{fila[s]['veredicto']}"
                 + (f"({fila[s]['m']})" if fila[s].get("m") else "")
                 for s in sistemas]
        filas_detalle.append([str(i + 1), p["cat"], ganador,
                              " ".join(detalle)])

    print(_tabla(["#", "cat", "ganador", "nous / gbrain / memlord"],
                 filas_detalle))

    print(f"\n📊 Marcador — nous {victorias['nous']} · "
          f"gbrain {victorias['gbrain']} · memlord {victorias['memlord']} · "
          f"empates {victorias['empate']}")

    # por categoría
    for cat in ("detalle", "entidad", "tematico", "temporal", "cruzado"):
        idxs = [i for i, p in enumerate(PREGUNTAS) if p["cat"] == cat]
        if not idxs:
            continue
        linea = []
        for sis in sistemas:
            pts = sum(1 for i in idxs
                      if resultados[i][sis]["veredicto"] == "hit")
            par = sum(1 for i in idxs
                      if resultados[i][sis]["veredicto"] == "parcial")
            sd = sum(1 for i in idxs
                     if resultados[i][sis]["veredicto"] == "sin_datos")
            linea.append(f"{sis} {pts}hit/{par}par/{sd}sd")
        print(f"  {cat:9s} {' · '.join(linea)}")

    if default_scope:
        ds_hits = sum(1 for v in default_scope.values() if v == "hit")
        print(f"\n🔎 Default-scope (workspace personal sin override): "
              f"nous acierta {ds_hits}/{len(default_scope)} — los corpus "
              f"migrados no son visibles sin workspace= (aislamiento "
              f"multi-tenant por diseño)")

    print("\n⚡ Latencia por llamada (todos los sistemas, mismas preguntas)")
    filas_lat: list[list[str]] = []
    for sis in sistemas:
        vals = latencias[sis]
        if not vals:
            filas_lat.append([sis, "ERROR", "-", "-"])
            continue
        filas_lat.append([
            sis,
            f"{_percentil(vals, 0.5):.0f}ms",
            f"{_percentil(vals, 0.95):.0f}ms",
            f"{statistics.mean(vals):.0f}ms",
        ])
    print(_tabla(["Sistema", "p50", "p95", "promedio"], filas_lat))

    for sis, err in errores.items():
        print(f"⚠️  {sis}: {err}")

    metricas = {
        "consultas": len(PREGUNTAS),
        "top_k": TOP_K,
        "marcador": victorias,
        "detalle": [
            {"q": p["q"], "cat": p["cat"], "corpus": p["corpus"],
             "resultados": resultados[i]}
            for i, p in enumerate(PREGUNTAS)
        ],
        "latencia_ms": {
            s: {"p50": round(_percentil(v, 0.5)),
                "p95": round(_percentil(v, 0.95)),
                "media": round(statistics.mean(v))}
            for s, v in latencias.items() if v
        },
        "default_scope_nous": {
            "hits": sum(1 for v in default_scope.values() if v == "hit"),
            "total": len(default_scope),
            "nota": "visibilidad multi-tenant: los corpus migrados "
                    "exigen workspace= explícito",
        },
    }

    if args.persist:
        from src.storage.database import crear_engine, get_session
        from src.storage.models import BenchmarkRunModel
        from src.storage.repositories import BenchmarkRepository

        engine = crear_engine()
        try:
            async with get_session(engine) as session:
                await BenchmarkRepository(session).registrar(
                    BenchmarkRunModel(
                        nombre="retrieval_headtohead",
                        escenario="Nous vs gbrain vs memlord — retrieval "
                                  "sobre datos reales migrados, 22 consultas "
                                  "aleatorias",
                        parametros={"sistemas": list(sistemas),
                                    "top_k": TOP_K,
                                    "consultas": len(PREGUNTAS)},
                        metricas=metricas,
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()
        print("\n💾 Corrida persistida en benchmarks")


if __name__ == "__main__":
    asyncio.run(main())
