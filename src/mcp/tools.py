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
from src.core.exceptions import (
    CupoCuradoError,
    CupoExcedidoError,
    ErrorValidacion,
    PermisoDenegadoError,
)
from src.core.identidad import Contexto, contexto_actual, resolver_workspace
from src.core.memoria.bootstrap import (
    SeccionBootstrap,
    empaquetar_bootstrap,
    formatear_snapshot,
)
from src.core.memoria.calibracion import (
    PrediccionResuelta,
)
from src.core.memoria.calibracion import (
    reporte_calibracion as _reporte_calibracion_core,
)
from src.core.memoria.consolidacion import (
    EventoConsolidable,
    decidir_consolidacion,
    resumen_legible,
)
from src.core.memoria.contexto import ContextoActual, boost_contextual
from src.core.memoria.decay import media_vida_dias, proxima_revision
from src.core.memoria.diversidad import diversificar, es_casi_recuerdo
from src.core.memoria.epistemica import detectar_lagunas
from src.core.memoria.handoff import generar_handoff
from src.core.memoria.interferencia import (
    INTERFERENCIA_FACTOR,
    detectar_conflicto,
    es_competidor_proactivo,
)
from src.core.memoria.novedad import (
    amplitud_con_sorpresa,
    es_flashbulb,
    marca_frontera,
    sorpresa,
)
from src.core.memoria.prospectiva import (
    Intencion,
    TriggerTipo,
    disparada_por_evento,
)
from src.core.memoria.rem import pares_rem
from src.core.memoria.retrieval import (
    COACTIVA_DELTA,
    COACTIVA_PESO_MAX,
    COACTIVA_TOP_K,
    RIF_FACTOR,
    RIF_PISO_WEIGHT,
    confianza_de_evento,
    desglose_confianza,
    esta_labil,
    extincion_valencia,
    factor_zeigarnik,
    pares_coactivos,
    peso_efectivo_coactiva,
    ventana_labil,
)
from src.core.memoria.ritmos import (
    UMBRAL_EPOCA_SEG,
    es_primero_de_epoca,
    ventana_optima_sueno,
)
from src.core.memoria.scripts import (
    EventoSecuencia,
)
from src.core.memoria.scripts import (
    minar_scripts as _minar_patrones,
)
from src.core.memoria.semanticizacion import (
    EpisodioGist,
    plan_gists,
)
from src.core.memoria.tipos import (
    TipoMemoria,
    clasificar_tipo_memoria,
    estimar_estado_emocional,
)
from src.core.memoria.trabajo import (
    CAPACIDAD_MAX,
    TTL_BORRADOR_SEG,
    debe_promover,
    esta_expirado,
    hay_cupo,
)
from src.core.memoria.verificacion import (
    UMBRAL_ACTIVACION,
    CensorCoincidente,
    parsear_censor,
    veredicto_censores,
)
from src.core.modelos import (
    Dimension,
    Evento,
    ResultadoBusqueda,
    Trayectoria,
)
from src.core.reranker import ReordenadorLocal
from src.core.temporal import detectar_trayectorias, proyectar
from src.mcp.server import mcp_server
from src.storage.database import crear_engine, get_session, obtener_engine
from src.storage.models import (
    BorradorModel,
    EventoModel,
    IntencionProspectivaModel,
    ObjetivoModel,
    PrediccionModel,
    ProyeccionModel,
    ReporteConsolidacionModel,
    ScriptDetectadoModel,
    SesionModel,
)
from src.storage.repositories import (
    BorradorRepository,
    ConfiguracionRepository,
    EventoRepository,
    FuenteRepository,
    IntencionProspectivaRepository,
    ObjetivoRepository,
    PrediccionRepository,
    ProyeccionRepository,
    RelacionRepository,
    ReporteConsolidacionRepository,
    ScriptRepository,
    SesionRepository,
)

logger = logging.getLogger(__name__)

# Encadenamiento episódico: dos eventos del mismo origen dentro de
# esta ventana se encadenan — la continuidad temporal del flujo.
_VENTANA_ENCADENA_SEG = 3600

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
    fuente: str = "mcp"


@dataclass(frozen=True, slots=True)
class _ItemConsolidacion:
    """Trabajo de consolidación: efectos post-evocación.

    Lleva las valencias codificadas de los resultados y el estado
    actual para la extinción emocional — el worker aplica la lógica
    pura de ``retrieval`` fuera del camino caliente.
    """

    eventos_ids: tuple[uuid.UUID, ...]
    valencias: tuple[tuple[uuid.UUID, float], ...] = ()
    estado_actual: float | None = None
    # RIF (Neuro-4): ids recuperados que compitieron y perdieron el
    # corte — la evocación de los ganadores los suprime un poco.
    debilitar_ids: tuple[uuid.UUID, ...] = ()


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
    consolidaciones = [
        item for item in items if isinstance(item, _ItemConsolidacion)
    ]
    if consolidaciones:
        await _consolidar_luego(engine, consolidaciones)


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
        "estado_emocional": modelo.estado_emocional,
        "contexto": dict(modelo.contexto or {}),
        "weight": modelo.weight,
        "archivado": modelo.archivado,
        "curado": modelo.curado,
        "olvido_dirigido": modelo.olvido_dirigido,
        "cuarentena": modelo.cuarentena,
        "staged": modelo.staged,
        "parametrica": modelo.parametrica,
        "debilitados": modelo.debilitados,
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
        "weight": evento.weight,
        "archivado": evento.archivado,
        "olvido_dirigido": evento.olvido_dirigido,
        "cuarentena": evento.cuarentena,
        "tipo": evento.tipo,
        "fuente": evento.fuente,
        "debilitados": evento.debilitados,
        "staged": evento.staged,
        "parametrica": evento.parametrica,
    }


def _resultado_a_dict(
    r: ResultadoBusqueda, confiable: bool | None = True
) -> dict[str, Any]:
    """Serializa un resultado de búsqueda con su contexto temporal."""
    assert r.evento is not None
    ahora = int(time.time())
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
        # Metamemoria: qué tan fiable es el recuerdo devuelto —
        # el agente pondera la respuesta en vez de tratar toda
        # memoria como igualmente cierta. ``confiable`` es la
        # confianza en la fuente de origen (amnesia de fuente) y
        # ``parametrica`` el techo epistémico (Neuro-6).
        "confianza": confianza_de_evento(
            r.evento.refuerzos,
            r.evento.weight,
            r.evento.amplitud,
            r.evento.timestamp_unix,
            ahora,
            archivado=r.evento.archivado,
            fuente_confiable=confiable,
            parametrica=r.evento.parametrica,
        ),
        # Neuro-6 — la confianza descompuesta: cada señal por
        # separado, auditable, no un número opaco.
        "desglose_confianza": desglose_confianza(
            r.evento.refuerzos,
            r.evento.weight,
            r.evento.amplitud,
            max(0.0, (ahora - r.evento.timestamp_unix) / 86400.0),
            archivado=r.evento.archivado,
            fuente_confiable=confiable,
            parametrica=r.evento.parametrica,
        ),
    }


