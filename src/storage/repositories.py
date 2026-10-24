"""Repositorios de acceso a datos (patrón Repository).

Encapsulan las consultas sobre los modelos. La lógica de negocio
vive en src/core/, nunca aquí.
"""

import time
import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import BigInteger, and_, case, delete, func, literal, or_, select, union_all, update
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
    BorradorModel,
    ConfiguracionModel,
    EventoModel,
    FuenteModel,
    IntencionProspectivaModel,
    LlamadaModel,
    ObjetivoModel,
    ProyeccionModel,
    RelacionModel,
    ReporteConsolidacionModel,
    ScriptDetectadoModel,
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
            # Lo archivado por consolidación ya no es evocable —
            # sigue en la worldline (reversible), fuera del recall.
            EventoModel.archivado.is_(False),
            # La cuarentena tampoco es evocable: la fuente no es
            # confiable todavía — el evento existe pero no contamina
            # el recall hasta aprobarse (sistema inmune).
            EventoModel.cuarentena.is_(False),
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
            EventoModel.archivado.is_(False),
            EventoModel.cuarentena.is_(False),
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

    async def reforzar(
        self, eventos_ids: list[uuid.UUID], labil_hasta: int | None = None
    ) -> int:
        """Incrementa el contador de consolidación de los eventos evocados.

        Cada evocación fortalece la huella (reconsolidación): los
        eventos más consultados decaen más lento en el ranking. Con
        ``labil_hasta`` además abre la ventana de reconsolidación —
        un supersede dentro de ella cuenta como ``reconsolida``.

        Args:
            eventos_ids: UUIDs de los eventos devueltos al agente.
            labil_hasta: Fin de la ventana lábil (unix), calculado
                por la capa core; None = solo refuerzo.

        Returns:
            Número de filas actualizadas.
        """
        if not eventos_ids:
            return 0
        valores: dict[str, Any] = {
            "refuerzos": EventoModel.refuerzos + 1,
            "ultimo_acceso": int(time.time()),
        }
        if labil_hasta is not None:
            valores["labil_hasta"] = labil_hasta
        stmt = (
            update(EventoModel)
            .where(EventoModel.id.in_(eventos_ids))
            .values(**valores)
        )
        resultado = cast(CursorResult[EventoModel], await self._session.execute(stmt))
        return resultado.rowcount

    async def obtener_muchos(
        self, eventos_ids: list[uuid.UUID]
    ) -> Sequence[EventoModel]:
        """Carga varios eventos por id en una sola query."""
        if not eventos_ids:
            return []
        stmt = select(EventoModel).where(EventoModel.id.in_(eventos_ids))
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def pendientes_revision(
        self, ahora: int, limite: int = 20
    ) -> Sequence[EventoModel]:
        """Memorias cuyo repaso óptimo (SRS) ya venció.

        ``proxima_revision`` la fija la capa core desde la curva de
        retención — este repo solo trae las vencidas, las más viejas
        primero (las que más se están olvidando).
        """
        stmt = (
            select(EventoModel)
            .where(
                EventoModel.proxima_revision.is_not(None),
                EventoModel.proxima_revision <= ahora,
                EventoModel.archivado.is_(False),
                EventoModel.cuarentena.is_(False),
                *self._vis(),
            )
            .order_by(EventoModel.proxima_revision)
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def fijar_proximas_revisiones(
        self, revisiones: dict[uuid.UUID, int]
    ) -> int:
        """Fija ``proxima_revision`` por lote (UPDATE/CASE, no N+1).

        Args:
            revisiones: ``evento_id → timestamp`` del repaso óptimo.
        """
        if not revisiones:
            return 0
        whens = [
            (EventoModel.id == eid, ts) for eid, ts in revisiones.items()
        ]
        stmt = (
            update(EventoModel)
            .where(EventoModel.id.in_(list(revisiones)))
            .values(proxima_revision=case(*whens))
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
        return resultado.rowcount

    async def marcar_olvido_dirigido(
        self, evento_id: uuid.UUID, dirigido: bool
    ) -> bool:
        """Marca/desmarca el olvido dirigido de un evento."""
        stmt = (
            update(EventoModel)
            .where(EventoModel.id == evento_id, *self._vis())
            .values(olvido_dirigido=dirigido)
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
        return resultado.rowcount > 0

    async def marcar_cuarentena(self, evento_id: uuid.UUID) -> None:
        """Pone el evento en cuarentena (fuente no confiable)."""
        await self._session.execute(
            update(EventoModel)
            .where(EventoModel.id == evento_id)
            .values(cuarentena=True)
        )

    async def marcar_inaugura_epoca(self, evento_id: uuid.UUID) -> None:
        """Marca el evento como frontera de época (sorpresa)."""
        await self._session.execute(
            update(EventoModel)
            .where(EventoModel.id == evento_id)
            .values(inaugura_epoca=True)
        )

    async def listar_cuarentena(
        self, limite: int = 50
    ) -> Sequence[EventoModel]:
        """Eventos en cuarentena del workspace (auditoría inmune)."""
        stmt = (
            select(EventoModel)
            .where(EventoModel.cuarentena.is_(True), *self._vis())
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def aprobar_cuarentena_fuente(self, fuente: str) -> int:
        """Saca de cuarentena todos los eventos de una fuente."""
        stmt = (
            update(EventoModel)
            .where(
                EventoModel.fuente == fuente,
                EventoModel.cuarentena.is_(True),
                *self._vis(),
            )
            .values(cuarentena=False)
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
        return resultado.rowcount

    async def debilitar_lote(
        self,
        evento_ids: Sequence[uuid.UUID],
        factor: float,
        piso: float = 0.1,
    ) -> int:
        """Debilita en lote las memorias que perdieron una competencia.

        RIF (competidor no seleccionado en el recall) e interferencia
        proactiva (versión vieja ocluida por una nueva) comparten el
        mecanismo: ``weight × factor`` + contador ``debilitados`` —
        el contrapeso auditable de ``refuerzos``.

        Lo curado es inmune (invariante del tier) y bajo ``piso`` el
        decay natural ya hace el trabajo — no se castiga lo que el
        tiempo ya olvidó.

        Returns:
            Cantidad de memorias debilitadas.
        """
        if not evento_ids:
            return 0
        stmt = (
            update(EventoModel)
            .where(
                EventoModel.id.in_(list(evento_ids)),
                EventoModel.curado.is_(False),
                EventoModel.weight > piso,
                *self._vis(),
            )
            .values(
                weight=EventoModel.weight * factor,
                debilitados=EventoModel.debilitados + 1,
            )
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
        return resultado.rowcount

    async def ids_compromisos_abiertos(
        self, evento_ids: Sequence[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Subconjunto de ``evento_ids`` que son compromisos abiertos.

        Un compromiso está abierto si ninguna relación ``cierra`` lo
        toca — mismo criterio que ``listar_compromisos_abiertos``,
        restringido al lote pedido (Zeigarnik en el recall).
        """
        if not evento_ids:
            return set()
        cierre = select(RelacionModel.id).where(
            RelacionModel.tipo == "cierra",
            or_(
                RelacionModel.origen_id == EventoModel.id,
                RelacionModel.destino_id == EventoModel.id,
            ),
        )
        stmt = select(EventoModel.id).where(
            EventoModel.id.in_(list(evento_ids)),
            EventoModel.tipo == "compromiso",
            EventoModel.reemplazado_por.is_(None),
            ~cierre.exists(),
            *self._vis(),
        )
        resultado = await self._session.execute(stmt)
        return set(resultado.scalars().all())

    async def marcar_flashbulb(self, evento_id: uuid.UUID) -> None:
        """Codificación flashbulb: weight ×1.5 (cap) + tag auditable.

        El tag deja constancia de por qué la huella es tan fuerte —
        "sorpresa × emoción" es una decisión del motor, no del
        usuario, y debe poder auditarse como todo lo demás.
        """
        stmt = select(EventoModel).where(EventoModel.id == evento_id)
        modelo = (
            await self._session.execute(stmt)
        ).scalar_one_or_none()
        if modelo is None:
            return
        modelo.weight = min(modelo.weight * 1.5, 3.0)
        tags = list(modelo.tags or [])
        if "flashbulb" not in tags:
            tags.append("flashbulb")
            modelo.tags = tags

    async def episodios_de_gist(
        self, gist_id: uuid.UUID, limite: int = 3
    ) -> Sequence[EventoModel]:
        """Episodios miembros de un gist semántico (aristas ``gist``).

        La relación es no dirigida: el gist puede ser origen o destino
        del par canónico — el miembro es siempre el otro extremo.
        """
        otro = case(
            (RelacionModel.origen_id == gist_id, RelacionModel.destino_id),
            else_=RelacionModel.origen_id,
        )
        stmt = (
            select(EventoModel)
            .join(RelacionModel, EventoModel.id == otro)
            .where(
                RelacionModel.tipo == "gist",
                or_(
                    RelacionModel.origen_id == gist_id,
                    RelacionModel.destino_id == gist_id,
                ),
            )
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def anterior_misma_fuente(
        self, fuente: str, timestamp_unix: int, ventana_seg: int
    ) -> EventoModel | None:
        """Evento más reciente de la misma fuente dentro de la ventana.

        El candidato a encadenar: el último evento del mismo origen
        antes de este — la cadena episódica del flujo continuo.
        """
        stmt = (
            select(EventoModel)
            .where(
                EventoModel.fuente == fuente,
                EventoModel.timestamp_unix < timestamp_unix,
                EventoModel.timestamp_unix >= timestamp_unix - ventana_seg,
                EventoModel.archivado.is_(False),
                *self._vis(),
            )
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(1)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalar_one_or_none()

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

    async def fijar_tipo_memoria(
        self,
        evento_id: uuid.UUID,
        tipo_memoria: str,
        estado_emocional: float | None = None,
    ) -> None:
        """Asigna tipo de memoria y estado emocional tras clasificar.

        Args:
            evento_id: UUID del evento.
            tipo_memoria: 'episodica' | 'semantica' | 'procedimental'.
            estado_emocional: Valence 0-1 si se estimó (opcional).

        Nota:
            COALESCE respeta valores ya declarados en el INSERT
            (p. ej. check-in con valencia explícita) — el estimador
            solo rellena huecos, nunca sobrescribe.
        """
        valores: dict[str, Any] = {
            "tipo_memoria": func.coalesce(
                EventoModel.tipo_memoria, tipo_memoria
            )
        }
        if estado_emocional is not None:
            valores["estado_emocional"] = func.coalesce(
                EventoModel.estado_emocional, estado_emocional
            )
        stmt = (
            update(EventoModel)
            .where(EventoModel.id == evento_id)
            .values(**valores)
        )
        await self._session.execute(stmt)

    async def listar_para_consolidacion(
        self, limite: int = 5000
    ) -> Sequence[EventoModel]:
        """Candidatos de la corrida de sueño: no archivados aún.

        La consolidación decide en core qué hacer; el repo solo trae
        la materia prima acotada por workspace.
        """
        stmt = (
            select(EventoModel)
            .where(EventoModel.archivado.is_(False), *self._vis())
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def aplicar_consolidacion(
        self,
        fortalecer: dict[str, float],
        promover: list[str],
        archivar: dict[str, str],
        ahora_unix: int,
    ) -> None:
        """Aplica el plan de consolidación en tres UPDATEs por lote.

        Args:
            fortalecer: ``evento_id → weight nuevo``.
            promover: ids que pasan al tier curado.
            archivar: ``evento_id → razon`` del archivado.
            ahora_unix: timestamp de la corrida.
        """
        if fortalecer:
            whens = [
                (EventoModel.id == uuid.UUID(eid), peso)
                for eid, peso in fortalecer.items()
            ]
            await self._session.execute(
                update(EventoModel)
                .where(
                    EventoModel.id.in_(
                        [uuid.UUID(i) for i in fortalecer]
                    )
                )
                .values(weight=case(*whens))
            )
        if promover:
            await self._session.execute(
                update(EventoModel)
                .where(EventoModel.id.in_([uuid.UUID(i) for i in promover]))
                .values(curado=True)
            )
        if archivar:
            whens_razon = [
                (EventoModel.id == uuid.UUID(eid), razon)
                for eid, razon in archivar.items()
            ]
            await self._session.execute(
                update(EventoModel)
                .where(
                    EventoModel.id.in_([uuid.UUID(i) for i in archivar])
                )
                .values(
                    archivado=True,
                    archivado_en=ahora_unix,
                    razon_archivo=case(*whens_razon),
                )
            )

    async def ultimo_estado_somatico(self) -> float | None:
        """Valencia emocional más reciente — el "estado actual".

        Toma el ``estado_emocional`` del evento somático nuevo con dato;
        el boost contextual de la búsqueda lo usa como ancla. Una sola
        consulta indexada por dimensión+timestamp — costo despreciable
        en el camino de recall.

        Returns:
            Valencia 0-1 del último evento somático con estado
            estimado, o None si nunca se estimó uno.
        """
        stmt = (
            select(EventoModel.estado_emocional)
            .where(
                EventoModel.dimension == "somatica",
                EventoModel.estado_emocional.is_not(None),
                *self._vis(),
            )
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def timestamp_anterior(self, antes_de: int) -> int | None:
        """Timestamp del evento visible más reciente anterior a una marca.

        Sirve para detectar si el evento entrante inaugura época
        (efecto primacía): el gap desde este timestamp decide.

        Args:
            antes_de: Marca temporal de referencia (exclusiva).

        Returns:
            timestamp_unix del evento previo, o None si no hay.
        """
        stmt = (
            select(EventoModel.timestamp_unix)
            .where(EventoModel.timestamp_unix < antes_de, *self._vis())
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def actualizar_amplitud(self, evento_id: uuid.UUID, amplitud: float) -> None:
        """Actualiza la amplitud de un evento (boost de novedad post-index)."""
        stmt = (
            update(EventoModel)
            .where(EventoModel.id == evento_id)
            .values(amplitud=amplitud)
        )
        await self._session.execute(stmt)

    async def atenuar_valencias(self, pares: list[tuple[uuid.UUID, float]]) -> int:
        """Extingue la valencia de recuerdos evocados en estado neutro.

        Args:
            pares: ``(evento_id, nueva_valencia)`` ya calculados por
                :func:`retrieval.extincion_valencia` — el repo solo
                aplica, no decide.

        Returns:
            Filas actualizadas.
        """
        if not pares:
            return 0
        stmt = (
            update(EventoModel)
            .where(EventoModel.id.in_([eid for eid, _ in pares]))
            .values(
                estado_emocional=case(
                    *[
                        (EventoModel.id == eid, nueva)
                        for eid, nueva in pares
                    ]
                )
            )
        )
        resultado = cast(CursorResult[EventoModel], await self._session.execute(stmt))
        return resultado.rowcount

    async def embeddings_recientes(
        self, limite: int = 20
    ) -> Sequence[EventoModel]:
        """Eventos recientes con embedding — la trayectoria esperada.

        La sorpresa del evento entrante se mide contra esta ventana,
        no contra lo semánticamente más cercano.

        Args:
            limite: Tamaño de la ventana de expectativa.

        Returns:
            Eventos con embedding, más recientes primero.
        """
        stmt = (
            select(EventoModel)
            .where(
                EventoModel.embedding.is_not(None),
                EventoModel.archivado.is_(False),
                *self._vis(),
            )
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def episodicas_con_embedding(
        self, limite: int = 400
    ) -> Sequence[EventoModel]:
        """Episodios activos con embedding — materia prima del gist.

        La semanticización (sueño) agrupa estos por similitud y
        destila eventos ``semantica`` vinculados por aristas ``gist``.
        """
        stmt = (
            select(EventoModel)
            .where(
                EventoModel.tipo_memoria == "episodica",
                EventoModel.embedding.is_not(None),
                EventoModel.archivado.is_(False),
                EventoModel.reemplazado_por.is_(None),
                EventoModel.fuente != "sueno",
                *self._vis(),
            )
            .order_by(EventoModel.timestamp_unix.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def listar_archivados(self, limite: int = 100) -> Sequence[EventoModel]:
        """Eventos archivados por consolidación — recuperables.

        Args:
            limite: Máximo de resultados.

        Returns:
            Eventos archivados, del más reciente al más antiguo.
        """
        stmt = (
            select(EventoModel)
            .where(EventoModel.archivado.is_(True), *self._vis())
            .order_by(EventoModel.archivado_en.desc().nulls_last())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def restaurar(self, evento_id: uuid.UUID) -> bool:
        """Restaura un evento archivado — deshace el olvido.

        Restaurar sube ``weight`` a 1.0 (re-aprendido) y limpia las
        marcas de archivo. Es el "me acordé igual" de la UX reversible.

        Args:
            evento_id: UUID del evento archivado.

        Returns:
            True si el evento existía, estaba archivado y se restauró.
        """
        stmt = (
            update(EventoModel)
            .where(
                EventoModel.id == evento_id,
                EventoModel.archivado.is_(True),
            )
            .values(
                archivado=False,
                archivado_en=None,
                razon_archivo=None,
                weight=1.0,
            )
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
        return resultado.rowcount > 0

    async def marcar_curado(
        self, evento_id: uuid.UUID, curado: bool = True
    ) -> bool:
        """Marca o desmarca un evento como curado (inmune al olvido).

        El tier curado es el MEMORY.md vivo (ADR-003): ni decay ni
        consolidación lo tocan. Respeta la visibilidad del workspace.

        Args:
            evento_id: UUID del evento.
            curado: True para curar, False para devolver al ciclo normal.

        Returns:
            True si el evento existía dentro de la visibilidad y cambió.
        """
        stmt = (
            update(EventoModel)
            .where(EventoModel.id == evento_id, *self._vis())
            .values(curado=curado)
        )
        resultado = cast(
            CursorResult[EventoModel], await self._session.execute(stmt)
        )
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

    async def reforzar_coactivas(
        self,
        pares: list[tuple[uuid.UUID, uuid.UUID]],
        delta: float,
        peso_max: float,
    ) -> int:
        """Crea o refuerza aristas ``coactiva`` entre co-evocados.

        Hebbiana: cada co-recall suma ``delta`` al peso de la arista
        con tope ``peso_max`` — la asociación se vuelve estable con
        el uso, no se duplica. El par viaja en orden canónico.

        Args:
            pares: Pares de UUIDs co-evocados (de
                :func:`retrieval.pares_coactivos`).
            delta: Incremento por co-evocación.
            peso_max: Tope fisiológico del vínculo.

        Returns:
            Aristas tocadas (creadas o reforzadas).
        """
        tocadas = 0
        for a_id, b_id in pares:
            if a_id == b_id:
                continue
            origen, destino = (a_id, b_id) if a_id < b_id else (b_id, a_id)
            stmt = (
                pg_insert(RelacionModel)
                .values(
                    origen_id=origen,
                    destino_id=destino,
                    tipo="coactiva",
                    peso=delta,
                )
                .on_conflict_do_update(
                    constraint="uq_relaciones_par_tipo",
                    set_={
                        "peso": func.least(
                            RelacionModel.peso + delta, peso_max
                        )
                    },
                )
            )
            resultado = await self._session.execute(stmt)
            tocadas += cast(
                CursorResult[RelacionModel], resultado
            ).rowcount
        return tocadas

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

    async def es_confiable(self, nombre: str) -> bool:
        """True si la fuente está aprobada (default: confiable).

        Una fuente no registrada cuenta como confiable — la
        cuarentena solo aplica a fuentes explícitamente marcadas
        como no confiables, no a cualquier etiqueta nueva.
        """
        stmt = select(FuenteModel.confiable).where(
            FuenteModel.nombre == nombre,
            FuenteModel.workspace_id == self._ws,
        )
        resultado = await self._session.execute(stmt)
        valor = resultado.scalar_one_or_none()
        return True if valor is None else bool(valor)

    async def fuentes_confiables(
        self, nombres: Sequence[str]
    ) -> dict[str, bool]:
        """Mapa nombre → confiable para un lote de fuentes.

        Las no registradas no aparecen en el mapa — el llamante
        decide cómo tratar lo anónimo (amnesia de fuente).
        """
        if not nombres:
            return {}
        stmt = select(FuenteModel.nombre, FuenteModel.confiable).where(
            FuenteModel.nombre.in_(list(nombres)),
            FuenteModel.workspace_id == self._ws,
        )
        resultado = await self._session.execute(stmt)
        return {n: bool(c) for n, c in resultado.all()}

    async def marcar_confiable(self, nombre: str, confiable: bool) -> bool:
        """Marca una fuente como confiable o en cuarentena."""
        stmt = (
            update(FuenteModel)
            .where(
                FuenteModel.nombre == nombre,
                FuenteModel.workspace_id == self._ws,
            )
            .values(confiable=confiable)
        )
        resultado = cast(
            CursorResult[FuenteModel], await self._session.execute(stmt)
        )
        return resultado.rowcount > 0

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


class IntencionProspectivaRepository:
    """Acceso a datos de las intenciones prospectivas (ADR-003)."""

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async.

        Args:
            session: Sesión async de SQLAlchemy.
            ctx: Contexto de identidad para la visibilidad multi-tenant.
        """
        self._session = session
        self._ctx = ctx

    def _vis(self) -> list[ColumnElement[bool]]:
        """Filtros de visibilidad sobre ``intenciones_prospectivas``.

        La tabla no lleva ``scope``: una intención es siempre personal
        — se ve dentro del workspace pero solo del autor (o sin
        atribuir, importaciones/CLI).
        """
        if self._ctx is None:
            return []
        return [
            and_(
                IntencionProspectivaModel.workspace_id
                == self._ctx.workspace_id,
                or_(
                    IntencionProspectivaModel.autor_id
                    == self._ctx.usuario_id,
                    IntencionProspectivaModel.autor_id.is_(None),
                ),
            )
        ]

    async def crear(
        self, intencion: IntencionProspectivaModel
    ) -> IntencionProspectivaModel:
        """Persiste una intención prospectiva.

        Args:
            intencion: Instancia a persistir.

        Returns:
            La intención con su id asignado.
        """
        self._session.add(intencion)
        await self._session.flush()
        return intencion

    async def listar(
        self,
        ejecutadas: bool | None = None,
        limite: int = 100,
    ) -> Sequence[IntencionProspectivaModel]:
        """Lista intenciones del workspace.

        Args:
            ejecutadas: None = todas; False = solo pendientes
                (ni ejecutadas ni canceladas); True = ya disparadas.
            limite: Máximo de filas.

        Returns:
            Intenciones ordenadas de la más nueva a la más vieja.
        """
        filtros = [*self._vis()]
        if ejecutadas is False:
            filtros.append(IntencionProspectivaModel.ejecutada.is_(False))
            filtros.append(IntencionProspectivaModel.cancelada.is_(False))
        elif ejecutadas is True:
            filtros.append(IntencionProspectivaModel.ejecutada.is_(True))
        stmt = (
            select(IntencionProspectivaModel)
            .where(*filtros)
            .order_by(IntencionProspectivaModel.creada_en.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def pendientes_temporales(
        self, ahora_unix: int
    ) -> Sequence[IntencionProspectivaModel]:
        """Intenciones time-based cuyo trigger ya venció.

        Args:
            ahora_unix: Timestamp actual; el trigger madura cuando
                ``trigger_valor['ts'] <= ahora``.

        Returns:
            Intenciones pendientes con trigger vencido.
        """
        stmt = select(IntencionProspectivaModel).where(
            IntencionProspectivaModel.trigger_tipo == "time",
            IntencionProspectivaModel.ejecutada.is_(False),
            IntencionProspectivaModel.cancelada.is_(False),
            # Snooze: una intención aplazada no dispara aunque su
            # trigger ya haya vencido — el aplazamiento manda.
            or_(
                IntencionProspectivaModel.aplazada_hasta.is_(None),
                IntencionProspectivaModel.aplazada_hasta <= ahora_unix,
            ),
            IntencionProspectivaModel.trigger_valor["ts"].astext.cast(
                BigInteger
            )
            <= ahora_unix,
            *self._vis(),
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def pendientes_por_evento(
        self, ahora_unix: int
    ) -> Sequence[IntencionProspectivaModel]:
        """Intenciones event-based pendientes — candidatas de evaluación.

        Args:
            ahora_unix: Timestamp actual; las aplazadas (snooze) con
                ``aplazada_hasta > ahora`` quedan fuera.

        Returns:
            Intenciones con ``trigger_tipo='event'`` sin ejecutar ni
            cancelar ni aplazar.
        """
        stmt = select(IntencionProspectivaModel).where(
            IntencionProspectivaModel.trigger_tipo == "event",
            IntencionProspectivaModel.ejecutada.is_(False),
            IntencionProspectivaModel.cancelada.is_(False),
            or_(
                IntencionProspectivaModel.aplazada_hasta.is_(None),
                IntencionProspectivaModel.aplazada_hasta <= ahora_unix,
            ),
            *self._vis(),
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def marcar_ejecutada(
        self, intencion_id: uuid.UUID, ahora_unix: int
    ) -> bool:
        """Marca una intención como disparada.

        Args:
            intencion_id: UUID de la intención.
            ahora_unix: Timestamp de la ejecución.

        Returns:
            True si la intención estaba pendiente y se marcó.
        """
        stmt = (
            update(IntencionProspectivaModel)
            .where(
                IntencionProspectivaModel.id == intencion_id,
                IntencionProspectivaModel.ejecutada.is_(False),
                IntencionProspectivaModel.cancelada.is_(False),
            )
            .values(ejecutada=True, ejecutada_en=ahora_unix)
        )
        resultado = cast(
            CursorResult[IntencionProspectivaModel],
            await self._session.execute(stmt),
        )
        return resultado.rowcount > 0

    async def cancelar(
        self, intencion_id: uuid.UUID, ahora_unix: int
    ) -> bool:
        """Cancela una intención pendiente.

        Args:
            intencion_id: UUID de la intención.
            ahora_unix: Timestamp de la cancelación.

        Returns:
            True si la intención estaba pendiente y se canceló.
        """
        stmt = (
            update(IntencionProspectivaModel)
            .where(
                IntencionProspectivaModel.id == intencion_id,
                IntencionProspectivaModel.ejecutada.is_(False),
                IntencionProspectivaModel.cancelada.is_(False),
            )
            .values(cancelada=True, cancelada_en=ahora_unix)
        )
        resultado = cast(
            CursorResult[IntencionProspectivaModel],
            await self._session.execute(stmt),
        )
        return resultado.rowcount > 0

    async def aplazar(
        self, intencion_id: uuid.UUID, hasta_unix: int
    ) -> bool:
        """Pospone el disparo de una intención pendiente (snooze).

        La intención sigue viva — ni ejecutada ni cancelada — pero
        ningún trigger la evalúa hasta ``hasta_unix``.

        Returns:
            True si la intención estaba pendiente y se aplazó.
        """
        stmt = (
            update(IntencionProspectivaModel)
            .where(
                IntencionProspectivaModel.id == intencion_id,
                IntencionProspectivaModel.ejecutada.is_(False),
                IntencionProspectivaModel.cancelada.is_(False),
            )
            .values(aplazada_hasta=hasta_unix)
        )
        resultado = cast(
            CursorResult[IntencionProspectivaModel],
            await self._session.execute(stmt),
        )
        return resultado.rowcount > 0

    async def fijar_preparacion(
        self, intencion_id: uuid.UUID, pack: list[dict[str, Any]]
    ) -> None:
        """Adjunta el prep pack calculado al disparar la intención."""
        await self._session.execute(
            update(IntencionProspectivaModel)
            .where(IntencionProspectivaModel.id == intencion_id)
            .values(preparacion=pack)
        )


class ScriptRepository:
    """Acceso a datos de los scripts/patrones minados (ADR-003)."""

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async."""
        self._session = session
        self._ctx = ctx

    def _vis(self) -> list[ColumnElement[bool]]:
        """Filtros de visibilidad sobre ``scripts_detectados``.

        Los scripts son patrones del workspace entero (se minan de la
        línea completa) — sin ``scope``, el aislamiento es el
        workspace.
        """
        if self._ctx is None:
            return []
        return [
            ScriptDetectadoModel.workspace_id == self._ctx.workspace_id
        ]

    async def guardar(
        self, script: ScriptDetectadoModel
    ) -> ScriptDetectadoModel:
        """Persiste un script detectado (o actualiza si ya hay par).

        El upsert se hace por ``(workspace_id, nombre)``: minar dos
        veces el mismo patrón actualiza frecuencia/confianza en vez
        de duplicar la fila.

        Args:
            script: Instancia a persistir.

        Returns:
            El script persistido con id asignado.
        """
        existente = (
            await self._session.execute(
                select(ScriptDetectadoModel).where(
                    ScriptDetectadoModel.workspace_id
                    == script.workspace_id,
                    ScriptDetectadoModel.nombre == script.nombre,
                )
            )
        ).scalar_one_or_none()
        if existente is not None:
            existente.pasos = script.pasos
            existente.frecuencia = script.frecuencia
            existente.confianza = script.confianza
            existente.actualizado_en = script.actualizado_en
            await self._session.flush()
            return existente
        self._session.add(script)
        await self._session.flush()
        return script

    async def listar(
        self, limite: int = 50
    ) -> Sequence[ScriptDetectadoModel]:
        """Lista los scripts detectados por confianza descendente.

        Args:
            limite: Máximo de filas.

        Returns:
            Scripts del workspace, del más confiable al menos.
        """
        stmt = (
            select(ScriptDetectadoModel)
            .where(*self._vis())
            .order_by(ScriptDetectadoModel.confianza.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()


class ReporteConsolidacionRepository:
    """Acceso a datos de los reportes de consolidación (dream diary)."""

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa el repositorio con una sesión async."""
        self._session = session
        self._ctx = ctx

    def _vis(self) -> list[ColumnElement[bool]]:
        """Filtros de visibilidad sobre ``reportes_consolidacion``."""
        if self._ctx is None:
            return []
        return [
            ReporteConsolidacionModel.workspace_id == self._ctx.workspace_id
        ]

    async def crear(
        self, reporte: ReporteConsolidacionModel
    ) -> ReporteConsolidacionModel:
        """Persiste el reporte de una corrida de consolidación.

        Args:
            reporte: Instancia a persistir.

        Returns:
            El reporte con id asignado.
        """
        self._session.add(reporte)
        await self._session.flush()
        return reporte

    async def listar(
        self, limite: int = 20
    ) -> Sequence[ReporteConsolidacionModel]:
        """Lista reportes recientes primero.

        Args:
            limite: Máximo de filas.

        Returns:
            Reportes del workspace por ``ejecutado_en`` descendente.
        """
        stmt = (
            select(ReporteConsolidacionModel)
            .where(*self._vis())
            .order_by(ReporteConsolidacionModel.ejecutado_en.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()


class BorradorRepository:
    """Acceso a datos de la memoria de trabajo (buffer de Baddeley).

    Visibilidad: workspace ∪ propios ∪ sin atribuir — como los
    eventos, salvo que el borrador no tiene ``scope``: es efímero
    por naturaleza y siempre personal de quien lo escribió.
    """

    def __init__(
        self, session: AsyncSession, ctx: Contexto | None = None
    ) -> None:
        """Inicializa con sesión y contexto de visibilidad opcional."""
        self._session = session
        self._ctx = ctx

    def _vis(self) -> list[ColumnElement[bool]]:
        if self._ctx is None:
            return []
        return [
            and_(
                BorradorModel.workspace_id == self._ctx.workspace_id,
                or_(
                    BorradorModel.autor_id == self._ctx.usuario_id,
                    BorradorModel.autor_id.is_(None),
                ),
            )
        ]

    async def crear(self, borrador: BorradorModel) -> BorradorModel:
        """Persiste un borrador en el buffer."""
        self._session.add(borrador)
        await self._session.flush()
        return borrador

    async def obtener(self, borrador_id: uuid.UUID) -> BorradorModel | None:
        """Retorna el borrador por id si es visible."""
        stmt = select(BorradorModel).where(
            BorradorModel.id == borrador_id, *self._vis()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def listar_vigentes(
        self, ahora: int, limite: int = 50
    ) -> Sequence[BorradorModel]:
        """Borradores no expirados — el contenido vivo del buffer.

        Args:
            ahora: Timestamp unix actual.
            limite: Máximo de filas.

        Returns:
            Borradores vigentes, más recientes primero.
        """
        stmt = (
            select(BorradorModel)
            .where(BorradorModel.expira_en > ahora, *self._vis())
            .order_by(BorradorModel.creado_en.desc())
            .limit(limite)
        )
        resultado = await self._session.execute(stmt)
        return resultado.scalars().all()

    async def contar_vigentes(self, ahora: int) -> int:
        """Cuántos borradores vivos hay — control de capacidad."""
        stmt = select(func.count(BorradorModel.id)).where(
            BorradorModel.expira_en > ahora, *self._vis()
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def ensayar(self, borrador_id: uuid.UUID) -> int | None:
        """Suma un ensayo (repetición) al borrador.

        Returns:
            El nuevo conteo de ensayos, o None si no existe/expiró.
        """
        stmt = (
            update(BorradorModel)
            .where(BorradorModel.id == borrador_id, *self._vis())
            .values(ensayos=BorradorModel.ensayos + 1)
            .returning(BorradorModel.ensayos)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def descartar(self, borrador_id: uuid.UUID) -> bool:
        """Saca el borrador del buffer (promovido o descartado)."""
        stmt = delete(BorradorModel).where(
            BorradorModel.id == borrador_id, *self._vis()
        )
        resultado = cast(
            CursorResult[BorradorModel], await self._session.execute(stmt)
        )
        return resultado.rowcount > 0
