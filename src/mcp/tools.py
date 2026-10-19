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
from dataclasses import dataclass, replace
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import get_settings
from src.core import identidad as idn
from src.core.busqueda import BusquedaHibrida4D
from src.core.clasificador import ClasificadorDimensiones
from src.core.contexto import analizar_contexto, fusionar_tags, parsear_ttl
from src.core.embeddings import GeneradorEmbeddings
from src.core.episodios import GAP_MAX_DEFECTO_SEGUNDOS, segmentar
from src.core.exceptions import ErrorValidacion, PermisoDenegadoError
from src.core.identidad import Contexto, contexto_actual, resolver_workspace
from src.core.memoria.consolidacion import (
    EventoConsolidable,
    decidir_consolidacion,
    resumen_legible,
)
from src.core.memoria.contexto import ContextoActual, boost_contextual
from src.core.memoria.interferencia import detectar_conflicto
from src.core.memoria.prospectiva import (
    Intencion,
    TriggerTipo,
    disparada_por_evento,
)
from src.core.memoria.scripts import (
    EventoSecuencia,
)
from src.core.memoria.scripts import (
    minar_scripts as _minar_patrones,
)
from src.core.memoria.tipos import (
    clasificar_tipo_memoria,
    estimar_estado_emocional,
)
from src.core.modelos import Evento, ResultadoBusqueda, Trayectoria
from src.core.reranker import ReordenadorLocal
from src.core.temporal import detectar_trayectorias, proyectar
from src.mcp.server import mcp_server
from src.storage.database import crear_engine, get_session, obtener_engine
from src.storage.models import (
    EventoModel,
    IntencionProspectivaModel,
    ObjetivoModel,
    ProyeccionModel,
    ReporteConsolidacionModel,
    ScriptDetectadoModel,
)
from src.storage.repositories import (
    ConfiguracionRepository,
    EventoRepository,
    FuenteRepository,
    IntencionProspectivaRepository,
    ObjetivoRepository,
    ProyeccionRepository,
    RelacionRepository,
    ReporteConsolidacionRepository,
    ScriptRepository,
)

logger = logging.getLogger(__name__)

_embeddings: GeneradorEmbeddings | None = None
_busqueda: BusquedaHibrida4D | None = None
_clasificador: ClasificadorDimensiones | None = None
_reordenador: ReordenadorLocal | None = None
_provider_decision: Any = None
_provider_resuelto = False


@dataclass(frozen=True, slots=True)
class _ItemIndexacion:
    """Trabajo de indexación write-behind para un evento insertado."""

    evento_id: uuid.UUID
    contenido: str
    ts: int
    amplitud: float
    workspace_id: uuid.UUID
    autor_id: uuid.UUID | None


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


async def _ctx(workspace: str | None = None) -> Contexto:
    """Resuelve el contexto de identidad de la llamada en curso.

    En HTTP lo fija el BearerTokenMiddleware por request; en stdio es
    la identidad del proceso (NOUS_API_KEY o el owner local).

    ``workspace`` (slug o UUID) permite a una tool operar sobre otro
    workspace del mismo usuario — la membresía en el destino fija el
    rol efectivo (grill-me Q6, opción C).
    """
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    if workspace is not None:
        ctx = await resolver_workspace(engine, ctx, workspace)
    return ctx


async def _ctx_escritura(workspace: str | None = None) -> Contexto:
    """Contexto con permiso de escritura; rechaza viewers."""
    ctx = await _ctx(workspace)
    if not ctx.puede_escribir:
        raise PermisoDenegadoError(
            "rol de solo lectura: sin permiso de escritura"
        )
    return ctx


async def _preferencia_leer(clave: str) -> dict[str, Any]:
    """Lee un documento de preferencias persistido en PostgreSQL."""
    ctx = await _ctx()
    async with get_session(obtener_engine()) as session:
        valor = await ConfiguracionRepository(session, ctx).obtener(clave)
    return valor if isinstance(valor, dict) else {}


async def _preferencia_guardar(clave: str, valor: dict[str, Any]) -> None:
    """Persiste (upsert) un documento de preferencias en PostgreSQL."""
    ctx = await _ctx_escritura()
    async with get_session(obtener_engine()) as session:
        await ConfiguracionRepository(session, ctx).guardar(clave, valor)
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
        _clasificador = ClasificadorDimensiones(
            _obtener_embeddings(), provider=_obtener_provider_decision()
        )
    return _clasificador


