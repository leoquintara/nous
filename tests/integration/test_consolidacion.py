"""Tests de integración de la consolidación por evocación."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.busqueda import BusquedaHibrida4D
from src.core.embeddings import GeneradorEmbeddings
from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository


async def test_buscar_incrementa_refuerzos_de_los_resultados(
    session: AsyncSession,
) -> None:
    """Cada evocación consolida la huella: refuerzos crece al devolverla."""
    embeddings = GeneradorEmbeddings()
    repo = EventoRepository(session)
    await repo.crear(
        EventoModel(
            timestamp_unix=1_800_000_000,
            dimension="cognitiva",
            fuente="test",
            contenido="consulta sobre persistencia local",
            embedding=await embeddings.generar(
                "consulta sobre persistencia local"
            ),
        )
    )
    busqueda = BusquedaHibrida4D(embeddings)
    resultados = await busqueda.buscar(session, "persistencia local", top_n=5)
    assert resultados

    ids = [uuid.UUID(r.evento_id) for r in resultados]
    filas = (
        await session.execute(
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
