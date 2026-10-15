"""Tools MCP de dominio de Nous: memoria persistente 4D.

Cada tool declara explícitamente cuándo usarla y cuándo NO, para que
el modelo que la invoque no confunda sus responsabilidades.
"""

import asyncio
import gc
import logging
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import get_settings
from src.core.busqueda import BusquedaHibrida4D
from src.core.clasificador import ClasificadorDimensiones
from src.core.contexto import analizar_contexto, fusionar_tags, parsear_ttl
from src.core.embeddings import GeneradorEmbeddings
from src.core.episodios import GAP_MAX_DEFECTO_SEGUNDOS, segmentar
from src.core.exceptions import ErrorValidacion
from src.core.modelos import Evento, ResultadoBusqueda, Trayectoria
from src.core.reranker import ReordenadorLocal
from src.core.temporal import detectar_trayectorias, proyectar
from src.mcp.server import mcp_server
from src.storage.database import crear_engine, get_session, obtener_engine
from src.storage.models import EventoModel, ObjetivoModel, ProyeccionModel
from src.storage.repositories import (
    ConfiguracionRepository,
    EventoRepository,
    FuenteRepository,
    ObjetivoRepository,
    ProyeccionRepository,
    RelacionRepository,
)

logger = logging.getLogger(__name__)

_embeddings: GeneradorEmbeddings | None = None
_busqueda: BusquedaHibrida4D | None = None
_clasificador: ClasificadorDimensiones | None = None
_reordenador: ReordenadorLocal | None = None


@dataclass(frozen=True, slots=True)
class _ItemIndexacion:
    """Trabajo de indexación write-behind para un evento insertado."""

    evento_id: uuid.UUID
    contenido: str
    ts: int
    amplitud: float


@dataclass(frozen=True, slots=True)
class _ItemConsolidacion:
    """Trabajo de consolidación: refuerzo de huellas evocadas."""

    eventos_ids: tuple[uuid.UUID, ...]


_ItemFondo = _ItemIndexacion | _ItemConsolidacion
_cola_fondo: queue.Queue[_ItemFondo] = queue.Queue()
_worker_thread: threading.Thread | None = None

# Actividad reciente del cerebro (ring buffer efímero, en memoria):
# cada recordar/consultar deja un evento para que el dashboard
# visualice los "pensamientos" en vivo sobre el grafo 3D.
_ACTIVIDAD_MAX = 100
_actividad: deque[dict[str, Any]] = deque(maxlen=_ACTIVIDAD_MAX)


def _registrar_actividad(kind: str, consulta: str, ids: list[str]) -> None:
    """Registra un evento de actividad cerebral para el dashboard.

    Args:
        kind: Tipo de acceso — ``store`` (huella nueva) o ``recall``
            (evocación que devolvió huellas).
        consulta: Texto que originó la actividad (truncado).
        ids: UUIDs de los eventos implicados.
    """
    if consulta == "__warmup__":
        return
    _actividad.appendleft(
        {
            "kind": kind,
            "q": consulta[:80],
            "ids": ids[:12],
            "ts": int(time.time()),
        }
    )


def actividad_reciente(limite: int = 50) -> list[dict[str, Any]]:
    """Devuelve los eventos de actividad más recientes, nuevos primero."""
    return list(_actividad)[: max(1, min(limite, _ACTIVIDAD_MAX))]


async def _preferencia_leer(clave: str) -> dict[str, Any]:
    """Lee un documento de preferencias persistido en PostgreSQL."""
    async with get_session(obtener_engine()) as session:
        valor = await ConfiguracionRepository(session).obtener(clave)
    return valor if isinstance(valor, dict) else {}


async def _preferencia_guardar(clave: str, valor: dict[str, Any]) -> None:
    """Persiste (upsert) un documento de preferencias en PostgreSQL."""
    async with get_session(obtener_engine()) as session:
        await ConfiguracionRepository(session).guardar(clave, valor)
        await session.commit()


def _encolar_fondo(item: _ItemFondo) -> None:
    """Encola trabajo write-behind en el worker dedicado.

    El worker vive en un thread propio con su propio event loop y su
    propio engine: la inferencia ONNX y los commits de indexación y
    consolidación no compiten por el loop que atiende las requests.
    """
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(
            target=_worker_indexacion,
            args=(_cola_fondo,),
            name="nous-fondo",
            daemon=True,
        )
        _worker_thread.start()
    _cola_fondo.put(item)


_QUIESCENCIA_SEGUNDOS = 0.5
_LOTE_MAX = 50


def _worker_indexacion(cola: queue.Queue[_ItemFondo]) -> None:
    """Procesa la cola de trabajos por lotes durante la quietud.

    Espera a que el torrente de escrituras se aquiete (~0.5s sin items
    nuevos) antes de procesar el lote acumulado: la consolidación de
    huellas ocurre en reposo, sin competir por CPU/GIL con las
    escrituras en curso. ``_LOTE_MAX`` acota el lote para que bajo
    carga continua el trabajo no se difiera indefinidamente.
    """
    loop = asyncio.new_event_loop()
    engine = crear_engine()
    try:
        while True:
            items = [cola.get()]
            while len(items) < _LOTE_MAX:
                try:
                    items.append(cola.get(timeout=_QUIESCENCIA_SEGUNDOS))
                except queue.Empty:
                    break
            try:
                loop.run_until_complete(_ejecutar_lote(engine, items))
            finally:
                for _ in items:
                    cola.task_done()
            # Recolección proactiva en la quietud: el GC rara vez se
            # dispara dentro de una request (umbral gen0 alto en el
            # proceso principal).
            gc.collect()
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()


async def _ejecutar_lote(engine: AsyncEngine, items: list[_ItemFondo]) -> None:
    """Ejecuta un lote de trabajos: dedup por item y consolidación fusionada."""
    for item in items:
        if isinstance(item, _ItemIndexacion):
            await _indexar_luego(engine, item)
    ids = [
        eid for item in items if isinstance(item, _ItemConsolidacion) for eid in item.eventos_ids
    ]
    if ids:
        await _consolidar_luego(engine, ids)


async def _drenar_fondo() -> None:
    """Espera a que terminen las tareas write-behind pendientes.

    Pensado para tests y apagado ordenado; en producción las tareas
    terminan solas unos milisegundos después de la respuesta.
    """
    await asyncio.to_thread(_cola_fondo.join)


def _obtener_embeddings() -> GeneradorEmbeddings:
    """Retorna el generador de embeddings compartido del proceso."""
    global _embeddings
    if _embeddings is None:
        _embeddings = GeneradorEmbeddings()
    return _embeddings


