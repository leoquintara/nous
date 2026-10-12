"""Test E2E de la búsqueda híbrida 4D (DB real + embeddings reales)."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.busqueda import BusquedaHibrida4D
from src.core.embeddings import GeneradorEmbeddings
from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository

TEXTOS = [
    "decidí usar PostgreSQL como base de datos de nous",
    "comí pasta al mediodía con la familia",
    "reflexión profunda sobre la consciencia y el tiempo",
]


async def test_buscar_retorna_resultados_con_contexto_4d(
    session: AsyncSession,
) -> None:
    """Verifica el pipeline completo: fusión, re-score y contexto 4D."""
    embeddings = GeneradorEmbeddings()
    repo = EventoRepository(session)
    vectores = await embeddings.generar_lote(TEXTOS)
    for indice, (texto, vector) in enumerate(zip(TEXTOS, vectores, strict=True)):
        await repo.crear(
            EventoModel(
                timestamp_unix=1_700_000_000 + indice * 100,
                dimension="cognitiva",
                fuente="test",
                contenido=texto,
                embedding=vector,
            )
        )

    busqueda = BusquedaHibrida4D(embeddings)
    resultados = await busqueda.buscar(
        session, "reflexión sobre la mente", top_n=2
    )

    assert resultados
    top = resultados[0]
    assert top.evento is not None
    assert "reflexión" in top.evento.contenido
    assert top.score_final > 0.0
    assert top.contexto is not None
    assert len(top.contexto.previos) >= 2


async def test_buscar_sin_coincidencias_retorna_lista_vacia(
    session: AsyncSession,
) -> None:
    """Verifica que una query sin hits retorna lista vacía sin error."""
    busqueda = BusquedaHibrida4D(GeneradorEmbeddings())
    resultados = await busqueda.buscar(session, "xyzzy plugh inexistente")
    assert resultados == []
