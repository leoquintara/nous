"""Suite de benchmarks Neuro-6 (capa epistémica).

Complementa a ``benchmark_neuro5``. Mide las piezas nuevas — cada
una donde vive: puro en µs, repo con rollback:

AJ. Metamemoria epistémica — ``confianza`` (techo paramétrico) +
    ``desglose_confianza`` + ``techo_parametrica``.
AK. STP/LTP — ``peso_efectivo_coactiva`` (potenciación ~6h).
AL. Lagunas — ``detectar_lagunas`` (el mapa de lo que falta).
AM. Hebbianas DB — ``reforzar_coactivas`` (sello STP) +
    ``vecinos_coactivos`` (la expansión en recall).

El efecto neto (la confianza paramétrica no se autopromueve, lo que
falta se declara) es semántica — lo medible es el costo de
declararlo, que es lo que reportan estas secciones.

Uso:
    poetry run python -m scripts.benchmark_neuro6 [--iteraciones N]
"""

import argparse
import asyncio
import io
import sys
import time
import uuid
from collections.abc import Callable
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from scripts.benchmark_neuro2 import _cronometrar, _filas_metricas
from scripts.benchmark_search import _imprimir_tabla, _persistir
from src.core.memoria.epistemica import detectar_lagunas
from src.core.memoria.retrieval import (
    STP_MEDIA_VIDA_SEG,
    confianza,
    desglose_confianza,
    peso_efectivo_coactiva,
    techo_parametrica,
)
from src.storage.database import crear_engine, get_session
from src.storage.models import EventoModel
from src.storage.repositories import RelacionRepository


def _benchmark_metamemoria(iteraciones: int) -> dict[str, float]:
    """Sección AJ (puro): la confianza epistémica descompuesta."""
    us_techo = _cronometrar(techo_parametrica, iteraciones, True, 0)
    us_conf = _cronometrar(
        confianza,
        iteraciones,
        2,
        1.5,
        0.7,
        30.0,
        1,
        False,
        None,
        True,
    )
    us_desglose = _cronometrar(
        desglose_confianza,
        iteraciones,
        2,
        1.5,
        0.7,
        30.0,
        1,
        False,
        None,
        True,
    )
    return {
        "techo_parametrica (µs)": round(us_techo, 3),
        "confianza con techo (µs)": round(us_conf, 3),
        "desglose_confianza (µs)": round(us_desglose, 3),
    }


def _benchmark_stp(iteraciones: int) -> dict[str, float]:
    """Sección AK (puro): STP sobre el peso LTP duradero."""
    ahora = 1_800_000_000
    us = _cronometrar(
        peso_efectivo_coactiva,
        iteraciones,
        1.5,
        ahora - STP_MEDIA_VIDA_SEG // 2,
        ahora,
    )
    return {"peso_efectivo_coactiva (µs)": round(us, 3)}


def _benchmark_lagunas(iteraciones: int) -> dict[str, float]:
    """Sección AL (puro): declarar lo que la consulta no cubrió."""
    us = _cronometrar(
        detectar_lagunas,
        iteraciones,
        7,
        10,
        ["cognitiva"] * 7,
        [0.6, 0.7, 0.55, 0.8, 0.62, 0.71, 0.66],
        2,
    )
    return {"detectar_lagunas (µs)": round(us, 3)}


async def _benchmark_coactivas_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección AM (DB): sellar co-encendidos y leer los vecinos.

    Siembra un anillo de aristas coactivas (el rollback las deshace)
    y mide el costo de la expansión hebbiana por consulta.
    """
    repo = RelacionRepository(session)
    # Semilla: 40 eventos para que las aristas tengan a quién
    # apuntar (FK relaciones → eventos); el rollback los deshace.
    ws = uuid.UUID("00000000-0000-0000-0000-000000000002")
    eventos: list[EventoModel] = []
    for i in range(40):
        ev = EventoModel(
            timestamp_unix=int(time.time()),
            dimension="cognitiva",
            tipo="hecho",
            contenido=f"bench neuro6 {i}",
            fuente="bench",
            workspace_id=ws,
        )
        session.add(ev)
        eventos.append(ev)
    await session.flush()
    ids = [ev.id for ev in eventos]
    pares = [(ids[i], ids[i + 1]) for i in range(len(ids) - 1)]

    inicio = time.perf_counter()
    tocadas = await repo.reforzar_coactivas(
        pares, delta=0.2, peso_max=3.0, ahora_unix=int(time.time())
    )
    lat_sembrar = (time.perf_counter() - inicio) * 1000

    lat_vecinos: list[float] = []
    n = 0
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        aristas = await repo.vecinos_coactivos(ids[:10], limite=24)
        lat_vecinos.append((time.perf_counter() - inicio) * 1000)
        n = len(aristas)
    return {
        f"reforzar_coactivas x{tocadas} (ms)": round(lat_sembrar, 3),
        "vecinos_coactivos (ms)": round(
            sum(lat_vecinos) / len(lat_vecinos), 3
        ),
        "vecinos devueltos": float(n),
    }


async def main() -> None:
    """Ejecuta la suite Neuro-6 completa y persiste las métricas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteraciones", type=int, default=100)
    args = parser.parse_args()
    iteraciones = args.iteraciones
    iteraciones_db = max(10, iteraciones // 4)

    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )

    puras: dict[str, Callable[[int], dict[str, float]]] = {
        "AJ_metamemoria": _benchmark_metamemoria,
        "AK_stp_ltp": _benchmark_stp,
        "AL_lagunas": _benchmark_lagunas,
    }
    metricas: dict[str, object] = {}
    for nombre, fn in puras.items():
        metricas[nombre] = fn(iteraciones)
        _imprimir_tabla(
            f"⚡ {nombre}",
            ["Métrica", "Valor"],
            _filas_metricas(metricas, nombre),
        )

    engine = crear_engine()
    async with get_session(engine) as session:
        metricas["AM_coactivas_db"] = await _benchmark_coactivas_db(
            session, iteraciones_db
        )
        await session.rollback()
    await engine.dispose()

    _imprimir_tabla(
        "📊 AM_coactivas_db",
        ["Métrica", "Valor"],
        _filas_metricas(metricas, "AM_coactivas_db"),
    )

    await _persistir(
        "suite_neuro6",
        f"{iteraciones} iteraciones",
        vars(args),
        metricas,
    )
    print("\n✅ Corrida persistida en benchmarks (suite_neuro6)")


if __name__ == "__main__":
    asyncio.run(main())