def _obtener_provider_decision() -> Any:
    """Provider System One configurado, o None (heurísticos puros).

    Se resuelve una sola vez por proceso: ``decision_provider='auto'``
    intenta Laya local → Ollama; sin ninguno disponible queda None y
    el pipeline usa el fallback léxico — nunca rompe por su ausencia.
    """
    global _provider_decision, _provider_resuelto
    if not _provider_resuelto:
        from src.core.decision.factory import construir_provider

        settings = get_settings()
        _provider_decision = construir_provider(
            settings.decision_provider,
            ollama_endpoint=settings.ollama_endpoint,
            ollama_model=settings.ollama_model,
            jev_api_key=settings.jev_api_key,
            jev_endpoint=settings.jev_endpoint,
            permitir_externos=settings.privacy_permitir_externos,
        )
        _provider_resuelto = True
    return _provider_decision


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
        "tipo_memoria": modelo.tipo_memoria,
        "weight": modelo.weight,
        "archivado": modelo.archivado,
        "curado": modelo.curado,
        "scope": modelo.scope,
        "workspace_id": str(modelo.workspace_id),
        "autor_id": str(modelo.autor_id) if modelo.autor_id else None,
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
        "contexto": evento.contexto,
        "estado_emocional": evento.estado_emocional,
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
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
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
    ctx = await _ctx_escritura(workspace)
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
        repo = EventoRepository(session, ctx)
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
            workspace_id=ctx.workspace_id,
            autor_id=ctx.usuario_id,
            scope=scope,
            # Contexto de codificación (ADR-003): la búsqueda
            # dependiente de contexto premia recuerdos grabados
            # en situaciones parecidas a la actual.
            contexto={
                "hora_dia": time.localtime(ts).tm_hour,
                "fuente": fuente,
            },
        )
        await repo.crear(evento)
        await FuenteRepository(session, ctx).crear(fuente, tipo="agente")
        respuesta["contexto"] = await analizar_contexto(
            session, contenido, excluir_id=evento.id, ctx=ctx
        )
        vinculos = 0
        relaciones = RelacionRepository(session, ctx)
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
    _encolar_fondo(
        _ItemIndexacion(
            evento.id,
            contenido,
            ts,
            amplitud,
            ctx.workspace_id,
            ctx.usuario_id,
        )
    )
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
            sobrevive = True
            if duplicado:
                existente = similar[0][0]
                existente.refuerzos += 1
                existente.amplitud = max(existente.amplitud, item.amplitud)
                if await repo.eliminar_debil(item.evento_id):
                    sobrevive = False
                else:
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
            if sobrevive:
                # ADR-003: tipo de memoria (provider o heurístico).
                tipo_mem = await clasificar_tipo_memoria(
                    item.contenido, _obtener_provider_decision()
                )
                await repo.fijar_tipo_memoria(
                    item.evento_id,
                    tipo_mem.value,
                    estado_emocional=estimar_estado_emocional(
                        item.contenido
                    ),
                )
                # ADR-003: interferencia — marcador de superación +
                # vecino semántico cercano = contradicción probable.
                conflictos = detectar_conflicto(
                    item.contenido,
                    [
                        (str(v.id), s)
                        for v, s in similar
                        if v.id != item.evento_id
                    ],
                )
                if conflictos:
                    relaciones = RelacionRepository(session)
                    for conflicto in conflictos[:3]:
                        await relaciones.vincular(
                            item.evento_id,
                            uuid.UUID(conflicto.evento_id),
                            "contradice",
                            conflicto.similitud,
                        )
            # ADR-003: memoria prospectiva — el evento entrante puede
            # disparar intenciones event-based del workspace, aunque la
            # huella se haya fusionado (la mención ocurrió igual).
            await _evaluar_intenciones_evento(session, item)
            await session.commit()
    except Exception:  # noqa: BLE001 — el fallo no debe propagarse al loop
        logger.exception("indexacion en segundo plano fallo para %s", item.evento_id)


async def _disparar_intencion(
    session: Any,
    repo: IntencionProspectivaRepository,
    modelo: IntencionProspectivaModel,
) -> EventoModel | None:
    """Marca una intención como ejecutada y deja su hecho recordatorio.

    El recordatorio entra a la línea de universo como ``hecho`` de
    fuente ``prospectiva`` — la próxima evocación lo hace visible, y
    el disparo queda auditado en la historia del usuario.

    Returns:
        El evento recordatorio creado, o None si la intención ya no
        estaba pendiente.
    """
    ahora = int(time.time())
    if not await repo.marcar_ejecutada(modelo.id, ahora):
        return None
    recordatorio = EventoModel(
        timestamp_unix=ahora,
        dimension="conductual",
        fuente="prospectiva",
        contenido=f"Intención prospectiva disparada: {modelo.intencion}",
        amplitud=0.7,
        tags=["intencion", "prospectiva"],
        tipo="hecho",
        workspace_id=modelo.workspace_id,
        autor_id=modelo.autor_id,
        scope="personal",
    )
    session.add(recordatorio)
    await session.flush()
    _encolar_fondo(
        _ItemIndexacion(
            recordatorio.id,
            recordatorio.contenido,
            ahora,
            0.7,
            modelo.workspace_id,
            modelo.autor_id,
        )
    )
    _registrar_actividad("intencion", modelo.intencion, [str(recordatorio.id)])
    return recordatorio


async def _evaluar_intenciones_evento(
    session: Any, item: _ItemIndexacion
) -> None:
    """Dispara intenciones event-based contra el evento entrante.

    Args:
        session: Sesión activa del worker de fondo.
        item: Trabajo de indexación del evento recién insertado.
    """
    repo = IntencionProspectivaRepository(
        session,
        Contexto(
            usuario_id=item.autor_id,
            workspace_id=item.workspace_id,
            rol="member",
        ),
    )
    pendientes = await repo.pendientes_por_evento()
    for modelo in pendientes:
        intencion = Intencion(
            id=str(modelo.id),
            intencion=modelo.intencion,
            trigger_tipo=TriggerTipo(modelo.trigger_tipo),
            trigger_valor=modelo.trigger_valor or {},
            creada_en=modelo.creada_en,
            ejecutada=modelo.ejecutada,
            cancelada=modelo.cancelada,
        )
        if disparada_por_evento(intencion, item.contenido):
            await _disparar_intencion(session, repo, modelo)


async def _disparar_intenciones_temporales(
    session: Any, ctx: Contexto
) -> list[dict[str, Any]]:
    """Dispara las intenciones time-based ya vencidas del workspace.

    Se llama desde el camino de consulta — cada evocación es la
    oportunidad natural de "acordarse" (no hay scheduler dedicado:
    Nous recuerda cuando se lo consulta).

    Returns:
        Recordatorios disparados (serializados) — posiblemente [].
    """
    repo = IntencionProspectivaRepository(session, ctx)
    vencidas = await repo.pendientes_temporales(int(time.time()))
    recordatorios = []
    for modelo in vencidas:
        evento = await _disparar_intencion(session, repo, modelo)
        if evento is not None:
            recordatorios.append(
                {
                    "id": str(evento.id),
                    "intencion": modelo.intencion,
                    "creada_en": modelo.creada_en,
                }
            )
    return recordatorios


