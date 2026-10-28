"""Indexación de fragmentos: troceo + embeddings + persistencia.

Orquesta la capa pura (``src.core.chunking.trocear``), el generador
de embeddings y el repositorio — mismo patrón que la indexación del
evento, una capa más fina. Solo se indexan eventos cuyo contenido
excede el tamaño objetivo: un texto corto ya es atómico, duplicarlo
como "un chunk" no aporta señal.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.chunking import TAMANO_OBJETIVO, trocear
from src.core.embeddings import GeneradorEmbeddings
from src.storage.repositories import EventoRepository


async def indexar_chunks_evento(
    session: AsyncSession,
    evento_id: uuid.UUID,
    contenido: str,
    embeddings: GeneradorEmbeddings,
) -> int:
    """Trocea, embebe y reindexa los fragmentos de un evento.

    Args:
        session: Sesión async de SQLAlchemy (sin commit — queda a
            cargo del llamante).
        evento_id: Evento dueño de los fragmentos.
        contenido: Texto completo del evento.
        embeddings: Generador local para vectorizar los fragmentos.

    Returns:
        Número de fragmentos indexados (0 si el contenido no amerita
        troceo o está vacío).
    """
    piezas = trocear(contenido)
    if len(piezas) <= 1:
        # Contenido atómico: el embedding del evento ya lo representa.
        return 0
    vectores = await embeddings.generar_lote(piezas)
    return await EventoRepository(session).reemplazar_chunks(
        evento_id, piezas, vectores
    )


async def indexar_chunks_lote(
    session: AsyncSession,
    items: list[tuple[uuid.UUID, str]],
    embeddings: GeneradorEmbeddings,
) -> int:
    """Indexa los fragmentos de varios eventos (importaciones/backfill).

    Args:
        session: Sesión async de SQLAlchemy.
        items: Pares ``(evento_id, contenido)``.
        embeddings: Generador local.

    Returns:
        Total de fragmentos indexados.
    """
    total = 0
    for evento_id, contenido in items:
        total += await indexar_chunks_evento(
            session, evento_id, contenido, embeddings
        )
    return total


def requiere_troceo(contenido: str) -> bool:
    """True si el contenido amerita fragmentación."""
    return len(contenido.strip()) > TAMANO_OBJETIVO
