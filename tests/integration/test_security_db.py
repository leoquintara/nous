"""Tests de seguridad OWASP contra PostgreSQL real (nous_test).

A03 Injection y los clamps de A04 solo se verifican de verdad contra
la base de datos: payloads SQLi/tsquery a través de las funciones de
tools, que son la única puerta a las consultas.
"""

import time
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp import tools
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_security"

PAYLOADS_SQLI = [
    "'; DROP TABLE eventos; --",
    "' OR '1'='1' /*",
    "\\'; DELETE FROM eventos WHERE '1'='1",
    "1 UNION SELECT password FROM users--",
]


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Redirige las tools a nous_test y limpia las filas creadas."""
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


async def _cuenta_eventos(motor: AsyncEngine) -> int:
    """Cuenta filas de eventos: prueba de que la tabla sigue existiendo."""
    async with motor.connect() as conn:
        return int(
            (await conn.execute(text("SELECT count(*) FROM eventos")))
            .scalar_one()
        )


@pytest.mark.parametrize("payload", PAYLOADS_SQLI)
async def test_a03_sqli_en_busqueda_no_ejecuta_codigo(
    motor: AsyncEngine, payload: str
) -> None:
    """A03: payloads SQLi en q viajan parametrizados; la tabla sobrevive."""
    await tools._consultar(payload, 5)
    # La tabla sigue ahí y accesible — el payload no se ejecutó.
    assert await _cuenta_eventos(motor) >= 0


@pytest.mark.parametrize("payload", PAYLOADS_SQLI)
async def test_a03_sqli_en_contenido_se_guarda_literal(
    motor: AsyncEngine, payload: str
) -> None:
    """A03: el contenido SQLi se persiste como texto, nunca como SQL."""
    respuesta = await tools._recordar(
        payload, "cognitiva", FUENTE_TEST, 0.5, None, None
    )
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text("SELECT contenido FROM eventos WHERE id = :id"),
                {"id": respuesta["id"]},
            )
        ).one()
    assert fila[0] == payload
    assert await _cuenta_eventos(motor) >= 1


async def test_a03_operadores_tsquery_no_rompen_busqueda(
    motor: AsyncEngine,
) -> None:
    """A03: metacaracteres de tsquery llegan escapados vía plainto."""
    await tools._consultar("a!:*&|b' (paren", 5)
    assert await _cuenta_eventos(motor) >= 0


async def test_a04_limite_desbordado_se_acota(motor: AsyncEngine) -> None:
    """A04: limite=1_000_000 no desborda — la capa aplica el tope 500."""
    ahora = int(time.time())
    eventos = await tools._linea(0, ahora, 1_000_000)
    assert len(eventos) <= 500


async def test_a04_top_n_desbordado_se_acota(motor: AsyncEngine) -> None:
    """A04: top_n=9999 se acota a 50 en la búsqueda híbrida."""
    resultados = await tools._consultar("memoria", 9999)
    assert len(resultados) <= 50


async def test_a04_uuid_inexistente_no_es_inyeccion(
    motor: AsyncEngine,
) -> None:
    """Un UUID ajeno simplemente no existe; no hay path de inyección."""
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text("SELECT id FROM eventos WHERE id = :id"),
                {"id": str(uuid.uuid4())},
            )
        ).first()
    assert fila is None
