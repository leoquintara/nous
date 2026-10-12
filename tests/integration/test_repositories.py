"""Tests de integración de los repositorios.

Requieren un PostgreSQL real accesible (NOUS_TEST_DATABASE_URL).
Se saltan automáticamente si la DB no está disponible.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository

EMBEDDING_DIM = 384


def _evento(timestamp_unix: int, contenido: str, **kwargs: object) -> EventoModel:
    """Factory de eventos de prueba con defaults mínimos."""
    return EventoModel(
        timestamp_unix=timestamp_unix,
        dimension="cognitiva",
        fuente="test",
        contenido=contenido,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_crear_y_obtener_evento(session: AsyncSession) -> None:
    """Verifica que un evento se persiste y se recupera por id."""
    repo = EventoRepository(session)
    evento = await repo.crear(
        _evento(1_700_000_000, "decidí usar PostgreSQL para nous")
    )

    recuperado = await repo.obtener(evento.id)

    assert recuperado is not None
    assert recuperado.contenido == "decidí usar PostgreSQL para nous"
    assert recuperado.dimension == "cognitiva"


async def test_buscar_por_texto_encuentra_evento(session: AsyncSession) -> None:
    """Verifica que la búsqueda tsvector encuentra eventos por contenido."""
    repo = EventoRepository(session)
    await repo.crear(_evento(1_700_000_000, "medité veinte minutos hoy"))
    await repo.crear(_evento(1_700_000_100, "hice tres commits en el repo"))

    resultados = await repo.buscar_por_texto("meditar")

    assert len(resultados) == 1
    assert "medité" in resultados[0][0].contenido
    assert resultados[0][1] > 0.0


async def test_buscar_por_vector_ordena_por_similitud(session: AsyncSession) -> None:
    """Verifica que la búsqueda coseno ordena por cercanía del vector."""
    repo = EventoRepository(session)
    vector_cercano = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    vector_lejano = [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2)
    await repo.crear(
        _evento(1_700_000_000, "evento cercano", embedding=vector_cercano)
    )
    await repo.crear(
        _evento(1_700_000_100, "evento lejano", embedding=vector_lejano)
    )

    resultados = await repo.buscar_por_vector(vector_cercano)

    assert len(resultados) == 2
    assert resultados[0][0].contenido == "evento cercano"
    assert resultados[0][1] > resultados[1][1]


async def test_adyacentes_retorna_previos_y_siguientes(
    session: AsyncSession,
) -> None:
    """Verifica que adyacentes devuelve contexto temporal en ambas direcciones."""
    repo = EventoRepository(session)
    await repo.crear(_evento(1_700_000_000, "pasado lejano"))
    await repo.crear(_evento(1_700_000_050, "pasado cercano"))
    await repo.crear(_evento(1_700_000_200, "futuro cercano"))

    previos, siguientes = await repo.adyacentes(1_700_000_100, cantidad=5)

    assert [e.contenido for e in previos] == ["pasado cercano", "pasado lejano"]
    assert [e.contenido for e in siguientes] == ["futuro cercano"]