def _obtener_clasificador() -> ClasificadorDimensiones:
    """Retorna el clasificador de dimensiones compartido del proceso."""
    global _clasificador
    if _clasificador is None:
        _clasificador = ClasificadorDimensiones(_obtener_embeddings())
    return _clasificador


def _obtener_busqueda() -> BusquedaHibrida4D:
    """Retorna el orquestador de búsqueda compartido del proceso."""
    global _busqueda, _reordenador
    if _busqueda is None:
        settings = get_settings()
        _reordenador = ReordenadorLocal() if settings.reranker_habilitado else None
        _busqueda = BusquedaHibrida4D(
            _obtener_embeddings(),
            reordenador=_reordenador,
            vida_media_dias=settings.recencia_vida_media_dias,
        )
    return _busqueda


def precalentar_modelos() -> None:
    """Carga en segundo plano los modelos locales del proceso.

    Sin warm-up, la primera consulta paga ~10-30s de carga de
    sentence-transformers; con él, el servidor responde al protocolo
    inmediatamente mientras los modelos se cargan en un hilo aparte.
    Las cargas están protegidas por lock: una consulta que llegue
    durante el warm-up espera a que termine en vez de duplicar la
    carga.
    """
    _obtener_busqueda()
    _obtener_embeddings().precalentar()
    if _reordenador is not None:
        _reordenador.precalentar()


def _modelo_a_dict(modelo: EventoModel) -> dict[str, Any]:
    """Serializa un EventoModel ORM a dict JSON-compatible."""
    return {
        "id": str(modelo.id),
        "timestamp_unix": modelo.timestamp_unix,
        "dimension": modelo.dimension,
        "fuente": modelo.fuente,
        "contenido": modelo.contenido,
        "amplitud": modelo.amplitud,
        "frecuencia": modelo.frecuencia,
        "refuerzos": modelo.refuerzos,
        "tags": list(modelo.tags or []),
        "rama": modelo.rama,
        "tipo": modelo.tipo,
        "expira_en": modelo.expira_en,
        "reemplazado_por": (
            str(modelo.reemplazado_por) if modelo.reemplazado_por else None
        ),
        "con_quien": modelo.con_quien,
    }


def _evento_a_dict(evento: Evento) -> dict[str, Any]:
    """Serializa el dataclass de dominio a dict JSON-compatible."""
    return {
        "id": evento.id,
        "timestamp_unix": evento.timestamp_unix,
        "dimension": str(evento.dimension),
        "contenido": evento.contenido,
        "amplitud": evento.amplitud,
        "refuerzos": evento.refuerzos,
    }


def _resultado_a_dict(r: ResultadoBusqueda) -> dict[str, Any]:
    """Serializa un resultado de búsqueda con su contexto temporal."""
    assert r.evento is not None
    contexto = None
    if r.contexto is not None:
        contexto = {
            "previos": [_evento_a_dict(e) for e in r.contexto.previos],
            "siguientes": [_evento_a_dict(e) for e in r.contexto.siguientes],
        }
    return {
        "evento": _evento_a_dict(r.evento),
        "score_final": round(r.score_final, 4),
        "score_bm25": round(r.score_bm25, 4),
        "score_vectorial": round(r.score_vectorial, 4),
        "contexto_temporal": contexto,
    }


TIPOS_EVENTO = ("evento", "hecho", "creencia", "compromiso", "preferencia")


async def _recordar(
    contenido: str,
    dimension: Literal["cognitiva", "somatica", "conductual"] | None,
    fuente: str,
    amplitud: float,
    frecuencia: float | None,
    timestamp_unix: int | None,
    relaciona_ids: list[str] | None = None,
    tags: list[str] | None = None,
    rama: str = "main",
    tipo: str = "evento",
    ttl: str | int | None = None,
    reemplaza_id: str | None = None,
    con_quien: str | None = None,
) -> dict[str, Any]:
    """Persiste un evento con indexación write-behind.

    Inserta la fila sin embedding (la columna tsvector computed la hace
    buscable por texto al instante) y delega embedding + dedup semántico
    a una tarea en segundo plano, de modo que la respuesta no paga la
    inferencia del modelo (~30-50ms). Si ``relaciona_ids`` trae UUIDs de
    eventos existentes, se registran vínculos explícitos con ellos.
    ``reemplaza_id`` declara un supersede: el evento viejo queda para
    arqueología y el nuevo encabeza la cadena. ``tipo="compromiso"``
    abre un loop que permanece abierto hasta que un evento lo cierre
    (relación ``cierra``). ``ttl`` acepta "30d"/"12h"/epoch.
    """
    if not 0.0 <= amplitud <= 1.0:
        raise ErrorValidacion("amplitud debe estar entre 0.0 y 1.0")
    if frecuencia is not None and frecuencia <= 0.0:
        raise ErrorValidacion("frecuencia debe ser positiva (Hz)")
    if tipo not in TIPOS_EVENTO:
        raise ErrorValidacion(
            f"tipo debe ser uno de {', '.join(TIPOS_EVENTO)}"
        )
    ts = timestamp_unix if timestamp_unix is not None else int(time.time())
    try:
        expira_en = parsear_ttl(ttl, ts)
    except ValueError as e:
        raise ErrorValidacion(str(e)) from e
    if con_quien and tipo != "compromiso":
        raise ErrorValidacion("con_quien solo aplica a tipo='compromiso'")
    respuesta: dict[str, Any] = {}
    if dimension is None:
        resultado = await _obtener_clasificador().clasificar(contenido)
        dimension = resultado.dimension.value
        respuesta["confianza_clasificacion"] = resultado.confianza
        respuesta["dimension_dudosa"] = resultado.dudoso
    tags_final = fusionar_tags(tags, contenido)
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session)
        evento = EventoModel(
            timestamp_unix=ts,
            dimension=dimension,
            fuente=fuente,
            contenido=contenido,
            embedding=None,
            amplitud=amplitud,
            frecuencia=frecuencia,
            tags=tags_final,
            rama=rama,
            tipo=tipo,
            expira_en=expira_en,
            con_quien=con_quien,
        )
        await repo.crear(evento)
        await FuenteRepository(session).crear(fuente, tipo="agente")
        respuesta["contexto"] = await analizar_contexto(
            session, contenido, excluir_id=evento.id
        )
        vinculos = 0
        relaciones = RelacionRepository(session)
        if reemplaza_id:
            try:
                viejo_id = uuid.UUID(str(reemplaza_id))
            except (ValueError, AttributeError) as e:
                raise ErrorValidacion(
                    f"reemplaza_id no es un UUID válido: {reemplaza_id!r}"
                ) from e
            if await repo.obtener(viejo_id) is None:
                raise ErrorValidacion(
                    f"el evento a reemplazar no existe: {reemplaza_id}"
                )
            await repo.marcar_reemplazado(viejo_id, evento.id)
            if await relaciones.vincular(viejo_id, evento.id, "reemplaza"):
                respuesta["reemplaza"] = str(viejo_id)
        if relaciona_ids:
            for texto_id in relaciona_ids[:20]:
                try:
                    otro_id = uuid.UUID(str(texto_id))
                except (ValueError, AttributeError):
                    continue
                if await repo.obtener(otro_id) is None:
                    continue
                if await relaciones.vincular(evento.id, otro_id, "explicita"):
                    vinculos += 1
        if vinculos:
            respuesta["vinculos_creados"] = vinculos
        await session.commit()
    _encolar_fondo(_ItemIndexacion(evento.id, contenido, ts, amplitud))
    _registrar_actividad("store", contenido, [str(evento.id)])
    respuesta.update(
        {
            "id": str(evento.id),
            "timestamp_unix": ts,
            "dimension": dimension,
            "tipo": tipo,
            "indexado": False,
        }
    )
    if expira_en:
        respuesta["expira_en"] = expira_en
    if con_quien:
        respuesta["con_quien"] = con_quien
    if tags_final:
        respuesta["tags"] = tags_final
    return respuesta


