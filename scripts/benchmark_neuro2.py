"""Suite de benchmarks Neuro-2 (patrones ADR-003 + paths no cubiertos).

Complementa a ``benchmark_search`` (A–D: búsqueda, clasificación,
proyección, memoria). Mide los patrones neurocognitivos nuevos y los
paths de escritura/consolidación:

E. Write-path — recordar: embedding + sorpresa + clasificación.
F. Metamemoria — ``confianza`` derivada por resultado.
G. Hebbiana — pares coactivos + refuerzo batch de aristas.
H. Extinción — atenuación de valencia en recall neutro.
I. Reconsolidación — ventana lábil + chequeo.
J. Spacing/decay — retención con intervalo medio y primacía.
K. Semanticización — clustering de episodios → plan de gists.
L. Buffer de trabajo — CRUD de borradores (TTL/ensayo).
M. Consolidación — decidir + aplicar plan sobre el corpus.
N. Circadiano — ventana óptima de sueño desde histograma.

Las secciones DB corren dentro de una transacción con ROLLBACK: miden
latencia real sin dejar datos de prueba. La corrida se persiste como
``suite_neuro2`` en la tabla ``benchmarks`` (visible en el dashboard).

Uso:
    poetry run python -m scripts.benchmark_neuro2 [--iteraciones N]
"""

import argparse
import asyncio
import io
import random
import sys
import time
import uuid
from collections.abc import Callable
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.benchmark_search import _imprimir_tabla, _percentil, _persistir
from src.core.embeddings import GeneradorEmbeddings
from src.core.memoria.consolidacion import (
    EventoConsolidable,
    decidir_consolidacion,
)
from src.core.memoria.decay import retencion_actual
from src.core.memoria.novedad import amplitud_con_sorpresa, sorpresa
from src.core.memoria.retrieval import (
    COACTIVA_DELTA,
    COACTIVA_PESO_MAX,
    confianza_de_evento,
    esta_labil,
    extincion_valencia,
    pares_coactivos,
    ventana_labil,
)
from src.core.memoria.ritmos import ventana_optima_sueno
from src.core.memoria.semanticizacion import EpisodioGist, plan_gists
from src.core.memoria.tipos import TipoMemoria
from src.core.modelos import Dimension
from src.storage.database import crear_engine, get_session
from src.storage.models import BorradorModel, EventoModel
from src.storage.repositories import (
    BorradorRepository,
    EventoRepository,
    RelacionRepository,
)

TOP_K_RECALL = 10


async def _corpus_embeddings(
    session: AsyncSession, limite: int = 300
) -> list[list[float]]:
    """Trae embeddings reales del corpus para benchmarks puros."""
    stmt = (
        select(EventoModel.embedding)
        .where(EventoModel.embedding.is_not(None))
        .order_by(func.random())
        .limit(limite)
    )
    return [
        list(fila)
        for fila in (await session.execute(stmt)).scalars()
        if fila is not None
    ]


def _cronometrar(
    fn: object, iteraciones: int, *args: object
) -> float:
    """Ejecuta fn(*args) N veces; retorna media en microsegundos."""
    inicio = time.perf_counter()
    for _ in range(iteraciones):
        fn(*args)  # type: ignore[operator]
    return (time.perf_counter() - inicio) / iteraciones * 1e6


async def _benchmark_write_path(
    session: AsyncSession,
    embeddings: GeneradorEmbeddings,
    iteraciones: int,
) -> dict[str, float]:
    """Sección E: latencia del path de escritura completo por etapa."""
    frases = [
        f"benchmark de escritura: decisión técnica número {i} sobre "
        "arquitectura de memoria y embeddings locales"
        for i in range(iteraciones)
    ]
    lat_emb: list[float] = []
    lat_sorpresa: list[float] = []
    recientes = await _corpus_embeddings(session, 50)
    for frase in frases:
        inicio = time.perf_counter()
        vector = await embeddings.generar(frase)
        lat_emb.append((time.perf_counter() - inicio) * 1000)
        inicio = time.perf_counter()
        sorpresa_val = sorpresa(vector, recientes)
        amplitud_con_sorpresa(0.7, sorpresa_val)
        lat_sorpresa.append((time.perf_counter() - inicio) * 1000)
    return {
        "embed (ms p50)": _percentil(lat_emb, 50),
        "embed (ms p95)": _percentil(lat_emb, 95),
        "sorpresa (ms p95)": _percentil(lat_sorpresa, 95),
        "sorpresa (ms media)": round(
            sum(lat_sorpresa) / len(lat_sorpresa), 3
        ),
    }


def _benchmark_metamemoria(iteraciones: int) -> dict[str, float]:
    """Sección F: costo de derivar confianza por resultado."""
    ahora = int(time.time())
    us = _cronometrar(
        confianza_de_evento,
        iteraciones,
        5,
        1.4,
        0.7,
        ahora - 86400 * 30,
        ahora,
    )
    return {"confianza (µs/llamada)": round(us, 2)}