async def _consolidar_luego(engine: AsyncEngine, eventos_ids: list[uuid.UUID]) -> None:
    """Persiste los refuerzos de evocación fuera del camino caliente."""
    try:
        async with get_session(engine) as session:
            await EventoRepository(session).reforzar(eventos_ids)
            await session.commit()
    except Exception:  # noqa: BLE001 — el fallo no debe propagarse al loop
        logger.exception("no se pudieron consolidar refuerzos")


async def _consultar(
    consulta: str,
    top_n: int,
    rama: str | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Ejecuta la búsqueda híbrida 4D y serializa los resultados."""
    ctx = await _ctx(workspace)
    recordatorios: list[dict[str, Any]] = []
    async with get_session(obtener_engine()) as session:
        # ADR-003: cada consulta es la oportunidad natural de
        # "acordarse" — las intenciones temporales vencidas disparan
        # aquí y encabezan la respuesta como recordatorios.
        vencidas = await _disparar_intenciones_temporales(session, ctx)
        if vencidas:
            await session.commit()
            recordatorios = [
                {
                    "evento": {
                        "id": v["id"],
                        "timestamp_unix": int(time.time()),
                        "dimension": "conductual",
                        "contenido": (
                            "Intención prospectiva disparada: "
                            + v["intencion"]
                        ),
                        "amplitud": 0.7,
                        "refuerzos": 0,
                    },
                    "score_final": 1.0,
                    "score_bm25": 0.0,
                    "score_vectorial": 0.0,
                    "contexto_temporal": None,
                    "recordatorio": True,
                }
                for v in vencidas
            ]
        resultados = await _obtener_busqueda().buscar(
            session,
            consulta,
            top_n=max(1, min(top_n, 50)),
            rama=rama,
            ctx=ctx,
        )
        if resultados:
            # ADR-003 patrón 6 — memoria dependiente de contexto/estado:
            # recuerdos codificados en una hora/fuente/estado parecidos
            # al actual suben de rango. Solo premia, nunca excluye.
            actual = ContextoActual(
                hora_dia=time.localtime().tm_hour,
                fuente="mcp",
                estado_emocional=await EventoRepository(
                    session, ctx
                ).ultimo_estado_somatico(),
            )
            resultados = [
                (
                    replace(
                        r,
                        score_final=boost_contextual(
                            r.score_final,
                            r.evento.contexto,
                            r.evento.estado_emocional,
                            actual,
                        ),
                    )
                    if r.evento is not None
                    else r
                )
                for r in resultados
            ]
            resultados.sort(key=lambda r: r.score_final, reverse=True)
    _encolar_fondo(_ItemConsolidacion(tuple(uuid.UUID(r.evento_id) for r in resultados)))
    if resultados:
        _registrar_actividad("recall", consulta, [r.evento_id for r in resultados])
    return recordatorios + [_resultado_a_dict(r) for r in resultados]


async def _tags(limite: int, workspace: str | None = None) -> list[dict[str, Any]]:
    """Lista los tags agregados con su frecuencia de uso."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        pares = await EventoRepository(session, ctx).listar_tags(limite)
    return [{"tag": tag, "eventos": n} for tag, n in pares]


async def _fuentes(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lista las fuentes registradas con su conteo de eventos."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        filas = await FuenteRepository(session, ctx).listar()
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
            .where(EventoModel.workspace_id == ctx.workspace_id)
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
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registra una fuente de datos (idempotente por nombre)."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        fuente = await FuenteRepository(session, ctx).crear(
            nombre, tipo=tipo, namespace=namespace,
            rama_origen=rama_origen, extra=metadata,
        )
        fuente.autor_id = ctx.usuario_id
        await session.commit()
    return {
        "nombre": fuente.nombre,
        "tipo": fuente.tipo,
        "estado": fuente.estado,
        "namespace": fuente.namespace,
        "rama_origen": fuente.rama_origen,
    }


async def _estado_fuente(nombre: str, estado: str, workspace: str | None = None) -> bool:
    """Activa o archiva una fuente registrada."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        ok = await FuenteRepository(session, ctx).cambiar_estado(nombre, estado)
        await session.commit()
    return ok


async def _eliminar_fuente(nombre: str, workspace: str | None = None) -> bool:
    """Borra el registro de una fuente (sus eventos quedan intactos)."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        ok = await FuenteRepository(session, ctx).eliminar(nombre)
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
    ctx = await _ctx_escritura()
    async with get_session(obtener_engine()) as session:
        repo = FuenteRepository(session, ctx)
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


async def _relaciones(limite: int, workspace: str | None = None) -> list[dict[str, Any]]:
    """Lista los vínculos entre eventos (explícitos y semánticos)."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        filas = await RelacionRepository(session, ctx).listar(
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
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Devuelve los eventos del rango temporal en orden cronológico."""
    if desde_unix > hasta_unix:
        raise ErrorValidacion("desde_unix debe ser <= hasta_unix")
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session, ctx).listar_por_rango(
            desde_unix, hasta_unix, min(max(limite, 1), 4000), rama, repartir
        )
    return [_modelo_a_dict(e) for e in eventos]


