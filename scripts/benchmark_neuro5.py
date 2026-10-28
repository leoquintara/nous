"""Suite de benchmarks Neuro-5 (ciclo de sesión completo).

Complementa a ``benchmark_neuro4``. Mide las ocho piezas nuevas —
cada una donde vive: puro en µs, repo con rollback:

AC. Bootstrap — ``empaquetar_bootstrap`` + ``formatear_snapshot``.
AD. Verificación — ``veredicto_censores`` puro + ``listar_censores``.
AE. Calibración — ``reporte_calibracion`` puro + predicción DB.
AF. Handoff — ``generar_handoff`` puro + sesión abrir/cerrar DB.
AG. Staged — ``listar_staged`` + ``aprobar_staged`` (UPDATE batch).
AH. Scrub — ``redactar_secretos`` puro.
AI. Grace floor — ``decidir_consolidacion`` puro con piso.

El efecto neto (bootstrap cabe en el presupuesto, el censor veta,
Brier mide honestidad) es semántica — lo medible es el costo de
decidirlo y persistirlo, que es lo que reportan estas secciones.

Uso:
    poetry run python -m scripts.benchmark_neuro5 [--iteraciones N]
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
from src.core.decision.scrub import redactar_secretos
from src.core.memoria.bootstrap import (
    SeccionBootstrap,
    empaquetar_bootstrap,
    formatear_snapshot,
)
from src.core.memoria.calibracion import (
    PrediccionResuelta,
    reporte_calibracion,
)
from src.core.memoria.consolidacion import (
    EventoConsolidable,
    decidir_consolidacion,
)
from src.core.memoria.handoff import generar_handoff
from src.core.memoria.tipos import TipoMemoria
from src.core.memoria.verificacion import (
    CensorCoincidente,
    veredicto_censores,
)
from src.core.modelos import Dimension
from src.storage.database import crear_engine, get_session
from src.storage.models import EventoModel, PrediccionModel, SesionModel
from src.storage.repositories import EventoRepository


def _benchmark_bootstrap(iteraciones: int) -> dict[str, float]:
    """Sección AC (puro): empaquetar un pack contra presupuesto."""
    secciones = [
        SeccionBootstrap(
            clave=f"s{i}",
            titulo=f"Seccion {i}",
            lineas=tuple(f"linea {j} de la seccion {i}" for j in range(20)),
        )
        for i in range(6)
    ]
    us = _cronometrar(empaquetar_bootstrap, iteraciones, secciones, 4000)
    plan = empaquetar_bootstrap(secciones, 4000)
    us_snap = _cronometrar(formatear_snapshot, iteraciones, plan)
    return {
        "empaquetar_bootstrap (µs)": round(us, 3),
        "formatear_snapshot (µs)": round(us_snap, 3),
        "usado (chars)": float(plan.usado),
    }


def _benchmark_verificacion(iteraciones: int) -> dict[str, float]:
    """Sección AD (puro): el veredicto de censores cuesta nada."""
    coincidencias = [
        CensorCoincidente(
            id=str(uuid.uuid4()),
            contenido=f"regla {i}",
            severidad="bloquear" if i % 3 == 0 else "advertir",
            dominio="test",
            similitud=0.8,
        )
        for i in range(8)
    ]
    us = _cronometrar(veredicto_censores, iteraciones, coincidencias)
    return {"veredicto_censores (µs)": round(us, 3)}


async def _benchmark_censores_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección AD (DB): listar los guardrails del workspace."""
    repo = EventoRepository(session)
    lat: list[float] = []
    n = 0
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        censores = await repo.listar_censores()
        lat.append((time.perf_counter() - inicio) * 1000)
        n = len(censores)
    return {
        "censores vigentes": float(n),
        "listar_censores (ms)": round(
            sum(lat) / len(lat), 3
        ),
    }


