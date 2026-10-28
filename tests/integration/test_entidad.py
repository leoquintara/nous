"""Tests E2E de la ficha de entidad (lookup título + grafo + span temporal)."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository, RelacionRepository


async def _pagina(
    session: AsyncSession, titulo: str, cuerpo: str, ts: int
) -> EventoModel:
    repo = EventoRepository(session)
    return await repo.crear(
        EventoModel(
            timestamp_unix=ts,
            dimension="cognitiva",
            fuente="test",
            contenido=f"# {titulo}\n\n{cuerpo}",
        )
    )


async def test_ficha_resuelve_pagina_de_la_entidad(
    session: AsyncSession,
) -> None:
    """El lookup de título resuelve la página que lleva el nombre."""
    repo = EventoRepository(session)
    pagina = await _pagina(
        session, "Oscar Lopez", "Coordinador tecnico.", 1_700_000_000
    )
    await _pagina(
        session,
        "Manual General",
        "Documento que menciona a Oscar en el cuerpo.",
        1_700_000_100,
    )

    hits = await repo.buscar_por_titulo("oscar")

    assert hits
    assert hits[0][0].id == pagina.id


async def test_ficha_lista_vinculos_de_la_card(
    session: AsyncSession,
) -> None:
    """Los vínculos reportan la arista y el título del otro extremo."""
    pagina = await _pagina(
        session, "Oscar Lopez", "Coordinador tecnico.", 1_700_000_000
    )
    otro = await _pagina(
        session, "Kinam", "Proyecto asignado por Oscar.", 1_700_000_100
    )
    rel_repo = RelacionRepository(session)
    await rel_repo.vincular(pagina.id, otro.id, "explicita", 0.9)

    vinculos = await rel_repo.vinculos_de(pagina.id)

    assert len(vinculos) == 1
    relacion, otro_id, titulo = vinculos[0]
    assert relacion.tipo == "explicita"
    assert otro_id == otro.id
    assert "Kinam" in titulo


async def test_ficha_menciones_excluye_la_card(
    session: AsyncSession,
) -> None:
    """Las menciones traen backlinks, no la propia página."""
    repo = EventoRepository(session)
    pagina = await _pagina(
        session, "Oscar Lopez", "Coordinador tecnico.", 1_700_000_000
    )
    mencion = await _pagina(
        session,
        "Minuta Semanal",
        "Oscar asigno los proyectos del sprint.",
        1_700_000_200,
    )

    menciones = await repo.menciones_de("oscar", [pagina.id])

    assert [m.id for m in menciones] == [mencion.id]


async def test_ficha_span_reporta_ventana_temporal(
    session: AsyncSession,
) -> None:
    """El span da la primera y última aparición del nombre."""
    repo = EventoRepository(session)
    await _pagina(
        session, "Nota Vieja", "Primera vez que aparece Oscar.", 1_600_000_000
    )
    await _pagina(
        session, "Nota Nueva", "Ultima actividad de Oscar.", 1_700_000_000
    )
    await _pagina(session, "Otra Nota", "Sin la entidad.", 1_650_000_000)

    primero, ultimo, total = await repo.span_menciones("oscar")

    assert primero == 1_600_000_000
    assert ultimo == 1_700_000_000
    assert total == 2


async def test_ficha_vinculos_omite_eventos_reemplazados(
    session: AsyncSession,
) -> None:
    """Una versión histórica no aparece como vecino de la card."""
    repo = EventoRepository(session)
    pagina = await _pagina(
        session, "Oscar Lopez", "Coordinador tecnico.", 1_700_000_000
    )
    vieja = await _pagina(
        session, "Kinam", "Version vieja del proyecto.", 1_600_000_000
    )
    await repo.marcar_reemplazado(vieja.id, pagina.id)
    rel_repo = RelacionRepository(session)
    await rel_repo.vincular(pagina.id, vieja.id, "explicita", 0.9)

    vinculos = await rel_repo.vinculos_de(pagina.id)

    assert vinculos == []
