"""Suite de benchmarks de Nous (objetivo: < 200ms p95 en búsqueda).

Mide cuatro frentes y persiste cada sección en la tabla
``benchmarks`` para comparación histórica en el dashboard:

A. Latencia de búsqueda híbrida (cold/warm, con/sin reranker).
B. Throughput y precisión del clasificador (con/sin léxico).
C. Latencia de proyección temporal (con/sin boost de racha).
D. Uso de memoria (tracemalloc) en búsqueda sobre corpus grande.

Uso:
    poetry run python -m scripts.benchmark_search [--iteraciones N]
"""

import argparse
import asyncio
import logging
import subprocess
import sys
import time
import tracemalloc

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.busqueda import BusquedaHibrida4D
from src.core.clasificador import ClasificadorDimensiones
from src.core.embeddings import GeneradorEmbeddings
from src.core.modelos import Dimension, Evento
from src.core.reranker import ReordenadorLocal
from src.core.temporal import SEGUNDOS_POR_DIA, proyectar
from src.storage.database import crear_engine, get_session
from src.storage.models import BenchmarkRunModel, EventoModel
from src.storage.repositories import BenchmarkRepository

logger = logging.getLogger("nous.scripts.benchmark_search")

FRASES_SEMILLA = [
    "reflexioné sobre cómo funciona la memoria en humanos y máquinas",
    "decidí migrar la base de datos a PostgreSQL con pgvector",
    "me desperté con mucha energía y claridad mental",
    "quiero dejar de revisar el teléfono por las mañanas",
    "idea: usar RRF ponderado para la búsqueda híbrida",
    "hice ejercicio cuarenta minutos, sensación de bienestar",
    "revisé el pipeline de retrieval de gbrain en detalle",
    "ansiedad leve antes de la presentación del proyecto",
    "implementé el reranker local con cross-encoder",
    "conversación larga sobre consciencia y cuarta dimensión",
    "caminata nocturna resolviendo un bug mentalmente",
    "medité veinte minutos, mente más estable después",
    "diseñé el esquema de benchmarks para el dashboard",
    "frustración con la configuración de WSL y los puertos",
    "avancé en las tools MCP de recordar y consultar",
]

FRASES_CLASIFICACION: dict[Dimension, tuple[str, ...]] = {
    Dimension.COGNITIVA: (
        "pensé en una arquitectura nueva",
        "decidí refactorizar el módulo",
        "reflexioné sobre el diseño",
        "aprendí un patrón nuevo",
        "analicé el problema con calma",
        "planeé la estrategia del sprint",
        "me hice una pregunta difícil",
        "llegué a una conclusión clara",
        "dudé sobre el enfoque correcto",
        "formulé una hipótesis técnica",
    ),
    Dimension.SOMATICA: (
        "tengo dolor de espalda",
        "me siento con mucha energía",
        "tengo ansiedad leve",
        "estoy cansado y con sueño",
        "me sentí relajado y tranquilo",
        "tengo malestar de estómago",
        "estaba tenso en los hombros",
        "sentí miedo e inquietud",
        "estoy feliz y en paz",
        "me dolió la cabeza toda la tarde",
    ),
    Dimension.CONDUCTUAL: (
        "fui al gimnasio a entrenar",
        "hice commit de los cambios",
        "cociné la cena familiar",
        "caminé hasta el parque",
        "tuve una reunión de trabajo",
        "instalé el programa nuevo",
        "escribí el documento del proyecto",
        "llamé a un amigo por teléfono",
        "limpié y organicé mi escritorio",
        "presenté los resultados al equipo",
    ),
}

OBJETIVO_P95_MS = 200


def _percentil(latencias: list[float], p: float) -> float:
    """Calcula el percentil p (0-100) de una lista de latencias en ms."""
    if not latencias:
        return 0.0
    ordenadas = sorted(latencias)
    indice = min(len(ordenadas) - 1, int(len(ordenadas) * p / 100))
    return round(ordenadas[indice], 2)


