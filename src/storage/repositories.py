"""Repositorios de acceso a datos (patrón Repository).

Encapsulan las consultas sobre los modelos. La lógica de negocio
vive en src/core/, nunca aquí.
"""

import time
import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import and_, delete, func, literal, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.core.identidad import (
    USUARIO_SISTEMA_ID,
    WORKSPACE_PERSONAL_ID,
    Contexto,
)
from src.storage.models import (
    BenchmarkRunModel,
    ConfiguracionModel,
    EventoModel,
    FuenteModel,
    LlamadaModel,
    ObjetivoModel,
    ProyeccionModel,
    RelacionModel,
)


def _vis_eventos(ctx: Contexto | None) -> ColumnElement[bool] | None:
    """Cláusula de visibilidad multi-tenant sobre ``eventos``.

    Regla (grill-me Q3/Q7): dentro del workspace activo, lo visible
    es ``scope='workspace'`` ∪ lo propio (``autor_id=me``) ∪ lo sin
    atribuir (``autor_id IS NULL``, filas de importaciones/scripts).
    El workspace es el límite de aislamiento — nada cruza fronteras.
    Con ``ctx=None`` devuelve ``None`` — sin filtro (CLI, importers,
    tests de capa storage).
    """
    if ctx is None:
        return None
    return and_(
        EventoModel.workspace_id == ctx.workspace_id,
        or_(
            EventoModel.scope == "workspace",
            EventoModel.autor_id == ctx.usuario_id,
            EventoModel.autor_id.is_(None),
        ),
    )


def _vis_generico(
    modelo: Any, ctx: Contexto | None
) -> ColumnElement[bool] | None:
    """Misma regla de visibilidad para entidades con scope+autor."""
    if ctx is None:
        return None
    return and_(
        modelo.workspace_id == ctx.workspace_id,
        or_(
            modelo.scope == "workspace",
            modelo.autor_id == ctx.usuario_id,
            modelo.autor_id.is_(None),
        ),
    )


