"""Suite de benchmarks Neuro-3 (patrones de mejora continua).

Complementa a ``benchmark_neuro2`` (E–N). Mide los doce patrones
nuevos — cada uno donde vive: puro en µs, repo con rollback:

O. Olvido dirigido — media_vida con factor 0.33 + UPDATE del flag.
P. SRS — ``proxima_revision`` puro + query ``pendientes_revision``.
Q. Cuarentena — ``es_confiable`` + ``listar_cuarentena`` + liberación.
R. REM — ``pares_rem`` greedy sobre el corpus de embeddings.
S. Chaining — ``anterior_misma_fuente`` (índice ix_eventos_fuente).
T. Snooze — ``aplazar`` intención (UPDATE puntual).
U. Fan/ToT — ``diversificar`` + ``es_casi_recuerdo`` por resultado.
V. Frontera — ``marca_frontera`` puro + ``marcar_inaugura_epoca``.
W. Gist expansion — ``episodios_de_gist`` join por arista.

Los patrones ya cubiertos por otras suites (prep pack = una
búsqueda estándar, salvados = dentro de ``decidir_consolidacion``)
no se repiten aquí — se referencian en el reporte final.

Uso:
    poetry run python -m scripts.benchmark_neuro3 [--iteraciones N]
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

from scripts.benchmark_neuro2 import (
    _corpus_embeddings,
    _cronometrar,
    _filas_metricas,
)
from scripts.benchmark_search import _imprimir_tabla, _percentil, _persistir
from src.core.memoria.decay import media_vida_dias, proxima_revision
from src.core.memoria.diversidad import diversificar, es_casi_recuerdo
from src.core.memoria.novedad import marca_frontera, sorpresa
from src.core.memoria.rem import pares_rem
from src.core.memoria.semanticizacion import EpisodioGist
from src.core.memoria.tipos import TipoMemoria
from src.core.modelos import Dimension
from src.storage.database import crear_engine, get_session
from src.storage.models import EventoModel, IntencionProspectivaModel
from src.storage.repositories import (
    EventoRepository,
    FuenteRepository,
    IntencionProspectivaRepository,
)


def _benchmark_olvido_dirigido(iteraciones: int) -> dict[str, float]:
    """Sección O: media-vida normal vs dirigida (factor 0.33)."""
    base = media_vida_dias(
        Dimension.COGNITIVA, TipoMemoria.EPISODICA, 0.5, 0
    )
    dirigida = media_vida_dias(
        Dimension.COGNITIVA, TipoMemoria.EPISODICA, 0.5, 0,
        olvido_dirigido=True,
    )
    us = _cronometrar(
        media_vida_dias,
        iteraciones,
        Dimension.COGNITIVA,
        TipoMemoria.EPISODICA,
        0.5,
        0,
        False,
        True,
    )
    return {
        "media_vida normal (días)": round(base, 1),
        "media_vida dirigida (días)": round(dirigida, 1),
        "aceleración": round(base / dirigida, 2) if dirigida else 0.0,
        "media_vida_dirigida (µs)": round(us, 2),
    }


def _benchmark_srs(iteraciones: int) -> dict[str, float]:
    """Sección P (puro): timestamp del repaso óptimo."""
    ahora = int(time.time())
    us = _cronometrar(proxima_revision, iteraciones, ahora, 30.0)
    return {"proxima_revision (µs/llamada)": round(us, 2)}


async def _benchmark_srs_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección P (DB): pendientes_revision sobre el índice parcial."""
    repo = EventoRepository(session)
    ahora = int(time.time())
    lat: list[float] = []
    n = 0
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        pendientes = await repo.pendientes_revision(ahora, limite=20)
        lat.append((time.perf_counter() - inicio) * 1000)
        n = len(pendientes)
    return {
        "vencidas en corpus": float(n),
        "pendientes_revision (ms p95)": _percentil(lat, 95),
    }