async def _indexar_luego(engine: AsyncEngine, item: _ItemIndexacion) -> None:
    """Embebe y deduplica un evento recién insertado (write-behind).

    Si encuentra una huella semánticamente idéntica dentro de la
    ventana de dedup, la nueva se fusiona en la existente (refuerzo +
    amplitud máxima) en vez de quedar duplicada.
    """
    try:
        vector = await _obtener_embeddings().generar(item.contenido)
        settings = get_settings()
        async with get_session(engine) as session:
            repo = EventoRepository(session)
            similar = await repo.buscar_por_vector(
                vector, limite=settings.relacion_max + 2
            )
            duplicado = (
                similar
                and similar[0][0].id != item.evento_id
                and similar[0][1] >= settings.dedup_umbral
                and abs(item.ts - similar[0][0].timestamp_unix) <= settings.dedup_ventana_segundos
            )
            if duplicado:
                existente = similar[0][0]
                existente.refuerzos += 1
                existente.amplitud = max(existente.amplitud, item.amplitud)
                if not await repo.eliminar_debil(item.evento_id):
                    await repo.actualizar_embedding(item.evento_id, vector)
            else:
                await repo.actualizar_embedding(item.evento_id, vector)
                # Vínculos semánticos: el nuevo evento se ata a las
                # huellas previas por encima del umbral (debajo del de
                # dedup — esas son "el mismo" evento, no un vínculo).
                relaciones = RelacionRepository(session)
                creados = 0
                for vecino, sim in similar:
                    if creados >= settings.relacion_max:
                        break
                    if (
                        vecino.id != item.evento_id
                        and settings.relacion_umbral
                        <= sim
                        < settings.dedup_umbral
                    ):
                        if await relaciones.vincular(
                            item.evento_id, vecino.id, "semantica", sim
                        ):
                            creados += 1
            await session.commit()
    except Exception:  # noqa: BLE001 — el fallo no debe propagarse al loop
        logger.exception("indexacion en segundo plano fallo para %s", item.evento_id)


async def _consolidar_luego(engine: AsyncEngine, eventos_ids: list[uuid.UUID]) -> None:
    """Persiste los refuerzos de evocación fuera del camino caliente."""
    try:
        async with get_session(engine) as session:
            await EventoRepository(session).reforzar(eventos_ids)
            await session.commit()
    except Exception:  # noqa: BLE001 — el fallo no debe propagarse al loop
        logger.exception("no se pudieron consolidar refuerzos")


async def _consultar(
    consulta: str, top_n: int, rama: str | None = None
) -> list[dict[str, Any]]:
    """Ejecuta la búsqueda híbrida 4D y serializa los resultados."""
    async with get_session(obtener_engine()) as session:
        resultados = await _obtener_busqueda().buscar(
            session,
            consulta,
            top_n=max(1, min(top_n, 50)),
            rama=rama,
        )
    _encolar_fondo(_ItemConsolidacion(tuple(uuid.UUID(r.evento_id) for r in resultados)))
    if resultados:
        _registrar_actividad("recall", consulta, [r.evento_id for r in resultados])
    return [_resultado_a_dict(r) for r in resultados]


async def _tags(limite: int) -> list[dict[str, Any]]:
    """Lista los tags agregados con su frecuencia de uso."""
    async with get_session(obtener_engine()) as session:
        pares = await EventoRepository(session).listar_tags(limite)
    return [{"tag": tag, "eventos": n} for tag, n in pares]


async def _fuentes() -> list[dict[str, Any]]:
    """Lista las fuentes registradas con su conteo de eventos."""
    async with get_session(obtener_engine()) as session:
        filas = await FuenteRepository(session).listar()
        # Fuentes usadas pero nunca registradas (datos antiguos).
        registradas = {f.nombre for f, _ in filas}
        resultado = [
            {
                "nombre": f.nombre,
                "tipo": f.tipo,
                "estado": f.estado,
                "namespace": f.namespace,
                "rama_origen": f.rama_origen,
                "metadata": f.extra,
                "eventos": n,
            }
            for f, n in filas
        ]
        crudo = await session.execute(
            select(EventoModel.fuente, func.count())
            .group_by(EventoModel.fuente)
        )
        for nombre, n in crudo.all():
            if nombre not in registradas:
                resultado.append(
                    {
                        "nombre": nombre,
                        "tipo": "manual",
                        "estado": "activa",
                        "namespace": "",
                        "rama_origen": "main",
                        "metadata": {},
                        "eventos": int(n),
                    }
                )
    return resultado


