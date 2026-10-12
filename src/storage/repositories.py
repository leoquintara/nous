"""Repositorios de acceso a datos (patrón Repository).

Encapsulan las consultas sobre los modelos. La lógica de negocio
vive en src/core/, nunca aquí.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import EventoModel


class EventoRepository:
    """Acceso a datos de los eventos de la línea de universo."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
        """
        self._session = session

    async def crear(self, evento: EventoModel) -> EventoModel:
        """Persiste un evento y lo retorna con su id asignado.

        Args:
            evento: Instancia de EventoModel a persistir.

        Returns:
            El evento persistido con id y campos server-default poblados.
        """
        self._session.add(evento)
        await self._session.flush()
        await self._session.refresh(evento)
        return evento

    async def obtener(self, evento_id: uuid.UUID) -> EventoModel | None:
        """Retorna el evento por id.

        Args:
            evento_id: UUID del evento.

        Returns:
            El evento o None si no existe.
        """
        return await self._session.get(EventoModel, evento_id)

    async def listar_por_rango(
        self, desde: int, hasta: int, limite: int = 100
    ) -> Sequence[EventoModel]:
        """Lista eventos en el rango temporal [desde, hasta].

        Args:
            desde: Timestamp Unix de inicio (inclusive).
            hasta: Timestamp Unix de fin (inclusive).
            limite: Máximo de eventos a retornar.

        Returns:
            Eventos ordenados por timestamp ascendente.
        """
        stmt = (
            select(EventoModel)
            .where(EventoModel.timestamp_unix.between(desde, hasta))
            .order_by(EventoModel.timestamp_unix)
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def buscar_por_texto(
        self, query: str, limite: int = 20
    ) -> Sequence[tuple[EventoModel, float]]:
        """Búsqueda full-text sobre contenido vía tsvector (español).

        Args:
            query: Texto a buscar (plainto_tsquery).
            limite: Máximo de resultados.

        Returns:
            Tuplas (evento, ts_rank) ordenadas por rank descendente.
        """
        tsquery = func.plainto_tsquery("spanish", query)
        rank = func.ts_rank(EventoModel.contenido_tsv, tsquery).label("score")
        stmt = (
            select(EventoModel, rank)
            .where(EventoModel.contenido_tsv.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return [
            (fila["EventoModel"], float(fila["score"]))
            for fila in resultado.mappings().all()
        ]

    async def buscar_por_vector(
        self, vector: list[float], limite: int = 20
    ) -> Sequence[tuple[EventoModel, float]]:
        """Búsqueda por similitud coseno sobre el embedding.

        Args:
            vector: Vector de consulta (misma dimensión que el embedding).
            limite: Máximo de resultados.

        Returns:
            Tuplas (evento, similitud_coseno) ordenadas por similitud
            descendente.
        """
        distancia = EventoModel.embedding.cosine_distance(vector).label(
            "distancia"
        )
        stmt = (
            select(EventoModel, distancia)
            .where(EventoModel.embedding.is_not(None))
            .order_by(distancia)
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return [
            (fila["EventoModel"], 1.0 - float(fila["distancia"]))
            for fila in resultado.mappings().all()
        ]

    async def adyacentes(
        self, timestamp_unix: int, cantidad: int = 3
    ) -> tuple[Sequence[EventoModel], Sequence[EventoModel]]:
        """Retorna los eventos adyacentes en la línea temporal.

        Args:
            timestamp_unix: Punto temporal de referencia.
            cantidad: Máximo de eventos por lado.

        Returns:
            Tupla (eventos_previos, eventos_siguientes), cada una ordenada
            desde el más cercano al timestamp.
        """
        stmt_previos = (
            select(EventoModel)
            .where(EventoModel.timestamp_unix < timestamp_unix)
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(cantidad)
        )
        stmt_siguientes = (
            select(EventoModel)
            .where(EventoModel.timestamp_unix > timestamp_unix)
            .order_by(EventoModel.timestamp_unix)
            .limit(cantidad)
        )
        previos = (await self._session.execute(stmt_previos)).scalars().all()
        siguientes = (await self._session.execute(stmt_siguientes)).scalars().all()
        return previos, siguientes
