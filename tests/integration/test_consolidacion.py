"""Tests de integración de la consolidación por evocación.

El refuerzo se persiste en segundo plano (write-behind): la búsqueda
es read-only y ``_consultar`` agenda la consolidación al responder.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from src.core.embeddings import GeneradorEmbeddings
from src.mcp import tools
from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_consolidacion"


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Engine a nous_test con tools redirigidas y limpieza final."""
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


async def test_consultar_incrementa_refuerzos_en_segundo_plano(
    motor: AsyncEngine,
) -> None:
    """Cada evocación consolida la huella, fuera del camino caliente."""
    embeddings = GeneradorEmbeddings()
    async with motor.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO eventos (timestamp_unix, dimension, fuente,"
                " contenido, embedding, amplitud) VALUES (:ts, 'cognitiva',"
                " :fuente, :contenido, CAST(:emb AS vector), 0.5)"
            ),
            {
                "ts": 1_800_000_000,
                "fuente": FUENTE_TEST,
                "contenido": "qzx consulta sobre persistencia local zq9",
                "emb": str(
                    await embeddings.generar(
                        "consulta sobre persistencia local"
                    )
                ),
            },
        )

    resultados = await tools._consultar("persistencia local", 5)
    assert resultados
    await tools._drenar_fondo()

    ids = [uuid.UUID(r["evento"]["id"]) for r in resultados]
    async with motor.connect() as conn:
        filas = (
            await conn.execute(
                select(EventoModel.id, EventoModel.refuerzos).where(
                    EventoModel.id.in_(ids)
                )
            )
        ).all()
    assert all(refuerzos >= 1 for _, refuerzos in filas)


async def test_reforzar_sin_ids_no_toca_la_base(
    session: AsyncSession,
) -> None:
    """reforzar([]) es un no-op que retorna 0."""
    repo = EventoRepository(session)
    assert await repo.reforzar([]) == 0


async def test_reforzar_ids_desconocidos_retorna_cero(
    session: AsyncSession,
) -> None:
    """UUIDs inexistentes no rompen el UPDATE."""
    repo = EventoRepository(session)
    assert await repo.reforzar([uuid.uuid4()]) == 0