class EventoRepository:
    """Acceso a datos de los eventos de la línea de universo."""

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
            ctx: Contexto de identidad; si se pasa, toda lectura se
                filtra por la visibilidad del llamante.
        """
        self._session = session
        self._ctx = ctx

    def _vis(self) -> list[ColumnElement[bool]]:
        """Filtros de visibilidad a incluir en las lecturas."""
        clausula = _vis_eventos(self._ctx)
        return [clausula] if clausula is not None else []

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
            El evento o None si no existe o no es visible.
        """
        stmt = select(EventoModel).where(EventoModel.id == evento_id, *self._vis())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def listar_por_rango(
        self,
        desde: int,
        hasta: int,
        limite: int = 100,
        rama: str | None = None,
        repartir: bool = False,
    ) -> Sequence[EventoModel]:
        """Lista eventos en el rango temporal [desde, hasta].

        Args:
            desde: Timestamp Unix de inicio (inclusive).
            hasta: Timestamp Unix de fin (inclusive).
            limite: Máximo de eventos a retornar.
            rama: Línea de universo a listar; None = todas las ramas
                ("main" solo si se pasa explícitamente).
            repartir: Si es True, el límite se reparte por fuente
                (cuota = limite / nº de fuentes) — evita que una fuente
                masiva (p. ej. benchmarks) acapare toda la ventana y
                deje el grafo con una sola procedencia.

        Returns:
            Eventos ordenados por timestamp ascendente.
        """
        filtros: list[Any] = [
            EventoModel.timestamp_unix.between(desde, hasta),
            *self._vis(),
        ]
        if rama is not None:
            filtros.append(EventoModel.rama == rama)
        if repartir:
            return await self._listar_repartido(filtros, limite)
        stmt = (
            select(EventoModel)
            .where(*filtros)
            .order_by(EventoModel.timestamp_unix)
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def _listar_repartido(
        self, filtros: list[Any], limite: int
    ) -> Sequence[EventoModel]:
        """Reparte el límite entre las fuentes presentes en el filtro.

        Cada fuente aporta sus ``cuota`` eventos más recientes vía
        ``ROW_NUMBER() OVER (PARTITION BY fuente)``; las fuentes con
        menos eventos que la cuota aportan todo lo que tienen.
        La cuota sobrante se rellena con los eventos más recientes del
        resto de fuentes para no desperdiciar la ventana.
        """
        nfuentes = int(
            (
                await self._session.execute(
                    select(func.count(func.distinct(EventoModel.fuente))).where(
                        *filtros
                    )
                )
            ).scalar_one()
            or 1
        )
        cuota = max(1, limite // nfuentes)
        rn = (
            func.row_number()
            .over(
                partition_by=EventoModel.fuente,
                order_by=EventoModel.timestamp_unix.desc(),
            )
            .label("rn")
        )
        sub = select(EventoModel.id.label("id"), rn).where(*filtros).subquery()
        stmt = (
            select(EventoModel)
            .join(sub, EventoModel.id == sub.c.id)
            .where(sub.c.rn <= cuota)
            .order_by(EventoModel.timestamp_unix)
        )
        resultado = await self._session.execute(stmt)
        eventos = list(resultado.scalars().all())
        faltan = limite - len(eventos)
        if faltan > 0:
            ids = {e.id for e in eventos}
            relleno = await self._session.execute(
                select(EventoModel)
                .where(*filtros, EventoModel.id.notin_(ids))
                .order_by(EventoModel.timestamp_unix.desc())
                .limit(faltan)
            )
            eventos += list(relleno.scalars().all())
            eventos.sort(key=lambda e: e.timestamp_unix)
        return eventos

    async def listar_compromisos_abiertos(
        self, rama: str = "main", ahora: int | None = None
    ) -> Sequence[EventoModel]:
        """Lista los compromisos aún abiertos (motor de open loops).

        Un compromiso está abierto mientras ningún evento lo cierre
        (relación tipo ``cierra``), no esté reemplazado ni expirado.

        Args:
            rama: Línea de universo a consultar.
            ahora: Timestamp Unix actual (default: ahora real).

        Returns:
            Compromisos ordenados por antigüedad — primero los que
            llevan más tiempo esperando respuesta.
        """
        cierre = select(RelacionModel.id).where(
            RelacionModel.tipo == "cierra",
            or_(
                RelacionModel.origen_id == EventoModel.id,
                RelacionModel.destino_id == EventoModel.id,
            ),
        )
        ahora = ahora if ahora is not None else int(time.time())
        stmt = (
            select(EventoModel)
            .where(
                EventoModel.tipo == "compromiso",
                EventoModel.rama == rama,
                EventoModel.reemplazado_por.is_(None),
                or_(
                    EventoModel.expira_en.is_(None),
                    EventoModel.expira_en > ahora,
                ),
                ~cierre.exists(),
                *self._vis(),
            )
            .order_by(EventoModel.timestamp_unix)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def marcar_reemplazado(
        self, viejo_id: uuid.UUID, nuevo_id: uuid.UUID
    ) -> None:
        """Marca un evento como sustituido por otro más reciente.

        Args:
            viejo_id: Evento que queda para arqueología.
            nuevo_id: Evento cabeza de la cadena de supersede.
        """
        stmt = (
            update(EventoModel)
            .where(EventoModel.id == viejo_id)
            .values(reemplazado_por=nuevo_id)
        )
        await self._session.execute(stmt)

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
            .where(*self._vis())
            .group_by(tag)
            .order_by(func.count().desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return [(str(fila["tag"]), int(fila["n"])) for fila in resultado.mappings().all()]

    async def buscar_por_texto(
        self, query: str, limite: int = 20, rama: str | None = None
    ) -> Sequence[tuple[EventoModel, float]]:
        """Búsqueda full-text sobre contenido vía tsvector (español).

        Args:
            query: Texto a buscar (plainto_tsquery).
            limite: Máximo de resultados.
            rama: Línea de universo a la que limitar la búsqueda
                (None = todas las ramas).

        Returns:
            Tuplas (evento, ts_rank) ordenadas por rank descendente.
        """
        tsquery = func.plainto_tsquery("spanish", query)
        rank = func.ts_rank(EventoModel.contenido_tsv, tsquery).label("score")
        ahora = int(time.time())
        # Los tags explícitos también hacen match (no caben en el
        # tsvector generado: array_to_string no es immutable). Los
        # eventos expirados (TTL cumplido) no son evocables.
        filtros = [
            or_(
                EventoModel.contenido_tsv.op("@@")(tsquery),
                func.array_to_string(EventoModel.tags, " ").ilike(
                    f"%{query}%"
                ),
            ),
            or_(
                EventoModel.expira_en.is_(None),
                EventoModel.expira_en > ahora,
            ),
            *self._vis(),
        ]
        if rama is not None:
            filtros.append(EventoModel.rama == rama)
        stmt = (
            select(EventoModel, rank)
            .where(*filtros)
            .order_by(rank.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return [(fila["EventoModel"], float(fila["score"])) for fila in resultado.mappings().all()]

    async def buscar_por_vector(
        self, vector: list[float], limite: int = 20, rama: str | None = None
    ) -> Sequence[tuple[EventoModel, float]]:
        """Búsqueda por similitud coseno sobre el embedding.

        Args:
            vector: Vector de consulta (misma dimensión que el embedding).
            limite: Máximo de resultados.
            rama: Línea de universo a la que limitar la búsqueda
                (None = todas las ramas).

        Returns:
            Tuplas (evento, similitud_coseno) ordenadas por similitud
            descendente.
        """
        distancia = EventoModel.embedding.cosine_distance(vector).label("distancia")
        ahora = int(time.time())
        filtros = [
            EventoModel.embedding.is_not(None),
            or_(
                EventoModel.expira_en.is_(None),
                EventoModel.expira_en > ahora,
            ),
            *self._vis(),
        ]
        if rama is not None:
            filtros.append(EventoModel.rama == rama)
        stmt = (
            select(EventoModel, distancia)
            .where(*filtros)
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
            EventoModel.contenido_tsv.op("@@")(tsquery),
            *self._vis(),
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
            *self._vis(),
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
            *self._vis(),
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
            .where(EventoModel.timestamp_unix < timestamp_unix, *self._vis())
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(cantidad)
        )
        stmt_siguientes = (
            select(EventoModel)
            .where(EventoModel.timestamp_unix > timestamp_unix, *self._vis())
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
                .where(EventoModel.timestamp_unix < ts, *self._vis())
                .order_by(EventoModel.timestamp_unix.desc())
                .limit(cantidad)
            )
            brazos.append(
                select(
                    EventoModel,
                    literal(i).label("ancla"),
                    literal(1).label("lado"),
                )
                .where(EventoModel.timestamp_unix > ts, *self._vis())
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

    async def eliminar(self, evento_id: uuid.UUID) -> bool:
        """Elimina un evento de la memoria — borrado duro, irreversible.

        A diferencia de ``eliminar_debil`` (fusión de duplicados) o de
        ``reemplaza_id`` (auditable), esto es el "derecho al olvido":
        la fila desaparece de la línea de universo. El llamador debe
        limpiar primero los vínculos que lo referencian.

        Args:
            evento_id: UUID del evento a borrar.

        Returns:
            True si la fila existía y fue eliminada.
        """
        stmt = delete(EventoModel).where(EventoModel.id == evento_id)
        resultado = cast(CursorResult[EventoModel], await self._session.execute(stmt))
        return resultado.rowcount > 0


class RelacionRepository:
    """Acceso a datos de los vínculos entre eventos."""

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
            ctx: Contexto de identidad; filtra los vínculos a los del
                workspace del llamante.
        """
        self._session = session
        self._ctx = ctx

    def _vis(self) -> list[ColumnElement[bool]]:
        """Filtros de visibilidad a incluir en las lecturas."""
        if self._ctx is None:
            return []
        return [RelacionModel.workspace_id == self._ctx.workspace_id]

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
            .where(*self._vis())
            .order_by(RelacionModel.creado_en.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def eliminar_de(self, evento_id: uuid.UUID) -> int:
        """Borra todos los vínculos que referencian un evento.

        Limpieza previa al borrado de un evento: ninguna relación debe
        quedar apuntando a una fila inexistente.

        Args:
            evento_id: UUID del evento eliminado.

        Returns:
            Número de vínculos borrados.
        """
        stmt = delete(RelacionModel).where(
            or_(
                RelacionModel.origen_id == evento_id,
                RelacionModel.destino_id == evento_id,
            )
        )
        resultado = cast(CursorResult[RelacionModel], await self._session.execute(stmt))
        return resultado.rowcount


class FuenteRepository:
    """Acceso a datos de las fuentes administrables."""

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
            ctx: Contexto de identidad; las fuentes se acotan a su
                workspace (default: el bootstrap ``personal``).
        """
        self._session = session
        self._ws = ctx.workspace_id if ctx is not None else WORKSPACE_PERSONAL_ID

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
            .where(EventoModel.workspace_id == self._ws)
            .group_by(EventoModel.fuente)
            .subquery()
        )
        stmt = (
            select(FuenteModel, func.coalesce(conteo.c.n, 0).label("n"))
            .outerjoin(conteo, conteo.c.fuente == FuenteModel.nombre)
            .where(FuenteModel.workspace_id == self._ws)
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
                workspace_id=self._ws,
                tipo=tipo,
                namespace=namespace,
                rama_origen=rama_origen,
                extra=extra or {},
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)
        existente = await self._session.get(
            FuenteModel, (nombre, self._ws)
        )
        assert existente is not None
        return existente

    async def obtener(self, nombre: str) -> FuenteModel | None:
        """Retorna la fuente por nombre o None."""
        return await self._session.get(
            FuenteModel, (nombre, self._ws)
        )

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
            .where(
                FuenteModel.nombre == nombre,
                FuenteModel.workspace_id == self._ws,
            )
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
            delete(FuenteModel).where(
                FuenteModel.nombre == nombre,
                FuenteModel.workspace_id == self._ws,
            )
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

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
            ctx: Contexto de identidad; la config se acota a su
                workspace bajo el usuario ``sistema`` (preferencias a
                nivel workspace; el aislamiento por usuario llega con
                el dashboard de equipo).
        """
        self._session = session
        self._ws = ctx.workspace_id if ctx is not None else WORKSPACE_PERSONAL_ID

    async def obtener(self, clave: str) -> dict[str, Any] | None:
        """Retorna el documento JSON asociado a la clave.

        Args:
            clave: Identificador de la preferencia (p. ej. ``cerebro``).

        Returns:
            El documento almacenado, o None si la clave no existe.
        """
        stmt = select(ConfiguracionModel.valor).where(
            ConfiguracionModel.clave == clave,
            ConfiguracionModel.workspace_id == self._ws,
            ConfiguracionModel.usuario_id == USUARIO_SISTEMA_ID,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def guardar(self, clave: str, valor: dict[str, Any]) -> None:
        """Persiste (upsert) un documento JSON bajo la clave dada.

        Args:
            clave: Identificador de la preferencia.
            valor: Documento JSON serializable.
        """
        stmt = (
            pg_insert(ConfiguracionModel)
            .values(
                clave=clave,
                workspace_id=self._ws,
                usuario_id=USUARIO_SISTEMA_ID,
                valor=valor,
            )
            .on_conflict_do_update(
                index_elements=[
                    ConfiguracionModel.clave,
                    ConfiguracionModel.workspace_id,
                    ConfiguracionModel.usuario_id,
                ],
                set_={"valor": valor, "actualizado_en": func.now()},
            )
        )
        await self._session.execute(stmt)


class ProyeccionRepository:
    """Acceso a datos de las proyecciones persistidas."""

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
            ctx: Contexto de identidad; filtra por visibilidad.
        """
        self._session = session
        self._ctx = ctx

    def _vis(self) -> list[ColumnElement[bool]]:
        """Filtros de visibilidad a incluir en las lecturas."""
        clausula = _vis_generico(ProyeccionModel, self._ctx)
        return [clausula] if clausula is not None else []

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
            .where(*self._vis())
            .order_by(ProyeccionModel.creado_en.desc())
            .limit(1)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().first()


class ObjetivoRepository:
    """Acceso a datos de los objetivos administrables del usuario.

    Un objetivo ancla una proyección futura: los cauces-fantasma del
    río temporal convergen hacia él. Ciclo de vida auditable —
    cumplido/abandonado son transiciones persistidas, no borrados.
    """

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
            ctx: Contexto de identidad; filtra por visibilidad.
        """
        self._session = session
        self._ctx = ctx

    def _vis(self) -> list[ColumnElement[bool]]:
        """Filtros de visibilidad a incluir en las lecturas."""
        clausula = _vis_generico(ObjetivoModel, self._ctx)
        return [clausula] if clausula is not None else []

    async def crear(self, objetivo: ObjetivoModel) -> ObjetivoModel:
        """Persiste un objetivo y lo retorna con su id asignado.

        Args:
            objetivo: Instancia de ObjetivoModel a persistir.

        Returns:
            El objetivo persistido con id y timestamps poblados.
        """
        self._session.add(objetivo)
        await self._session.flush()
        await self._session.refresh(objetivo)
        return objetivo

    async def listar(
        self,
        estado: str | None = None,
        rama: str = "main",
    ) -> Sequence[ObjetivoModel]:
        """Lista objetivos filtrados por estado y línea de universo.

        Args:
            estado: Filtro de ciclo de vida; None = todos los estados.
            rama: Línea de universo a consultar.

        Returns:
            Objetivos ordenados por fecha límite ascendente — primero
            el más próximo, el ancla más cercana del futuro.
        """
        filtros: list[ColumnElement[bool]] = [
            ObjetivoModel.rama == rama,
            *self._vis(),
        ]
        if estado is not None:
            filtros.append(ObjetivoModel.estado == estado)
        stmt = (
            select(ObjetivoModel)
            .where(*filtros)
            .order_by(ObjetivoModel.fecha_objetivo)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def obtener(self, objetivo_id: uuid.UUID) -> ObjetivoModel | None:
        """Retorna el objetivo por id, o None si no existe."""
        stmt = select(ObjetivoModel).where(
            ObjetivoModel.id == objetivo_id, *self._vis()
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().first()

    async def actualizar(
        self,
        objetivo_id: uuid.UUID,
        cambios: dict[str, Any],
    ) -> ObjetivoModel | None:
        """Aplica cambios parciales sobre un objetivo.

        Args:
            objetivo_id: UUID del objetivo.
            cambios: Campos a actualizar (titulo, descripcion,
                dimension, fecha_objetivo, estado, evento_cierre_id).

        Returns:
            El objetivo actualizado, o None si no existe.
        """
        stmt = (
            update(ObjetivoModel)
            .where(ObjetivoModel.id == objetivo_id)
            .values(**cambios)
            .returning(ObjetivoModel)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().first()

    async def eliminar(self, objetivo_id: uuid.UUID) -> bool:
        """Borra un objetivo en duro (petición explícita del usuario).

        Args:
            objetivo_id: UUID del objetivo.

        Returns:
            True si existía y se eliminó.
        """
        stmt = delete(ObjetivoModel).where(ObjetivoModel.id == objetivo_id)
        resultado = await self._session.execute(stmt)
        return (cast(CursorResult[ObjetivoModel], resultado).rowcount or 0) > 0


class CifradoRepository:
    """Cifrado simétrico server-side vía pgcrypto.

    Las claves de proveedores externos se guardan cifradas dentro del
    JSONB de ``configuracion``; la passphrase nunca sale del proceso.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
        """
        self._session = session

    async def cifrar(self, texto: str, passphrase: str) -> str:
        """Cifra un texto con ``pgp_sym_encrypt`` y lo devuelve en base64.

        Args:
            texto: Secreto en claro (p. ej. una API key).
            passphrase: Frase de cifrado simétrico.

        Returns:
            Ciphertext codificado en base64, apto para guardar en JSONB.
        """
        stmt = select(
            func.encode(func.pgp_sym_encrypt(texto, passphrase), "base64")
        )
        return str((await self._session.execute(stmt)).scalar_one())

    async def descifrar(self, b64: str, passphrase: str) -> str:
        """Descifra un ciphertext base64 con ``pgp_sym_decrypt``.

        Args:
            b64: Ciphertext base64 producido por ``cifrar``.
            passphrase: Frase de cifrado simétrico.

        Returns:
            El texto original en claro.
        """
        stmt = select(
            func.pgp_sym_decrypt(func.decode(b64, "base64"), passphrase)
        )
        return str((await self._session.execute(stmt)).scalar_one())


class LlamadaRepository:
    """Acceso a datos del registro de llamadas (trazabilidad)."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
        """
        self._session = session

    async def insertar_lote(self, filas: list[dict[str, Any]]) -> int:
        """Inserta un lote de llamadas en una sola consulta.

        Args:
            filas: Dicts con ts, metodo, ruta, origen, agente,
                latencia_ms, status, es_ping.

        Returns:
            Número de filas insertadas.
        """
        if not filas:
            return 0
        await self._session.execute(pg_insert(LlamadaModel).values(filas))
        return len(filas)

    async def listar(
        self, limite: int = 200, ruta: str | None = None
    ) -> Sequence[LlamadaModel]:
        """Lista llamadas recientes primero, con filtro opcional de ruta.

        Args:
            limite: Máximo de filas (1-1000).
            ruta: Filtro exacto de ruta; None = todas.

        Returns:
            Llamadas ordenadas por ts descendente.
        """
        filtros = [] if ruta is None else [LlamadaModel.ruta == ruta]
        stmt = (
            select(LlamadaModel)
            .where(*filtros)
            .order_by(LlamadaModel.ts.desc())
            .limit(max(1, min(limite, 1000)))
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def stats(self) -> dict[str, Any]:
        """Agrega conteos y latencias para el panel de trazabilidad.

        Returns:
            total, por_ruta (conteo+latencia media), por_origen, pings
            (total y último) — todo en una sola pasada de consultas.
        """
        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(LlamadaModel)
                )
            ).scalar_one()
        )
        por_ruta_rows = (
            await self._session.execute(
                select(
                    LlamadaModel.ruta,
                    func.count().label("n"),
                    func.round(func.avg(LlamadaModel.latencia_ms)).label(
                        "latencia_media"
                    ),
                )
                .where(LlamadaModel.es_ping.is_(False))
                .group_by(LlamadaModel.ruta)
                .order_by(func.count().desc())
                .limit(25)
            )
        ).all()
        por_origen_rows = (
            await self._session.execute(
                select(
                    LlamadaModel.origen,
                    func.count().label("n"),
                )
                .group_by(LlamadaModel.origen)
                .order_by(func.count().desc())
                .limit(15)
            )
        ).all()
        ping_rows = (
            await self._session.execute(
                select(
                    func.count().label("n"),
                    func.max(LlamadaModel.ts).label("ultimo"),
                ).where(LlamadaModel.es_ping.is_(True))
            )
        ).one()
        return {
            "total": total,
            "por_ruta": [
                {
                    "ruta": r.ruta,
                    "llamadas": r.n,
                    "latencia_media": int(r.latencia_media or 0),
                }
                for r in por_ruta_rows
            ],
            "por_origen": [
                {"origen": r.origen or "desconocido", "llamadas": r.n}
                for r in por_origen_rows
            ],
            "pings": {"total": ping_rows.n, "ultimo": ping_rows.ultimo},
        }
