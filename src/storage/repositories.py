"""Repositorios de acceso a datos (patrón Repository).

Encapsulan las consultas sobre los modelos. La lógica de negocio
vive en src/core/, nunca aquí.
"""

import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, func, literal, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import (
    BenchmarkRunModel,
    ConfiguracionModel,
    EventoModel,
    FuenteModel,
    ProyeccionModel,
    RelacionModel,
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
        self,
        desde: int,
        hasta: int,
        limite: int = 100,
        rama: str | None = None,
    ) -> Sequence[EventoModel]:
        """Lista eventos en el rango temporal [desde, hasta].

        Args:
            desde: Timestamp Unix de inicio (inclusive).
            hasta: Timestamp Unix de fin (inclusive).
            limite: Máximo de eventos a retornar.
            rama: Línea de universo a listar; None = todas las ramas
                ("main" solo si se pasa explícitamente).

        Returns:
            Eventos ordenados por timestamp ascendente.
        """
        stmt = (
            select(EventoModel)
            .where(EventoModel.timestamp_unix.between(desde, hasta))
            .order_by(EventoModel.timestamp_unix)
            .limit(limite)
        )
        if rama is not None:
            stmt = stmt.where(EventoModel.rama == rama)
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def listar_tags(self, limite: int = 200) -> Sequence[tuple[str, int]]:
        """Agrega los tags de todos los eventos con su conteo.

        Args:
            limite: Máximo de tags a retornar.

        Returns:
            Tuplas (tag, número de eventos) ordenadas por frecuencia.
        """
        tag = func.unnest(EventoModel.tags).label("tag")
        stmt = (
            select(tag, func.count().label("n"))
            .group_by(tag)
            .order_by(func.count().desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return [(str(fila["tag"]), int(fila["n"])) for fila in resultado.mappings().all()]

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
        # Los tags explícitos también hacen match (no caben en el
        # tsvector generado: array_to_string no es immutable).
        stmt = (
            select(EventoModel, rank)
            .where(
                or_(
                    EventoModel.contenido_tsv.op("@@")(tsquery),
                    func.array_to_string(EventoModel.tags, " ").ilike(
                        f"%{query}%"
                    ),
                )
            )
            .order_by(rank.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return [(fila["EventoModel"], float(fila["score"])) for fila in resultado.mappings().all()]

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
        distancia = EventoModel.embedding.cosine_distance(vector).label("distancia")
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

    async def existe_mencion(
        self, termino: str, excluir_id: uuid.UUID | None = None
    ) -> bool:
        """Indica si algún evento previo menciona el término dado.

        Usa el tsvector en español para detectar antecedentes de una
        entidad (p. ej. un nombre propio) antes de registrarla.

        Args:
            termino: Término a buscar (plainto_tsquery).
            excluir_id: Evento a excluir de la búsqueda (el recién
                creado), o None.

        Returns:
            True si existe al menos un antecedente.
        """
        tsquery = func.plainto_tsquery("spanish", termino)
        stmt = select(EventoModel.id).where(
            EventoModel.contenido_tsv.op("@@")(tsquery)
        )
        if excluir_id is not None:
            stmt = stmt.where(EventoModel.id != excluir_id)
        resultado = await self._session.execute(stmt.limit(1))
        return resultado.scalar_one_or_none() is not None

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
        resultado = cast(CursorResult[EventoModel], await self._session.execute(stmt))
        return resultado.rowcount

    async def contar_podables(self, antes_de: int, amplitud_max: float, refuerzos_max: int) -> int:
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

    async def podar(self, antes_de: int, amplitud_max: float, refuerzos_max: int) -> int:
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
        resultado = cast(CursorResult[EventoModel], await self._session.execute(stmt))
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
        filas = (await self._session.execute(select(union))).mappings().all()
        salida: dict[int, tuple[list[EventoModel], list[EventoModel]]] = {
            i: ([], []) for i in range(len(marcas))
        }
        for fila in filas:
            modelo = EventoModel(**{k: v for k, v in fila.items() if k not in ("ancla", "lado")})
            lado = salida[fila["ancla"]][0 if fila["lado"] == 0 else 1]
            lado.append(modelo)
        return salida

    async def actualizar_embedding(self, evento_id: uuid.UUID, vector: list[float]) -> None:
        """Asigna el embedding a un evento insertado sin él (write-behind).

        Args:
            evento_id: UUID del evento a indexar.
            vector: Embedding generado en segundo plano.
        """
        stmt = update(EventoModel).where(EventoModel.id == evento_id).values(embedding=vector)
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
        resultado = cast(CursorResult[EventoModel], await self._session.execute(stmt))
        return resultado.rowcount > 0


class RelacionRepository:
    """Acceso a datos de los vínculos entre eventos."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
        """
        self._session = session

    async def vincular(
        self,
        a_id: uuid.UUID,
        b_id: uuid.UUID,
        tipo: str,
        peso: float = 1.0,
    ) -> bool:
        """Crea el vínculo entre dos eventos si no existe ya.

        El par se normaliza en orden canónico (menor UUID primero) —
        la relación es no dirigida. Duplicados (mismo par y tipo) se
        ignoran silenciosamente.

        Args:
            a_id: UUID de un extremo.
            b_id: UUID del otro extremo.
            tipo: ``explicita`` (declarada) o ``semantica`` (inferida).
            peso: Fuerza del vínculo (0-1; en semánticas, el coseno).

        Returns:
            True si el vínculo se creó, False si ya existía o el par
            era degenerado (mismo evento en ambos extremos).
        """
        if a_id == b_id:
            return False
        origen, destino = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        stmt = (
            pg_insert(RelacionModel)
            .values(
                origen_id=origen,
                destino_id=destino,
                tipo=tipo,
                peso=peso,
            )
            .on_conflict_do_nothing(constraint="uq_relaciones_par_tipo")
        )
        resultado = await self._session.execute(stmt)
        return cast(CursorResult[RelacionModel], resultado).rowcount > 0

    async def listar(self, limite: int = 2000) -> Sequence[RelacionModel]:
        """Lista los vínculos registrados, más recientes primero.

        Args:
            limite: Máximo de vínculos a retornar.

        Returns:
            Relaciones ordenadas por fecha de creación descendente.
        """
        stmt = (
            select(RelacionModel)
            .order_by(RelacionModel.creado_en.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()


class FuenteRepository:
    """Acceso a datos de las fuentes administrables."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
        """
        self._session = session

    async def listar(
        self, incluir_archivadas: bool = True
    ) -> Sequence[tuple[FuenteModel, int]]:
        """Lista las fuentes con su conteo de eventos real.

        Args:
            incluir_archivadas: Si False, solo fuentes activas.

        Returns:
            Tuplas (fuente, nº de eventos) ordenadas por nombre.
        """
        conteo = (
            select(EventoModel.fuente, func.count().label("n"))
            .group_by(EventoModel.fuente)
            .subquery()
        )
        stmt = (
            select(FuenteModel, func.coalesce(conteo.c.n, 0).label("n"))
            .outerjoin(conteo, conteo.c.fuente == FuenteModel.nombre)
            .order_by(FuenteModel.nombre)
        )
        if not incluir_archivadas:
            stmt = stmt.where(FuenteModel.estado == "activa")
        resultado = await self._session.execute(stmt)
        return [
            (fila["FuenteModel"], int(fila["n"]))
            for fila in resultado.mappings().all()
        ]

    async def crear(
        self,
        nombre: str,
        tipo: str = "manual",
        namespace: str = "",
        rama_origen: str = "main",
        extra: dict[str, Any] | None = None,
    ) -> FuenteModel:
        """Registra una fuente nueva (idempotente por nombre).

        Args:
            nombre: Identificador de la fuente (``eventos.fuente``).
            tipo: manual/obsidian/gbrain/memlord/agente/import.
            namespace: Sub-agrupación opcional dentro de la fuente.
            rama_origen: Rama de la línea de universo donde nace.
            extra: Metadata libre (JSONB).

        Returns:
            La fuente persistida o la existente si ya estaba.
        """
        stmt = (
            pg_insert(FuenteModel)
            .values(
                nombre=nombre,
                tipo=tipo,
                namespace=namespace,
                rama_origen=rama_origen,
                extra=extra or {},
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)
        existente = await self._session.get(FuenteModel, nombre)
        assert existente is not None
        return existente

    async def obtener(self, nombre: str) -> FuenteModel | None:
        """Retorna la fuente por nombre o None."""
        return await self._session.get(FuenteModel, nombre)

    async def cambiar_estado(self, nombre: str, estado: str) -> bool:
        """Activa o archiva una fuente.

        Args:
            nombre: Fuente a modificar.
            estado: ``activa`` o ``archivada``.

        Returns:
            True si la fuente existía y quedó actualizada.
        """
        resultado = await self._session.execute(
            update(FuenteModel)
            .where(FuenteModel.nombre == nombre)
            .values(estado=estado)
        )
        return cast(CursorResult[FuenteModel], resultado).rowcount > 0

    async def eliminar(self, nombre: str) -> bool:
        """Borra el registro de la fuente (no sus eventos).

        Args:
            nombre: Fuente a eliminar del registro.

        Returns:
            True si existía.
        """
        resultado = await self._session.execute(
            delete(FuenteModel).where(FuenteModel.nombre == nombre)
        )
        return cast(CursorResult[FuenteModel], resultado).rowcount > 0


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
        stmt = select(BenchmarkRunModel).order_by(BenchmarkRunModel.creado_en.desc()).limit(limite)
        if nombre is not None:
            stmt = stmt.where(BenchmarkRunModel.nombre == nombre)
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()


class ConfiguracionRepository:
    """Acceso a datos del almacén clave-valor de preferencias."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
        """
        self._session = session

    async def obtener(self, clave: str) -> dict[str, Any] | None:
        """Retorna el documento JSON asociado a la clave.

        Args:
            clave: Identificador de la preferencia (p. ej. ``cerebro``).

        Returns:
            El documento almacenado, o None si la clave no existe.
        """
        stmt = select(ConfiguracionModel.valor).where(ConfiguracionModel.clave == clave)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def guardar(self, clave: str, valor: dict[str, Any]) -> None:
        """Persiste (upsert) un documento JSON bajo la clave dada.

        Args:
            clave: Identificador de la preferencia.
            valor: Documento JSON serializable.
        """
        stmt = (
            pg_insert(ConfiguracionModel)
            .values(clave=clave, valor=valor)
            .on_conflict_do_update(
                index_elements=[ConfiguracionModel.clave],
                set_={"valor": valor, "actualizado_en": func.now()},
            )
        )
        await self._session.execute(stmt)


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
        stmt = select(ProyeccionModel).order_by(ProyeccionModel.creado_en.desc()).limit(1)
        resultado = await self._session.execute(stmt)
        return resultado.scalars().first()
