"""Suite de benchmarks Neuro-4 (competencia, loops y procedencia).

Complementa a ``benchmark_neuro3`` (O–W). Mide los cinco patrones
nuevos — cada uno donde vive: puro en µs, repo con rollback:

X. Zeigarnik — ``factor_zeigarnik`` puro + ``ids_compromisos_abiertos``.
Y. RIF — ``competidores_rif`` puro + ``debilitar_lote`` (UPDATE batch).
Z. Interferencia proactiva — ``es_competidor_proactivo`` puro.
AA. Flashbulb — ``es_flashbulb`` puro + ``marcar_flashbulb`` (RMW).
AB. Amnesia de fuente — ``confianza`` puro + ``fuentes_confiables``.

El efecto neto sobre el recall (×1.15 boost, ×0.97/×0.92 debilitar)
es aritmética trivial — lo medible es el costo de decidirlo y de
persistirlo, que es lo que estas secciones reportan.

Uso:
    poetry run python -m scripts.benchmark_neuro4 [--iteraciones N]
"""

import argparse
import asyncio
import io
import sys
import time
import uuid
from collections.abc import Callable
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.benchmark_neuro2 import _cronometrar, _filas_metricas
from scripts.benchmark_search import _imprimir_tabla, _percentil, _persistir
from src.core.memoria.interferencia import es_competidor_proactivo
from src.core.memoria.novedad import es_flashbulb
from src.core.memoria.retrieval import (
    competidores_rif,
    confianza,
    factor_zeigarnik,
)
from src.storage.database import crear_engine, get_session
from src.storage.models import EventoModel, FuenteModel
from src.storage.repositories import EventoRepository, FuenteRepository


def _benchmark_zeigarnik(iteraciones: int) -> dict[str, float]:
    """Sección X (puro): el boost de loop abierto cuesta nada."""
    us = _cronometrar(
        factor_zeigarnik, iteraciones, "compromiso", False
    )
    return {"factor_zeigarnik (µs)": round(us, 3)}


async def _benchmark_zeigarnik_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección X (DB): subconsulta NOT EXISTS sobre lote de ids."""
    repo = EventoRepository(session)
    ids = [
        r[0]
        for r in (
            await session.execute(
                select(EventoModel.id)
                .where(EventoModel.tipo == "compromiso")
                .limit(50)
            )
        ).all()
    ]
    if not ids:
        return {"compromisos": 0.0, "ids_abiertos (ms)": 0.0}
    lat: list[float] = []
    n = 0
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        abiertos = await repo.ids_compromisos_abiertos(ids)
        lat.append((time.perf_counter() - inicio) * 1000)
        n = len(abiertos)
    return {
        "compromisos en lote": float(len(ids)),
        "abiertos": float(n),
        "ids_compromisos_abiertos (ms p95)": _percentil(lat, 95),
    }


def _benchmark_rif(iteraciones: int) -> dict[str, float]:
    """Sección Y (puro): selección de perdedores del recall."""
    candidatos = [str(uuid.uuid4()) for _ in range(20)]
    elegidos = candidatos[:4]
    us = _cronometrar(
        competidores_rif, iteraciones, list(candidatos), list(elegidos)
    )
    return {"competidores_rif (µs)": round(us, 3)}


async def _benchmark_debilitar(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección Y (DB): UPDATE batch weight×factor + contador."""
    repo = EventoRepository(session)
    ids = [
        r[0]
        for r in (
            await session.execute(select(EventoModel.id).limit(20))
        ).all()
    ]
    if not ids:
        return {"debilitar_lote (ms)": 0.0, "filas": 0.0}
    lat: list[float] = []
    afectadas = 0
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        afectadas = await repo.debilitar_lote(ids[:8], 0.97)
        await session.flush()
        lat.append((time.perf_counter() - inicio) * 1000)
    return {
        "filas debilitadas": float(afectadas),
        "debilitar_lote 8 (ms p95)": _percentil(lat, 95),
    }


def _benchmark_interferencia(iteraciones: int) -> dict[str, float]:
    """Sección Z (puro): la banda de competencia se decide en µs."""
    us = _cronometrar(
        es_competidor_proactivo, iteraciones, 0.87, 0.95
    )
    return {"es_competidor_proactivo (µs)": round(us, 3)}