def _benchmark_hebbiana(iteraciones: int) -> dict[str, float]:
    """Sección G: pares coactivos de un recall top-k (puro)."""
    ids = [str(uuid.uuid4()) for _ in range(TOP_K_RECALL)]
    us = _cronometrar(pares_coactivos, iteraciones, ids)
    return {"pares_coactivos (µs/recall)": round(us, 2)}


def _benchmark_extincion(iteraciones: int) -> dict[str, float]:
    """Sección H: atenuación de valencia por evento negativo."""
    us = _cronometrar(extincion_valencia, iteraciones, -0.9, 0.1)
    return {"extincion_valencia (µs/llamada)": round(us, 2)}


def _benchmark_reconsolidacion(iteraciones: int) -> dict[str, float]:
    """Sección I: ventana lábil y su chequeo por evento."""
    ahora = int(time.time())
    labil = ventana_labil(ahora)
    us = _cronometrar(esta_labil, iteraciones, labil, ahora)
    return {
        "ventana_labil (µs)": round(_cronometrar(ventana_labil, iteraciones, ahora), 2),
        "esta_labil (µs/llamada)": round(us, 2),
    }


def _benchmark_decay(iteraciones: int) -> dict[str, float]:
    """Sección J: retención con spacing + primacía por evento."""
    ahora = int(time.time())
    us_espaciado = _cronometrar(
        retencion_actual,
        iteraciones,
        ahora,
        ahora - 86400 * 20,
        ahora - 86400 * 2,
        Dimension.COGNITIVA,
        TipoMemoria.SEMANTICA,
        0.7,
        4,
        False,
        False,
    )
    us_primacia = _cronometrar(
        retencion_actual,
        iteraciones,
        ahora,
        ahora - 86400 * 20,
        None,
        Dimension.COGNITIVA,
        TipoMemoria.SEMANTICA,
        0.7,
        0,
        False,
        True,
    )
    return {
        "retencion+spacing (µs)": round(us_espaciado, 2),
        "retencion+primacia (µs)": round(us_primacia, 2),
    }


