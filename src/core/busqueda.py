"""Búsqueda híbrida 4D: tsvector + vectorial + RRF con re-score.

Inspirado en el pipeline de retrieval de gbrain (garrytan/gbrain):
brazos de búsqueda en paralelo, fusión RRF ponderada, re-score que
mezcla RRF con similitud coseno real, y boosts post-fusión. La
contextualización 4D (eventos adyacentes en la línea temporal) es
el diferenciador propio de Nous.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.embeddings import GeneradorEmbeddings
from src.core.modelos import Contexto4D, Dimension, Evento, ResultadoBusqueda
from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository

K_RRF_DEFAULT = 60
PESO_RRF = 0.7
PESO_COSENO = 0.3
BOOST_AMPLITUD = 0.25
SOBREMUESTREO = 4


def fusion_rrf(
    *listas_resultados: list[ResultadoBusqueda],
    pesos: list[float] | None = None,
    k: int = K_RRF_DEFAULT,
    top_n: int = 10,
) -> list[ResultadoBusqueda]:
    """Fusiona múltiples listas de resultados usando Reciprocal Rank Fusion.

    RRF(d) = Σ peso_i / (k + rank_i(d)) donde rank_i es el ranking
    del documento d en la lista i (1-indexed). Sin pesos, todas las
    listas pesan 1.0 (paper original RRF).

    Args:
        *listas_resultados: Listas de resultados a fusionar.
        pesos: Peso opcional por lista (mismo orden que las listas).
        k: Constante de suavizado (default 60).
        top_n: Número máximo de resultados a retornar.

    Returns:
        Lista fusionada ordenada por score RRF descendente.

    Raises:
        ValueError: Si alguna lista está vacía o los pesos no cuadran.
    """
    if not listas_resultados or any(not lista for lista in listas_resultados):
        raise ValueError("Todas las listas de resultados deben ser no vacías")
    if pesos is not None and len(pesos) != len(listas_resultados):
        raise ValueError("Debe haber un peso por cada lista de resultados")

    pesos_reales = pesos or [1.0] * len(listas_resultados)
    scores: dict[str, float] = {}
    representantes: dict[str, ResultadoBusqueda] = {}
    for peso, lista in zip(pesos_reales, listas_resultados, strict=True):
        for rank, resultado in enumerate(lista, start=1):
            scores[resultado.evento_id] = (
                scores.get(resultado.evento_id, 0.0) + peso / (k + rank)
            )
            representantes.setdefault(resultado.evento_id, resultado)

    ordenados = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        replace(representantes[evento_id], score_rrf=score)
        for evento_id, score in ordenados[:top_n]
    ]


class BusquedaHibrida4D:
    """Orquesta la búsqueda híbrida con contextualización temporal 4D."""

    def __init__(
        self, embeddings: GeneradorEmbeddings, cantidad_contexto: int = 3
    ) -> None:
        """Inicializa el orquestador de búsqueda.

        Args:
            embeddings: Generador de embeddings para vectorizar el query.
            cantidad_contexto: Eventos adyacentes por lado a adjuntar.
        """
        self._embeddings = embeddings
        self._cantidad_contexto = cantidad_contexto

    async def buscar(
        self, session: AsyncSession, query: str, top_n: int = 10
    ) -> list[ResultadoBusqueda]:
        """Ejecuta la búsqueda híbrida completa con contexto 4D.

        Args:
            session: Sesión async de SQLAlchemy.
            query: Texto de la consulta.
            top_n: Máximo de resultados finales.

        Returns:
            Resultados ordenados por score_final con contexto temporal.
        """
        vector_query = await self._embeddings.generar(query)
        repo = EventoRepository(session)
        hits_texto, hits_vector = await asyncio.gather(
            repo.buscar_por_texto(query),
            repo.buscar_por_vector(vector_query),
        )

        modelos = _indice_modelos(hits_texto, hits_vector)
        fusionados = _fusionar(hits_texto, hits_vector, top_n)
        if not fusionados:
            return []

        resultados = _re_scored(fusionados, modelos, vector_query)
        resultados = _deduplicar(resultados)[:top_n]
        return await self._contextualizar(repo, resultados)

    async def _contextualizar(
        self, repo: EventoRepository, resultados: list[ResultadoBusqueda]
    ) -> list[ResultadoBusqueda]:
        """Adjunta los eventos adyacentes a cada resultado (en paralelo)."""
        tareas = []
        for r in resultados:
            assert r.evento is not None
            tareas.append(
                repo.adyacentes(r.evento.timestamp_unix, self._cantidad_contexto)
            )
        contextos = await asyncio.gather(*tareas)
        return [
            replace(
                resultado,
                contexto=Contexto4D(
                    previos=tuple(_a_evento(m) for m in previos),
                    siguientes=tuple(_a_evento(m) for m in siguientes),
                ),
            )
            for resultado, (previos, siguientes) in zip(
                resultados, contextos, strict=True
            )
        ]


def _indice_modelos(
    hits_texto: Sequence[tuple[EventoModel, float]],
    hits_vector: Sequence[tuple[EventoModel, float]],
) -> dict[str, EventoModel]:
    """Indexa los modelos ORM recuperados por id para el re-score."""
    return {str(m.id): m for m, _ in (*hits_texto, *hits_vector)}


def _fusionar(
    hits_texto: Sequence[tuple[EventoModel, float]],
    hits_vector: Sequence[tuple[EventoModel, float]],
    top_n: int,
) -> list[ResultadoBusqueda]:
    """Convierte los hits en ResultadoBusqueda y los fusiona con RRF."""
    lista_bm25 = [
        ResultadoBusqueda(evento_id=str(m.id), score_bm25=score)
        for m, score in hits_texto
    ]
    lista_vector = [
        ResultadoBusqueda(evento_id=str(m.id), score_vectorial=score)
        for m, score in hits_vector
    ]
    listas = [lista for lista in (lista_bm25, lista_vector) if lista]
    if not listas:
        return []
    return fusion_rrf(*listas, top_n=top_n * SOBREMUESTREO)


def _re_scored(
    fusionados: list[ResultadoBusqueda],
    modelos: dict[str, EventoModel],
    vector_query: list[float],
) -> list[ResultadoBusqueda]:
    """Mezcla RRF con coseno real y aplica boost por amplitud."""
    rrf_max = max(r.score_rrf for r in fusionados)
    resultados = []
    for r in fusionados:
        modelo = modelos[r.evento_id]
        coseno = _similitud(vector_query, modelo.embedding)
        blend = PESO_RRF * (r.score_rrf / rrf_max) + PESO_COSENO * coseno
        score_final = blend * (1.0 + BOOST_AMPLITUD * modelo.amplitud)
        resultados.append(
            replace(
                r,
                score_vectorial=max(r.score_vectorial, coseno),
                score_final=score_final,
                evento=_a_evento(modelo),
            )
        )
    return sorted(resultados, key=lambda r: r.score_final, reverse=True)


def _deduplicar(
    resultados: list[ResultadoBusqueda],
) -> list[ResultadoBusqueda]:
    """Elimina resultados con contenido normalizado idéntico."""
    vistos: set[str] = set()
    unicos = []
    for r in resultados:
        clave = r.evento.contenido.strip().lower() if r.evento else r.evento_id
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(r)
    return unicos


def _similitud(vector_query: list[float], embedding: list[float] | None) -> float:
    """Calcula la similitud coseno (vectores ya normalizados → producto punto)."""
    if embedding is None or len(embedding) == 0:
        return 0.0
    producto = sum(
        float(q) * float(e)
        for q, e in zip(vector_query, embedding, strict=True)
    )
    return max(-1.0, min(1.0, producto))


def _a_evento(modelo: EventoModel) -> Evento:
    """Convierte un modelo ORM en el dataclass de dominio."""
    return Evento(
        id=str(modelo.id),
        timestamp_unix=modelo.timestamp_unix,
        dimension=Dimension(modelo.dimension),
        contenido=modelo.contenido,
        amplitud=modelo.amplitud,
    )