def _benchmark_calibracion(iteraciones: int) -> dict[str, float]:
    """Sección AE (puro): Brier + curva sobre 1K predicciones."""
    resueltas = [
        PrediccionResuelta(confianza=0.5 + (i % 50) / 100, acierto=i % 2 == 0)
        for i in range(1000)
    ]
    us = _cronometrar(reporte_calibracion, iteraciones, resueltas)
    return {"reporte_calibracion 1K (µs)": round(us, 3)}


async def _benchmark_prediccion_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección AE (DB): registrar y resolver una predicción."""
    from src.storage.repositories import PrediccionRepository

    repo = PrediccionRepository(session, None)  # type: ignore[arg-type]
    lat_reg: list[float] = []
    lat_res: list[float] = []
    ids: list[uuid.UUID] = []
    for i in range(iteraciones):
        inicio = time.perf_counter()
        p = await repo.crear(
            PrediccionModel(
                workspace_id=uuid.UUID(
                    "00000000-0000-0000-0000-000000000002"
                ),
                afirmacion=f"bench prediccion {i}",
                confianza=0.7,
                creado_en=int(time.time()),
            )
        )
        lat_reg.append((time.perf_counter() - inicio) * 1000)
        ids.append(p.id)
        inicio = time.perf_counter()
        await repo.resolver(
            p.id, acierto=True, resuelto_en=int(time.time())
        )
        lat_res.append((time.perf_counter() - inicio) * 1000)
    # Los registros mueren con el rollback del runner — sin cleanup.
    return {
        "registrar (ms)": round(sum(lat_reg) / len(lat_reg), 3),
        "resolver (ms)": round(sum(lat_res) / len(lat_res), 3),
    }


def _benchmark_handoff(iteraciones: int) -> dict[str, float]:
    """Sección AF (puro): componer el markdown de continuidad."""
    us = _cronometrar(
        generar_handoff,
        iteraciones,
        "bench",
        1_800_000_000,
        1_800_003_600,
        "resumen de la sesión de benchmark",
        [{"tipo": "hecho", "contenido": f"escrito {i}"} for i in range(15)],
        [{"contenido": f"loop {i}", "dias_abierto": i} for i in range(10)],
        [{"contenido": f"nucleo {i}"} for i in range(8)],
        [
            {"contenido": f"regla {i}", "severidad": "bloquear"}
            for i in range(4)
        ],
    )
    return {"generar_handoff (µs)": round(us, 3)}


async def _benchmark_sesion_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección AF (DB): abrir y cerrar una sesión persistiendo handoff."""
    from src.storage.repositories import SesionRepository

    repo = SesionRepository(session, None)  # type: ignore[arg-type]
    lat_abrir: list[float] = []
    lat_cerrar: list[float] = []
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        s = await repo.crear(
            SesionModel(
                agente="bench",
                inicio=int(time.time()),
            )
        )
        lat_abrir.append((time.perf_counter() - inicio) * 1000)
        inicio = time.perf_counter()
        s.fin = int(time.time()) + 60
        s.handoff = "# handoff"
        s.eventos = 5
        await session.flush()
        lat_cerrar.append((time.perf_counter() - inicio) * 1000)
    return {
        "crear sesion (ms)": round(
            sum(lat_abrir) / len(lat_abrir), 3
        ),
        "cerrar sesion (ms)": round(
            sum(lat_cerrar) / len(lat_cerrar), 3
        ),
    }