async def _benchmark_semanticizacion(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección K: clustering de episodios y plan de gists."""
    vectores = await _corpus_embeddings(session, 400)
    if not vectores:
        return {"episodios": 0, "plan_gists (ms)": 0.0}
    episodios = [
        EpisodioGist(
            id=str(i), contenido=f"episodio {i}", weight=1.0, embedding=v
        )
        for i, v in enumerate(vectores)
    ]
    inicio = time.perf_counter()
    planes = 0
    for _ in range(iteraciones):
        planes = len(plan_gists(episodios))
    ms = (time.perf_counter() - inicio) / iteraciones * 1000
    return {
        "episodios": float(len(episodios)),
        "clusters→gists": float(planes),
        "plan_gists (ms)": round(ms, 2),
    }


async def _benchmark_buffer(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección L: CRUD del buffer de trabajo (rollback al final)."""
    repo = BorradorRepository(session)
    ahora = int(time.time())
    lat_crear: list[float] = []
    lat_listar: list[float] = []
    lat_ensayar: list[float] = []
    ids: list[uuid.UUID] = []
    for i in range(iteraciones):
        inicio = time.perf_counter()
        b = await repo.crear(
            BorradorModel(
                contenido=f"borrador benchmark {i}",
                dimension="cognitiva",
                creado_en=ahora,
                expira_en=ahora + 1800,
            )
        )
        await session.flush()
        lat_crear.append((time.perf_counter() - inicio) * 1000)
        ids.append(b.id)
        inicio = time.perf_counter()
        await repo.listar_vigentes(ahora)
        lat_listar.append((time.perf_counter() - inicio) * 1000)
        inicio = time.perf_counter()
        await repo.ensayar(b.id)
        lat_ensayar.append((time.perf_counter() - inicio) * 1000)
    for b_id in ids:
        await repo.descartar(b_id)
    await session.flush()
    return {
        "crear (ms p95)": _percentil(lat_crear, 95),
        "listar_vigentes (ms p95)": _percentil(lat_listar, 95),
        "ensayar (ms p95)": _percentil(lat_ensayar, 95),
    }


async def _benchmark_consolidacion(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección M: decidir_consolidacion sobre el corpus completo."""
    repo = EventoRepository(session)
    filas = list(await repo.listar_para_consolidacion())
    ahora = int(time.time())
    consolidables = [
        EventoConsolidable(
            id=str(m.id),
            timestamp_unix=m.timestamp_unix,
            dimension=Dimension(m.dimension),
            tipo=str(m.tipo),
            amplitud=float(m.amplitud),
            refuerzos=int(m.refuerzos),
            weight=float(m.weight),
            tipo_memoria=(
                TipoMemoria(m.tipo_memoria) if m.tipo_memoria else None
            ),
            ultimo_acceso=m.ultimo_acceso,
            archivado=bool(m.archivado),
            curado=bool(m.curado),
            inaugura_epoca=bool(m.inaugura_epoca),
        )
        for m in filas
    ]
    inicio = time.perf_counter()
    plan = None
    for _ in range(iteraciones):
        plan = decidir_consolidacion(consolidables, ahora)
    ms = (time.perf_counter() - inicio) / iteraciones * 1000
    return {
        "eventos": float(len(consolidables)),
        "decidir (ms)": round(ms, 2),
        "archivar": float(len(plan.archivar)) if plan else 0.0,
        "fortalecer": float(len(plan.fortalecer)) if plan else 0.0,
    }


def _benchmark_circadiano(iteraciones: int) -> dict[str, float]:
    """Sección N: ventana óptima desde histograma de actividad."""
    rng = random.Random(42)
    base = int(time.time()) - 30 * 86400
    timestamps = [
        base + rng.randint(0, 30 * 86400) for _ in range(10_000)
    ]
    us = _cronometrar(ventana_optima_sueno, iteraciones, timestamps)
    return {"ventana_sueno 10k ts (µs)": round(us, 1)}


async def _benchmark_hebbiana_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección G (DB): upsert batch de aristas coactivas (rollback)."""
    repo = RelacionRepository(session)
    ids = list(
        (await session.execute(
            select(EventoModel.id)
            .where(EventoModel.embedding.is_not(None))
            .limit(TOP_K_RECALL)
        )).scalars()
    )
    if len(ids) < 2:
        return {"reforzar_coactivas (ms)": 0.0}
    pares = [
        (ids[i], ids[j])
        for i in range(len(ids))
        for j in range(i + 1, len(ids))
    ]
    lat: list[float] = []
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        await repo.reforzar_coactivas(
            pares, COACTIVA_DELTA, COACTIVA_PESO_MAX
        )
        await session.flush()
        lat.append((time.perf_counter() - inicio) * 1000)
    return {
        f"pares por recall ({len(pares)})": float(len(pares)),
        "reforzar_coactivas (ms p95)": _percentil(lat, 95),
        "reforzar_coactivas (ms media)": round(sum(lat) / len(lat), 3),
    }


def _filas_metricas(metricas: dict[str, object], nombre: str) -> list[list[str]]:
    """Filas [métrica, valor] para _imprimir_tabla desde el dict."""
    datos = metricas[nombre]
    if not isinstance(datos, dict):
        return [[nombre, str(datos)]]
    return [[str(k), str(v)] for k, v in datos.items()]


async def main() -> None:
    """Ejecuta la suite Neuro-2 completa y persiste las métricas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteraciones", type=int, default=100)
    args = parser.parse_args()
    iteraciones = args.iteraciones
    iteraciones_db = max(10, iteraciones // 4)

    # La consola de Windows (cp1252) no soporta emojis ni box-drawing.
    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )

    print("🧠 Nous Neuro-2 Benchmark Suite v1.0")
    print("═" * 52)
    embeddings = GeneradorEmbeddings()
    engine = crear_engine()
    metricas: dict[str, object] = {}

    async with get_session(engine) as session:
        metricas["E_write_path"] = await _benchmark_write_path(
            session, embeddings, iteraciones_db
        )
        _imprimir_tabla(
            f"📊 E. Write-path (recordar: embed + sorpresa) ×{iteraciones_db}",
            ["Etapa", "ms"],
            _filas_metricas(metricas, "E_write_path"),
        )
        secciones: list[tuple[str, Callable[[], dict[str, float]]]] = [
            ("F_metamemoria", lambda: _benchmark_metamemoria(iteraciones * 100)),
            ("G_hebbiana", lambda: _benchmark_hebbiana(iteraciones * 100)),
            ("H_extincion", lambda: _benchmark_extincion(iteraciones * 100)),
            ("I_reconsolidacion", lambda: _benchmark_reconsolidacion(iteraciones * 100)),
            ("J_spacing_decay", lambda: _benchmark_decay(iteraciones * 100)),
            ("N_circadiano", lambda: _benchmark_circadiano(iteraciones)),
        ]
        for nombre, fn in secciones:
            metricas[nombre] = fn()
            _imprimir_tabla(
                f"📊 {nombre}",
                ["Métrica", "Valor"],
                _filas_metricas(metricas, nombre),
            )
        metricas["G_hebbiana_db"] = await _benchmark_hebbiana_db(
            session, iteraciones_db
        )
        metricas["K_semanticizacion"] = await _benchmark_semanticizacion(
            session, iteraciones_db
        )
        metricas["L_buffer"] = await _benchmark_buffer(session, iteraciones_db)
        metricas["M_consolidacion"] = await _benchmark_consolidacion(
            session, iteraciones_db
        )
        await session.rollback()
    await engine.dispose()

    for nombre in (
        "G_hebbiana_db",
        "K_semanticizacion",
        "L_buffer",
        "M_consolidacion",
    ):
        _imprimir_tabla(
            f"📊 {nombre}",
            ["Métrica", "Valor"],
            _filas_metricas(metricas, nombre),
        )

    await _persistir(
        "suite_neuro2",
        f"{iteraciones} iteraciones",
        vars(args),
        metricas,
    )
    print("\n✅ Corrida persistida en benchmarks (suite_neuro2)")


if __name__ == "__main__":
    asyncio.run(main())
