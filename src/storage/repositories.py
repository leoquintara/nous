"""Repositorios de acceso a datos (patrón Repository).

Encapsulan las consultas sobre los modelos. La lógica de negocio
vive en src/core/, nunca aquí.
"""

import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import delete, func, literal, select, union_all, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import (
    BenchmarkRunModel,
    EventoModel,
    ProyeccionModel,
)


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
            El flush emite INSERT ... RETURNING, que ya trae de vuelta
            las columnas con server_default; un refresh extra sería un
            roundtrip redundante.
        """
        self._session.add(evento)
        await self._session.flush()
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

    async def reforzar(self, eventos_ids: list[uuid.UUID]) -> int:
        """Incrementa el contador de consolidación de los eventos evocados.

        Cada evocación fortalece la huella (reconsolidación): los
        eventos más consultados decaen más lento en el ranking.

        Args:
            eventos_ids: UUIDs de los eventos devueltos al agente.

        Returns:
            Número de filas actualizadas.
        """
        if not eventos_ids:
            return 0
        stmt = (
            update(EventoModel)
            .where(EventoModel.id.in_(eventos_ids))
            .values(refuerzos=EventoModel.refuerzos + 1)
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
        return resultado.rowcount

    async def contar_podables(
        self, antes_de: int, amplitud_max: float, refuerzos_max: int
    ) -> int:
        """Cuenta los eventos candidatos a poda sin eliminarlos.

        Args:
            antes_de: Timestamp Unix; solo eventos anteriores.
            amplitud_max: Poda solo huellas con amplitud <= este valor.
            refuerzos_max: Poda solo huellas con refuerzos <= este valor.

        Returns:
            Número de eventos que ``podar`` eliminaría.
        """
        stmt = select(func.count(EventoModel.id)).where(
            EventoModel.timestamp_unix < antes_de,
            EventoModel.amplitud <= amplitud_max,
            EventoModel.refuerzos <= refuerzos_max,
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def podar(
        self, antes_de: int, amplitud_max: float, refuerzos_max: int
    ) -> int:
        """Elimina las huellas débiles y olvidadas (poda sináptica).

        Args:
            antes_de: Timestamp Unix; solo eventos anteriores.
            amplitud_max: Elimina solo eventos con amplitud <= este valor.
            refuerzos_max: Elimina solo eventos con refuerzos <= este valor.

        Returns:
            Número de filas eliminadas.
        """
        stmt = delete(EventoModel).where(
            EventoModel.timestamp_unix < antes_de,
            EventoModel.amplitud <= amplitud_max,
            EventoModel.refuerzos <= refuerzos_max,
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
        return resultado.rowcount

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

    async def adyacentes_lote(
        self, marcas: list[int], cantidad: int = 3
    ) -> dict[int, tuple[list[EventoModel], list[EventoModel]]]:
        """Trae los adyacentes de todas las marcas en una sola consulta.

        Equivalente a llamar ``adyacentes`` por cada marca, pero en un
        único roundtrip: un UNION ALL de subconsultas acotadas con
        ``LIMIT`` por lado (índice ix_eventos_timestamp).

        Args:
            marcas: Timestamps Unix de referencia (uno por resultado).
            cantidad: Máximo de eventos por lado y por marca.

        Returns:
            Mapa posición_en_marcas → (previos, siguientes), cada lista
            ordenada desde el más cercano a la marca.
        """
        if not marcas:
            return {}
        brazos = []
        for i, ts in enumerate(marcas):
            brazos.append(
                select(
                    EventoModel,
                    literal(i).label("ancla"),
                    literal(0).label("lado"),
                )
                .where(EventoModel.timestamp_unix < ts)
                .order_by(EventoModel.timestamp_unix.desc())
                .limit(cantidad)
            )
            brazos.append(
                select(
                    EventoModel,
                    literal(i).label("ancla"),
                    literal(1).label("lado"),
                )
                .where(EventoModel.timestamp_unix > ts)
                .order_by(EventoModel.timestamp_unix)
                .limit(cantidad)
            )
        union = union_all(*brazos).subquery()
        filas = (
            await self._session.execute(select(union))
        ).mappings().all()
        salida: dict[int, tuple[list[EventoModel], list[EventoModel]]] = {
            i: ([], []) for i in range(len(marcas))
        }
        for fila in filas:
            modelo = EventoModel(
                **{
                    k: v
                    for k, v in fila.items()
                    if k not in ("ancla", "lado")
                }
            )
            lado = salida[fila["ancla"]][0 if fila["lado"] == 0 else 1]
            lado.append(modelo)
        return salida

    async def actualizar_embedding(
        self, evento_id: uuid.UUID, vector: list[float]
    ) -> None:
        """Asigna el embedding a un evento insertado sin él (write-behind).

        Args:
            evento_id: UUID del evento a indexar.
            vector: Embedding generado en segundo plano.
        """
        stmt = (
            update(EventoModel)
            .where(EventoModel.id == evento_id)
            .values(embedding=vector)
        )
        await self._session.execute(stmt)

    async def eliminar_debil(self, evento_id: uuid.UUID) -> bool:
        """Elimina un evento solo si nadie lo ha consolidado todavía.

        Usado por la indexación write-behind para fusionar duplicados:
        si la huella nueva ya ganó refuerzos no se borra.

        Args:
            evento_id: UUID del evento candidato a eliminar.

        Returns:
            True si la fila fue eliminada.
        """
        stmt = delete(EventoModel).where(
            EventoModel.id == evento_id,
            EventoModel.refuerzos == 0,
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
        return resultado.rowcount > 0


class BenchmarkRepository:
    """Acceso a datos de las corridas de benchmark persistidas."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
        """
        self._session = session

    async def registrar(self, corrida: BenchmarkRunModel) -> BenchmarkRunModel:
        """Persiste una corrida de benchmark.

        Args:
            corrida: Instancia de BenchmarkRunModel a persistir.

        Returns:
            La corrida persistida con id y timestamp poblados.
        """
        self._session.add(corrida)
        await self._session.flush()
        await self._session.refresh(corrida)
        return corrida

    async def listar(
        self, nombre: str | None = None, limite: int = 50
    ) -> Sequence[BenchmarkRunModel]:
        """Lista corridas de benchmark, más recientes primero.

        Args:
            nombre: Filtra por nombre de benchmark (None = todas).
            limite: Máximo de corridas a retornar.

        Returns:
            Corridas ordenadas por fecha de creación descendente.
        """
        stmt = (
            select(BenchmarkRunModel)
            .order_by(BenchmarkRunModel.creado_en.desc())
            .limit(limite)
        )
        if nombre is not None:
            stmt = stmt.where(BenchmarkRunModel.nombre == nombre)
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()


class ProyeccionRepository:
    """Acceso a datos de las proyecciones persistidas."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
        """
        self._session = session

    async def registrar(self, proyeccion: ProyeccionModel) -> ProyeccionModel:
        """Persiste una proyección.

        Args:
            proyeccion: Instancia de ProyeccionModel a persistir.

        Returns:
            La proyección persistida con id y timestamp poblados.
        """
        self._session.add(proyeccion)
        await self._session.flush()
        await self._session.refresh(proyeccion)
        return proyeccion

    async def ultima(self) -> ProyeccionModel | None:
        """Retorna la proyección más reciente.

        Returns:
            La última proyección persistida, o None si no hay ninguna.
        """
        stmt = (
            select(ProyeccionModel)
            .order_by(ProyeccionModel.creado_en.desc())
            .limit(1)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().first()
