"""Tests de separación de patrones (Ronda 3 — dedup semántico).

Con indexación write-behind, ambos eventos se insertan de inmediato y
la deduplicación ocurre en segundo plano: la huella nueva se fusiona
en la existente (refuerzo + amplitud máxima) dentro de la ventana.
"""

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

    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente = :fuente"),
            {"fuente": FUENTE_TEST},
        )
    monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
    # El worker de indexación vive en un thread aparte y pide su
    # propio engine vía crear_engine: apuntarlo también a nous_test.
    monkeypatch.setattr(
        tools, "crear_engine",
        lambda: create_async_engine(TEST_DB_URL),
    )
    yield engine
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente = :fuente"),
            {"fuente": FUENTE_TEST},
        )
    await engine.dispose()


async def _existe(motor: AsyncEngine, evento_id: str) -> bool:
    """Indica si la fila sigue en la tabla tras el drenado."""
    async with motor.connect() as conn:
        return bool(
            (
                await conn.execute(
                    text("SELECT 1 FROM eventos WHERE id = :id"),
                    {"id": evento_id},
                )
            ).scalar_one_or_none()
        )


async def test_recordar_duplicado_en_ventana_consolida_el_existente(
    motor: AsyncEngine,
) -> None:
    """El segundo recordar casi idéntico se fusiona: refuerza el primero."""
    contenido = "qzx anoté la idea del experimento de la terraza zq9"
    primero = await tools.recordar_evento(
        contenido, dimension="cognitiva", fuente=FUENTE_TEST
    )
    await tools._drenar_fondo()
    segundo = await tools.recordar_evento(
        contenido, dimension="cognitiva", fuente=FUENTE_TEST
    )
    await tools._drenar_fondo()

    async with motor.connect() as conn:
        refuerzos = (
            await conn.execute(
                text("SELECT refuerzos FROM eventos WHERE id = :id"),
                {"id": primero["id"]},
            )
        ).scalar_one()

    assert refuerzos >= 1
    assert not await _existe(motor, segundo["id"])


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
    await tools._drenar_fondo()

    assert nuevo["id"] != viejo["id"]
    assert await _existe(motor, viejo["id"])
    assert await _existe(motor, nuevo["id"])


async def test_recordar_indexa_el_embedding_en_segundo_plano(
    motor: AsyncEngine,
) -> None:
    """La respuesta es inmediata y el vector llega tras el drenado."""
    respuesta = await tools.recordar_evento(
        "qzx idea única sobre jardines verticales zq9",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    assert respuesta["indexado"] is False

    await tools._drenar_fondo()
    async with motor.connect() as conn:
        tiene_vector = (
            await conn.execute(
                text(
                    "SELECT embedding IS NOT NULL FROM eventos "
                    "WHERE id = :id"
                ),
                {"id": respuesta["id"]},
            )
        ).scalar_one()
    assert tiene_vector is True
