"""Tests de poda sináptica (Ronda 5 — homeostasis, Tononi & Cirelli)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.mcp import tools
from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_poda"


@pytest_asyncio.fixture(autouse=True)
async def limpiar() -> AsyncIterator[None]:
    """Borra las filas del test tras el rollback del fixture session.

    Corre sobre su propio engine después de que la sesión del test
    cerró su transacción, así elimina también lo que otros tests
    commitearon por conexiones paralelas.
    """
    yield
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente = :f"),
            {"f": FUENTE_TEST},
        )
    await engine.dispose()


def _modelo(ts: int, amp: float, ref: int) -> EventoModel:
    return EventoModel(
        timestamp_unix=ts,
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        contenido=f"evento {ts}",
        amplitud=amp,
        refuerzos=ref,
    )


async def _sembrar(repo: EventoRepository) -> None:
    for ts, amp, ref in [
        (1_000_000_000, 0.1, 0),   # viejo, débil, olvidado → podable
        (1_000_000_100, 0.9, 0),   # viejo pero intenso → se conserva
        (1_000_000_200, 0.1, 3),   # viejo, débil pero consolidado → se conserva
        (1_800_000_000, 0.1, 0),   # reciente y débil → se conserva
    ]:
        await repo.crear(_modelo(ts, amp, ref))


async def test_contar_podables_solo_cuenta_huellas_debiles_y_viejas(
    session: AsyncSession,
) -> None:
    """La poda respeta amplitud alta, consolidación y recencia."""
    repo = EventoRepository(session)
    await _sembrar(repo)

    assert await repo.contar_podables(
        antes_de=1_500_000_000, amplitud_max=0.3, refuerzos_max=0
    ) == 1


async def test_podar_elimina_solo_los_candidatos(
    session: AsyncSession,
) -> None:
    """DELETE real: una fila podada, tres conservadas."""
    repo = EventoRepository(session)
    await _sembrar(repo)

    eliminados = await repo.podar(
        antes_de=1_500_000_000, amplitud_max=0.3, refuerzos_max=0
    )
    assert eliminados == 1
    restantes = (
        await session.execute(
            select(func.count(EventoModel.id)).where(
                EventoModel.fuente == FUENTE_TEST
            )
        )
    ).scalar_one()
    assert restantes == 3


async def test_obtener_episodios_agrupa_por_huecos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La tool expone la segmentación: ráfaga + gap + ráfaga = 2 episodios."""
    engine = create_async_engine(TEST_DB_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as otra:
        repo = EventoRepository(otra)
        for ts in [1000, 2000, 52_000]:
            await repo.crear(_modelo(ts, 0.5, 0))
        await otra.commit()
    monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
    # El worker de indexación vive en un thread aparte y pide su
    # propio engine vía crear_engine: apuntarlo también a nous_test.
    monkeypatch.setattr(
        tools, "crear_engine",
        lambda: create_async_engine(TEST_DB_URL),
    )

    episodios = await tools._episodios(0, 100_000, gap_max_segundos=14_400)

    assert len(episodios) == 2
    assert episodios[0]["eventos"] == 2
    assert episodios[1]["eventos"] == 1
    await engine.dispose()