async def _open_loops(rama: str = "main", workspace: str | None = None) -> list[dict[str, Any]]:
    """Lista los compromisos abiertos — quién espera respuesta tuya."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        loops = await EventoRepository(session, ctx).listar_compromisos_abiertos(rama)
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
    ctx = await _ctx_escritura()
    try:
        cid = uuid.UUID(str(compromiso_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"compromiso_id no es un UUID válido: {compromiso_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
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
    ctx = await _ctx_escritura()
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"evento_id no es un UUID válido: {evento_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        # Borrado ajeno solo para owners: un member puede olvidar
        # sus propias memorias, no las del resto del workspace.
        repo = EventoRepository(session, ctx)
        objetivo_borrar = await repo.obtener(eid)
        if (
            objetivo_borrar is not None
            and not ctx.es_owner
            and objetivo_borrar.autor_id is not None
            and objetivo_borrar.autor_id != ctx.usuario_id
        ):
            raise PermisoDenegadoError(
                "solo el owner puede borrar memorias de otros miembros"
            )
        vinculos = await RelacionRepository(session).eliminar_de(eid)
        ok = await repo.eliminar(eid)
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
    estado: str | None = "activo",
    rama: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista objetivos del usuario filtrados por estado y rama."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        filas = await ObjetivoRepository(session, ctx).listar(estado, rama)
    return [_objetivo_a_dict(o) for o in filas]


async def _crear_objetivo(
    titulo: str,
    fecha_objetivo: int,
    dimension: str | None,
    descripcion: str | None,
    rama: str,
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
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
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        objetivo = await ObjetivoRepository(session, ctx).crear(
            ObjetivoModel(
                titulo=titulo,
                descripcion=descripcion,
                dimension=dimension,
                fecha_objetivo=fecha_objetivo,
                rama=rama,
                workspace_id=ctx.workspace_id,
                autor_id=ctx.usuario_id,
                scope=scope,
            )
        )
        await session.commit()
    return _objetivo_a_dict(objetivo)


async def _actualizar_objetivo(
    objetivo_id: str, cambios: dict[str, Any]
) -> dict[str, Any]:
    """Aplica cambios parciales a un objetivo existente."""
    ctx = await _ctx_escritura()
    try:
        oid = uuid.UUID(str(objetivo_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"objetivo_id no es un UUID válido: {objetivo_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        objetivo = await ObjetivoRepository(session, ctx).actualizar(oid, cambios)
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


async def _contexto_inicial(
    top_n: int = 8, workspace: str | None = None
) -> dict[str, Any]:
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
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        recientes = await repo.listar_por_rango(semana, ahora, 500)
        loops = await repo.listar_compromisos_abiertos("main", ahora)
        vis = repo._vis()
        total = int(
            (
                await session.execute(
                    select(func.count(EventoModel.id)).where(*vis)
                )
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


async def _proyectar(
    horizonte_dias: int,
    dias_historial: int,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Detecta trayectorias, proyecta la dimensión futura y la persiste."""
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    desde = ahora - min(max(dias_historial, 1), 365) * 86_400
    horizonte = min(max(horizonte_dias, 1), 90)
    async with get_session(obtener_engine()) as session:
        modelos = await EventoRepository(session, ctx).listar_por_rango(desde, ahora, 500)
        eventos = [Evento.desde_modelo(m) for m in modelos]
        proyeccion = proyectar(eventos, horizonte, ahora)
        trayectorias = detectar_trayectorias(eventos)
        persistida = await ProyeccionRepository(session, ctx).registrar(
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
                workspace_id=ctx.workspace_id,
                autor_id=ctx.usuario_id,
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
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
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
        scope: "personal" (solo tú lo ves, default — nada se filtra al
            equipo por accidente) o "workspace" (visible para todo el
            equipo del workspace).
        workspace: Slug o UUID del workspace destino (override del
            default de la key). Requiere membresía en el destino.

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
        scope,
        workspace,
    )