async def _benchmark_cuarentena(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección Q: chequeo de confianza + listado de aislados."""
    repo_f = FuenteRepository(session)
    repo_e = EventoRepository(session)
    lat_conf: list[float] = []
    lat_lista: list[float] = []
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        await repo_f.es_confiable("benchmark-quarentena")
        lat_conf.append((time.perf_counter() - inicio) * 1000)
        inicio = time.perf_counter()
        aislados = await repo_e.listar_cuarentena(limite=50)
        lat_lista.append((time.perf_counter() - inicio) * 1000)
    return {
        "en cuarentena": float(len(aislados)),
        "es_confiable (ms p95)": _percentil(lat_conf, 95),
        "listar_cuarentena (ms p95)": _percentil(lat_lista, 95),
    }


async def _benchmark_rem(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección R: selección greedy de pares soñables."""
    vectores = await _corpus_embeddings(session, 400)
    if not vectores:
        return {"episodios": 0.0, "pares_rem (ms)": 0.0}
    episodios = [
        EpisodioGist(
            id=str(i), contenido=f"episodio rem {i}", weight=1.0,
            embedding=v,
        )
        for i, v in enumerate(vectores)
    ]
    inicio = time.perf_counter()
    n_pares = 0
    for _ in range(iteraciones):
        n_pares = len(pares_rem(episodios))
    ms = (time.perf_counter() - inicio) / iteraciones * 1000
    return {
        "episodios": float(len(episodios)),
        "pares soñados": float(n_pares),
        "pares_rem (ms)": round(ms, 2),
    }


async def _benchmark_chaining(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección S: anterior_misma_fuente — el eslabón previo."""
    repo = EventoRepository(session)
    fila = (
        await session.execute(
            select(EventoModel.fuente, EventoModel.timestamp_unix)
            .where(EventoModel.fuente.is_not(None))
            .limit(1)
        )
    ).first()
    if fila is None:
        return {"anterior_misma_fuente (ms)": 0.0}
    fuente, ts = str(fila[0]), int(fila[1])
    lat: list[float] = []
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        await repo.anterior_misma_fuente(fuente, ts, 86400)
        lat.append((time.perf_counter() - inicio) * 1000)
    return {"anterior_misma_fuente (ms p95)": _percentil(lat, 95)}


async def _benchmark_snooze(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección T: aplazar una intención (UPDATE puntual, rollback)."""
    repo = IntencionProspectivaRepository(session)
    intencion_id = (
        await session.execute(
            select(IntencionProspectivaModel.id).limit(1)
        )
    ).scalar_one_or_none()
    if intencion_id is None:
        return {"aplazar (ms)": 0.0, "intenciones": 0.0}
    lat: list[float] = []
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        await repo.aplazar(intencion_id, int(time.time()) + 3600)
        await session.flush()
        lat.append((time.perf_counter() - inicio) * 1000)
    return {
        "intenciones": 1.0,
        "aplazar (ms p95)": _percentil(lat, 95),
    }


def _benchmark_diversidad(iteraciones: int) -> dict[str, float]:
    """Sección U: penalización fan + señal tip-of-the-tongue."""
    items = [
        (
            str(uuid.uuid4()),
            f"memoria de prueba {i} sobre agentes y recuerdo "
            f"episódico variante {i % 4}",
            0.9 - i * 0.03,
        )
        for i in range(10)
    ]
    us_div = _cronometrar(diversificar, iteraciones, list(items))
    us_tot = _cronometrar(es_casi_recuerdo, iteraciones * 10, 0.17)
    return {
        "diversificar 10 hits (µs)": round(us_div, 2),
        "es_casi_recuerdo (µs)": round(us_tot, 3),
    }


async def _benchmark_frontera(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección V: marca de frontera pura + UPDATE inaugura_epoca."""
    us = _cronometrar(marca_frontera, iteraciones, 0.71)
    evento_id = (
        await session.execute(select(EventoModel.id).limit(1))
    ).scalar_one_or_none()
    lat: list[float] = []
    if evento_id is not None:
        repo = EventoRepository(session)
        for _ in range(iteraciones):
            inicio = time.perf_counter()
            await repo.marcar_inaugura_epoca(evento_id)
            await session.flush()
            lat.append((time.perf_counter() - inicio) * 1000)
    return {
        "marca_frontera (µs)": round(us, 3),
        "marcar_epoca (ms p95)": _percentil(lat, 95) if lat else 0.0,
    }


async def _benchmark_gist_expansion(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección W: episodios miembros de un gist (join por arista)."""
    repo = EventoRepository(session)
    gist_id = (
        await session.execute(
            select(EventoModel.id)
            .where(EventoModel.tags.contains(["gist"]))
            .limit(1)
        )
    ).scalar_one_or_none()
    if gist_id is None:
        return {"episodios_de_gist (ms)": 0.0, "gists": 0.0}
    lat: list[float] = []
    n = 0
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        miembros = await repo.episodios_de_gist(gist_id, limite=3)
        lat.append((time.perf_counter() - inicio) * 1000)
        n = len(miembros)
    return {
        "gists": 1.0,
        "miembros traídos": float(n),
        "episodios_de_gist (ms p95)": _percentil(lat, 95),
    }


async def _benchmark_sorpresa_frontera_path(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sorpresa→frontera: el costo completo del write-path extra."""
    recientes = await _corpus_embeddings(session, 50)
    if not recientes:
        return {"sorpresa+frontera (ms)": 0.0}
    vector = recientes[0]
    inicio = time.perf_counter()
    for _ in range(iteraciones):
        marca_frontera(sorpresa(vector, recientes))
    ms = (time.perf_counter() - inicio) / iteraciones * 1000
    return {"sorpresa+frontera 50 vecinos (ms)": round(ms, 3)}


async def main() -> None:
    """Ejecuta la suite Neuro-3 completa y persiste las métricas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteraciones", type=int, default=100)
    args = parser.parse_args()
    iteraciones = args.iteraciones
    iteraciones_db = max(10, iteraciones // 4)

    # La consola de Windows (cp1252) no soporta emojis ni box-drawing.
    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )

    print("🧠 Nous Neuro-3 Benchmark Suite v1.0")
    print("═" * 52)
    engine = crear_engine()
    metricas: dict[str, object] = {}

    async with get_session(engine) as session:
        secciones: list[tuple[str, Callable[[], dict[str, float]]]] = [
            ("O_olvido_dirigido", lambda: _benchmark_olvido_dirigido(iteraciones * 100)),
            ("P_srs_calculo", lambda: _benchmark_srs(iteraciones * 100)),
            ("U_fan_tot", lambda: _benchmark_diversidad(iteraciones * 100)),
        ]
        for nombre, fn in secciones:
            metricas[nombre] = fn()
            _imprimir_tabla(
                f"📊 {nombre}",
                ["Métrica", "Valor"],
                _filas_metricas(metricas, nombre),
            )
        metricas["P_srs_db"] = await _benchmark_srs_db(session, iteraciones_db)
        metricas["Q_cuarentena"] = await _benchmark_cuarentena(
            session, iteraciones_db
        )
        metricas["R_rem"] = await _benchmark_rem(session, iteraciones_db)
        metricas["S_chaining"] = await _benchmark_chaining(
            session, iteraciones_db
        )
        metricas["T_snooze"] = await _benchmark_snooze(session, iteraciones_db)
        metricas["V_frontera"] = await _benchmark_frontera(
            session, iteraciones_db
        )
        metricas["W_gist_expansion"] = await _benchmark_gist_expansion(
            session, iteraciones_db
        )
        metricas["V_sorpresa_path"] = await _benchmark_sorpresa_frontera_path(
            session, iteraciones_db
        )
        await session.rollback()
    await engine.dispose()

    for nombre in (
        "P_srs_db",
        "Q_cuarentena",
        "R_rem",
        "S_chaining",
        "T_snooze",
        "V_frontera",
        "W_gist_expansion",
        "V_sorpresa_path",
    ):
        _imprimir_tabla(
            f"📊 {nombre}",
            ["Métrica", "Valor"],
            _filas_metricas(metricas, nombre),
        )

    await _persistir(
        "suite_neuro3",
        f"{iteraciones} iteraciones",
        vars(args),
        metricas,
    )
    print("\n✅ Corrida persistida en benchmarks (suite_neuro3)")


if __name__ == "__main__":
    asyncio.run(main())