def _benchmark_flashbulb(iteraciones: int) -> dict[str, float]:
    """Sección AA (puro): sorpresa × emoción → flag de codificación."""
    us = _cronometrar(es_flashbulb, iteraciones, 0.7, 0.85)
    return {"es_flashbulb (µs)": round(us, 3)}


async def _benchmark_flashbulb_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección AA (DB): weight×1.5 + tag — read-modify-write."""
    repo = EventoRepository(session)
    evento_id = (
        await session.execute(select(EventoModel.id).limit(1))
    ).scalar_one_or_none()
    if evento_id is None:
        return {"marcar_flashbulb (ms)": 0.0}
    lat: list[float] = []
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        await repo.marcar_flashbulb(evento_id)
        await session.flush()
        lat.append((time.perf_counter() - inicio) * 1000)
    return {"marcar_flashbulb (ms p95)": _percentil(lat, 95)}


def _benchmark_amnesia(iteraciones: int) -> dict[str, float]:
    """Sección AB (puro): confianza con procedencia evaluada."""
    us = _cronometrar(
        confianza,
        iteraciones,
        5, 1.4, 0.7, 30.0,
        0, False, False,
    )
    return {"confianza c/fuente (µs)": round(us, 3)}


async def _benchmark_amnesia_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección AB (DB): mapa de confiabilidad para un lote."""
    repo = FuenteRepository(session)
    nombres = [
        r[0]
        for r in (
            await session.execute(select(FuenteModel.nombre).limit(30))
        ).all()
    ]
    if not nombres:
        return {"fuentes": 0.0, "fuentes_confiables (ms)": 0.0}
    lat: list[float] = []
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        await repo.fuentes_confiables(nombres)
        lat.append((time.perf_counter() - inicio) * 1000)
    return {
        "fuentes en lote": float(len(nombres)),
        "fuentes_confiables (ms p95)": _percentil(lat, 95),
    }


async def main() -> None:
    """Ejecuta la suite Neuro-4 completa y persiste las métricas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteraciones", type=int, default=100)
    args = parser.parse_args()
    iteraciones = args.iteraciones
    iteraciones_db = max(10, iteraciones // 4)

    # La consola de Windows (cp1252) no soporta emojis ni box-drawing.
    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )

    print("🧠 Nous Neuro-4 Benchmark Suite v1.0")
    print("═" * 52)
    engine = crear_engine()
    metricas: dict[str, object] = {}

    async with get_session(engine) as session:
        secciones: list[tuple[str, Callable[[], dict[str, float]]]] = [
            (
                "X_zeigarnik",
                lambda: _benchmark_zeigarnik(iteraciones * 100),
            ),
            ("Y_rif_seleccion", lambda: _benchmark_rif(iteraciones * 100)),
            (
                "Z_interferencia",
                lambda: _benchmark_interferencia(iteraciones * 100),
            ),
            (
                "AA_flashbulb",
                lambda: _benchmark_flashbulb(iteraciones * 100),
            ),
            (
                "AB_amnesia_fuente",
                lambda: _benchmark_amnesia(iteraciones * 100),
            ),
        ]
        for nombre, fn in secciones:
            metricas[nombre] = fn()
            _imprimir_tabla(
                f"📊 {nombre}",
                ["Métrica", "Valor"],
                _filas_metricas(metricas, nombre),
            )
        metricas["X_zeigarnik_db"] = await _benchmark_zeigarnik_db(
            session, iteraciones_db
        )
        metricas["Y_debilitar_db"] = await _benchmark_debilitar(
            session, iteraciones_db
        )
        metricas["AA_flashbulb_db"] = await _benchmark_flashbulb_db(
            session, iteraciones_db
        )
        metricas["AB_amnesia_db"] = await _benchmark_amnesia_db(
            session, iteraciones_db
        )
        await session.rollback()
    await engine.dispose()

    for nombre in (
        "X_zeigarnik_db",
        "Y_debilitar_db",
        "AA_flashbulb_db",
        "AB_amnesia_db",
    ):
        _imprimir_tabla(
            f"📊 {nombre}",
            ["Métrica", "Valor"],
            _filas_metricas(metricas, nombre),
        )

    await _persistir(
        "suite_neuro4",
        f"{iteraciones} iteraciones",
        vars(args),
        metricas,
    )
    print("\n✅ Corrida persistida en benchmarks (suite_neuro4)")


if __name__ == "__main__":
    asyncio.run(main())
