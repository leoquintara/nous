"""Persiste el benchmark head-to-head Nous vs gbrain vs memlord.

La comparativa se corrió en vivo contra los tres sistemas MCP con
los datos migrados reales (5 rondas, 21 consultas). Este script
registra el veredicto en la tabla ``benchmarks`` para que el
dashboard lo muestre junto a las corridas de latencia.

Uso:
    poetry run python -m scripts.benchmark_headtohead
"""

import asyncio
import io
import logging
import subprocess
import sys
from typing import cast

from src.storage.database import crear_engine, get_session
from src.storage.models import BenchmarkRunModel
from src.storage.repositories import BenchmarkRepository

logger = logging.getLogger("nous.scripts.benchmark_headtohead")

# Veredicto de la corrida de validación (octubre 2025). Cada ronda
# lista sus consultas con el ganador; "empate" cuando dos sistemas
# respondieron igual de bien y "sin_datos" cuando ninguno tenía la
# evidencia (ausencia honesta, no fallo de retrieval).
RONDAS: dict[str, dict[str, object]] = {
    "r1_entidades": {
        "nous": 3,
        "gbrain": 1,
        "empate": 1,
        "consultas": [
            "quién es Eduardo → nous (ficha + span + vínculos)",
            "email de Carlos Abad → sin_datos (honesto en los 3)",
            "email de Ricardo → nous (hecho exacto #1)",
            "dev más nuevo → gbrain (router de Nous falseó, corregido)",
            "perfil Samay → nous (ficha + 16 vínculos)",
        ],
    },
    "r2_temporal": {
        "nous": 3,
        "gbrain": 0,
        "memlord": 0,
        "consultas": [
            "qué pasó el 19-may → replay cronológico completo",
            "primer mensaje signal → memory-1/2/3 exactos",
            "semana 20-26 jul → 4 episodios segmentados",
        ],
    },
    "r3_tema": {
        "nous": 2,
        "gbrain": 1,
        "empate": 1,
        "consultas": [
            "react v1 vs v2 → empate",
            "decisión deploy prod → nous (conversación real + hilo)",
            "crítico para gatsby → gbrain (falso positivo entidad, corregido)",
            "leo→samay sin whatsapp → solo nous (contenido + hilo)",
        ],
    },
    "r4_cognitivos": {
        "nous": 3,
        "empate": 4,
        "capacidades_unicas": [
            "mapa_lagunas (evalúa la consulta, no solo grafo)",
            "consolidación/sueño con reporte auditable",
            "prospectiva (intenciones time/event)",
        ],
    },
    "r5_cross_workspace": {
        "nous": 1,
        "gbrain": 0,
        "memlord": 0,
        "nota": "Solo Nous responde una consulta a través de 3 "
        "fuentes (ficha canónica gbrain + mensajes memlord + grafo).",
    },
}

METRICAS: dict[str, object] = {
    "consultas_totales": 21,
    "rondas": RONDAS,
    "marcador": {"nous": 12, "gbrain": 2, "empates": 7, "memlord": 0},
    "bugs_encontrados_y_corregidos": [
        "router: nombres relacionales/adjetivales como entidad",
        "temporal: andamiaje interrogativo diluía el sujeto; "
        "extremo perdido tras rerank → ancla post-rerank",
    ],
}


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


async def main() -> None:
    """Persiste la corrida comparativa en benchmarks."""
    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )
    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            await BenchmarkRepository(session).registrar(
                BenchmarkRunModel(
                    nombre="headtohead",
                    escenario="Nous vs gbrain vs memlord — 5 rondas, 21 consultas",
                    parametros={
                        "sistemas": ["nous", "gbrain", "memlord"],
                        "rondas": 5,
                        "consultas": 21,
                    },
                    metricas=METRICAS,
                    git_commit=_git_commit(),
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    logger.info("Corrida head-to-head persistida en benchmarks")
    print("Corrida head-to-head persistida: Nous 12 — gbrain 2 — "
          "empates 7 — memlord 0")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
