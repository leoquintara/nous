"""Benchmark de la búsqueda híbrida 4D (objetivo: < 200ms p95).

Mide latencias de búsqueda sobre la DB real y persiste cada corrida
en la tabla ``benchmarks`` para comparación histórica en el dashboard.

Uso:
    poetry run python -m scripts.benchmark_search [--consultas N] [--sembrar N]
"""

import argparse
import asyncio
import logging
import statistics
import subprocess
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.busqueda import BusquedaHibrida4D
from src.core.embeddings import GeneradorEmbeddings
from src.storage.database import crear_engine, get_session
from src.storage.models import BenchmarkRunModel, EventoModel
from src.storage.repositories import BenchmarkRepository

logger = logging.getLogger("nous.scripts.benchmark_search")

CONSULTAS_DEFAULT = [
    "reflexión sobre la consciencia",
    "decisión técnica de la semana",
    "estado de ánimo al despertar",
    "hábito que quiero cambiar",
    "idea para el proyecto",
]

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
    sembrados = 0
    for i in range(faltantes):
        texto = FRASES_SEMILLA[i % len(FRASES_SEMILLA)]
        vector = await embeddings.generar(texto)
        session.add(
            EventoModel(
                timestamp_unix=ahora - (i * 3600),
                dimension=("cognitiva", "somatica", "conductual")[i % 3],
                fuente="benchmark_seed",
                contenido=f"{texto} #{i}",
                embedding=vector,
                amplitud=0.3 + 0.6 * ((i * 37) % 10) / 10,
            )
        )
        sembrados += 1
    await session.flush()
    return sembrados


async def main() -> None:
    """Ejecuta el benchmark y persiste la corrida en la DB."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consultas", type=int, default=30)
    parser.add_argument("--sembrar", type=int, default=200)
    args = parser.parse_args()

    engine = crear_engine()
    embeddings = GeneradorEmbeddings()
    busqueda = BusquedaHibrida4D(embeddings)

    async with get_session(engine) as session:
        sembrados = await _sembrar(session, args.sembrar)
        await session.commit()
        logger.info("Eventos sembrados: %d", sembrados)

    latencias: list[float] = []
    async with get_session(engine) as session:
        for i in range(args.consultas):
            query = CONSULTAS_DEFAULT[i % len(CONSULTAS_DEFAULT)]
            inicio = time.perf_counter()
            resultados = await busqueda.buscar(session, query, top_n=10)
            latencias.append((time.perf_counter() - inicio) * 1000)
            _ = len(resultados)

    metricas = {
        "latencia_ms_media": round(statistics.mean(latencias), 2),
        "latencia_ms_p50": _percentil(latencias, 50),
        "latencia_ms_p95": _percentil(latencias, 95),
        "latencia_ms_p99": _percentil(latencias, 99),
        "latencia_ms_max": round(max(latencias), 2),
        "consultas": len(latencias),
        "objetivo_p95_ms": 200,
        "pasa_objetivo": _percentil(latencias, 95) < 200,
    }
    logger.info("Métricas: %s", metricas)

    corrida = BenchmarkRunModel(
        nombre="busqueda_hibrida",
        escenario=f"{args.consultas} consultas sobre {args.sembrar}+ eventos",
        parametros={
            "consultas": args.consultas,
            "sembrar": args.sembrar,
            "top_n": 10,
            "modelo_embedding": get_settings().embedding_model,
        },
        metricas=metricas,
        git_commit=_git_commit(),
    )
    async with get_session(engine) as session:
        await BenchmarkRepository(session).registrar(corrida)
        await session.commit()

    await engine.dispose()
    logger.info("Corrida persistida en benchmarks (id=%s)", corrida.id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
