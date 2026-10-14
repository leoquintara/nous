"""Tests de integración del registro de llamadas contra PostgreSQL real."""

import time
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core import trazas
from src.storage.repositories import LlamadaRepository
from tests.integration.conftest import TEST_DB_URL


def _fila(**kw: object) -> dict:
    """Fila válida de llamada con defaults arbitrarios."""
    base = {
        "ts": int(time.time()),
        "metodo": "GET",
        "ruta": "/api/test",
        "origen": "dashboard",
        "agente": "pytest",
        "latencia_ms": 5,
        "status": 200,
        "es_ping": False,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_insertar_y_listar_devuelve_mas_reciente_primero(
    session: AsyncSession,
) -> None:
    """El listado ordena por ts descendente (rollback del fixture limpia)."""
    repo = LlamadaRepository(session)
    await repo.insertar_lote(
        [_fila(ts=1, ruta="/ruta-test"), _fila(ts=2, ruta="/ruta-test")]
    )
    filas = await repo.listar(limite=10, ruta="/ruta-test")
    assert [f.ts for f in filas] == [2, 1]


@pytest.mark.asyncio
async def test_stats_agrega_por_ruta_origen_y_pings(
    session: AsyncSession,
) -> None:
    """Los agregados cuentan llamadas, latencias y pings."""
    marca = f"origen-{uuid.uuid4().hex[:8]}"
    ruta = f"/api/test-{uuid.uuid4().hex[:8]}"
    repo = LlamadaRepository(session)
    await repo.insertar_lote(
        [
            _fila(ruta=ruta, origen=marca, latencia_ms=10),
            _fila(ruta=ruta, origen=marca, latencia_ms=30),
            _fila(origen=marca, es_ping=True),
        ]
    )
    stats = await repo.stats()
    origen = next(o for o in stats["por_origen"] if o["origen"] == marca)
    assert origen["llamadas"] == 3
    fila_ruta = next(r for r in stats["por_ruta"] if r["ruta"] == ruta)
    assert fila_ruta["latencia_media"] == 20
    assert stats["pings"]["total"] >= 1
    assert stats["pings"]["ultimo"] is not None


@pytest.mark.asyncio
async def test_drenar_persiste_el_buffer(session: AsyncSession) -> None:
    """drenar() vacía el deque a la tabla (la sesión hace rollback)."""
    trazas._buffer.clear()
    trazas.registrar("POST", "/api/ping", "cli", "ua", 1, 200, True)
    # drenar usa su propia sesión sobre el engine — el INSERT queda
    # committed fuera del rollback del fixture, así que insertamos con
    # un marcador único y verificamos lectura en esta misma sesión vía
    # engine real. Para mantener aislamiento, drenamos y borramos.
    engine = create_async_engine(TEST_DB_URL)
    try:
        n = await trazas.drenar(engine)
        assert n >= 1
        assert trazas.pendientes() == 0
    finally:
        from sqlalchemy import delete

        from src.storage.database import get_session
        from src.storage.models import LlamadaModel

        async with get_session(engine) as s:
            await s.execute(
                delete(LlamadaModel).where(LlamadaModel.origen == "cli")
            )
            await s.commit()
        await engine.dispose()