async def _crear_fuente(
    nombre: str,
    tipo: str,
    namespace: str,
    rama_origen: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Registra una fuente de datos (idempotente por nombre)."""
    async with get_session(obtener_engine()) as session:
        fuente = await FuenteRepository(session).crear(
            nombre, tipo=tipo, namespace=namespace,
            rama_origen=rama_origen, extra=metadata,
        )
        await session.commit()
    return {
        "nombre": fuente.nombre,
        "tipo": fuente.tipo,
        "estado": fuente.estado,
        "namespace": fuente.namespace,
        "rama_origen": fuente.rama_origen,
    }


async def _estado_fuente(nombre: str, estado: str) -> bool:
    """Activa o archiva una fuente registrada."""
    async with get_session(obtener_engine()) as session:
        ok = await FuenteRepository(session).cambiar_estado(nombre, estado)
        await session.commit()
    return ok


async def _eliminar_fuente(nombre: str) -> bool:
    """Borra el registro de una fuente (sus eventos quedan intactos)."""
    async with get_session(obtener_engine()) as session:
        ok = await FuenteRepository(session).eliminar(nombre)
        await session.commit()
    return ok


async def _ramificar_fuente(
    nombre: str, rama: str, namespace: str
) -> dict[str, Any] | None:
    """Crea una línea de universo alterna derivada de una fuente.

    La rama nace como sub-fuente ``{nombre}:{rama}``: los eventos que
    se registren en ella viven en la línea paralela ``rama`` sin tocar
    la principal (comparten todo el pasado).
    """
    async with get_session(obtener_engine()) as session:
        repo = FuenteRepository(session)
        origen = await repo.obtener(nombre)
        if origen is None:
            return None
        derivada = await repo.crear(
            f"{nombre}:{rama}",
            tipo=origen.tipo,
            namespace=namespace or origen.namespace,
            rama_origen=rama,
            extra={"derivada_de": nombre},
        )
        await session.commit()
    return {
        "nombre": derivada.nombre,
        "tipo": derivada.tipo,
        "estado": derivada.estado,
        "namespace": derivada.namespace,
        "rama_origen": derivada.rama_origen,
    }


async def _relaciones(limite: int) -> list[dict[str, Any]]:
    """Lista los vínculos entre eventos (explícitos y semánticos)."""
    async with get_session(obtener_engine()) as session:
        filas = await RelacionRepository(session).listar(
            min(max(limite, 1), 5000)
        )
    return [
        {
            "origen_id": str(r.origen_id),
            "destino_id": str(r.destino_id),
            "tipo": r.tipo,
            "peso": r.peso,
        }
        for r in filas
    ]


async def _linea(
    desde_unix: int,
    hasta_unix: int,
    limite: int,
    rama: str | None = None,
    repartir: bool = False,
) -> list[dict[str, Any]]:
    """Devuelve los eventos del rango temporal en orden cronológico."""
    if desde_unix > hasta_unix:
        raise ErrorValidacion("desde_unix debe ser <= hasta_unix")
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session).listar_por_rango(
            desde_unix, hasta_unix, min(max(limite, 1), 4000), rama, repartir
        )
    return [_modelo_a_dict(e) for e in eventos]


async def _open_loops(rama: str = "main") -> list[dict[str, Any]]:
    """Lista los compromisos abiertos — quién espera respuesta tuya."""
    async with get_session(obtener_engine()) as session:
        loops = await EventoRepository(session).listar_compromisos_abiertos(rama)
    ahora = int(time.time())
    return [
        {
            **_modelo_a_dict(e),
            "dias_abierto": (ahora - e.timestamp_unix) // 86_400,
        }
        for e in loops
    ]


async def _cerrar_loop(
    compromiso_id: str,
    resolucion: str,
    fuente: str,
) -> dict[str, Any]:
    """Cierra un compromiso registrando su resolución como evento.

    El cierre es una transición con auditoría — el compromiso queda
    en la línea de universo enlazado por una relación ``cierra`` al
    evento de resolución, nunca borrado.
    """
    try:
        cid = uuid.UUID(str(compromiso_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"compromiso_id no es un UUID válido: {compromiso_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session)
        compromiso = await repo.obtener(cid)
        if compromiso is None:
            raise ErrorValidacion(f"compromiso no encontrado: {compromiso_id}")
        if compromiso.tipo != "compromiso":
            raise ErrorValidacion(
                f"el evento {compromiso_id} es tipo '{compromiso.tipo}', "
                "no 'compromiso'"
            )
    cierre = await _recordar(
        resolucion,
        "conductual",
        fuente,
        0.6,
        None,
        None,
        tags=["cierre"],
    )
    cierre_id = uuid.UUID(str(cierre["id"]))
    async with get_session(obtener_engine()) as session:
        await RelacionRepository(session).vincular(cid, cierre_id, "cierra")
        await session.commit()
    return {
        "compromiso_id": str(cid),
        "cierre_id": str(cierre_id),
        "estado": "cerrado",
    }


async def _olvidar(evento_id: str) -> dict[str, Any]:
    """Borra un evento de la memoria — derecho al olvido.

    Borrado duro e irreversible: el evento y sus vínculos desaparecen
    de la línea de universo. Para correcciones con historia usa
    ``reemplaza_id`` en ``recordar_evento`` en su lugar.
    """
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"evento_id no es un UUID válido: {evento_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        vinculos = await RelacionRepository(session).eliminar_de(eid)
        ok = await EventoRepository(session).eliminar(eid)
        await session.commit()
    return {
        "id": str(eid),
        "eliminado": ok,
        "vinculos_borrados": vinculos,
    }


def _objetivo_a_dict(o: ObjetivoModel) -> dict[str, Any]:
    """Serializa un objetivo a dict JSON-friendly."""
    return {
        "id": str(o.id),
        "titulo": o.titulo,
        "descripcion": o.descripcion,
        "dimension": o.dimension,
        "fecha_objetivo": o.fecha_objetivo,
        "estado": o.estado,
        "rama": o.rama,
        "evento_cierre_id": (
            str(o.evento_cierre_id) if o.evento_cierre_id else None
        ),
    }


async def _objetivos(
    estado: str | None = "activo", rama: str = "main"
) -> list[dict[str, Any]]:
    """Lista objetivos del usuario filtrados por estado y rama."""
    async with get_session(obtener_engine()) as session:
        filas = await ObjetivoRepository(session).listar(estado, rama)
    return [_objetivo_a_dict(o) for o in filas]


async def _crear_objetivo(
    titulo: str,
    fecha_objetivo: int,
    dimension: str | None,
    descripcion: str | None,
    rama: str,
) -> dict[str, Any]:
    """Persiste un objetivo que ancla una proyección futura."""
    titulo = titulo.strip()
    if not titulo:
        raise ErrorValidacion("el titulo del objetivo no puede estar vacío")
    if dimension is not None and dimension not in (
        "cognitiva", "somatica", "conductual",
    ):
        raise ErrorValidacion(
            f"dimension inválida: {dimension!r} "
            "(cognitiva|somatica|conductual)"
        )
    async with get_session(obtener_engine()) as session:
        objetivo = await ObjetivoRepository(session).crear(
            ObjetivoModel(
                titulo=titulo,
                descripcion=descripcion,
                dimension=dimension,
                fecha_objetivo=fecha_objetivo,
                rama=rama,
            )
        )
        await session.commit()
    return _objetivo_a_dict(objetivo)


async def _actualizar_objetivo(
    objetivo_id: str, cambios: dict[str, Any]
) -> dict[str, Any]:
    """Aplica cambios parciales a un objetivo existente."""
    try:
        oid = uuid.UUID(str(objetivo_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"objetivo_id no es un UUID válido: {objetivo_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        objetivo = await ObjetivoRepository(session).actualizar(oid, cambios)
        if objetivo is None:
            raise ErrorValidacion(f"objetivo no encontrado: {objetivo_id}")
        await session.commit()
    return _objetivo_a_dict(objetivo)


async def _cumplir_objetivo(
    objetivo_id: str, estado: str, resolucion: str | None, fuente: str
) -> dict[str, Any]:
    """Cierra un objetivo (cumplido/abandonado) con auditoría opcional.

    Si hay resolución, se registra como evento conductual enlazado al
    objetivo vía ``evento_cierre_id`` — el cierre es transición
    persistida, nunca borrado.
    """
    if estado not in ("cumplido", "abandonado"):
        raise ErrorValidacion(
            f"estado de cierre inválido: {estado!r} (cumplido|abandonado)"
        )
    cambios: dict[str, Any] = {"estado": estado}
    if resolucion:
        cierre = await _recordar(
            resolucion,
            "conductual",
            fuente,
            0.7,
            None,
            None,
            tags=["objetivo", estado],
        )
        cambios["evento_cierre_id"] = uuid.UUID(str(cierre["id"]))
    return await _actualizar_objetivo(objetivo_id, cambios)


async def _contexto_inicial(top_n: int = 8) -> dict[str, Any]:
    """Pack de arranque de sesión: memoria núcleo + loops + hechos
    calientes — cero LLM, todo consultas locales (estilo context_pack).

    Args:
        top_n: Eventos núcleo y hechos calientes a incluir (máx. 16).

    Returns:
        ``nucleo`` (eventos recientes de mayor amplitud), ``loops``
        (compromisos abiertos con su contraparte), ``calientes``
        (eventos de la última semana con más refuerzos) y ``stats``.
    """
    n = min(max(top_n, 1), 16)
    ahora = int(time.time())
    semana = ahora - 7 * 86_400
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session)
        recientes = await repo.listar_por_rango(semana, ahora, 500)
        loops = await repo.listar_compromisos_abiertos("main", ahora)
        total = int(
            (
                await session.execute(select(func.count(EventoModel.id)))
            ).scalar_one()
        )
    vigentes = [
        e
        for e in recientes
        if e.expira_en is None or e.expira_en > ahora
    ]
    nucleo = sorted(vigentes, key=lambda e: (-e.amplitud, -e.refuerzos))[:n]
    calientes = sorted(vigentes, key=lambda e: (-e.refuerzos, -e.timestamp_unix))[
        :n
    ]
    return {
        "nucleo": [_modelo_a_dict(e) for e in nucleo],
        "loops": [
            {
                **_modelo_a_dict(e),
                "dias_abierto": (ahora - e.timestamp_unix) // 86_400,
            }
            for e in loops
        ],
        "calientes": [_modelo_a_dict(e) for e in calientes],
        "stats": {
            "eventos_totales": total,
            "eventos_semana": len(recientes),
            "compromisos_abiertos": len(loops),
        },
    }


async def _proyectar(horizonte_dias: int, dias_historial: int) -> dict[str, Any]:
    """Detecta trayectorias, proyecta la dimensión futura y la persiste."""
    ahora = int(time.time())
    desde = ahora - min(max(dias_historial, 1), 365) * 86_400
    horizonte = min(max(horizonte_dias, 1), 90)
    async with get_session(obtener_engine()) as session:
        modelos = await EventoRepository(session).listar_por_rango(desde, ahora, 500)
        eventos = [Evento.desde_modelo(m) for m in modelos]
        proyeccion = proyectar(eventos, horizonte, ahora)
        trayectorias = detectar_trayectorias(eventos)
        persistida = await ProyeccionRepository(session).registrar(
            ProyeccionModel(
                horizonte_dias=horizonte,
                variable_objetivo=proyeccion.variable_objetivo,
                probabilidad=proyeccion.probabilidad,
                vector_proyectado=proyeccion.vector_proyectado,
                rama_decision=max(
                    proyeccion.vector_proyectado,
                    key=lambda d: proyeccion.vector_proyectado[d],
                ),
                basada_en_eventos=list(proyeccion.basada_en_eventos),
            )
        )
        await session.commit()
    return {
        "id": str(persistida.id),
        "horizonte_dias": horizonte,
        "dimension_probable": persistida.rama_decision,
        "probabilidad": proyeccion.probabilidad,
        "distribucion": proyeccion.vector_proyectado,
        "eventos_analizados": len(eventos),
        "trayectorias": [_trayectoria_a_dict(t) for t in trayectorias],
    }


@mcp_server.tool()
async def recordar_evento(
    contenido: str,
    dimension: Literal["cognitiva", "somatica", "conductual"] | None = None,
    fuente: str = "agente",
    amplitud: float = 0.5,
    frecuencia: float | None = None,
    timestamp_unix: int | None = None,
    relaciona_ids: list[str] | None = None,
    tags: list[str] | None = None,
    rama: str = "main",
    tipo: str = "evento",
    ttl: str | int | None = None,
    reemplaza_id: str | None = None,
    con_quien: str | None = None,
) -> dict[str, Any]:
    """Guarda un evento nuevo en la memoria persistente 4D del usuario.

    USAR CUANDO: haya que registrar un pensamiento, decisión, emoción,
    estado corporal o acción para consultarlo en el futuro.
    NO usar para buscar memorias existentes: eso es consultar_memoria.
    NO usar para listar un rango de fechas: obtener_linea_de_universo.

    El contenido se vectoriza localmente en segundo plano (nada sale
    de la máquina): la respuesta es inmediata ("indexado": false) y la
    búsqueda semántica lo encuentra milisegundos después. La búsqueda
    por texto lo ve al instante. Si no indicas dimension, se clasifica
    automáticamente por semejanza semántica.
    Separación de patrones: un evento casi idéntico registrado dentro
    de la última hora no se duplica — al indexarse se fusiona con el
    existente (refuerzo). Eventos recurrentes en momentos distintos sí
    crean huellas separadas.

    Args:
        contenido: Texto del evento (p. ej. "decidí usar PostgreSQL").
        dimension: "cognitiva" (ideas/decisiones/reflexiones),
            "somatica" (emociones/energía/cuerpo) o "conductual"
            (acciones/hábitos/commits). Omitir para clasificación automática.
        fuente: Origen del evento (p. ej. "devin", "claude", "usuario").
        amplitud: Intensidad/importancia entre 0.0 y 1.0. Default 0.5.
        frecuencia: Frecuencia cerebral estimada en Hz si proviene de
            un wearable/EEG (Delta <4, Theta 4-8, Alfa 8-13, Beta 13-30,
            Gamma >30). Omitir si no hay dato biométrico; Nous no la
            estima por sí solo.
        timestamp_unix: Unix epoch en SEGUNDOS. Default: ahora.
        relaciona_ids: UUIDs de eventos existentes a vincular
            explícitamente con este (p. ej. cuando el usuario declara
            que dos hechos van juntos). Los ids inválidos o inexistentes
            se ignoran.
        tags: Etiquetas que relacionan el contenido con otras memorias
            (p. ej. ["trabajo", "franz"]). Los #hashtags escritos en el
            propio contenido se extraen y fusionan automáticamente.
        rama: Línea de universo donde registrar el evento
            ("main" por defecto). Las ramas son líneas paralelas que
            divergen de la principal, útiles para escenarios.
        tipo: Clase epistémica del recuerdo — "evento" (default),
            "hecho" (dato objetivo), "creencia" (opinión revisable),
            "compromiso" (promesa pendiente → abre un loop que
            compromisos_abiertos lista hasta que se cierre) o
            "preferencia" (gusto del usuario).
        ttl: Caducidad del recuerdo — "30d", "12h", "45m", "2w" o
            epoch Unix. Omitido = no expira. Los expirados dejan de
            ser evocables por búsqueda pero quedan en la línea.
        reemplaza_id: UUID del evento que este sustituye (supersede):
            "mi peso es 79" reemplaza a "mi peso es 80". El viejo queda
            para arqueología en la línea; el nuevo encabeza la cadena.
        con_quien: Solo con tipo="compromiso" — la contraparte que
            espera respuesta (persona, equipo, sistema).

    Returns:
        Dict con "id", "timestamp_unix", "dimension" usada,
        "indexado" (false mientras el embedding se genera en segundo
        plano), "confianza_clasificacion" si se clasificó
        automáticamente, "vinculos_creados" si se pidieron vínculos y
        "contexto": {"completo": bool, ...} — si el evento menciona
        entidades sin antecedentes en la memoria, completo=false y la
        sugerencia indica qué preguntar al usuario para completar el
        panorama. RESPONDE preguntando por esa pieza cuando aparezca.
    """
    return await _recordar(
        contenido,
        dimension,
        fuente,
        amplitud,
        frecuencia,
        timestamp_unix,
        relaciona_ids,
        tags,
        rama,
        tipo,
        ttl,
        reemplaza_id,
        con_quien,
    )


@mcp_server.tool()
async def compromisos_abiertos(
    rama: str = "main",
) -> list[dict[str, Any]]:
    """Lista los compromisos abiertos — quién espera respuesta tuya.

    USAR CUANDO: preguntes "qué tengo pendiente", "a quién le debo
    respuesta" o quieras revisar promesas sin cerrar. Un compromiso
    permanece abierto hasta que se cierra con cerrar_compromiso —
    nunca se borra, solo cambia de estado.

    Args:
        rama: Línea de universo a consultar ("main" por defecto).

    Returns:
        Compromisos con id, contenido, con_quien (contraparte),
        timestamp_unix y dias_abierto — los más antiguos primero.
    """
    return await _open_loops(rama)


@mcp_server.tool()
async def cerrar_compromiso(
    compromiso_id: str,
    resolucion: str,
    fuente: str = "agente",
) -> dict[str, Any]:
    """Cierra un compromiso abierto registrando su resolución.

    USAR CUANDO: el usuario cumplió o descartó una promesa que estaba
    abierta. El cierre es una transición con auditoría — el compromiso
    queda en la línea enlazado al evento de resolución (relación
    "cierra"), nunca se borra.

    Args:
        compromiso_id: UUID del compromiso (de compromisos_abiertos).
        resolucion: Texto de cómo se resolvió ("le respondí a Franz").
        fuente: Origen del cierre (default "agente").

    Returns:
        Dict con compromiso_id, cierre_id (evento de resolución) y
        estado="cerrado".
    """
    return await _cerrar_loop(compromiso_id, resolucion, fuente)


@mcp_server.tool()
async def contexto_inicial(
    top_n: int = 8,
) -> dict[str, Any]:
    """Pack de arranque de sesión: qué traigo a esta conversación.

    USAR CUANDO: inicies una sesión con el usuario o tras un cambio de
    contexto — devuelve memoria núcleo + compromisos abiertos + hechos
    calientes en UNA llamada, sin LLM ni latencia de búsqueda. No
    reemplaza a consultar_memoria para preguntas concretas.

    Args:
        top_n: Eventos núcleo y hechos calientes a incluir (máx. 16).

    Returns:
        Dict con "nucleo" (recientes de mayor amplitud), "loops"
        (compromisos abiertos con contraparte y días de espera),
        "calientes" (más reforzados de la semana) y "stats" globales.
    """
    return await _contexto_inicial(top_n)


@mcp_server.tool()
async def listar_objetivos(
    estado: str = "activo",
    rama: str = "main",
) -> list[dict[str, Any]]:
    """Lista los objetivos del usuario — las metas que anclan el futuro.

    USAR CUANDO: preguntes "qué metas tengo", "para cuándo es X" o
    quieras ver hacia qué convergen las proyecciones. Un objetivo
    tiene fecha límite y dimensión opcional; su ciclo de vida es
    auditable (activo → cumplido/abandonado).

    Args:
        estado: Filtro de ciclo de vida ("activo" default,
            "cumplido", "abandonado", "*" para todos).
        rama: Línea de universo a consultar ("main" por defecto).

    Returns:
        Objetivos con id, titulo, dimension, fecha_objetivo, estado —
        ordenados por fecha límite, el más próximo primero.
    """
    return await _objetivos(None if estado == "*" else estado, rama)


@mcp_server.tool()
async def crear_objetivo(
    titulo: str,
    fecha_objetivo: int,
    dimension: str | None = None,
    descripcion: str | None = None,
    rama: str = "main",
) -> dict[str, Any]:
    """Fija una meta en la línea de universo — las proyecciones
    fantasma convergen hacia ella.

    USAR CUANDO: el usuario declare una meta con plazo ("quiero
    lanzar nous en junio", "meditar 30 días"). El timestamp es Unix
    epoch en SEGUNDOS. Sin dimensión = objetivo transversal.

    Args:
        titulo: Nombre corto de la meta.
        fecha_objetivo: Fecha límite, Unix epoch en segundos.
        dimension: cognitiva|somatica|conductual, o None transversal.
        descripcion: Detalle opcional de la meta.
        rama: Línea de universo donde vive ("main" por defecto).

    Returns:
        El objetivo creado con su id.
    """
    return await _crear_objetivo(
        titulo, fecha_objetivo, dimension, descripcion, rama
    )


@mcp_server.tool()
async def cerrar_objetivo(
    objetivo_id: str,
    estado: str,
    resolucion: str | None = None,
    fuente: str = "agente",
) -> dict[str, Any]:
    """Cierra un objetivo como cumplido o abandonado — transición
    auditable, nunca borrado.

    USAR CUANDO: el usuario logró o descartó una meta. Si pasas
    ``resolucion`` se registra como evento conductual enlazado al
    objetivo — la línea conserva cómo se resolvió.

    Args:
        objetivo_id: UUID del objetivo (de listar_objetivos).
        estado: "cumplido" o "abandonado".
        resolucion: Texto opcional del cierre ("lancé la beta").
        fuente: Origen del cierre (default "agente").

    Returns:
        El objetivo actualizado con su nuevo estado.
    """
    return await _cumplir_objetivo(objetivo_id, estado, resolucion, fuente)


@mcp_server.tool()
async def consultar_memoria(
    consulta: str,
    top_n: int = 10,
    rama: str = "main",
) -> list[dict[str, Any]]:
    """Busca memorias del usuario por TEMA con búsqueda híbrida 4D.

    USAR CUANDO: haya que recuperar lo que el usuario pensó, sintió o
    hizo relacionado con un tema ("qué decidí sobre X", "cómo me sentía
    la semana pasada"). Combina coincidencia textual + similitud
    semántica + reranking local, ordenado por relevancia.

    NO usar para guardar eventos: recordar_evento.
    NO usar para recorrer un rango de fechas: obtener_linea_de_universo.

    Args:
        consulta: Pregunta o tema en lenguaje natural.
        top_n: Máximo de resultados (1-50). Default 10.
        rama: Línea de universo a consultar — "main" por defecto, así
            los escenarios en ramas alternativas no contaminan el
            recuerdo de la línea real. Usa "*" para todas las ramas.

    Returns:
        Resultados ordenados por relevancia; cada uno trae el "evento"
        completo, sus scores y "contexto_temporal": los eventos que
        ocurrieron justo antes y después en la línea de tiempo.
    """
    return await _consultar(
        consulta, top_n, None if rama == "*" else rama
    )


@mcp_server.tool()
async def obtener_linea_de_universo(
    desde_unix: int,
    hasta_unix: int,
    limite: int = 100,
    rama: str = "main",
) -> list[dict[str, Any]]:
    """Devuelve los eventos de la línea temporal en un rango de fechas.

    USAR CUANDO: el usuario pregunte "qué pasó ayer/esta semana/entre X
    e Y" o se deba reconstruir su historia en un intervalo concreto.
    Devuelve eventos en orden cronológico, del más antiguo al más nuevo.

    NO usar para buscar por tema o semejanza: consultar_memoria.
    Los timestamps son Unix epoch en SEGUNDOS, no milisegundos.

    Args:
        desde_unix: Inicio del rango, Unix epoch en segundos (inclusive).
        hasta_unix: Fin del rango, Unix epoch en segundos (inclusive).
        limite: Máximo de eventos a devolver (1-500). Default 100.
        rama: Línea de universo a recorrer ("main" por defecto;
            "*" para todas las ramas).

    Returns:
        Eventos {"id","timestamp_unix","dimension","fuente","contenido",
        "amplitud","frecuencia"} ordenados cronológicamente.
    """
    return await _linea(
        desde_unix, hasta_unix, limite, None if rama == "*" else rama
    )


async def _episodios(
    desde_unix: int, hasta_unix: int, gap_max_segundos: int
) -> list[dict[str, Any]]:
    """Agrupa los eventos del rango en episodios por huecos temporales."""
    if desde_unix > hasta_unix:
        raise ErrorValidacion("desde_unix debe ser <= hasta_unix")
    gap = min(max(gap_max_segundos, 60), 86_400)
    async with get_session(obtener_engine()) as session:
        modelos = await EventoRepository(session).listar_por_rango(desde_unix, hasta_unix, 500)
    episodios = segmentar([Evento.desde_modelo(m) for m in modelos], gap_max_segundos=gap)
    return [
        {
            "inicio": e.inicio,
            "fin": e.fin,
            "eventos": len(e.eventos),
            "dimension_dominante": e.dimension_dominante.value,
            "amplitud_media": round(e.amplitud_media, 3),
        }
        for e in episodios
    ]


@mcp_server.tool()
async def obtener_episodios(
    desde_unix: int,
    hasta_unix: int,
    gap_max_segundos: int = GAP_MAX_DEFECTO_SEGUNDOS,
) -> list[dict[str, Any]]:
    """Agrupa la línea temporal en episodios por huecos de inactividad.

    USAR CUANDO: haya que entender la jornada del usuario en bloques
    ("¿cómo fue mi día?", "¿en qué sesiones trabajé esta semana?").
    Cada episodio resume inicio, fin, número de eventos y dimensión
    dominante — la estructura narrativa de la línea, no los eventos
    sueltos.

    NO usar para buscar por tema: consultar_memoria.
    NO usar para el detalle evento a evento: obtener_linea_de_universo.

    Args:
        desde_unix: Inicio del rango, Unix epoch en segundos.
        hasta_unix: Fin del rango, Unix epoch en segundos.
        gap_max_segundos: Hueco que separa dos episodios (60-86400).
            Default 14400 (4 horas).

    Returns:
        Episodios cronológicos con "inicio", "fin", "eventos",
        "dimension_dominante" y "amplitud_media".
    """
    return await _episodios(desde_unix, hasta_unix, gap_max_segundos)


def _trayectoria_a_dict(t: Trayectoria) -> dict[str, Any]:
    """Serializa una Trayectoria de dominio a dict JSON-compatible."""
    return {
        "tipo": t.tipo,
        "fecha_inicio": t.fecha_inicio,
        "fecha_fin": t.fecha_fin,
        "descripcion": t.descripcion,
        "eventos": len(t.eventos_ids),
        "amplitud_promedio": t.amplitud_promedio,
        "confianza": t.confianza,
        "activa": t.activa,
    }


@mcp_server.tool()
async def proyectar_trayectoria(
    horizonte_dias: int = 7,
    dias_historial: int = 30,
) -> dict[str, Any]:
    """Proyecta la dimensión probable del futuro del usuario.

    USAR CUANDO: pregunten hacia dónde va el usuario ("¿cómo ves mi
    semana?", "¿qué patrón se está formando?", "¿qué es probable que
    pase?"). Analiza el historial reciente, detecta rachas de dimensión
    activas y proyecta la distribución probable de la dimensión dominante.

    NO usar para recuperar memorias del pasado: consultar_memoria.
    NO usar para listar eventos de un rango: obtener_linea_de_universo.
    Cada llamada persiste la proyección como historial auditable.

    Args:
        horizonte_dias: Días hacia adelante a proyectar (1-90). Default 7.
        dias_historial: Días de historial a analizar (1-365). Default 30.

    Returns:
        Dict con "dimension_probable", "probabilidad", "distribucion"
        por dimensión, "trayectorias" detectadas (con flag "activa") e
        "id" de la proyección persistida en la tabla proyecciones.
    """
    return await _proyectar(horizonte_dias, dias_historial)


@mcp_server.tool()
async def olvidar_evento(evento_id: str) -> dict[str, Any]:
    """Borra un evento de la memoria para siempre — derecho al olvido.

    USAR CUANDO: el usuario pida explícitamente olvidar algo ("borra
    eso", "no quiero que recuerdes X"). El evento y sus vínculos
    desaparecen de la línea de universo; es irreversible.

    NO usar para corregir un dato que sigue siendo válido con otro
    valor: eso es ``reemplaza_id`` en recordar_evento (auditable).
    NO usar para compromisos resueltos: cerrar_compromiso (el loop
    queda cerrado en la línea, no borrado).

    Args:
        evento_id: UUID del evento a eliminar.

    Returns:
        Dict con "eliminado" y "vinculos_borrados".
    """
    return await _olvidar(evento_id)


@mcp_server.tool()
async def listar_tags(limite: int = 100) -> list[dict[str, Any]]:
    """Lista los tags de la memoria con su frecuencia de uso.

    USAR CUANDO: necesites descubrir los hilos temáticos del usuario
    antes de consultar (los tags enlazan eventos que comparten tema),
    o para revisar qué etiquetas acumuló la memoria.

    Args:
        limite: Máximo de tags (1-500). Default 100.

    Returns:
        [{"tag", "eventos"}] ordenados por frecuencia descendente.
    """
    return await _tags(min(max(limite, 1), 500))


@mcp_server.tool()
async def listar_fuentes() -> list[dict[str, Any]]:
    """Lista las fuentes registradas y las usadas sin registrar.

    USAR CUANDO: necesites saber de dónde viene la memoria (qué
    agentes/importaciones escriben) o antes de crear una rama con
    ramificar_fuente — la rama nace como sub-fuente de otra.

    Returns:
        Fuentes con nombre, tipo, estado, namespace, rama_origen y
        conteo de eventos.
    """
    return await _fuentes()


@mcp_server.tool()
async def ramificar_fuente(
    nombre: str,
    rama: str,
    namespace: str = "",
) -> dict[str, Any] | None:
    """Crea una línea de universo alterna derivada de una fuente.

    USAR CUANDO: el usuario quiera explorar un escenario sin tocar su
    historia real ("¿y si hubiera...?"). La rama nace como sub-fuente
    ``{nombre}:{rama}``: los eventos registrados bajo ella con
    ``rama=<rama>`` viven en la línea paralela — comparten el pasado
    pero divergen del presente, y consultar_memoria no los mezcla
    con la línea main salvo que se pida ``rama="*"``.

    Args:
        nombre: Fuente origen (de listar_fuentes).
        rama: Nombre de la línea alterna a crear.
        namespace: Agrupador opcional (hereda el de la fuente).

    Returns:
        La sub-fuente creada, o None si la fuente origen no existe.
    """
    return await _ramificar_fuente(nombre, rama, namespace)


@mcp_server.tool()
async def listar_relaciones(limite: int = 500) -> list[dict[str, Any]]:
    """Lista los vínculos entre memorias — el grafo explícito.

    USAR CUANDO: quieras ver cómo se conectan los recuerdos
    (explícitos declarados con relaciona_ids y semánticos inferidos),
    o auditar la estructura del grafo antes de proponer vínculos.

    Args:
        limite: Máximo de vínculos (1-5000). Default 500.

    Returns:
        [{"origen_id", "destino_id", "tipo", "peso"}] recientes
        primero.
    """
    return await _relaciones(limite)


@mcp_server.tool()
async def leer_preferencia(clave: str) -> dict[str, Any]:
    """Lee un documento de preferencias persistido del usuario.

    USAR CUANDO: necesites recordar una configuración duradera que el
    usuario pidió en otra sesión ("prefiero respuestas breves",
    "mi timezone es CDMX"). Las preferencias son JSON arbitrario
    namespacado por clave — sobreviven sesiones y reinicios.

    Args:
        clave: Clave del documento (p. ej. "estilo.respuestas").

    Returns:
        El documento guardado, o {} si no existe.
    """
    return await _preferencia_leer(clave)


@mcp_server.tool()
async def guardar_preferencia(
    clave: str, valor: dict[str, Any]
) -> dict[str, Any]:
    """Persiste una preferencia duradera del usuario (upsert por clave).

    USAR CUANDO: el usuario declare una preferencia estable de
    comportamiento/configuración que aplique a futuras sesiones. Para
    hechos sobre el mundo/la persona usa recordar_evento (tipo
    "preferencia") — esto es para ajustes operativos del agente.

    Args:
        clave: Clave del documento (p. ej. "estilo.respuestas").
        valor: Documento JSON a persistir.

    Returns:
        {"clave", "guardado": true}.
    """
    await _preferencia_guardar(clave, valor)
    return {"clave": clave, "guardado": True}