TIPOS_EVENTO = (
    "evento",
    "hecho",
    "creencia",
    "compromiso",
    "preferencia",
    "censor",
)


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
    tipo_memoria: str | None = None,
    estado_emocional: float | None = None,
    contexto_extra: dict[str, Any] | None = None,
    parametrica: bool = False,
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
    if estado_emocional is not None and not -1.0 <= estado_emocional <= 1.0:
        raise ErrorValidacion("estado_emocional debe estar entre -1.0 y 1.0")
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
        previo_ts = await repo.timestamp_anterior(ts)
        # Primacía de época: tras un hueco ≥ UMBRAL_EPOCA_SEG el
        # evento inaugura periodo — el decay lo premia (~15% de
        # media-vida). El primer evento de la historia también.
        epoca_nueva = es_primero_de_epoca(
            ts - previo_ts if previo_ts is not None else UMBRAL_EPOCA_SEG
        )
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
            # en situaciones parecidas a la actual. El llamador
            # puede aportar claves extra; las automáticas mandan.
            contexto={
                **(contexto_extra or {}),
                "hora_dia": time.localtime(ts).tm_hour,
                "fuente": fuente,
            },
            # Campos declarados (ADR-003): si vienen en el request,
            # el estimador de indexación no los sobrescribe —
            # fijar_tipo_memoria usa COALESCE.
            tipo_memoria=tipo_memoria,
            estado_emocional=estado_emocional,
            inaugura_epoca=epoca_nueva,
            # Neuro-5 — write-approval: con el flag, la escritura es
            # propuesta (staged) — indexada pero fuera del recall
            # hasta que el humano apruebe.
            staged=get_settings().escritura_aprobacion,
            # Neuro-6 — contenido afirmado desde los pesos del
            # modelo, no observado: techo epistémico al recall.
            parametrica=parametrica,
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
            viejo = await repo.obtener(viejo_id)
            if viejo is None:
                raise ErrorValidacion(
                    f"el evento a reemplazar no existe: {reemplaza_id}"
                )
            await repo.marcar_reemplazado(viejo_id, evento.id)
            if await relaciones.vincular(viejo_id, evento.id, "reemplaza"):
                respuesta["reemplaza"] = str(viejo_id)
            # Reconsolidación: el viejo quedó lábil al evocarlo
            # hace poco — este supersede dentro de la ventana cuenta
            # como reescritura de la huella viva, no solo versión.
            if esta_labil(viejo.labil_hasta, int(time.time())):
                await relaciones.vincular(
                    viejo_id, evento.id, "reconsolida"
                )
                respuesta["reconsolida"] = True
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
            fuente,
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
    if evento.staged:
        respuesta["staged"] = True
        respuesta["mensaje"] = (
            "propuesta registrada — pendiente de aprobación "
            "(aprobar_evento / aprobar_pendientes)"
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
                # Interferencia proactiva (Neuro-4): los vecinos
                # similar-pero-distintos compiten por el mismo slot
                # de recall — la huella nueva los ocluye. Banda más
                # estrecha que la del vínculo (0.80+): afín es tema,
                # casi-igual es versión.
                proactivos = [
                    vecino.id
                    for vecino, sim in similar
                    if vecino.id != item.evento_id
                    and es_competidor_proactivo(sim, settings.dedup_umbral)
                ]
                if proactivos:
                    await repo.debilitar_lote(
                        proactivos, INTERFERENCIA_FACTOR
                    )
            if sobrevive:
                # ADR-003: tipo de memoria (provider o heurístico).
                tipo_mem = await clasificar_tipo_memoria(
                    item.contenido, _obtener_provider_decision()
                )
                valencia = estimar_estado_emocional(item.contenido)
                await repo.fijar_tipo_memoria(
                    item.evento_id,
                    tipo_mem.value,
                    estado_emocional=valencia,
                )
                # Novedad / error de predicción: si el evento queda
                # lejos de la trayectoria reciente, es inesperado —
                # la sorpresa premia amplitud (dopamina = "esto
                # importa"), nunca la baja.
                recientes = await EventoRepository(
                    session,
                    Contexto(
                        usuario_id=item.autor_id,
                        workspace_id=item.workspace_id,
                        rol="member",
                    ),
                ).embeddings_recientes(20)
                s = sorpresa(
                    vector,
                    [
                        r.embedding
                        for r in recientes
                        if r.embedding is not None
                        and r.id != item.evento_id
                    ],
                )
                nueva_amplitud = amplitud_con_sorpresa(item.amplitud, s)
                if nueva_amplitud > item.amplitud:
                    await repo.actualizar_amplitud(
                        item.evento_id, nueva_amplitud
                    )
                # Frontera por sorpresa (Neuro-3): un pico de novedad
                # marca época — los periodos se delimitan solos por
                # lo inesperado, no solo por calendario.
                if marca_frontera(s):
                    await repo.marcar_inaugura_epoca(item.evento_id)
                # Flashbulb (Neuro-4): sorpresa × emoción intensa es
                # la codificación más fuerte que existe — la huella
                # salta de peso y queda etiquetada para auditoría.
                if es_flashbulb(s, valencia):
                    await repo.marcar_flashbulb(item.evento_id)
                    await repo.marcar_inaugura_epoca(item.evento_id)
                # Encadenamiento episódico (Neuro-3): el evento se ata
                # al anterior del mismo origen dentro de la ventana —
                # el "¿y luego qué?" navegable por aristas encadena.
                anterior = await repo.anterior_misma_fuente(
                    item.fuente, item.ts, _VENTANA_ENCADENA_SEG
                )
                if anterior is not None and anterior.id != item.evento_id:
                    await relaciones.vincular(
                        item.evento_id, anterior.id, "encadena"
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
            # Cuarentena (Neuro-3): si la fuente no está aprobada, el
            # evento queda indexado pero fuera del recall — sistema
            # inmune contra inyección por fuentes no confiables.
            if not await FuenteRepository(
                session,
                Contexto(
                    usuario_id=item.autor_id,
                    workspace_id=item.workspace_id,
                    rol="member",
                ),
            ).es_confiable(item.fuente):
                await repo.marcar_cuarentena(item.evento_id)
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
    # Prep pack (Neuro-3): al disparar, la intención adjunta las
    # memorias relacionadas — el recordatorio llega con el "por qué"
    # listo, no solo con el "qué". El query del trigger es la mejor
    # clave de búsqueda: corto y focal; la intención completa lleva
    # palabras que el full-text AND exigiría en vano.
    consulta_pack = str(
        (modelo.trigger_valor or {}).get("query") or modelo.intencion
    )
    pack_rows = await EventoRepository(
        session,
        Contexto(
            usuario_id=modelo.autor_id,
            workspace_id=modelo.workspace_id,
            rol="member",
        ),
    ).buscar_por_texto(consulta_pack, limite=3)
    pack = [
        {"id": str(e.id), "contenido": e.contenido[:200]}
        for e, _ in pack_rows
    ]
    if pack:
        await repo.fijar_preparacion(modelo.id, pack)
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
            "prospectiva",
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
    pendientes = await repo.pendientes_por_evento(int(time.time()))
    for modelo in pendientes:
        intencion = Intencion(
            id=str(modelo.id),
            intencion=modelo.intencion,
            trigger_tipo=TriggerTipo(modelo.trigger_tipo),
            trigger_valor=modelo.trigger_valor or {},
            creada_en=modelo.creada_en,
            ejecutada=modelo.ejecutada,
            cancelada=modelo.cancelada,
            aplazada_hasta=modelo.aplazada_hasta,
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
                    "preparacion": modelo.preparacion or [],
                }
            )
    return recordatorios


async def _consolidar_luego(
    engine: AsyncEngine, items: list[_ItemConsolidacion]
) -> None:
    """Persiste los efectos post-evocación fuera del camino caliente.

    Por lote de consultas: refuerzo + ventana lábil (reconsolidación),
    aristas coactivas (hebbiana) y extinción de valencias en recuerdos
    evocados en estado neutro.
    """
    try:
        ids = [eid for item in items for eid in item.eventos_ids]
        if not ids:
            return
        async with get_session(engine) as session:
            ahora = int(time.time())
            await EventoRepository(session).reforzar(
                ids, labil_hasta=ventana_labil(ahora)
            )
            relaciones = RelacionRepository(session)
            # Ids realmente existentes: un dedup-merge o un evento
            # eliminado entre el recall y esta consolidación no debe
            # romper el lote entero (un FK muerto en una coactiva
            # rodaría refuerzo, SRS, extinción y RIF juntos).
            repo_ev = EventoRepository(session)
            modelos = await repo_ev.obtener_muchos(
                ids + [e for it in items for e in it.debilitar_ids]
            )
            existentes = {m.id for m in modelos}
            for item in items:
                pares = [
                    par
                    for par in pares_coactivos(
                        [str(e) for e in item.eventos_ids]
                    )
                    if uuid.UUID(par[0]) in existentes
                    and uuid.UUID(par[1]) in existentes
                ]
                if pares:
                    await relaciones.reforzar_coactivas(
                        [
                            (
                                uuid.UUID(a) if isinstance(a, str) else a,
                                uuid.UUID(b) if isinstance(b, str) else b,
                            )
                            for a, b in pares
                        ],
                        COACTIVA_DELTA,
                        COACTIVA_PESO_MAX,
                        int(time.time()),
                    )
            extinciones: list[tuple[uuid.UUID, float]] = []
            for item in items:
                for eid, valencia in item.valencias:
                    nueva = extincion_valencia(valencia, item.estado_actual)
                    if nueva is not None:
                        extinciones.append((eid, nueva))
            if extinciones:
                await EventoRepository(session).atenuar_valencias(extinciones)
            # SRS (Neuro-3): cada evocación recalcula el repaso óptimo
            # — el momento en que la curva cruzará el umbral de repaso.
            # Repasar justo antes de olvidar es cuando más consolida.
            # Solo los evocados: los perdedores RIF no "repasan" nada.
            evocados = set(ids)
            revisiones: dict[uuid.UUID, int] = {}
            for m in modelos:
                if m.id not in evocados:
                    continue
                mv = media_vida_dias(
                    Dimension(m.dimension),
                    TipoMemoria(m.tipo_memoria) if m.tipo_memoria else None,
                    m.amplitud,
                    m.refuerzos,
                    m.curado,
                    primacia=m.inaugura_epoca,
                    olvido_dirigido=m.olvido_dirigido,
                )
                proxima = proxima_revision(ahora, mv)
                if proxima is not None:
                    revisiones[m.id] = proxima
            if revisiones:
                await repo_ev.fijar_proximas_revisiones(revisiones)
            # RIF (Neuro-4): los competidores no elegidos se suprimen
            # — evocar al ganador debilita a los perdedores un poco.
            perdedores = [
                eid
                for item in items
                for eid in item.debilitar_ids
                if eid in existentes
            ]
            if perdedores:
                await repo_ev.debilitar_lote(
                    perdedores, RIF_FACTOR, piso=RIF_PISO_WEIGHT
                )
            await session.commit()
    except Exception:  # noqa: BLE001 — el fallo no debe propagarse al loop
        logger.exception("no se pudieron consolidar efectos de evocación")


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
                    # Prep pack: memorias relacionadas adjuntadas al
                    # disparar — el "por qué" listo para actuar.
                    "preparacion": v["preparacion"],
                }
                for v in vencidas
            ]
        competidores: list[str] = []
        resultados = await _obtener_busqueda().buscar(
            session,
            consulta,
            top_n=max(1, min(top_n, 50)),
            rama=rama,
            ctx=ctx,
            competidores=competidores,
        )
        estado_actual: float | None = None
        episodios_por_gist: dict[str, list[dict[str, Any]]] = {}
        confianza_fuentes: dict[str, bool] = {}
        asociados: list[tuple[EventoModel, float]] = []
        if resultados:
            # ADR-003 patrón 6 — memoria dependiente de contexto/estado:
            # recuerdos codificados en una hora/fuente/estado parecidos
            # al actual suben de rango. Solo premia, nunca excluye.
            estado_actual = await EventoRepository(
                session, ctx
            ).ultimo_estado_somatico()
            actual = ContextoActual(
                hora_dia=time.localtime().tm_hour,
                fuente="mcp",
                estado_emocional=estado_actual,
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
            # Fan effect (Neuro-3): diez versiones casi idénticas de
            # lo mismo compiten entre sí — la diversificación penaliza
            # near-duplicados para que cada slot muestre algo distinto.
            diversificados = diversificar(
                [
                    (r.evento_id, r.evento.contenido, r.score_final)
                    for r in resultados
                    if r.evento is not None
                ]
            )
            ajuste = {eid: s for eid, _, s in diversificados}
            resultados = [
                (
                    replace(r, score_final=ajuste[r.evento_id])
                    if r.evento is not None
                    and ajuste.get(r.evento_id, r.score_final)
                    != r.score_final
                    else r
                )
                for r in resultados
            ]
            resultados.sort(key=lambda r: r.score_final, reverse=True)
            # Zeigarnik (Neuro-4): los compromisos abiertos pesan más
            # — el cerebro no suelta lo pendiente. Un solo query por
            # lote, solo si hay compromisos entre los resultados.
            posibles = [
                r.evento_id
                for r in resultados
                if r.evento is not None and r.evento.tipo == "compromiso"
            ]
            if posibles:
                abiertos = await EventoRepository(
                    session, ctx
                ).ids_compromisos_abiertos(
                    [uuid.UUID(e) for e in posibles]
                )
                if abiertos:
                    resultados = [
                        (
                            replace(
                                r,
                                score_final=r.score_final
                                * factor_zeigarnik(
                                    r.evento.tipo,
                                    uuid.UUID(r.evento_id) not in abiertos,
                                ),
                            )
                            if r.evento is not None
                            else r
                        )
                        for r in resultados
                    ]
                    resultados.sort(
                        key=lambda r: r.score_final, reverse=True
                    )
            # Expansión de gist (Neuro-3): un resultado semántico
            # trae sus episodios miembros — la abstracción llega
            # con la evidencia que la sostiene.
            gist_ids = [
                uuid.UUID(r.evento_id)
                for r in resultados
                if r.evento is not None
                and "gist" in (r.evento.tags or [])
            ]
            for gid in gist_ids:
                miembros = await EventoRepository(
                    session, ctx
                ).episodios_de_gist(gid, limite=3)
                episodios_por_gist[str(gid)] = [
                    _evento_a_dict(Evento.desde_modelo(m)) for m in miembros
                ]
            # Amnesia de fuente (Neuro-4): la confianza pesa el
            # origen — un solo query por lote para todas las fuentes
            # presentes en los resultados.
            confianza_fuentes = await FuenteRepository(
                session, ctx
            ).fuentes_confiables(
                list(
                    {
                        r.evento.fuente
                        for r in resultados
                        if r.evento is not None and r.evento.fuente
                    }
                )
            )
            # Expansión hebbiana (Neuro-6): las aristas coactivas
            # grabadas por co-recalls previos traen a los vecinos
            # históricos — lo que se evocó junto, se recuerda junto.
            # STP pondera la recencia del último co-encendido.
            ahora_consulta = int(time.time())
            ganadores = {
                uuid.UUID(r.evento_id)
                for r in resultados[:COACTIVA_TOP_K]
                if r.evento is not None
            }
            if ganadores:
                aristas = await RelacionRepository(
                    session, ctx
                ).vecinos_coactivos(list(ganadores), limite=24)
                mejores: dict[uuid.UUID, float] = {}
                for ar in aristas:
                    peso_e = peso_efectivo_coactiva(
                        ar.peso, ar.ultima_coactiva, ahora_consulta
                    )
                    for extremo in (ar.origen_id, ar.destino_id):
                        if extremo in ganadores or str(extremo) in {
                            r.evento_id for r in resultados
                        }:
                            continue
                        if peso_e > mejores.get(extremo, 0.0):
                            mejores[extremo] = peso_e
                top_vecinos = sorted(
                    mejores.items(), key=lambda kv: kv[1], reverse=True
                )[:4]
                if top_vecinos:
                    modelos = await EventoRepository(
                        session, ctx
                    ).obtener_muchos([e for e, _ in top_vecinos])
                    pesos = dict(top_vecinos)
                    asociados = [
                        (m, pesos[m.id])
                        for m in modelos
                        if not m.staged
                        and not m.archivado
                        and not m.cuarentena
                        and m.reemplazado_por is None
                    ]
    _encolar_fondo(
        _ItemConsolidacion(
            tuple(uuid.UUID(r.evento_id) for r in resultados),
            tuple(
                (uuid.UUID(r.evento_id), r.evento.estado_emocional)
                for r in resultados
                if r.evento is not None and r.evento.estado_emocional is not None
            ),
            estado_actual,
            tuple(uuid.UUID(e) for e in competidores),
        )
    )
    if resultados:
        _registrar_actividad("recall", consulta, [r.evento_id for r in resultados])
    serializados = []
    for r in resultados:
        d = _resultado_a_dict(
            r,
            confiable=(
                confianza_fuentes.get(r.evento.fuente)
                if r.evento is not None and r.evento.fuente
                else None
            ),
        )
        # Tip-of-the-tongue: señal bajo el umbral de solidez se
        # declara honestamente ("casi recuerdo esto…"), ni amnesia
        # ni certeza fingida.
        if es_casi_recuerdo(r.score_final):
            d["casi_recuerdo"] = True
        if r.evento_id in episodios_por_gist:
            d["episodios"] = episodios_por_gist[r.evento_id]
        serializados.append(d)
    # Vecinos hebbianos — llegan marcados como asociados, nunca se
    # confunden con resultados de la consulta.
    for modelo, peso_e in asociados:
        ev = Evento.desde_modelo(modelo)
        serializados.append(
            {
                "evento": _evento_a_dict(ev),
                "via": "coactiva",
                "peso_efectivo": round(peso_e, 3),
                "confianza": confianza_de_evento(
                    ev.refuerzos,
                    ev.weight,
                    ev.amplitud,
                    ev.timestamp_unix,
                    int(time.time()),
                    archivado=ev.archivado,
                    parametrica=ev.parametrica,
                ),
            }
        )
    return recordatorios + serializados


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
                "confiable": f.confiable,
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
                        "confiable": True,
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
    top_n: int = 8,
    presupuesto: int | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Pack de arranque de sesión: guardrails + núcleo + loops + hechos
    calientes — cero LLM, todo consultas locales.

    Con ``presupuesto`` (caracteres) el pack se empaqueta por
    prioridad — censores → loops → curado → núcleo → calientes — y
    además devuelve ``snapshot``: el bloque frozen-snapshot pensado
    para inyectarse UNA VEZ en el system prompt al abrir sesión.

    Args:
        top_n: Eventos núcleo y hechos calientes a incluir (máx. 16).
        presupuesto: Máximo de caracteres del snapshot (None = sin
            límite — el pack completo como siempre).

    Returns:
        ``censores`` (guardrails activos), ``nucleo`` (eventos
        recientes de mayor amplitud), ``curado`` (tier inmune),
        ``loops`` (compromisos abiertos), ``calientes`` (reforzados
        de la semana), ``stats`` y, con presupuesto, ``snapshot`` +
        ``presupuesto_usado`` + ``omitidos``.
    """
    n = min(max(top_n, 1), 16)
    ahora = int(time.time())
    semana = ahora - 7 * 86_400
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        recientes = await repo.listar_por_rango(semana, ahora, 500)
        loops = await repo.listar_compromisos_abiertos("main", ahora)
        curados = await repo.listar_curados()
        censores = await repo.listar_censores()
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
    loops_dicts = [
        {
            **_modelo_a_dict(e),
            "dias_abierto": (ahora - e.timestamp_unix) // 86_400,
        }
        for e in loops
    ]
    respuesta: dict[str, Any] = {
        "censores": [_modelo_a_dict(e) for e in censores],
        "nucleo": [_modelo_a_dict(e) for e in nucleo],
        "curado": [_modelo_a_dict(e) for e in curados],
        "loops": loops_dicts,
        "calientes": [_modelo_a_dict(e) for e in calientes],
        "stats": {
            "eventos_totales": total,
            "eventos_semana": len(recientes),
            "compromisos_abiertos": len(loops),
            "censores_activos": len(censores),
        },
    }
    if presupuesto is not None:
        secciones = [
            SeccionBootstrap(
                "censores",
                "Guardrails",
                tuple(
                    f"[{parsear_censor(e.contexto)[0].upper()}] "
                    f"{e.contenido}"
                    for e in censores
                ),
            ),
            SeccionBootstrap(
                "loops",
                "Compromisos abiertos",
                tuple(
                    f"{loop['contenido']} ({loop['dias_abierto']}d)"
                    for loop in loops_dicts
                ),
            ),
            SeccionBootstrap(
                "curado",
                "Núcleo curado",
                tuple(e.contenido for e in curados),
            ),
            SeccionBootstrap(
                "nucleo",
                "Memoria reciente",
                tuple(e.contenido for e in nucleo),
            ),
            SeccionBootstrap(
                "calientes",
                "Hechos calientes",
                tuple(e.contenido for e in calientes),
            ),
        ]
        plan = empaquetar_bootstrap(secciones, presupuesto)
        respuesta["snapshot"] = formatear_snapshot(plan)
        respuesta["presupuesto_usado"] = plan.usado
        respuesta["presupuesto_total"] = presupuesto
        respuesta["omitidos"] = plan.omitidos
    return respuesta


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
    tipo_memoria: (
        Literal["episodica", "semantica", "procedimental"] | None
    ) = None,
    estado_emocional: float | None = None,
    parametrica: bool = False,
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
        tipo_memoria: ADR-003 — "episodica", "semantica" o
            "procedimental" si el llamador ya lo sabe (p. ej. un
            check-in declara "episodica"). Omitir para que el
            clasificador lo estime en la indexación.
        estado_emocional: Valencia afectiva declarada entre -1.0 y
            1.0 (negativo → positivo). Si viene, el estimador léxico
            no la sobrescribe. Útil para check-ins somáticos.
        parametrica: True si el contenido se afirma desde el
            conocimiento paramétrico del agente (sus pesos), no de
            una observación o fuente — la confianza al evocarlo
            queda techada a 0.70 hasta que la realidad lo corrobore
            (3 refuerzos útiles o aprobación humana). USAR para
            honestidad epistémica: "creo que…" ≠ "vi que…".

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
        tipo_memoria=tipo_memoria,
        estado_emocional=estado_emocional,
        parametrica=parametrica,
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
    presupuesto: int | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Pack de arranque de sesión: qué traigo a esta conversación.

    USAR CUANDO: inicies una sesión con el usuario o tras un cambio de
    contexto — devuelve guardrails + memoria núcleo + compromisos
    abiertos + hechos calientes en UNA llamada, sin LLM ni latencia
    de búsqueda. No reemplaza a consultar_memoria para preguntas
    concretas.

    Con ``presupuesto`` (caracteres) además devuelve ``snapshot``: un
    bloque frozen-snapshot para inyectar UNA VEZ en el system prompt
    al abrir la sesión — lo que no cupo queda reportado en
    ``omitidos`` y se alcanza con consultar_memoria si hace falta.

    Args:
        top_n: Eventos núcleo y hechos calientes a incluir (máx. 16).
        presupuesto: Máximo de caracteres del snapshot (p. ej. 4000
            ≈1000 tokens). Omitir = pack completo sin snapshot.
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Dict con "censores", "nucleo", "curado", "loops", "calientes",
        "stats" y, con presupuesto, "snapshot"/"presupuesto_usado"/
        "omitidos".
    """
    return await _contexto_inicial(top_n, presupuesto, workspace)


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


async def _mapa_lagunas(
    consulta: str,
    top_n: int,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Radiografía de lo que la consulta NO cubrió — las lagunas."""
    items = await _consultar(consulta, top_n, workspace=workspace)
    ev_items = [i for i in items if isinstance(i.get("evento"), dict)]
    lagunas = detectar_lagunas(
        n_resultados=len(ev_items),
        top_n=max(1, top_n),
        dimensiones=[
            str(i["evento"].get("dimension", "")) for i in ev_items
        ],
        confianzas=[float(i.get("confianza", 0.0)) for i in ev_items],
        n_parametricas=sum(
            1 for i in ev_items if i["evento"].get("parametrica")
        ),
    )
    return {
        "consulta": consulta,
        "resultados": len(ev_items),
        "cobertura": lagunas.cobertura,
        "confianza_media": lagunas.confianza_media,
        "lagunas": lagunas.lagunas,
        "es_laguna_total": lagunas.es_laguna_total,
        "n_parametricas": lagunas.n_parametricas,
    }


@mcp_server.tool()
async def mapa_lagunas(
    consulta: str,
    top_n: int = 10,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Lo que la memoria NO sabe sobre la consulta — el mapa de huecos.

    USAR CUANDO: antes de afirmar algo conviene saber si la memoria
    lo respalda — una respuesta honesta declara qué faltó, no solo
    qué encontró. Ejecuta la misma búsqueda que consultar_memoria y
    reporta cobertura, confianza media y las lagunas concretas
    (dimensiones sin evidencia, evidencia débil, contenido puramente
    paramétrico).

    Args:
        consulta: Pregunta o tema en lenguaje natural.
        top_n: Máximo de resultados a evaluar (default 10).
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        {consulta, resultados, cobertura, confianza_media, lagunas,
        es_laguna_total, n_parametricas} — ``lagunas`` lista cada
        hueco en lenguaje legible; vacía = la consulta quedó bien
        cubierta.
    """
    return await _mapa_lagunas(consulta, top_n, workspace)


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
        inaugura_epoca=modelo.inaugura_epoca,
        olvido_dirigido=modelo.olvido_dirigido,
    )


async def _consolidar(workspace: str | None = None) -> dict[str, Any]:
    """Ejecuta la corrida de consolidación-sueño y persiste su reporte.

    Además del replay/promoción/archivo de ADR-003, el sueño ahora
    **semanticiza**: clusters de episodios afines destilan un evento
    ``semantica`` (fuente ``sueno``) vinculado a sus miembros por
    aristas ``gist`` — la memoria abstrae, no solo guarda.
    """
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        eventos = await repo.listar_para_consolidacion()
        plan = decidir_consolidacion([_a_consolidable(e) for e in eventos])
        # Overflow-pressure en la promoción: el tier curado tiene
        # presupuesto — el sueño promueve solo lo que cabe y reporta
        # lo que quedó fuera (la escasez es la higiene del núcleo).
        promovidos = list(plan.promover)
        if promovidos:
            presupuesto = get_settings().curado_max_chars
            usado = await repo.chars_curados()
            por_id = {str(e.id): e for e in eventos}
            caben: list[str] = []
            for eid in promovidos:
                modelo = por_id.get(eid)
                largo = len(modelo.contenido) if modelo else 0
                if usado + largo <= presupuesto:
                    caben.append(eid)
                    usado += largo
            promovidos_fuera = len(promovidos) - len(caben)
            promovidos = caben
        else:
            promovidos_fuera = 0
        await repo.aplicar_consolidacion(
            plan.fortalecer,
            promovidos,
            plan.archivar,
            plan.ejecutado_en,
        )
        # Semanticización: episodios afines → gist semántico.
        episodicos = await repo.episodicas_con_embedding()
        gists = plan_gists(
            [
                EpisodioGist(
                    id=str(e.id),
                    contenido=e.contenido,
                    weight=e.weight,
                    embedding=e.embedding,
                )
                for e in episodicos
            ]
        )
        relaciones = RelacionRepository(session, ctx)
        gist_ids: list[str] = []
        for gist in gists:
            evento_gist = EventoModel(
                timestamp_unix=ahora,
                dimension="cognitiva",
                fuente="sueno",
                contenido=gist.contenido,
                amplitud=0.8,
                tipo="hecho",
                tipo_memoria="semantica",
                tags=["gist", "semanticizacion"],
                workspace_id=ctx.workspace_id,
                autor_id=ctx.usuario_id,
                scope="personal",
            )
            await repo.crear(evento_gist)
            for miembro in gist.miembros:
                await relaciones.vincular(
                    evento_gist.id, uuid.UUID(miembro), "gist"
                )
            gist_ids.append(str(evento_gist.id))
        # Fase REM (Neuro-3): pares de similitud media — distantes pero
        # relacionados — reciben aristas ``rem``. Es la recombinación
        # creativa del sueño: vínculos que el recall nunca descubriría.
        pares_soñados = pares_rem(
            [
                EpisodioGist(
                    id=str(e.id),
                    contenido=e.contenido,
                    weight=e.weight,
                    embedding=e.embedding,
                )
                for e in episodicos
            ]
        )
        rem_ids: list[tuple[str, str]] = []
        for id_a, id_b in pares_soñados:
            if await relaciones.vincular(
                uuid.UUID(id_a), uuid.UUID(id_b), "rem"
            ):
                rem_ids.append((id_a, id_b))
        reporte = ReporteConsolidacionModel(
            workspace_id=ctx.workspace_id,
            ejecutado_en=plan.ejecutado_en,
            fortalecidos=len(plan.fortalecer),
            archivados=len(plan.archivar),
            promovidos=len(promovidos),
            detalle={
                "fortalecidos": plan.fortalecer,
                "promovidos": promovidos,
                "archivados": plan.archivar,
                "gists": gist_ids,
                "rem": rem_ids,
                "salvados": plan.salvados,
                "curado_fuera_presupuesto": promovidos_fuera,
            },
        )
        await ReporteConsolidacionRepository(session, ctx).crear(reporte)
        await session.commit()
    # Ritmo circadiano: sugiere la ventana más quieta — recomendación,
    # nunca un scheduler (el sueño de Nous lo decide el operador).
    ventana = ventana_optima_sueno(
        [e.timestamp_unix for e in eventos]
    )
    return {
        "ejecutado_en": plan.ejecutado_en,
        "evaluados": len(eventos),
        "fortalecidos": len(plan.fortalecer),
        "promovidos": len(promovidos),
        "curado_fuera_presupuesto": promovidos_fuera,
        "archivados": len(plan.archivar),
        "semanticos": len(gist_ids),
        "rem": len(rem_ids),
        # Beneficio del sueño: memorias que habrían cruzado el umbral
        # de archivo pero el replay las rescató — retención salvada.
        "salvados": len(plan.salvados),
        "resumen": resumen_legible(plan),
        "ventana_sugerida": {
            "hora_inicio": ventana.hora_inicio,
            "duracion_horas": ventana.duracion_horas,
            "confianza": ventana.confianza,
        },
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


async def _marcar_curado(
    evento_id: str,
    curado: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Marca una memoria como curada — inmune al olvido (ADR-003)."""
    ctx = await _ctx_escritura(workspace)
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"evento_id no es un UUID válido: {evento_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        if curado:
            # Overflow-pressure: el tier curado tiene presupuesto —
            # curar lo que no cabe obliga a consolidar primero.
            existente = await repo.obtener(eid)
            if existente is not None and not existente.curado:
                presupuesto = get_settings().curado_max_chars
                usado = await repo.chars_curados()
                if usado + len(existente.contenido) > presupuesto:
                    raise CupoCuradoError(
                        f"tier curado lleno ({usado}/"
                        f"{presupuesto} chars) — haz espacio "
                        "descurando o consolidando antes de "
                        "curar otra memoria"
                    )
        ok = await repo.marcar_curado(eid, curado)
        await session.commit()
    if not ok:
        raise ErrorValidacion(
            f"el evento no existe en tu visibilidad: {evento_id}"
        )
    return {"id": str(eid), "curado": curado}


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


# ── ADR-002 Nivel 2: consejero prescriptivo (feature Pro) ────────


def _serializar_state_decision(
    dilema: str,
    eventos: Any,
    objetivos: Any,
    loops: Any,
) -> str:
    """Compone el ``state`` del wire protocol con la línea 4D.

    El consejero decide SOBRE la historia del usuario: dilema +
    eventos recientes de mayor amplitud + metas activas + loops
    abiertos, truncado para caber en un forward pass.
    """
    lineas = [f"Dilema: {dilema}"]
    recientes = sorted(eventos, key=lambda e: e.amplitud, reverse=True)[:12]
    if recientes:
        lineas.append("Historia reciente (mayor amplitud primero):")
        lineas += [
            f"- [{e.dimension}] {e.contenido[:120]}" for e in recientes
        ]
    if objetivos:
        lineas.append("Metas activas:")
        lineas += [f"- {o.titulo}" for o in objetivos[:8]]
    if loops:
        lineas.append("Loops abiertos:")
        lineas += [
            f"- {e.contenido[:100]} (con {e.con_quien or '?'})"
            for e in loops[:6]
        ]
    return "\n".join(lineas)


async def _consultar_decision(
    dilema: str,
    opciones: list[str] | None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Evalúa un dilema contra la línea de universo 4D del usuario."""
    if not idn.licencia_activa():
        raise CupoExcedidoError(
            "consultar_decision es una feature Pro — "
            "activa tu licencia (NOUS_LICENSE_KEY) para usarla"
        )
    opciones = opciones or ["sí", "no"]
    opciones = [o.strip() for o in opciones if o and o.strip()]
    if len(opciones) < 2:
        raise ErrorValidacion(
            "se requieren al menos 2 opciones distintas"
        )
    ctx = await _ctx(workspace)
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session, ctx).listar_por_rango(
            ahora - 30 * 86400, ahora, limite=40, rama="main"
        )
        objetivos = await ObjetivoRepository(session, ctx).listar(
            estado="activo"
        )
        loops = await EventoRepository(
            session, ctx
        ).listar_compromisos_abiertos("main", ahora)
    state = _serializar_state_decision(dilema, eventos, objetivos, loops)
    provider = _obtener_provider_decision()
    if provider is None:
        # Sin modelo System One instalado el consejero no inventa:
        # devuelve el contexto ensamblado para que el agente razone
        # con él — la calibración queda honestamente ausente.
        return {
            "disponible": False,
            "dilema": dilema,
            "opciones": opciones,
            "contexto": {
                "eventos_considerados": len(eventos),
                "metas_activas": len(objetivos),
                "loops_abiertos": len(loops),
            },
            "mensaje": (
                "sin provider System One (NOUS_DECISION_PROVIDER): "
                "contexto 4D ensamblado, calibración ausente"
            ),
        }
    from src.core.decision.consejero import recomendar

    rec = await recomendar(provider, state, opciones)
    if rec is None:
        return {
            "disponible": False,
            "dilema": dilema,
            "opciones": opciones,
            "mensaje": "el provider no devolvió respuesta útil",
        }
    return {
        "disponible": True,
        "dilema": dilema,
        "recomendacion": rec.opcion,
        "probabilidad": rec.probabilidad,
        "distribucion": rec.distribucion,
        "confianza": rec.confianza,
        "riesgo_oculto": rec.riesgo_oculto,
        "motor": rec.motor,
        "contexto": {
            "eventos_considerados": len(eventos),
            "metas_activas": len(objetivos),
            "loops_abiertos": len(loops),
        },
    }


@mcp_server.tool()
async def consultar_decision(
    dilema: str,
    opciones: list[str] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Consejero prescriptivo sobre la línea de universo 4D (Pro).

    USAR CUANDO: el usuario plantee un dilema concreto ("¿acepto este
    proyecto?", "¿compro el equipo A o el B?") y quiera una
    recomendación calibrada alimentada por SU historia — eventos
    recientes, metas activas y loops abiertos componen el state del
    modelo System One.

    NO ES: memoria factual (usa ``consultar_memoria``), ni
    extrapolación de rachas (usa ``proyectar_trayectoria``), ni
    contexto para decidir solo (usa el prompt ``evaluar_decision``).
    ``consultar_decision`` devuelve un veredicto con probabilidades;
    presenta el razonamiento y deja la decisión al usuario.

    Requiere licencia Pro (ADR-002 Nivel 2); sin provider System One
    devuelve ``disponible=false`` con el contexto ensamblado.

    Args:
        dilema: La decisión en texto libre.
        opciones: Opciones concretas (2-6); default ["sí", "no"].
        workspace: Slug o UUID del workspace.

    Returns:
        {disponible, recomendacion, probabilidad, distribucion,
        confianza, riesgo_oculto, motor, contexto}.
    """
    return await _consultar_decision(dilema, opciones, workspace)


def _borrador_a_dict(b: BorradorModel, ahora: int) -> dict[str, Any]:
    """Serializa un borrador del buffer de trabajo."""
    return {
        "id": str(b.id),
        "contenido": b.contenido,
        "dimension": b.dimension,
        "ensayos": b.ensayos,
        "creado_en": b.creado_en,
        "expira_en": b.expira_en,
        "expirado": esta_expirado(b.creado_en, ahora, b.expira_en - b.creado_en),
    }


async def _recordar_borrador(
    contenido: str,
    dimension: Literal["cognitiva", "somatica", "conductual"],
    workspace: str | None,
) -> dict[str, Any]:
    """Guarda un borrador si el buffer tiene cupo."""
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        repo = BorradorRepository(session, ctx)
        activos = await repo.contar_vigentes(ahora)
        if not hay_cupo(activos):
            raise ErrorValidacion(
                f"memoria de trabajo llena ({CAPACIDAD_MAX} borradores) — "
                "ensaya uno para promoverlo o descarta"
            )
        b = await repo.crear(
            BorradorModel(
                contenido=contenido,
                dimension=dimension,
                creado_en=ahora,
                expira_en=ahora + TTL_BORRADOR_SEG,
                workspace_id=ctx.workspace_id,
                autor_id=ctx.usuario_id,
            )
        )
        await session.commit()
    return {
        "id": str(b.id),
        "expira_en": b.expira_en,
        "capacidad": f"{activos + 1}/{CAPACIDAD_MAX}",
    }


async def _listar_borrador(
    workspace: str | None,
) -> list[dict[str, Any]]:
    """Borradores vivos del buffer (los no expirados)."""
    ctx = await _ctx(workspace)
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        filas = await BorradorRepository(session, ctx).listar_vigentes(ahora)
    return [_borrador_a_dict(b, ahora) for b in filas]


async def _ensayar_borrador(
    borrador_id: str,
    workspace: str | None,
) -> dict[str, Any]:
    """Suma un ensayo; al llegar al umbral promueve a evento."""
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        repo = BorradorRepository(session, ctx)
        ensayos = await repo.ensayar(uuid.UUID(str(borrador_id)))
        if ensayos is None:
            raise ErrorValidacion(
                f"borrador no existe o no es visible: {borrador_id}"
            )
        b = await repo.obtener(uuid.UUID(str(borrador_id)))
        if b is None or esta_expirado(
            b.creado_en, ahora, b.expira_en - b.creado_en
        ):
            raise ErrorValidacion("borrador ya expiró — se perdió")
        respuesta: dict[str, Any] = {"id": borrador_id, "ensayos": ensayos}
        if debe_promover(ensayos):
            contenido, dimension = b.contenido, b.dimension
            await repo.descartar(b.id)
            await session.commit()
            promovido = await _recordar(
                contenido=contenido,
                dimension=dimension,  # type: ignore[arg-type]
                amplitud=0.7,
                frecuencia=None,
                timestamp_unix=None,
                fuente="trabajo",
                tipo="hecho",
                workspace=workspace,
            )
            respuesta["promovido"] = True
            respuesta["evento_id"] = promovido.get("id")
        else:
            await session.commit()
            respuesta["promovido"] = False
    return respuesta


async def _descartar_borrador(
    borrador_id: str,
    workspace: str | None,
) -> dict[str, Any]:
    """Saca el borrador del buffer sin promoverlo."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        ok = await BorradorRepository(session, ctx).descartar(
            uuid.UUID(str(borrador_id))
        )
        await session.commit()
    return {"id": borrador_id, "descartado": ok}


@mcp_server.tool()
async def recordar_borrador(
    contenido: str,
    dimension: Literal["cognitiva", "somatica", "conductual"] = "cognitiva",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Guarda un pensamiento volátil en la memoria de trabajo.

    USAR CUANDO: el pensamiento es transitorio — borrador, hipótesis,
    contexto de sesión que no merece aún largo plazo. NO ES la línea
    de universo: expira en ~30 min sin ensayo (el olvido del buffer
    es su función). Si se ensaya lo bastante (≥2 repasos) promueve
    solo a evento real — la repetición es la señal de que importa.
    Para memoria durable usa ``recordar_evento``.

    Args:
        contenido: Texto del borrador.
        dimension: Dimensión del pensamiento (default cognitiva).
        workspace: Slug o UUID del workspace destino.

    Returns:
        {id, expira_en, capacidad} del borrador guardado.
    """
    return await _recordar_borrador(contenido, dimension, workspace)


@mcp_server.tool()
async def listar_borrador(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista los borradores vivos de la memoria de trabajo.

    USAR CUANDO: quieras ver qué tiene el agente "en mente" ahora —
    pensamientos transitorios aún no promovidos ni expirados.

    Returns:
        [{id, contenido, dimension, ensayos, expira_en, expirado}].
    """
    return await _listar_borrador(workspace)


@mcp_server.tool()
async def ensayar_borrador(
    borrador_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Ensaya (repasa) un borrador — la repetición lo promueve.

    Cada ensayo suma una repetición; al llegar a
    ``ENSAYOS_PARA_PROMOVER`` el borrador se gradúa solo: entra a la
    línea de universo como evento real y sale del buffer. Es el
    mecanismo por el que lo que importa sobrevive.

    Args:
        borrador_id: UUID del borrador a repasar.

    Returns:
        {id, ensayos, promovido, evento_id?}.
    """
    return await _ensayar_borrador(borrador_id, workspace)


@mcp_server.tool()
async def descartar_borrador(
    borrador_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Saca un borrador del buffer sin promoverlo.

    USAR CUANDO: el pensamiento ya no es útil — descartarlo es
    sano: el buffer es corto a propósito.

    Returns:
        {id, descartado}.
    """
    return await _descartar_borrador(borrador_id, workspace)


async def _olvidar_gradual(
    evento_id: str,
    olvidar: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Marca/desmarca el olvido dirigido de un evento."""
    ctx = await _ctx_escritura(workspace)
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"evento_id no es un UUID válido: {evento_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        if await repo.obtener(eid) is None:
            raise ErrorValidacion(f"el evento no existe: {evento_id}")
        await repo.marcar_olvido_dirigido(eid, olvidar)
        await session.commit()
    return {"id": str(eid), "olvido_dirigido": olvidar}


@mcp_server.tool()
async def olvidar_gradual(
    evento_id: str,
    olvidar: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Pide olvidar una memoria — decay acelerado, no borrado.

    Olvido dirigido: la huella se disuelve ~3x más rápido por su curva
    natural. NO es ``olvidar_evento`` (DELETE irreversible) ni archivar
    (decisión del sueño): es dejar que el tiempo haga su trabajo antes
    de tiempo. Con ``olvidar=false`` se revierte — la memoria vuelve a
    su curva normal.

    USAR CUANDO: el usuario pida "olvida esto" sin querer destruir la
    historia — el olvido es gradual y auditable.

    Args:
        evento_id: UUID del evento a olvidar.
        olvidar: True = acelerar el olvido; False = revertir.

    Returns:
        {id, olvido_dirigido}.
    """
    return await _olvidar_gradual(evento_id, olvidar, workspace)


async def _repasos_pendientes(
    limite: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista las memorias cuyo repaso óptimo (SRS) ya venció."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        pendientes = await EventoRepository(
            session, ctx
        ).pendientes_revision(int(time.time()), limite=limite)
        return [
            {
                "id": str(m.id),
                "contenido": m.contenido[:200],
                "timestamp_unix": m.timestamp_unix,
                "proxima_revision": m.proxima_revision,
                "dias_atraso": round(
                    (int(time.time()) - (m.proxima_revision or 0))
                    / 86400.0,
                    2,
                ),
                "dimension": m.dimension,
                "tipo_memoria": m.tipo_memoria,
            }
            for m in pendientes
        ]


@mcp_server.tool()
async def repasos_pendientes(
    limite: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Memorias que toca repasar — el SRS vive en la memoria misma.

    Spaced repetition: cada evocación recalcula el repaso óptimo desde
    la curva de retención (justo antes de olvidar es cuando el repaso
    más consolida). Esta lista son las que ya vencieron, las más
    atrasadas primero — consultarlas ES el repaso: refuerza la huella
    y reprograma la siguiente revisión.

    USAR CUANDO: quieras una sesión de repaso espaciado, o preguntar
    "qué se me está olvidando".

    Returns:
        [{id, contenido, timestamp_unix, proxima_revision,
          dias_atraso, dimension, tipo_memoria}].
    """
    return await _repasos_pendientes(limite, workspace)


async def _aplazar_intencion(
    intencion_id: str,
    segundos: int = 3600,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Pospone el disparo de una intención prospectiva (snooze)."""
    ctx = await _ctx_escritura(workspace)
    try:
        iid = uuid.UUID(str(intencion_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"intencion_id no es un UUID válido: {intencion_id!r}"
        ) from e
    hasta = int(time.time()) + max(0, segundos)
    async with get_session(obtener_engine()) as session:
        repo = IntencionProspectivaRepository(session, ctx)
        if not await repo.aplazar(iid, hasta):
            raise ErrorValidacion(
                f"la intención no está pendiente o no existe: {intencion_id}"
            )
        await session.commit()
    return {"id": str(iid), "aplazada_hasta": hasta, "segundos": segundos}


@mcp_server.tool()
async def aplazar_intencion(
    intencion_id: str,
    segundos: int = 3600,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Pospone una intención prospectiva — snooze, no cancelación.

    La intención sigue viva: ni ejecutada ni cancelada, pero ningún
    trigger la evalúa hasta ``segundos`` en el futuro. El snooze es
    honesto — el recordatorio vuelve, no se finge atendido.

    USAR CUANDO: el usuario diga "recuérdamelo luego" o "no ahora".

    Args:
        intencion_id: UUID de la intención a posponer.
        segundos: Cuánto posponer (default 1h).

    Returns:
        {id, aplazada_hasta, segundos}.
    """
    return await _aplazar_intencion(intencion_id, segundos, workspace)


async def _confiar_fuente(
    nombre: str,
    confiable: bool,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Aprueba o pone en cuarentena una fuente (sistema inmune)."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        repo_f = FuenteRepository(session, ctx)
        # Upsert: desconfiar preventivamente de una fuente que aún no
        # escribió nada es legítimo — queda registrada y bloqueada
        # antes de su primera inyección.
        await repo_f.crear(nombre, tipo="agente")
        if not await repo_f.marcar_confiable(nombre, confiable):
            raise ErrorValidacion(f"la fuente no existe: {nombre}")
        liberados = 0
        if confiable:
            # Aprobar la fuente saca del aislamiento a sus eventos —
            # ya indexados, ahora también evocables.
            liberados = await EventoRepository(
                session, ctx
            ).aprobar_cuarentena_fuente(nombre)
        await session.commit()
    return {"fuente": nombre, "confiable": confiable, "liberados": liberados}


@mcp_server.tool()
async def confiar_fuente(
    nombre: str,
    confiable: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Aprueba o marca no-confiable una fuente de memorias.

    Sistema inmune de la memoria: los eventos de una fuente no
    confiable quedan en cuarentena — indexados pero invisibles al
    recall, como un cuerpo que retiene al invasor sin dejarlo actuar.
    Aprobar la fuente libera sus eventos de una vez.

    USAR CUANDO: una fuente externa empiece a inyectar ruido o datos
    dudosos, o para rehabilitar una fuente antes sospechosa.

    Args:
        nombre: Nombre de la fuente (``eventos.fuente``).
        confiable: True = aprobar y liberar; False = cuarentena.

    Returns:
        {fuente, confiable, liberados}.
    """
    return await _confiar_fuente(nombre, confiable, workspace)


async def _listar_cuarentena(
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista los eventos retenidos en cuarentena del workspace."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        cuarentena = await EventoRepository(
            session, ctx
        ).listar_cuarentena(limite=limite)
        return [
            {
                "id": str(m.id),
                "contenido": m.contenido[:200],
                "timestamp_unix": m.timestamp_unix,
                "fuente": m.fuente,
                "dimension": m.dimension,
            }
            for m in cuarentena
        ]


@mcp_server.tool()
async def listar_cuarentena(
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Eventos en cuarentena — memorias retenidas por fuente no confiable.

    USAR CUANDO: auditar qué quedó aislado antes de decidir si la
    fuente se aprueba (``confiar_fuente``) o se poda.

    Returns:
        [{id, contenido, timestamp_unix, fuente, dimension}].
    """
    return await _listar_cuarentena(limite, workspace)


# ── Neuro-5: ciclo de sesión completo ──────────────────────────────
# bootstrap presupuestado → censores que vetan → predicciones que se
# calibran → sesiones que abren con snapshot y cierran con handoff.


async def _iniciar_sesion(
    agente: str | None = None,
    presupuesto: int = 4000,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Abre una sesión: crea el límite y devuelve el bootstrap.

    El arranque de sesión ES el boundary donde la memoria rinde:
    aquí disparan las intenciones temporales vencidas y se captura
    el frozen-snapshot — un bloque que el cliente inyecta UNA VEZ en
    el system prompt y no muta durante la sesión.
    """
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        vencidas = await _disparar_intenciones_temporales(session, ctx)
        sesion = await SesionRepository(session, ctx).crear(
            SesionModel(
                agente=(agente or "")[:80],
                inicio=ahora,
                workspace_id=ctx.workspace_id,
                autor_id=ctx.usuario_id,
            )
        )
        await session.commit()
    pack = await _contexto_inicial(
        top_n=8, presupuesto=presupuesto, workspace=workspace
    )
    pack["sesion_id"] = str(sesion.id)
    pack["inicio"] = ahora
    if vencidas:
        pack["recordatorios"] = [
            {"id": v["id"], "intencion": v["intencion"]}
            for v in vencidas
        ]
    return pack


@mcp_server.tool()
async def iniciar_sesion(
    agente: str | None = None,
    presupuesto: int = 4000,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Abre una sesión de trabajo — bootstrap + snapshot en UNA llamada.

    USAR CUANDO: arranques un turno de trabajo con el usuario. Crea
    el límite de sesión (para el handoff de cierre), dispara las
    intenciones temporales vencidas y devuelve el pack de arranque
    con ``snapshot`` — el bloque frozen-snapshot para inyectar una
    vez en el system prompt. Es ``contexto_inicial`` + persistencia
    de boundary.

    Args:
        agente: Nombre del agente que abre la sesión (p. ej. "devin").
        presupuesto: Caracteres del snapshot (default 4000 ≈ 1000
            tokens; 0 o negativo = pack completo sin medidor).
        workspace: Slug o UUID del workspace.

    Returns:
        {sesion_id, inicio, censores, nucleo, curado, loops,
        calientes, stats, snapshot, presupuesto_usado, omitidos,
        recordatorios?}.
    """
    return await _iniciar_sesion(agente, presupuesto, workspace)


async def _cerrar_sesion(
    sesion_id: str | None = None,
    resumen: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Cierra la sesión abierta y genera su handoff de continuidad.

    El handoff no es un dump — es el documento curado que la próxima
    sesión lee: guardrails heredados, loops aún abiertos, lo que esta
    sesión escribió y el núcleo curado vigente.
    """
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        repo_s = SesionRepository(session, ctx)
        if sesion_id:
            try:
                sid = uuid.UUID(str(sesion_id))
            except (ValueError, AttributeError) as e:
                raise ErrorValidacion(
                    f"sesion_id no es un UUID válido: {sesion_id!r}"
                ) from e
            sesion = await repo_s.obtener(sid)
        else:
            sesion = await repo_s.ultima_abierta()
        if sesion is None or sesion.fin is not None:
            raise ErrorValidacion(
                "no hay sesión abierta para cerrar"
                + (f" ({sesion_id})" if sesion_id else "")
            )
        repo_e = EventoRepository(session, ctx)
        escritos = await repo_e.listar_por_rango(
            sesion.inicio, ahora, limite=40
        )
        loops = await repo_e.listar_compromisos_abiertos("main", ahora)
        curados = await repo_e.listar_curados(limite=16)
        censores = await repo_e.listar_censores()
        handoff = generar_handoff(
            agente=sesion.agente,
            inicio=sesion.inicio,
            fin=ahora,
            resumen=resumen,
            escritos=[
                {"contenido": e.contenido, "tipo": e.tipo}
                for e in escritos
            ],
            loops=[
                {
                    "contenido": e.contenido,
                    "dias_abierto": (ahora - e.timestamp_unix) // 86_400,
                }
                for e in loops
            ],
            nucleo=[{"contenido": e.contenido} for e in curados],
            censores=[
                {
                    "contenido": e.contenido,
                    "severidad": parsear_censor(e.contexto)[0],
                }
                for e in censores
            ],
        )
        sesion.fin = ahora
        sesion.resumen = resumen
        sesion.handoff = handoff
        sesion.eventos = len(escritos)
        await session.commit()
    return {
        "id": str(sesion.id),
        "fin": ahora,
        "eventos": len(escritos),
        "handoff": handoff,
    }


@mcp_server.tool()
async def cerrar_sesion(
    sesion_id: str | None = None,
    resumen: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Cierra la sesión abierta — genera el handoff de continuidad.

    USAR CUANDO: termine un turno de trabajo — el boundary donde la
    memoria "rinde": lo que quedó abierto, escrito y curado se destila
    en un documento que la próxima sesión lee en dos minutos. Sin
    cierre no hay continuidad — la worldline guarda todo, pero el
    handoff cuenta solo lo que importa.

    Args:
        sesion_id: UUID de la sesión (default: la abierta más reciente).
        resumen: Resumen libre de qué se hizo (va al handoff).
        workspace: Slug o UUID del workspace.

    Returns:
        {id, fin, eventos, handoff}.
    """
    return await _cerrar_sesion(sesion_id, resumen, workspace)


async def _obtener_handoff(
    sesion_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Devuelve el handoff de una sesión cerrada."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        repo_s = SesionRepository(session, ctx)
        if sesion_id:
            try:
                sid = uuid.UUID(str(sesion_id))
            except (ValueError, AttributeError) as e:
                raise ErrorValidacion(
                    f"sesion_id no es un UUID válido: {sesion_id!r}"
                ) from e
            sesion = await repo_s.obtener(sid)
        else:
            sesion = await repo_s.ultima_cerrada()
        if sesion is None or not sesion.handoff:
            raise ErrorValidacion("no hay handoff disponible")
    return {
        "id": str(sesion.id),
        "agente": sesion.agente,
        "inicio": sesion.inicio,
        "fin": sesion.fin,
        "eventos": sesion.eventos,
        "handoff": sesion.handoff,
    }


@mcp_server.tool()
async def obtener_handoff(
    sesion_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """El documento de continuidad de una sesión cerrada.

    USAR CUANDO: quieras retomar el trabajo donde una sesión previa lo
    dejó — o pegárselo a otra agente/máquina como contexto de arranque.
    Sin id devuelve el handoff de la última sesión cerrada.

    Returns:
        {id, agente, inicio, fin, eventos, handoff}.
    """
    return await _obtener_handoff(sesion_id, workspace)


async def _listar_sesiones(
    limite: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Historial de sesiones del workspace."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        sesiones = await SesionRepository(session, ctx).listar(limite)
    return [
        {
            "id": str(s.id),
            "agente": s.agente,
            "inicio": s.inicio,
            "fin": s.fin,
            "eventos": s.eventos,
            "cerrada": s.fin is not None,
            "tiene_handoff": bool(s.handoff),
        }
        for s in sesiones
    ]


@mcp_server.tool()
async def listar_sesiones(
    limite: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Historial de sesiones — los boundaries de la memoria.

    Returns:
        [{id, agente, inicio, fin, eventos, cerrada, tiene_handoff}].
    """
    return await _listar_sesiones(limite, workspace)


async def _crear_censor(
    regla: str,
    severidad: str = "advertir",
    dominio: str | None = None,
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registra un censor — una corrección madura a regla de conducta."""
    if severidad not in ("bloquear", "advertir"):
        raise ErrorValidacion(
            "severidad debe ser 'bloquear' o 'advertir'"
        )
    respuesta = await _recordar(
        contenido=regla,
        dimension="conductual",
        fuente="usuario",
        amplitud=0.9,
        frecuencia=None,
        timestamp_unix=None,
        tipo="censor",
        tags=["censor"],
        scope=scope,
        workspace=workspace,
        contexto_extra={
            "severidad": severidad,
            **({"dominio": dominio} if dominio else {}),
        },
    )
    respuesta["severidad"] = severidad
    return respuesta


@mcp_server.tool()
async def crear_censor(
    regla: str,
    severidad: str = "advertir",
    dominio: str | None = None,
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Convierte una corrección en regla — el guardrail de la memoria.

    USAR CUANDO: el usuario enseñe "nunca hagas X" o "siempre verifica
    Y antes de Z" — la corrección madura a censor y pesa ANTES de
    actuar: ``verificar_accion`` la consulta; el bootstrap la carga
    de primera sección. ``bloquear`` veta la acción; ``advertir``
    deja pasar con cautela. Los censores son inmunes al olvido —
    una regla no se olvida sola.

    NO ES: una preferencia blanda (usa tipo='preferencia') ni un
    compromiso pendiente (usa tipo='compromiso').

    Args:
        regla: La regla en texto ("nunca pushear a main sin tests").
        severidad: "advertir" (default) o "bloquear" (veta).
        dominio: Ámbito opcional de la regla (p. ej. "git", "pagos").
        scope: "personal" (default) o "workspace" (regla del equipo).
        workspace: Slug o UUID del workspace.

    Returns:
        {id, severidad, staged?} — el evento censor creado.
    """
    return await _crear_censor(regla, severidad, dominio, scope, workspace)


async def _listar_censores(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Guardrails vigentes del workspace."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        censores = await EventoRepository(session, ctx).listar_censores()
    resultado = []
    for c in censores:
        severidad, dominio = parsear_censor(c.contexto)
        resultado.append(
            {
                **_modelo_a_dict(c),
                "severidad": severidad,
                "dominio": dominio,
            }
        )
    return resultado


@mcp_server.tool()
async def listar_censores(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Los guardrails activos — reglas aprendidas que vetan o advierten.

    USAR CUANDO: auditar qué reglas rigen la conducta antes de
    verificar una acción o de crear una nueva. Se revocan con
    ``olvidar_evento`` o se corrigen con ``reemplaza_id``.

    Returns:
        [{id, contenido, severidad, dominio, timestamp_unix, ...}].
    """
    return await _listar_censores(workspace)


def _coseno(a: list[float], b: list[float]) -> float:
    """Similitud coseno entre dos vectores — verificación en memoria."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))


async def _verificar_accion(
    accion: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Consulta los censores contra una acción propuesta — pre-acción."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        censores = await EventoRepository(session, ctx).listar_censores()
    if not censores:
        return {
            "permitido": True,
            "censores": [],
            "motivo": "sin censores vigentes — vía libre",
        }
    try:
        vector = await _obtener_embeddings().generar(accion)
    except Exception:
        vector = None
    coincidencias: list[CensorCoincidente] = []
    for c in censores:
        if vector is not None and c.embedding:
            sim = _coseno(vector, list(c.embedding))
        else:
            # Sin embedding disponible cae a cobertura léxica: ¿qué
            # fracción de los términos de la acción aparece en la
            # regla? — parafrasea mejor que la contención literal.
            terminos = {
                t for t in accion.lower().split() if len(t) > 2
            }
            if terminos:
                contenido = c.contenido.lower()
                cubiertos = sum(1 for t in terminos if t in contenido)
                sim = cubiertos / len(terminos)
            else:
                sim = 0.0
        if sim < UMBRAL_ACTIVACION:
            continue
        severidad, dominio = parsear_censor(c.contexto)
        coincidencias.append(
            CensorCoincidente(
                id=str(c.id),
                contenido=c.contenido,
                severidad=severidad,
                dominio=dominio,
                similitud=round(sim, 4),
            )
        )
    coincidencias.sort(key=lambda c: c.similitud, reverse=True)
    veredicto = veredicto_censores(coincidencias[:8])
    return {
        "permitido": veredicto.permitido,
        "censores": [
            {
                "id": c.id,
                "contenido": c.contenido,
                "severidad": c.severidad,
                "dominio": c.dominio,
                "similitud": c.similitud,
            }
            for c in veredicto.censores
        ],
        "motivo": veredicto.motivo,
    }


@mcp_server.tool()
async def verificar_accion(
    accion: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Verifica una acción ANTES de ejecutarla contra los guardrails.

    USAR CUANDO: estés a punto de hacer algo potencialmente prohibido
    — pushear, borrar, pagar, escribir en prod. Los censores del
    workspace se comparan semánticamente con la acción: un ``bloquear``
    veta (obedece — la regla existe porque alguien la enseñó); una
    ``advertir`` deja pasar informando el riesgo.

    NO ES: confirmación del usuario — es memoria de reglas: el
    veredicto sale de lo aprendido, no de una política externa.

    Args:
        accion: La acción propuesta en texto ("pushear a main").
        workspace: Slug o UUID del workspace.

    Returns:
        {permitido, censores: [{id, contenido, severidad, dominio,
        similitud}], motivo}.
    """
    return await _verificar_accion(accion, workspace)


async def _registrar_prediccion(
    afirmacion: str,
    confianza: float,
    evento_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Declara una confianza ANTES del resultado — materia del Brier."""
    if not 0.0 <= confianza <= 1.0:
        raise ErrorValidacion("confianza debe estar entre 0.0 y 1.0")
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    eid: uuid.UUID | None = None
    if evento_id:
        try:
            eid = uuid.UUID(str(evento_id))
        except (ValueError, AttributeError) as e:
            raise ErrorValidacion(
                f"evento_id no es un UUID válido: {evento_id!r}"
            ) from e
    async with get_session(obtener_engine()) as session:
        prediccion = await PrediccionRepository(session, ctx).crear(
            PrediccionModel(
                afirmacion=afirmacion,
                confianza=confianza,
                evento_id=eid,
                creado_en=ahora,
                workspace_id=ctx.workspace_id,
                autor_id=ctx.usuario_id,
            )
        )
        await session.commit()
    return {
        "id": str(prediccion.id),
        "afirmacion": afirmacion,
        "confianza": confianza,
        "creado_en": ahora,
    }


@mcp_server.tool()
async def registrar_prediccion(
    afirmacion: str,
    confianza: float,
    evento_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registra una predicción con su confianza — honestidad medible.

    USAR CUANDO: afirmes algo con probabilidad ("el deploy sale bien",
    "este bug era X") — la confianza declarada ANTES del resultado es
    lo único que hace medible la calibración. Al cumplirse o fallar,
    ``resolver_prediccion`` cierra el loop y el Brier score dice si el
    90% dicho valía 90%.

    Args:
        afirmacion: Lo que se predice, en texto.
        confianza: Probabilidad declarada (0.0–1.0).
        evento_id: UUID del evento relacionado, si lo hay.
        workspace: Slug o UUID del workspace.

    Returns:
        {id, afirmacion, confianza, creado_en}.
    """
    return await _registrar_prediccion(
        afirmacion, confianza, evento_id, workspace
    )


async def _resolver_prediccion(
    prediccion_id: str,
    acierto: bool,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Cierra el loop: la vida dijo si la predicción acertó."""
    ctx = await _ctx_escritura(workspace)
    try:
        pid = uuid.UUID(str(prediccion_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"prediccion_id no es un UUID válido: {prediccion_id!r}"
        ) from e
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        ok = await PrediccionRepository(session, ctx).resolver(
            pid, acierto, ahora
        )
        await session.commit()
    if not ok:
        raise ErrorValidacion(
            "la predicción no existe o ya está resuelta: "
            f"{prediccion_id}"
        )
    return {"id": str(pid), "acierto": acierto, "resuelto_en": ahora}


@mcp_server.tool()
async def resolver_prediccion(
    prediccion_id: str,
    acierto: bool,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Resuelve una predicción — acertó o falló, el Brier lo cuenta.

    USAR CUANDO: se sepa el resultado de algo predicho antes. Sin
    resolución no hay calibración: una confianza que nunca se cobra
    es solo una opinión.

    Args:
        prediccion_id: UUID de la predicción (de registrar_prediccion).
        acierto: True si la afirmación resultó cierta.
        workspace: Slug o UUID del workspace.

    Returns:
        {id, acierto, resuelto_en}.
    """
    return await _resolver_prediccion(prediccion_id, acierto, workspace)


async def _listar_predicciones(
    pendientes: bool | None = None,
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Historial de predicciones — abiertas y resueltas."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        filas = await PrediccionRepository(session, ctx).listar(
            pendientes=pendientes, limite=limite
        )
    return [
        {
            "id": str(p.id),
            "afirmacion": p.afirmacion,
            "confianza": p.confianza,
            "acierto": p.acierto,
            "creado_en": p.creado_en,
            "resuelto_en": p.resuelto_en,
        }
        for p in filas
    ]


@mcp_server.tool()
async def listar_predicciones(
    pendientes: bool | None = None,
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Predicciones del workspace — abiertas y resueltas.

    Args:
        pendientes: True = solo abiertas, False = solo resueltas,
            None = todas.
        limite: Máximo a devolver (default 50).
        workspace: Slug o UUID del workspace.

    Returns:
        [{id, afirmacion, confianza, acierto, creado_en, resuelto_en}].
    """
    return await _listar_predicciones(pendientes, limite, workspace)


async def _reporte_calibracion(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Brier + curva de confiabilidad — la honestidad medida."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        repo = PrediccionRepository(session, ctx)
        resueltas = await repo.listar(pendientes=False, limite=10_000)
        pendientes = await repo.listar(pendientes=True, limite=10_000)
    reporte = _reporte_calibracion_core(
        [
            PrediccionResuelta(confianza=p.confianza, acierto=bool(p.acierto))
            for p in resueltas
        ],
        n_pendientes=len(pendientes),
    )
    return {
        "resueltas": reporte.n_resueltas,
        "pendientes": len(pendientes),
        "brier": reporte.brier,
        "acierto_empirico": reporte.acierto_empirico,
        "confianza_media": reporte.confianza_media,
        "sesgo_sobreconfianza": reporte.sesgo,
        "curva": [
            {
                "desde": b.desde,
                "hasta": b.hasta,
                "n": b.n,
                "confianza_media": b.confianza_media,
                "acierto_empirico": b.acierto_empirico,
            }
            for b in reporte.curva
        ],
    }


@mcp_server.tool()
async def reporte_calibracion(
    workspace: str | None = None,
) -> dict[str, Any]:
    """El Brier score de la memoria — qué tan real es "90% seguro".

    Mide la distancia entre la confianza declarada y el acierto
    empírico: brier 0.0 = oráculo; la curva por deciles muestra DÓNDE
    falla la calibración (p. ej. sobreconfianza en el rango 0.7–0.9);
    ``sesgo_sobreconfianza`` positivo = el sistema se confía de más.

    USAR CUANDO: auditar la honestidad del sistema o ajustar cuánta
    fe poner en sus probabilidades. Sin predicciones resueltas el
    reporte confiesa ``brier: null`` — sin datos no hay métrica.

    Returns:
        {resueltas, pendientes, brier, acierto_empirico,
        confianza_media, sesgo_sobreconfianza, curva}.
    """
    return await _reporte_calibracion(workspace)


async def _pendientes_aprobacion(
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Propuestas de escritura esperando aprobación humana."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        staged = await EventoRepository(session, ctx).listar_staged(limite)
    return [_modelo_a_dict(e) for e in staged]


@mcp_server.tool()
async def pendientes_aprobacion(
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Escrituras staged — la memoria propone, el humano dispone.

    Solo hay pendientes si ``NOUS_ESCRITURA_APROBACION=true``: con la
    política activa, recordar_evento deja la huella indexada pero
    fuera del recall hasta que alguien la apruebe.

    Returns:
        [{id, contenido, tipo, fuente, timestamp_unix, ...}].
    """
    return await _pendientes_aprobacion(limite, workspace)


async def _aprobar_evento(
    evento_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Convierte una propuesta staged en memoria real."""
    ctx = await _ctx_escritura(workspace)
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"evento_id no es un UUID válido: {evento_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        aprobados = await EventoRepository(session, ctx).aprobar_staged([eid])
        await session.commit()
    if not aprobados:
        raise ErrorValidacion(
            f"no hay propuesta staged con ese id: {evento_id}"
        )
    return {"id": str(eid), "aprobado": True}


@mcp_server.tool()
async def aprobar_evento(
    evento_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Aprueba una escritura staged — entra al recall como memoria.

    Args:
        evento_id: UUID de la propuesta (de pendientes_aprobacion).
        workspace: Slug o UUID del workspace.

    Returns:
        {id, aprobado}.
    """
    return await _aprobar_evento(evento_id, workspace)


async def _rechazar_evento(
    evento_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Descarta una propuesta staged — la escritura nunca existió."""
    ctx = await _ctx_escritura(workspace)
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(
            f"evento_id no es un UUID válido: {evento_id!r}"
        ) from e
    async with get_session(obtener_engine()) as session:
        rechazados = await EventoRepository(
            session, ctx
        ).rechazar_staged([eid])
        await session.commit()
    if not rechazados:
        raise ErrorValidacion(
            f"no hay propuesta staged con ese id: {evento_id}"
        )
    return {"id": str(eid), "rechazado": True}


@mcp_server.tool()
async def rechazar_evento(
    evento_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Rechaza una escritura staged — se descarta sin dejar huella.

    NO ES olvidar_evento: la propuesta nunca fue memoria — borrarla
    es decidir que la escritura no ocurrió, no olvidar lo aprendido.

    Returns:
        {id, rechazado}.
    """
    return await _rechazar_evento(evento_id, workspace)


async def _aprobar_pendientes(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Aprueba de una vez todas las propuestas staged visibles."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        staged = await repo.listar_staged(limite=500)
        aprobados = await repo.aprobar_staged([e.id for e in staged])
        await session.commit()
    return {"aprobados": aprobados}


@mcp_server.tool()
async def aprobar_pendientes(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Aprueba todas las escrituras staged de una vez.

    USAR CUANDO: revisaste la cola (``pendientes_aprobacion``) y todo
    está bien — la aprobación en lote es la vía rápida, la individual
    es la revisada.

    Returns:
        {aprobados}.
    """
    return await _aprobar_pendientes(workspace)