def _git_commit() -> str | None:
    """Retorna el hash corto del commit actual, o None si falla."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _imprimir_tabla(titulo: str, cabeceras: list[str], filas: list[list[str]]) -> None:
    """Imprime una tabla ASCII alineada con cabeceras y filas dadas."""
    anchos = [
        max(len(c), *(len(f[i]) for f in filas)) for i, c in enumerate(cabeceras)
    ]
    borde = "─"
    print(f"\n{titulo}")
    print("┌" + "┬".join(borde * (a + 2) for a in anchos) + "┐")
    print(
        "│ "
        + " │ ".join(
            c.ljust(a) for c, a in zip(cabeceras, anchos, strict=False)
        )
        + " │"
    )
    print("├" + "┼".join(borde * (a + 2) for a in anchos) + "┤")
    for fila in filas:
        print(
            "│ "
            + " │ ".join(
                v.ljust(a) for v, a in zip(fila, anchos, strict=False)
            )
            + " │"
        )
    print("└" + "┴".join(borde * (a + 2) for a in anchos) + "┘")


async def _sembrar(session: AsyncSession, n: int) -> int:
    """Siembra eventos sintéticos con embeddings reales si faltan datos."""
    total = (
        await session.execute(select(func.count()).select_from(EventoModel))
    ).scalar_one()
    if total >= n:
        return 0
    faltantes = n - int(total)
    embeddings = GeneradorEmbeddings()
    ahora = int(time.time())
    lote = 200
    sembrados = 0
    while sembrados < faltantes:
        fraccion = min(lote, faltantes - sembrados)
        textos = [
            f"{FRASES_SEMILLA[(sembrados + j) % len(FRASES_SEMILLA)]}"
            f" #{sembrados + j}"
            for j in range(fraccion)
        ]
        vectores = await embeddings.generar_lote(textos)
        for j, (texto, vector) in enumerate(zip(textos, vectores, strict=True)):
            indice = sembrados + j
            session.add(
                EventoModel(
                    timestamp_unix=ahora - (indice * 3600),
                    dimension=("cognitiva", "somatica", "conductual")[indice % 3],
                    fuente="benchmark_seed",
                    contenido=texto,
                    embedding=vector,
                    amplitud=0.3 + 0.6 * ((indice * 37) % 10) / 10,
                )
            )
        await session.flush()
        sembrados += fraccion
        logger.info("Sembrados %d/%d eventos", sembrados, faltantes)
    return sembrados


async def _latencias_busqueda(
    busqueda: BusquedaHibrida4D,
    session: AsyncSession,
    iteraciones: int,
) -> list[float]:
    """Ejecuta iteraciones de búsqueda y retorna latencias en ms."""
    latencias: list[float] = []
    queries = FRASES_SEMILLA[:10]
    for i in range(iteraciones):
        inicio = time.perf_counter()
        await busqueda.buscar(session, queries[i % len(queries)], top_n=10)
        latencias.append((time.perf_counter() - inicio) * 1000)
    return latencias


async def _benchmark_busqueda(
    session: AsyncSession,
    embeddings: GeneradorEmbeddings,
    iteraciones: int,
) -> dict[str, list[float]]:
    """Sección A: cold/warm cache y con/sin reranking."""
    con_rerank = BusquedaHibrida4D(embeddings, reordenador=ReordenadorLocal())
    frio = await _latencias_busqueda(con_rerank, session, 1)
    caliente = await _latencias_busqueda(con_rerank, session, iteraciones)
    sin_rerank = BusquedaHibrida4D(embeddings)
    await _latencias_busqueda(sin_rerank, session, 1)
    lat_sin = await _latencias_busqueda(sin_rerank, session, iteraciones)
    return {
        "Cold cache": frio,
        "Warm cache (rerank)": caliente,
        "Warm cache (sin rerank)": lat_sin,
    }


def _fila_metricas(latencias: list[float]) -> list[str]:
    """Formatea p50/p95/p99/max de una serie de latencias."""
    return [
        f"{_percentil(latencias, 50):.0f}ms",
        f"{_percentil(latencias, 95):.0f}ms",
        f"{_percentil(latencias, 99):.0f}ms",
        f"{round(max(latencias))}ms",
    ]


async def _benchmark_clasificacion(
    embeddings: GeneradorEmbeddings, total: int
) -> dict[str, dict[str, float]]:
    """Sección B: throughput y precisión del clasificador."""
    frases = [
        (texto, dimension)
        for i in range(total)
        for dimension, textos in [list(FRASES_CLASIFICACION.items())[i % 3]]
        for texto in [textos[(i // 3) % len(textos)]]
    ]
    resultados: dict[str, dict[str, float]] = {}
    for nombre, usar_lexico in (("Semántico + léxico", True), ("Semántico puro", False)):
        clasificador = ClasificadorDimensiones(embeddings, usar_lexico)
        inicio = time.perf_counter()
        aciertos = 0
        for texto, esperada in frases:
            if (await clasificador.clasificar(texto)).dimension == esperada:
                aciertos += 1
        duracion = time.perf_counter() - inicio
        resultados[nombre] = {
            "frases_por_segundo": round(len(frases) / duracion, 1),
            "precision": round(aciertos / len(frases), 3),
        }
    return resultados


def _benchmark_proyeccion(iteraciones: int) -> dict[str, float]:
    """Sección C: latencia de proyección con/sin boost de racha."""
    ahora = int(time.time())
    eventos = [
        Evento(
            id=str(i),
            timestamp_unix=ahora - (30 * SEGUNDOS_POR_DIA) + i * 600,
            dimension=(Dimension.COGNITIVA, Dimension.SOMATICA, Dimension.CONDUCTUAL)[i % 3],
            contenido="",
        )
        for i in range(210)
    ]
    resultados: dict[str, float] = {}
    for nombre, boost in (("Con BOOST_RACHA", 1.4), ("Sin BOOST_RACHA", 1.0)):
        inicio = time.perf_counter()
        for _ in range(iteraciones):
            proyectar(eventos, horizonte_dias=90, ahora=ahora, boost_racha=boost)
        resultados[nombre] = round(
            (time.perf_counter() - inicio) / iteraciones * 1000, 2
        )
    return resultados


async def _benchmark_memoria(
    session: AsyncSession, embeddings: GeneradorEmbeddings
) -> dict[str, float]:
    """Sección D: pico de memoria (tracemalloc) durante la búsqueda."""
    tracemalloc.start()
    await BusquedaHibrida4D(embeddings).buscar(
        session, "reflexión sobre la memoria", top_n=10
    )
    _, pico_busqueda = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    tracemalloc.start()
    clasificador = ClasificadorDimensiones(embeddings)
    await clasificador.clasificar("frase de prueba de memoria")
    _, pico_clasif = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "Búsqueda": round(pico_busqueda / 1_048_576, 1),
        "Clasificación": round(pico_clasif / 1_048_576, 1),
    }


async def _persistir(
    nombre: str,
    escenario: str,
    parametros: dict[str, object],
    metricas: dict[str, object],
) -> None:
    """Persiste una corrida de benchmark en la tabla benchmarks."""
    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            await BenchmarkRepository(session).registrar(
                BenchmarkRunModel(
                    nombre=nombre,
                    escenario=escenario,
                    parametros=parametros,
                    metricas=metricas,
                    git_commit=_git_commit(),
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def main() -> None:
    """Ejecuta la suite completa y persiste las métricas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteraciones", type=int, default=10)
    parser.add_argument("--sembrar", type=int, default=200)
    parser.add_argument("--frases-clasificacion", type=int, default=1000)
    args = parser.parse_args()

    # La consola de Windows (cp1252) no soporta emojis ni box-drawing.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("🚀 Nous Benchmark Suite v1.0")
    print("═" * 52)
    embeddings = GeneradorEmbeddings()
    engine = crear_engine()

    async with get_session(engine) as session:
        sembrados = await _sembrar(session, args.sembrar)
        await session.commit()
        logger.info("Eventos sembrados: %d", sembrados)
        resultados_a = await _benchmark_busqueda(
            session, embeddings, args.iteraciones
        )
    await engine.dispose()

    _imprimir_tabla(
        f"📊 A. Búsqueda Híbrida (10 queries × {args.iteraciones} iteraciones)",
        ["Condición", "p50", "p95", "p99", "max"],
        [[cond] + _fila_metricas(lat) for cond, lat in resultados_a.items()],
    )

    resultados_b = await _benchmark_clasificacion(
        embeddings, args.frases_clasificacion
    )
    _imprimir_tabla(
        f"📊 B. Clasificación ({args.frases_clasificacion} frases)",
        ["Método", "frases/s", "precisión"],
        [
            [m, f"{r['frases_por_segundo']:.0f}", f"{r['precision'] * 100:.0f}%"]
            for m, r in resultados_b.items()
        ],
    )

    resultados_c = _benchmark_proyeccion(args.iteraciones)
    _imprimir_tabla(
        "📊 C. Proyección (30 días → 90 días)",
        ["Condición", "tiempo"],
        [[c, f"{t}ms"] for c, t in resultados_c.items()],
    )

    engine = crear_engine()
    async with get_session(engine) as session:
        sembrados_mem = await _sembrar(session, 10_000)
        await session.commit()
        if sembrados_mem:
            logger.info("Corpus de memoria: %d eventos", 10_000)
        resultados_d = await _benchmark_memoria(session, embeddings)
    await engine.dispose()
    _imprimir_tabla(
        "📊 D. Memoria (10k eventos)",
        ["Operación", "MB"],
        [[op, f"{mb}MB"] for op, mb in resultados_d.items()],
    )

    p95_warm = _percentil(resultados_a["Warm cache (rerank)"], 95)
    if p95_warm < OBJETIVO_P95_MS:
        print(f"\n✅ Target cumplido: p95 {p95_warm:.0f}ms < {OBJETIVO_P95_MS}ms (warm)")
    else:
        print(f"\n⚠️  Target incumplido: p95 {p95_warm:.0f}ms > {OBJETIVO_P95_MS}ms")

    await _persistir(
        "suite_benchmarks",
        f"{args.iteraciones} iteraciones",
        vars(args),
        {
            "busqueda": {k: _fila_metricas(v) for k, v in resultados_a.items()},
            "clasificacion": resultados_b,
            "proyeccion": resultados_c,
            "memoria_mb": resultados_d,
            "pasa_objetivo": p95_warm < OBJETIVO_P95_MS,
        },
    )
    logger.info("Corrida persistida en benchmarks")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
