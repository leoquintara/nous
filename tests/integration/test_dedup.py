"""Tests de separación de patrones (Ronda 3 — dedup semántico)."""

import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp import tools
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_dedup"


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Engine a nous_test con limpieza de las filas del test."""
    engine = create_async_engine(TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OSError:
        await engine.dispose()
        pytest.skip("PostgreSQL de test no disponible")

    monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente = :fuente"),
            {"fuente": FUENTE_TEST},
        )
    await engine.dispose()


async def test_recordar_duplicado_en_ventana_consolida_el_existente(
    motor: AsyncEngine,
) -> None:
    """Un segundo recordar casi idéntico no duplica: refuerza el primero."""
    contenido = "qzx anoté la idea del experimento de la terraza zq9"
    primero = await tools.recordar_evento(
        contenido, dimension="cognitiva", fuente=FUENTE_TEST
    )
    segundo = await tools.recordar_evento(
        contenido, dimension="cognitiva", fuente=FUENTE_TEST
    )

    assert segundo.get("deduplicado") is True
    assert segundo["id"] == primero["id"]

    async with motor.connect() as conn:
        refuerzos = (
            await conn.execute(
                text("SELECT refuerzos FROM eventos WHERE id = :id"),
                {"id": primero["id"]},
            )
        ).scalar_one()
    assert refuerzos == 1


async def test_recordar_similar_fuera_de_ventana_crea_huella_nueva(
    motor: AsyncEngine,
) -> None:
    """Eventos recurrentes en momentos distintos no se deduplican."""
    contenido = "qzx sesión de meditación matutina completada zq9"
    ahora = int(time.time())
    viejo = await tools.recordar_evento(
        contenido,
        dimension="somatica",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 7200,
    )
    nuevo = await tools.recordar_evento(
        contenido,
        dimension="somatica",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora,
    )

    assert "deduplicado" not in nuevo
    assert nuevo["id"] != viejo["id"]