async def _benchmark_staged_db(
    session: AsyncSession, iteraciones: int
) -> dict[str, float]:
    """Sección AG (DB): listar staged + aprobación batch."""
    repo = EventoRepository(session)
    # Semilla: 50 propuestas staged (el rollback las deshace).
    eventos: list[EventoModel] = []
    for i in range(50):
        ev = EventoModel(
            timestamp_unix=int(time.time()),
            dimension="cognitiva",
            tipo="hecho",
            contenido=f"bench staged {i}",
            fuente="bench",
            staged=True,
            workspace_id=uuid.UUID(
                "00000000-0000-0000-0000-000000000002"
            ),
        )
        session.add(ev)
        eventos.append(ev)
    await session.flush()
    ids = [ev.id for ev in eventos]
    lat_list: list[float] = []
    for _ in range(iteraciones):
        inicio = time.perf_counter()
        staged = await repo.listar_staged(50)
        lat_list.append((time.perf_counter() - inicio) * 1000)
    inicio = time.perf_counter()
    aprobados = await repo.aprobar_staged(ids)
    lat_aprobar = (time.perf_counter() - inicio) * 1000
    return {
        "staged sembradas": float(len(staged)),
        "listar_staged (ms)": round(sum(lat_list) / len(lat_list), 3),
        f"aprobar_staged x{aprobados} (ms)": round(lat_aprobar, 3),
    }


def _benchmark_scrub(iteraciones: int) -> dict[str, float]:
    """Sección AH (puro): redactar secretos sobre un payload típico."""
    payload = (
        "contexto de la sesión con api_key=sk-abcdefghij1234567890 y "
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig además texto normal "
        "de memoria que no debe tocarse " * 5
    )
    us = _cronometrar(redactar_secretos, iteraciones, payload)
    return {"redactar_secretos ~1KB (µs)": round(us, 3)}


def _benchmark_grace_floor(iteraciones: int) -> dict[str, float]:
    """Sección AI (puro): decidir_consolidacion sobre 2K eventos."""
    ahora = 1_800_000_000
    eventos = [
        EventoConsolidable(
            id=str(uuid.uuid4()),
            timestamp_unix=ahora - (i % 60) * 86400,
            dimension=Dimension.COGNITIVA,
            tipo="evento",
            amplitud=0.3 + (i % 7) * 0.1,
            refuerzos=i % 4,
            weight=1.0,
            tipo_memoria=TipoMemoria.EPISODICA,
            ultimo_acceso=(
                ahora - (i % 30) * 86400 if i % 3 else None
            ),
            archivado=False,
            curado=False,
        )
        for i in range(2000)
    ]
    us = _cronometrar(decidir_consolidacion, iteraciones, eventos, ahora)
    return {
        "decidir_consolidacion 2K (µs)": round(us / 1000, 3)
    }


async def main() -> None:
    """Ejecuta la suite Neuro-5 completa y persiste las métricas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteraciones", type=int, default=100)
    args = parser.parse_args()
    iteraciones = args.iteraciones
    iteraciones_db = max(10, iteraciones // 4)

    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )

    puras: dict[str, Callable[[int], dict[str, float]]] = {
        "AC_bootstrap": _benchmark_bootstrap,
        "AD_verificacion": _benchmark_verificacion,
        "AE_calibracion": _benchmark_calibracion,
        "AF_handoff": _benchmark_handoff,
        "AH_scrub": _benchmark_scrub,
        "AI_grace_floor": _benchmark_grace_floor,
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
        metricas["AD_censores_db"] = await _benchmark_censores_db(
            session, iteraciones_db
        )
        metricas["AE_prediccion_db"] = await _benchmark_prediccion_db(
            session, iteraciones_db
        )
        metricas["AF_sesion_db"] = await _benchmark_sesion_db(
            session, iteraciones_db
        )
        metricas["AG_staged_db"] = await _benchmark_staged_db(
            session, iteraciones_db
        )
        await session.rollback()
    await engine.dispose()

    for nombre in (
        "AD_censores_db",
        "AE_prediccion_db",
        "AF_sesion_db",
        "AG_staged_db",
    ):
        _imprimir_tabla(
            f"📊 {nombre}",
            ["Métrica", "Valor"],
            _filas_metricas(metricas, nombre),
        )

    await _persistir(
        "suite_neuro5",
        f"{iteraciones} iteraciones",
        vars(args),
        metricas,
    )
    print("\n✅ Corrida persistida en benchmarks (suite_neuro5)")


if __name__ == "__main__":
    asyncio.run(main())