@mcp_server.tool()
async def compromisos_abiertos(
    rama: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista los compromisos abiertos — quién espera respuesta tuya.

    USAR CUANDO: preguntes "qué tengo pendiente", "a quién le debo
    respuesta" o quieras revisar promesas sin cerrar. Un compromiso
    permanece abierto hasta que se cierra con cerrar_compromiso —
    nunca se borra, solo cambia de estado.

    Args:
        rama: Línea de universo a consultar ("main" por defecto).
        workspace: Slug o UUID del workspace a consultar (override
            del default de la key).

    Returns:
        Compromisos con id, contenido, con_quien (contraparte),
        timestamp_unix y dias_abierto — los más antiguos primero.
    """
    return await _open_loops(rama, workspace)


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
    workspace: str | None = None,
) -> dict[str, Any]:
    """Pack de arranque de sesión: qué traigo a esta conversación.

    USAR CUANDO: inicies una sesión con el usuario o tras un cambio de
    contexto — devuelve memoria núcleo + compromisos abiertos + hechos
    calientes en UNA llamada, sin LLM ni latencia de búsqueda. No
    reemplaza a consultar_memoria para preguntas concretas.

    Args:
        top_n: Eventos núcleo y hechos calientes a incluir (máx. 16).
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Dict con "nucleo" (recientes de mayor amplitud), "loops"
        (compromisos abiertos con contraparte y días de espera),
        "calientes" (más reforzados de la semana) y "stats" globales.
    """
    return await _contexto_inicial(top_n, workspace)


@mcp_server.tool()
async def listar_objetivos(
    estado: str = "activo",
    rama: str = "main",
    workspace: str | None = None,
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
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Objetivos con id, titulo, dimension, fecha_objetivo, estado —
        ordenados por fecha límite, el más próximo primero.
    """
    return await _objetivos(None if estado == "*" else estado, rama, workspace)


@mcp_server.tool()
async def crear_objetivo(
    titulo: str,
    fecha_objetivo: int,
    dimension: str | None = None,
    descripcion: str | None = None,
    rama: str = "main",
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
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
        scope: "personal" (solo tú la ves, default) o "workspace"
            (meta del equipo, visible para todos).
        workspace: Slug o UUID del workspace destino.

    Returns:
        El objetivo creado con su id.
    """
    return await _crear_objetivo(
        titulo, fecha_objetivo, dimension, descripcion, rama, scope, workspace
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
    workspace: str | None = None,
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
        workspace: Slug o UUID del workspace a consultar (override del
            default de la key). Requiere membresía.

    Returns:
        Resultados ordenados por relevancia; cada uno trae el "evento"
        completo, sus scores y "contexto_temporal": los eventos que
        ocurrieron justo antes y después en la línea de tiempo.
    """
    return await _consultar(
        consulta, top_n, None if rama == "*" else rama, workspace
    )


@mcp_server.tool()
async def obtener_linea_de_universo(
    desde_unix: int,
    hasta_unix: int,
    limite: int = 100,
    rama: str = "main",
    workspace: str | None = None,
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
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Eventos {"id","timestamp_unix","dimension","fuente","contenido",
        "amplitud","frecuencia"} ordenados cronológicamente.
    """
    return await _linea(
        desde_unix, hasta_unix, limite, None if rama == "*" else rama,
        workspace=workspace,
    )


async def _episodios(
    desde_unix: int,
    hasta_unix: int,
    gap_max_segundos: int,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Agrupa los eventos del rango en episodios por huecos temporales."""
    if desde_unix > hasta_unix:
        raise ErrorValidacion("desde_unix debe ser <= hasta_unix")
    gap = min(max(gap_max_segundos, 60), 86_400)
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        modelos = await EventoRepository(session, ctx).listar_por_rango(desde_unix, hasta_unix, 500)
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
    workspace: str | None = None,
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
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Episodios cronológicos con "inicio", "fin", "eventos",
        "dimension_dominante" y "amplitud_media".
    """
    return await _episodios(desde_unix, hasta_unix, gap_max_segundos, workspace)


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
    workspace: str | None = None,
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
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Dict con "dimension_probable", "probabilidad", "distribucion"
        por dimensión, "trayectorias" detectadas (con flag "activa") e
        "id" de la proyección persistida en la tabla proyecciones.
    """
    return await _proyectar(horizonte_dias, dias_historial, workspace)


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
async def listar_tags(
    limite: int = 100,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista los tags de la memoria con su frecuencia de uso.

    USAR CUANDO: necesites descubrir los hilos temáticos del usuario
    antes de consultar (los tags enlazan eventos que comparten tema),
    o para revisar qué etiquetas acumuló la memoria.

    Args:
        limite: Máximo de tags (1-500). Default 100.
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        [{"tag", "eventos"}] ordenados por frecuencia descendente.
    """
    return await _tags(min(max(limite, 1), 500), workspace)


@mcp_server.tool()
async def listar_fuentes(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lista las fuentes registradas y las usadas sin registrar.

    USAR CUANDO: necesites saber de dónde viene la memoria (qué
    agentes/importaciones escriben) o antes de crear una rama con
    ramificar_fuente — la rama nace como sub-fuente de otra.

    Args:
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Fuentes con nombre, tipo, estado, namespace, rama_origen y
        conteo de eventos.
    """
    return await _fuentes(workspace)


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
async def listar_relaciones(
    limite: int = 500,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista los vínculos entre memorias — el grafo explícito.

    USAR CUANDO: quieras ver cómo se conectan los recuerdos
    (explícitos declarados con relaciona_ids y semánticos inferidos),
    o auditar la estructura del grafo antes de proponer vínculos.

    Args:
        limite: Máximo de vínculos (1-5000). Default 500.
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        [{"origen_id", "destino_id", "tipo", "peso"}] recientes
        primero.
    """
    return await _relaciones(limite, workspace)


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


# ---------------------------------------------------------------------
# Identidad multi-tenant: workspaces, membresías e invitaciones.
# Paridad con los endpoints REST /api/workspaces — mismas reglas:
# leer el roster lo puede cualquier miembro; invitar/cambiar/expulsar
# exige rol owner EN EL WORKSPACE DESTINO; crear workspace deja al
# llamante como owner (sujeto al cupo de la edición libre).
# ---------------------------------------------------------------------


async def _ctx_owner_en(workspace: str | None) -> Contexto:
    """Resuelve el contexto y exige rol owner en el workspace destino.

    Args:
        workspace: Slug/UUID del workspace, o None para el activo.

    Returns:
        El contexto con el rol efectivo en el destino.

    Raises:
        PermisoDenegadoError: Sin membresía o rol inferior a owner.
        ErrorValidacion: Workspace inexistente.
    """
    ctx = await _ctx(workspace)
    if ctx.rol != "owner":
        raise PermisoDenegadoError(
            "se requiere rol owner en el workspace"
        )
    return ctx


async def _miembro_por_handle(
    ctx: Contexto, handle: str
) -> uuid.UUID:
    """Resuelve un handle de usuario a su UUID dentro del workspace."""
    engine = obtener_engine()
    miembros = await idn.listar_miembros(engine, ctx.workspace_id)
    for m in miembros:
        if m.handle == handle:
            return m.usuario_id
    raise ErrorValidacion(f"handle no es miembro del workspace: {handle!r}")


@mcp_server.tool()
async def listar_workspaces() -> list[dict[str, Any]]:
    """Lista los workspaces donde el usuario actual es miembro.

    USAR CUANDO: necesites saber a qué workspaces pertenece el usuario,
    su rol en cada uno, o resolver un slug antes de operar con el
    parámetro ``workspace=`` de otras tools.

    Returns:
        Lista de {id, slug, nombre, rol} ordenada por creación.
    """
    engine = obtener_engine()
    ctx = await _ctx()
    return [
        {"id": str(w.id), "slug": w.slug, "nombre": w.nombre, "rol": w.rol}
        for w in await idn.listar_workspaces_con_rol(engine, ctx.usuario_id)
    ]


@mcp_server.tool()
async def crear_workspace(slug: str, nombre: str) -> dict[str, Any]:
    """Crea un workspace nuevo; el llamante queda como owner.

    USAR CUANDO: el usuario pida crear un espacio de equipo o proyecto
    ("crea un workspace para acme"). El slug es inmutable y público —
    minúsculas, números y guiones. Después de crearlo, otras tools
    aceptan ``workspace=slug`` y las escrituras usan scope="workspace".

    Args:
        slug: Identificador único (p. ej. "acme-corp").
        nombre: Nombre legible (p. ej. "Acme Corp").

    Returns:
        {id, slug, nombre, rol: "owner"} del workspace creado.
    """
    ctx = await _ctx()
    if ctx.usuario_id is None:
        raise PermisoDenegadoError(
            "sin identidad de usuario (modo desarrollo)"
        )
    ws = await idn.crear_workspace(
        obtener_engine(), slug, nombre, ctx.usuario_id
    )
    return {
        "id": str(ws.id),
        "slug": ws.slug,
        "nombre": ws.nombre,
        "rol": "owner",
    }


@mcp_server.tool()
async def listar_miembros(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista el roster de un workspace — cualquier miembro puede leerlo.

    Args:
        workspace: Slug o UUID del workspace (default: el activo).

    Returns:
        Lista de {usuario_id, handle, nombre, rol} por antigüedad.
    """
    ctx = await _ctx(workspace)
    return [
        {
            "usuario_id": str(m.usuario_id),
            "handle": m.handle,
            "nombre": m.nombre,
            "rol": m.rol,
        }
        for m in await idn.listar_miembros(
            obtener_engine(), ctx.workspace_id
        )
    ]


@mcp_server.tool()
async def invitar_miembro(
    rol: str = "member",
    dias: int = 7,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Emite un token de invitación ``ns_inv_*`` para un workspace.

    USAR CUANDO: el usuario quiera sumar a alguien a un workspace.
    Solo owners del workspace destino. El token se devuelve EN CLARO
    una única vez — entrégalo al usuario para que lo comparta; el
    invitado lo canjea en /unirse del dashboard o vía REST.

    Args:
        rol: Rol del invitado — "member" (escribe) o "viewer" (lee).
        dias: Días hasta que expire el token (1-30).
        workspace: Slug o UUID del workspace (default: el activo).

    Returns:
        {token, expira_dias, workspace, rol}.
    """
    ctx = await _ctx_owner_en(workspace)
    token = await idn.crear_invitacion(
        obtener_engine(), ctx.workspace_id, rol, dias=dias,
        creada_por=ctx.usuario_id,
    )
    return {
        "token": token,
        "expira_dias": dias,
        "workspace_id": str(ctx.workspace_id),
        "rol": rol,
    }


@mcp_server.tool()
async def cambiar_rol_miembro(
    handle: str,
    rol: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Cambia el rol de un miembro — solo owners del workspace.

    Args:
        handle: Handle del miembro (visible en listar_miembros).
        rol: Nuevo rol — "member" o "viewer" (owner no es asignable).
        workspace: Slug o UUID del workspace (default: el activo).

    Returns:
        {handle, rol} confirmado.
    """
    ctx = await _ctx_owner_en(workspace)
    uid = await _miembro_por_handle(ctx, handle)
    await idn.cambiar_rol(obtener_engine(), ctx.workspace_id, uid, rol)
    return {"handle": handle, "rol": rol}


@mcp_server.tool()
async def quitar_miembro(
    handle: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Expulsa un miembro del workspace — solo owners.

    Sus memorias permanecen intactas (visibles según su scope); solo
    pierde acceso. El último owner no puede ser expulsado.

    Args:
        handle: Handle del miembro a expulsar.
        workspace: Slug o UUID del workspace (default: el activo).

    Returns:
        {handle, expulsado: true}.
    """
    ctx = await _ctx_owner_en(workspace)
    uid = await _miembro_por_handle(ctx, handle)
    await idn.quitar_miembro(obtener_engine(), ctx.workspace_id, uid)
    return {"handle": handle, "expulsado": True}


# ---------------------------------------------------------------------
# ADR-003 — patrones neurocognitivos: intenciones prospectivas,
# consolidación-sueño, olvido reversible y minería de scripts.
# ---------------------------------------------------------------------


async def _crear_intencion(
    intencion: str,
    en: str | int | None,
    cuando: str | None,
    umbral: float,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registra una intención prospectiva (time o event-based)."""
    if not intencion.strip():
        raise ErrorValidacion("intencion no puede estar vacía")
    if (en is None) == (cuando is None):
        raise ErrorValidacion(
            "declara exactamente un trigger: 'en' (fecha) o 'cuando' (evento)"
        )
    if not 0.0 < umbral <= 1.0:
        raise ErrorValidacion("umbral debe estar en (0, 1]")
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    if en is not None:
        try:
            ts = parsear_ttl(en, ahora)
        except ValueError as e:
            raise ErrorValidacion(str(e)) from e
        if ts is None or ts <= ahora:
            raise ErrorValidacion("en debe resolver a un instante futuro")
        trigger_tipo = "time"
        trigger_valor: dict[str, Any] = {"ts": ts}
    else:
        trigger_tipo = "event"
        trigger_valor = {"query": cuando, "umbral": umbral}
    async with get_session(obtener_engine()) as session:
        modelo = IntencionProspectivaModel(
            workspace_id=ctx.workspace_id,
            autor_id=ctx.usuario_id,
            creada_en=ahora,
            intencion=intencion.strip(),
            trigger_tipo=trigger_tipo,
            trigger_valor=trigger_valor,
        )
        await IntencionProspectivaRepository(session, ctx).crear(modelo)
        await session.commit()
    respuesta: dict[str, Any] = {
        "id": str(modelo.id),
        "intencion": modelo.intencion,
        "trigger_tipo": trigger_tipo,
        "creada_en": ahora,
    }
    if trigger_tipo == "time":
        respuesta["dispara_en"] = trigger_valor["ts"]
    else:
        respuesta["trigger_query"] = cuando
        respuesta["umbral"] = umbral
    return respuesta


async def _listar_intenciones(
    ejecutadas: bool | None, workspace: str | None = None
) -> list[dict[str, Any]]:
    """Lista las intenciones prospectivas del workspace."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        filas = await IntencionProspectivaRepository(session, ctx).listar(
            ejecutadas=ejecutadas
        )
    return [
        {
            "id": str(m.id),
            "intencion": m.intencion,
            "trigger_tipo": m.trigger_tipo,
            "trigger_valor": m.trigger_valor,
            "creada_en": m.creada_en,
            "ejecutada": m.ejecutada,
            "cancelada": m.cancelada,
            "ejecutada_en": m.ejecutada_en,
        }
        for m in filas
    ]


async def _cancelar_intencion(
    intencion_id: str, workspace: str | None = None
) -> dict[str, Any]:
    """Cancela una intención prospectiva pendiente."""
    ctx = await _ctx_escritura(workspace)
    try:
        iid = uuid.UUID(str(intencion_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"intencion_id no es un UUID válido: {intencion_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        ok = await IntencionProspectivaRepository(session, ctx).cancelar(
            iid, int(time.time())
        )
        await session.commit()
    if not ok:
        raise ErrorValidacion(
            f"la intención no existe o ya está resuelta: {intencion_id}"
        )
    return {"id": str(iid), "cancelada": True}


def _a_consolidable(modelo: EventoModel) -> EventoConsolidable:
    """Traduce el ORM al dataclass que la lógica de sueño consume."""
    from src.core.memoria.tipos import TipoMemoria
    from src.core.modelos import Dimension

    return EventoConsolidable(
        id=str(modelo.id),
        timestamp_unix=modelo.timestamp_unix,
        dimension=Dimension(modelo.dimension),
        tipo=modelo.tipo,
        amplitud=modelo.amplitud,
        refuerzos=modelo.refuerzos,
        weight=modelo.weight,
        tipo_memoria=(
            TipoMemoria(modelo.tipo_memoria) if modelo.tipo_memoria else None
        ),
        ultimo_acceso=modelo.ultimo_acceso,
        archivado=modelo.archivado,
        curado=modelo.curado,
    )


async def _consolidar(workspace: str | None = None) -> dict[str, Any]:
    """Ejecuta la corrida de consolidación-sueño y persiste su reporte."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session, ctx).listar_para_consolidacion()
        plan = decidir_consolidacion([_a_consolidable(e) for e in eventos])
        await EventoRepository(session, ctx).aplicar_consolidacion(
            plan.fortalecer,
            plan.promover,
            plan.archivar,
            plan.ejecutado_en,
        )
        reporte = ReporteConsolidacionModel(
            workspace_id=ctx.workspace_id,
            ejecutado_en=plan.ejecutado_en,
            fortalecidos=len(plan.fortalecer),
            archivados=len(plan.archivar),
            promovidos=len(plan.promover),
            detalle={
                "fortalecidos": plan.fortalecer,
                "promovidos": plan.promover,
                "archivados": plan.archivar,
            },
        )
        await ReporteConsolidacionRepository(session, ctx).crear(reporte)
        await session.commit()
    return {
        "ejecutado_en": plan.ejecutado_en,
        "evaluados": len(eventos),
        "fortalecidos": len(plan.fortalecer),
        "promovidos": len(plan.promover),
        "archivados": len(plan.archivar),
        "resumen": resumen_legible(plan),
    }


async def _minar_scripts(workspace: str | None = None) -> dict[str, Any]:
    """Mina secuencias recurrentes de la línea de universo."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session, ctx).listar_para_consolidacion()
        candidatos = _minar_patrones(
            [
                EventoSecuencia(
                    id=str(e.id),
                    timestamp_unix=e.timestamp_unix,
                    contenido=e.contenido,
                    dimension=e.dimension,
                )
                for e in eventos
            ]
        )
        ahora = int(time.time())
        repo = ScriptRepository(session, ctx)
        for c in candidatos[:20]:
            await repo.guardar(
                ScriptDetectadoModel(
                    workspace_id=ctx.workspace_id,
                    autor_id=ctx.usuario_id,
                    nombre=" → ".join(c.pasos),
                    pasos=list(c.pasos),
                    frecuencia=float(c.soporte),
                    confianza=c.confianza,
                    creado_en=ahora,
                    actualizado_en=ahora,
                )
            )
        await session.commit()
    return {
        "evaluados": len(eventos),
        "detectados": len(candidatos),
        "guardados": min(len(candidatos), 20),
        "scripts": [
            {"pasos": list(c.pasos), "soporte": c.soporte, "confianza": c.confianza}
            for c in candidatos[:20]
        ],
    }


async def _listar_scripts(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lista los scripts/patrones detectados del workspace."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        filas = await ScriptRepository(session, ctx).listar()
    return [
        {
            "id": str(m.id),
            "nombre": m.nombre,
            "pasos": m.pasos,
            "frecuencia": m.frecuencia,
            "confianza": m.confianza,
            "actualizado_en": m.actualizado_en,
        }
        for m in filas
    ]


async def _listar_archivados(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lista las memorias archivadas por consolidación (recuperables)."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        filas = await EventoRepository(session, ctx).listar_archivados()
    return [
        {
            "id": str(m.id),
            "timestamp_unix": m.timestamp_unix,
            "dimension": m.dimension,
            "contenido": m.contenido[:200],
            "archivado_en": m.archivado_en,
            "razon_archivo": m.razon_archivo,
        }
        for m in filas
    ]


async def _restaurar_evento(
    evento_id: str, workspace: str | None = None
) -> dict[str, Any]:
    """Restaura una memoria archivada — deshace el olvido."""
    ctx = await _ctx_escritura(workspace)
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"evento_id no es un UUID válido: {evento_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        ok = await EventoRepository(session, ctx).restaurar(eid)
        await session.commit()
    if not ok:
        raise ErrorValidacion(
            f"el evento no existe o no está archivado: {evento_id}"
        )
    return {"id": str(eid), "restaurado": True}


@mcp_server.tool()
async def crear_intencion(
    intencion: str,
    en: str | int | None = None,
    cuando: str | None = None,
    umbral: float = 0.6,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registra una intención futura — memoria prospectiva.

    USAR CUANDO: el usuario pida "recuérdame X el viernes" (trigger
    temporal: ``en="3d"``, epoch, ISO…) o "avisame cuando alguien
    mencione Y" (trigger de evento: ``cuando="texto a vigilar"``).
    NO ES una proyección/alarma de calendario: Nous recuerda la
    intención y la dispara cuando el trigger se cumple — en la
    próxima consulta (time) o al llegar el evento que calza (event).

    Args:
        intencion: Qué recordar ("llamar a María").
        en: Trigger temporal — "30d", "12h", epoch futuro.
        cuando: Trigger de evento — texto; dispara cuando un evento
            entrante lo cubre (similitud ≥ ``umbral``).
        umbral: Similitud mínima para triggers de evento (default 0.6).
        workspace: Slug o UUID del workspace destino.

    Returns:
        {id, intencion, trigger_tipo, dispara_en|trigger_query}.
    """
    return await _crear_intencion(intencion, en, cuando, umbral, workspace)


@mcp_server.tool()
async def listar_intenciones(
    ejecutadas: bool | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista las intenciones prospectivas registradas.

    USAR CUANDO: quieras revisar qué recordatorios están pendientes,
    cuáles ya se dispararon o auditar la memoria prospectiva.

    Args:
        ejecutadas: None = todas; False = solo pendientes; True = solo
            ya disparadas.
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        [{id, intencion, trigger_tipo, trigger_valor, ejecutada, …}].
    """
    return await _listar_intenciones(ejecutadas, workspace)


@mcp_server.tool()
async def cancelar_intencion(
    intencion_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Cancela una intención prospectiva pendiente.

    USAR CUANDO: el usuario ya no quiera el recordatorio ("olvida lo
    de llamar a María"). La intención queda cancelada — auditable,
    no borrada.

    Args:
        intencion_id: UUID de la intención (de listar_intenciones).
        workspace: Slug o UUID del workspace.

    Returns:
        {id, cancelada: true}.
    """
    return await _cancelar_intencion(intencion_id, workspace)


@mcp_server.tool()
async def consolidar_memoria(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Ejecuta la consolidación-sueño — replay, promoción y olvido.

    USAR CUANDO: quieras correr el ciclo de mantenimiento de memoria
    (idealmente en quietud, como el sueño): fortalece lo importante
    (replay), promueve a curado lo destacado (nunca decae) y archiva
    lo débil/olvidado — reversible, con reporte auditable.
    NO ES poda: nada se borra; lo archivado sigue en la worldline y
    se restaura con restaurar_evento.

    Args:
        workspace: Slug o UUID del workspace a consolidar.

    Returns:
        {evaluados, fortalecidos, promovidos, archivados, resumen}.
    """
    return await _consolidar(workspace)


@mcp_server.tool()
async def listar_reportes_consolidacion(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista los reportes de corridas de consolidación anteriores.

    USAR CUANDO: quieras auditar qué hizo el sueño de consolidación —
    qué se fortaleció, qué se archivó, qué se promovió a curado. Es
    el diario del proceso: nada se olvida en silencio.

    Args:
        workspace: Slug o UUID del workspace.

    Returns:
        [{id, ejecutado_en, fortalecidos, archivados, promovidos}].
    """
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        filas = await ReporteConsolidacionRepository(session, ctx).listar()
    return [
        {
            "id": str(r.id),
            "ejecutado_en": r.ejecutado_en,
            "fortalecidos": r.fortalecidos,
            "archivados": r.archivados,
            "promovidos": r.promovidos,
            "detalle": r.detalle,
        }
        for r in filas
    ]


@mcp_server.tool()
async def minar_scripts(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Detecta secuencias de comportamiento recurrentes (ADR-003).

    USAR CUANDO: quieras descubrir patrones de acción del usuario
    ("cada bug crítico: reproduzco → logs → fix → test") para
    anticipar el siguiente paso. Re-mínalo tras registrar muchos
    eventos nuevos; el upsert por nombre evita duplicados.

    Args:
        workspace: Slug o UUID del workspace a minar.

    Returns:
        {evaluados, detectados, guardados, scripts:[{pasos, soporte,
        confianza}]}.
    """
    return await _minar_scripts(workspace)


@mcp_server.tool()
async def listar_scripts(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista los scripts/patrones detectados por minería.

    USAR CUANDO: quieras ver qué rutinas conoce Nous del usuario —
    secuencias recurrentes con su frecuencia y confianza.

    Args:
        workspace: Slug o UUID del workspace.

    Returns:
        [{id, nombre, pasos, frecuencia, confianza}].
    """
    return await _listar_scripts(workspace)


@mcp_server.tool()
async def listar_archivados(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista las memorias archivadas por la consolidación-sueño.

    USAR CUANDO: el usuario quiera revisar qué se "olvidó" — cada
    archivado lleva su razón auditable y se restaura con
    restaurar_evento. Nada desaparece en silencio.

    Args:
        workspace: Slug o UUID del workspace.

    Returns:
        [{id, contenido, archivado_en, razon_archivo}].
    """
    return await _listar_archivados(workspace)


@mcp_server.tool()
async def restaurar_evento(
    evento_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Restaura una memoria archivada — deshace el olvido.

    USAR CUANDO: el usuario quiera recuperar algo que la consolidación
    archivó ("en realidad sí me acuerdo de X"). Restaurar sube su
    ``weight`` a 1.0 (re-aprendido) — vuelve a ser evocable.

    Args:
        evento_id: UUID del evento archivado (de listar_archivados).
        workspace: Slug o UUID del workspace.

    Returns:
        {id, restaurado: true}.
    """
    return await _restaurar_evento(evento_id, workspace)
