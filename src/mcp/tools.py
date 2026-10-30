"""Tools MCP de dominio de Nous: memoria persistente 4D.

Cada tool declara explícitamente cuándo usarla y cuándo NO, para que
el modelo que la invoque no confunda sus responsabilidades.
"""

import asyncio
import gc
import json
import logging
import queue
import threading
import time
import uuid
from collections import OrderedDict, deque
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
from src.core.memoria.operaciones import sugerir_clarificacion
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
from src.core.persona import (
    ARCHIVOS,
    CLAVE_BANCO,
    catalogo,
    faltantes,
    hash_banco,
    render,
)
from src.core.reranker import ReordenadorLocal
from src.core.routing import (
    ORDEN_POR_OP,
    IntencionConsulta,
    OpTemporal,
    clasificar_intencion,
    clasificar_op_temporal,
    consulta_nucleo,
    direccion_temporal,
    fecha_asof,
)
from src.core.temporal import detectar_trayectorias, proyectar
from src.mcp.contract import ingles
from src.mcp.server import mcp_server
from src.storage.chunks import indexar_chunks_evento
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
        raise PermisoDenegadoError("read-only role: no write permission")
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
        _invalidar_recall()


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
    consolidaciones = [item for item in items if isinstance(item, _ItemConsolidacion)]
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
        "reemplazado_por": (str(modelo.reemplazado_por) if modelo.reemplazado_por else None),
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


def _resultado_a_dict(r: ResultadoBusqueda, confiable: bool | None = True) -> dict[str, Any]:
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
        raise ErrorValidacion("amplitude must be between 0.0 and 1.0")
    if frecuencia is not None and frecuencia <= 0.0:
        raise ErrorValidacion("frequency must be positive (Hz)")
    if tipo not in TIPOS_EVENTO:
        raise ErrorValidacion(f"kind must be one of {', '.join(TIPOS_EVENTO)}")
    if estado_emocional is not None and not -1.0 <= estado_emocional <= 1.0:
        raise ErrorValidacion("emotional_state must be between -1.0 and 1.0")
    ts = timestamp_unix if timestamp_unix is not None else int(time.time())
    try:
        expira_en = parsear_ttl(ttl, ts)
    except ValueError as e:
        raise ErrorValidacion(str(e)) from e
    if con_quien and tipo != "compromiso":
        raise ErrorValidacion("with_whom only applies to kind='commitment'")
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
                raise ErrorValidacion(f"supersedes_id is not a valid UUID: {reemplaza_id!r}") from e
            viejo = await repo.obtener(viejo_id)
            if viejo is None:
                raise ErrorValidacion(f"event to supersede not found: {reemplaza_id}")
            await repo.marcar_reemplazado(viejo_id, evento.id)
            if await relaciones.vincular(viejo_id, evento.id, "reemplaza"):
                respuesta["reemplaza"] = str(viejo_id)
            # Reconsolidación: el viejo quedó lábil al evocarlo
            # hace poco — este supersede dentro de la ventana cuenta
            # como reescritura de la huella viva, no solo versión.
            if esta_labil(viejo.labil_hasta, int(time.time())):
                await relaciones.vincular(viejo_id, evento.id, "reconsolida")
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
        _invalidar_recall()
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
            similar = await repo.buscar_por_vector(vector, limite=settings.relacion_max + 2)
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
                        and settings.relacion_umbral <= sim < settings.dedup_umbral
                    ):
                        if await relaciones.vincular(item.evento_id, vecino.id, "semantica", sim):
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
                    await repo.debilitar_lote(proactivos, INTERFERENCIA_FACTOR)
            if sobrevive:
                # Retrieval a nivel fragmento: los documentos largos
                # también se indexan por secciones (evento_chunks).
                await indexar_chunks_evento(
                    session,
                    item.evento_id,
                    item.contenido,
                    _obtener_embeddings(),
                )
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
                        if r.embedding is not None and r.id != item.evento_id
                    ],
                )
                nueva_amplitud = amplitud_con_sorpresa(item.amplitud, s)
                if nueva_amplitud > item.amplitud:
                    await repo.actualizar_amplitud(item.evento_id, nueva_amplitud)
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
                    await relaciones.vincular(item.evento_id, anterior.id, "encadena")
                # ADR-003: interferencia — marcador de superación +
                # vecino semántico cercano = contradicción probable.
                conflictos = detectar_conflicto(
                    item.contenido,
                    [(str(v.id), s) for v, s in similar if v.id != item.evento_id],
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
            _invalidar_recall()
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
    consulta_pack = str((modelo.trigger_valor or {}).get("query") or modelo.intencion)
    pack_rows = await EventoRepository(
        session,
        Contexto(
            usuario_id=modelo.autor_id,
            workspace_id=modelo.workspace_id,
            rol="member",
        ),
    ).buscar_por_texto(consulta_pack, limite=3)
    pack = [{"id": str(e.id), "contenido": e.contenido[:200]} for e, _ in pack_rows]
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


async def _evaluar_intenciones_evento(session: Any, item: _ItemIndexacion) -> None:
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


async def _disparar_intenciones_temporales(session: Any, ctx: Contexto) -> list[dict[str, Any]]:
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


async def _consolidar_luego(engine: AsyncEngine, items: list[_ItemConsolidacion]) -> None:
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
            await EventoRepository(session).reforzar(ids, labil_hasta=ventana_labil(ahora))
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
                    for par in pares_coactivos([str(e) for e in item.eventos_ids])
                    if uuid.UUID(par[0]) in existentes and uuid.UUID(par[1]) in existentes
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
            perdedores = [eid for item in items for eid in item.debilitar_ids if eid in existentes]
            if perdedores:
                await repo_ev.debilitar_lote(perdedores, RIF_FACTOR, piso=RIF_PISO_WEIGHT)
            await session.commit()
            _invalidar_recall()
    except Exception:  # noqa: BLE001 — el fallo no debe propagarse al loop
        logger.exception("no se pudieron consolidar efectos de evocación")


# Cache de recall (Neuro-7): consultas idénticas en ráfaga
# (mismo usuario+workspace+rama+query) saltan el pipeline — el
# TTL corto lo hace seguro ante escrituras concurrentes y la
# clave aísla por tenant para que jamás se filtre entre
# workspaces. OrderedDict = LRU con tope acotado.
_RECALL_CACHE: OrderedDict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = OrderedDict()
_RECALL_CACHE_MAX = 64


def _invalidar_recall() -> None:
    """Descarta resultados cacheados tras una escritura.

    Toda mutación que cambie qué es evocable (insertar, archivar,
    restaurar, aprobar, cuarentena, olvido, dedup) debe invalidar:
    el TTL solo acota la vida de una entrada, no da coherencia.
    """
    _RECALL_CACHE.clear()


def _cache_key(
    ctx: Contexto,
    consulta: str,
    top_n: int,
    rama: str | None,
    max_tokens: int | None,
) -> tuple[Any, ...]:
    """Clave de cache por tenant — nunca mezcla workspaces."""
    return (
        ctx.usuario_id,
        ctx.workspace_id,
        rama,
        consulta.strip().lower(),
        top_n,
        max_tokens,
    )


def _estimar_tokens(item: dict[str, Any]) -> int:
    """Estimación de tokens de un item serializado (~4 chars/tok)."""
    return max(1, len(json.dumps(item, ensure_ascii=False, default=str)) // 4)


def _recortar_por_tokens(items: list[dict[str, Any]], max_tokens: int) -> list[dict[str, Any]]:
    """Recorta la respuesta al presupuesto de tokens pedido.

    Los recordatorios se preservan siempre — son la señal
    prospectiva que el turno exige; el recorte cae sobre la cola
    de resultados (la evidencia de menor rango se sacrifica
    primero). Los items omitidos se declaran con su conteo.
    """
    salida: list[dict[str, Any]] = []
    usados = 0
    omitidos = 0
    for item in items:
        if item.get("recordatorio"):
            usados += _estimar_tokens(item)
            salida.append(item)
            continue
        costo = _estimar_tokens(item)
        if usados + costo > max_tokens:
            omitidos += 1
            continue
        usados += costo
        salida.append(item)
    if omitidos:
        salida.append(
            {
                "truncado": {
                    "por": "max_tokens",
                    "presupuesto": max_tokens,
                    "omitidos": omitidos,
                }
            }
        )
    return salida


async def _consultar(
    consulta: str,
    top_n: int,
    rama: str | None = None,
    workspace: str | None = None,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Ejecuta la búsqueda híbrida 4D y serializa los resultados."""
    ctx = await _ctx(workspace)
    ttl = get_settings().recall_cache_ttl
    clave = _cache_key(ctx, consulta, top_n, rama, max_tokens)
    if ttl > 0 and (hit := _RECALL_CACHE.get(clave)) is not None:
        expira, salida = hit
        if expira > time.time():
            _RECALL_CACHE.move_to_end(clave)
            return [dict(item) for item in salida]
        del _RECALL_CACHE[clave]
    recordatorios: list[dict[str, Any]] = []
    async with get_session(obtener_engine()) as session:
        # ADR-003: cada consulta es la oportunidad natural de
        # "acordarse" — las intenciones temporales vencidas disparan
        # aquí y encabezan la respuesta como recordatorios.
        vencidas = await _disparar_intenciones_temporales(session, ctx)
        if vencidas:
            await session.commit()
            _invalidar_recall()
            recordatorios = [
                {
                    "evento": {
                        "id": v["id"],
                        "timestamp_unix": int(time.time()),
                        "dimension": "conductual",
                        "contenido": ("Intención prospectiva disparada: " + v["intencion"]),
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
        # Router de intención: «quién es X» → ficha de entidad al
        # frente; «primero/último/reciente» → pata ordenada por
        # timestamp_unix fusionada con el RAG normal (la temporal
        # sostiene la respuesta cuando el RAG es flojo).
        intencion, nombre_entidad = clasificar_intencion(consulta)
        # Neuro-7: la pregunta temporal se clasifica en su
        # operación de memoria — earliest/latest siguen la pata
        # ordenada; duration/asof/change/provenance tienen pata
        # y veredicto propios (span, estado-en-T, punto de
        # cambio, fuente primaria).
        op = clasificar_op_temporal(consulta) if intencion == IntencionConsulta.TEMPORAL else None
        orden = (
            ORDEN_POR_OP[op]
            if op is not None
            else direccion_temporal(consulta)
            if intencion == IntencionConsulta.TEMPORAL
            else None
        )
        asof_unix = fecha_asof(consulta) if op == OpTemporal.ASOF else None
        # La pata temporal filtra por la evidencia, no por el
        # andamiaje de la pregunta («primer mensaje de samay» se
        # resuelve ordenando los eventos de samay, no buscando la
        # palabra «mensaje» en cualquier parte). En las ops con
        # fecha explícita la fecha es el corte, no evidencia del
        # contenido.
        nucleo = (
            consulta_nucleo(consulta, descartar_fechas=op == OpTemporal.ASOF)
            if intencion == IntencionConsulta.TEMPORAL
            else None
        )
        verificacion: dict[str, Any] = {}
        resultados = await _obtener_busqueda().buscar(
            session,
            consulta,
            top_n=max(1, min(top_n, 50)),
            rama=rama,
            ctx=ctx,
            competidores=competidores,
            orden_temporal=orden,
            query_temporal=nucleo or None,
            op_temporal=op,
            asof_unix=asof_unix,
            verificacion=verificacion,
        )
        estado_actual: float | None = None
        episodios_por_gist: dict[str, list[dict[str, Any]]] = {}
        confianza_fuentes: dict[str, bool] = {}
        asociados: list[tuple[EventoModel, float]] = []
        # Vecinos por tag 1-hop: id → tags compartidos que los
        # trajeron (via = "tags", nunca confundidos con los hits).
        tags_vecinos: dict[uuid.UUID, tuple[str, ...]] = {}
        if resultados:
            # ADR-003 patrón 6 — memoria dependiente de contexto/estado:
            # recuerdos codificados en una hora/fuente/estado parecidos
            # al actual suben de rango. Solo premia, nunca excluye.
            estado_actual = await EventoRepository(session, ctx).ultimo_estado_somatico()
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
                    and ajuste.get(r.evento_id, r.score_final) != r.score_final
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
                abiertos = await EventoRepository(session, ctx).ids_compromisos_abiertos(
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
                    resultados.sort(key=lambda r: r.score_final, reverse=True)
            # Expansión de gist (Neuro-3): un resultado semántico
            # trae sus episodios miembros — la abstracción llega
            # con la evidencia que la sostiene.
            gist_ids = [
                uuid.UUID(r.evento_id)
                for r in resultados
                if r.evento is not None and "gist" in (r.evento.tags or [])
            ]
            for gid in gist_ids:
                miembros = await EventoRepository(session, ctx).episodios_de_gist(gid, limite=3)
                episodios_por_gist[str(gid)] = [
                    _evento_a_dict(Evento.desde_modelo(m)) for m in miembros
                ]
            # Amnesia de fuente (Neuro-4): la confianza pesa el
            # origen — un solo query por lote para todas las fuentes
            # presentes en los resultados.
            confianza_fuentes = await FuenteRepository(session, ctx).fuentes_confiables(
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
                uuid.UUID(r.evento_id) for r in resultados[:COACTIVA_TOP_K] if r.evento is not None
            }
            if ganadores:
                aristas = await RelacionRepository(session, ctx).vecinos_coactivos(
                    list(ganadores), limite=24
                )
                mejores: dict[uuid.UUID, float] = {}
                for ar in aristas:
                    peso_e = peso_efectivo_coactiva(ar.peso, ar.ultima_coactiva, ahora_consulta)
                    for extremo in (ar.origen_id, ar.destino_id):
                        if extremo in ganadores or str(extremo) in {
                            r.evento_id for r in resultados
                        }:
                            continue
                        if peso_e > mejores.get(extremo, 0.0):
                            mejores[extremo] = peso_e
                top_vecinos = sorted(mejores.items(), key=lambda kv: kv[1], reverse=True)[:4]
                if top_vecinos:
                    modelos = await EventoRepository(session, ctx).obtener_muchos(
                        [e for e, _ in top_vecinos]
                    )
                    pesos = dict(top_vecinos)
                    asociados = [
                        (m, pesos[m.id])
                        for m in modelos
                        if not m.staged
                        and not m.archivado
                        and not m.cuarentena
                        and m.reemplazado_por is None
                    ]
            # Expansión asociativa 1-hop por tags (Neuro-7): solo
            # cuando el recall vino corto y solo por tags raros —
            # un tag en >15% del corpus es un hub genérico que
            # conecta todo con todo; expandir por él no asocia,
            # solo inunda. Regla fingerprint: un tag raro y
            # específico basta; el resto exige ≥2 compartidos.
            repo_ev = EventoRepository(session, ctx)
            if resultados and len(resultados) < top_n:
                tags_ganadores = {
                    t
                    for r in resultados[:3]
                    if r.evento is not None and r.evento.tags
                    for t in r.evento.tags
                }
                if tags_ganadores:
                    total = await repo_ev.total_evocables(rama)
                    tope_hub = max(3, int(total * 0.15))
                    conteos = dict(await repo_ev.listar_tags(500))
                    raros = {t for t in tags_ganadores if conteos.get(t, 0) <= tope_hub}
                    if raros:
                        vecinos = await repo_ev.buscar_por_tags(
                            list(raros),
                            [uuid.UUID(r.evento_id) for r in resultados],
                            limite=6,
                            rama=rama,
                        )
                        for m in vecinos:
                            compartidos = set(m.tags or []) & raros
                            fingerprint = (
                                len(compartidos) == 1 and len(next(iter(compartidos))) >= 6
                            )
                            if len(compartidos) < 2 and not fingerprint:
                                continue
                            asociados.append((m, 0.0))
                            tags_vecinos[m.id] = tuple(sorted(compartidos))
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
    # Vecinos hebbianos y por tag — llegan marcados como
    # asociados, nunca se confunden con resultados de la
    # consulta. Los de tag declaran los tags que los trajeron.
    for modelo, peso_e in asociados:
        ev = Evento.desde_modelo(modelo)
        via_tags = tags_vecinos.get(modelo.id)
        item: dict[str, Any] = {
            "evento": _evento_a_dict(ev),
            "via": "tags" if via_tags else "coactiva",
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
        if via_tags:
            item["tags_compartidos"] = list(via_tags)
        else:
            item["peso_efectivo"] = round(peso_e, 3)
        serializados.append(item)
    # Intención entidad: la ficha encabeza la respuesta — card,
    # grafo, backlinks, compromisos y span temporal en un bloque;
    # los resultados difusos quedan detrás como contexto.
    if intencion == IntencionConsulta.ENTIDAD and nombre_entidad:
        ficha = await _entidad(nombre_entidad, top_n, workspace)
        if ficha.get("encontrado"):
            serializados = [
                s for s in serializados if s.get("evento", {}).get("id") != ficha["card"]["id"]
            ]
            serializados.insert(0, {"entidad": ficha})
    # Veredicto de la op + clarificación estructurada (Neuro-7):
    # la respuesta declara su solidez (certeza, falsado, empates
    # de posición) y, solo cuando amerita, sugiere la pregunta
    # exacta que cierra la ambigüedad — conflicto rival o
    # evidencia totalmente ausente. Las lagunas de cobertura
    # jamás disparan: el usuario no puede aportar lo que nadie
    # capturó.
    n_hits = sum(1 for s in serializados if isinstance(s.get("evento"), dict) and "via" not in s)
    rival = verificacion["conflictos"][0] if verificacion.get("conflictos") else None
    clarif = sugerir_clarificacion(
        n_resultados=n_hits,
        conflicto_rival=rival,
        tema=nucleo or consulta,
    )
    if verificacion or clarif is not None:
        meta: dict[str, Any] = {}
        if verificacion:
            meta["verificacion"] = verificacion
        if clarif is not None:
            meta["clarificacion"] = {
                "razon": clarif.razon,
                "accion": clarif.accion,
                "pregunta": clarif.pregunta,
                "opciones": clarif.opciones,
            }
        serializados.append(meta)
    salida = recordatorios + serializados
    if max_tokens is not None and max_tokens > 0:
        salida = _recortar_por_tokens(salida, max_tokens)
    if ttl > 0:
        _RECALL_CACHE[clave] = (time.time() + ttl, salida)
        if len(_RECALL_CACHE) > _RECALL_CACHE_MAX:
            _RECALL_CACHE.popitem(last=False)
    return salida


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
            nombre,
            tipo=tipo,
            namespace=namespace,
            rama_origen=rama_origen,
            extra=metadata,
        )
        fuente.autor_id = ctx.usuario_id
        await session.commit()
        _invalidar_recall()
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
        _invalidar_recall()
    return ok


async def _eliminar_fuente(nombre: str, workspace: str | None = None) -> bool:
    """Borra el registro de una fuente (sus eventos quedan intactos)."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        ok = await FuenteRepository(session, ctx).eliminar(nombre)
        await session.commit()
        _invalidar_recall()
    return ok


async def _ramificar_fuente(nombre: str, rama: str, namespace: str) -> dict[str, Any] | None:
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
        _invalidar_recall()
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
        filas = await RelacionRepository(session, ctx).listar(min(max(limite, 1), 5000))
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
        raise ErrorValidacion("from_unix must be <= to_unix")
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session, ctx).listar_por_rango(
            desde_unix, hasta_unix, min(max(limite, 1), 4000), rama, repartir
        )
    return [_modelo_a_dict(e) for e in eventos]


def _titulo_de(modelo: EventoModel) -> str:
    """Primera línea del contenido sin el '#' de encabezado."""
    return (modelo.contenido or "").split("\n", 1)[0].lstrip("# ").strip()


async def _entidad(
    nombre: str,
    limite_menciones: int,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Compone la ficha de una entidad: página, vínculos y span temporal.

    La búsqueda por relevancia recupera documentos; la ficha responde
    «quién es X»: la página que lleva su nombre (lookup IDF de título),
    sus aristas en el grafo, sus backlinks (documentos que la
    mencionan), los compromisos abiertos donde aparece y su ventana
    temporal en la línea de universo — cuándo se escribió de ella por
    primera y última vez.
    """
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        candidatos = await repo.buscar_por_titulo(nombre, limite=6)
        primero, ultimo, total = await repo.span_menciones(nombre)
        span = {
            "primera_unix": primero,
            "ultima_unix": ultimo,
            "menciones_total": total,
        }
        if not candidatos:
            return {"encontrado": False, "nombre": nombre, "span": span}
        # Empate de IDF: con un nombre de un solo lexema todos los
        # títulos puntúan igual — la card es el título MÁS CORTO que
        # lleva el nombre («# Oscar Lopez» es una ficha; la primera
        # línea de un fact es su texto completo, ~150 chars).
        card, score_card = min(candidatos, key=lambda c: len(_titulo_de(c[0])))
        excluir = [e.id for e, _ in candidatos]
        vinculos = await RelacionRepository(session, ctx).vinculos_de(card.id)
        menciones = await repo.menciones_de(nombre, excluir, max(1, min(limite_menciones, 24)))
        compromisos = [
            c
            for c in await repo.listar_compromisos_abiertos("main")
            if nombre.lower() in (c.contenido or "").lower()
        ]
    return {
        "encontrado": True,
        "nombre": nombre,
        "card": _modelo_a_dict(card),
        "score_card": round(score_card, 4),
        "variantes": [
            {
                "id": str(e.id),
                "titulo": _titulo_de(e),
                "score": round(s, 4),
            }
            for e, s in candidatos[1:]
        ],
        "vinculos": [
            {
                "evento_id": str(otro_id),
                "titulo": titulo,
                "tipo": r.tipo,
                "peso": r.peso,
            }
            for r, otro_id, titulo in vinculos
        ],
        "menciones": [_modelo_a_dict(m) for m in menciones],
        "compromisos_abiertos": [_modelo_a_dict(c) for c in compromisos],
        "span": span,
    }


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
        raise ErrorValidacion(f"commitment_id is not a valid UUID: {compromiso_id!r}") from e
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        compromiso = await repo.obtener(cid)
        if compromiso is None:
            raise ErrorValidacion(f"commitment not found: {compromiso_id}")
        if compromiso.tipo != "compromiso":
            raise ErrorValidacion(
                f"event {compromiso_id} is kind '{compromiso.tipo}', " "not 'commitment'"
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
        _invalidar_recall()
    return {
        "compromiso_id": str(cid),
        "cierre_id": str(cierre_id),
        "estado": "cerrado",
    }


async def _olvidar(evento_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Borra un evento de la memoria — derecho al olvido.

    Borrado duro e irreversible: el evento y sus vínculos desaparecen
    de la línea de universo. Para correcciones con historia usa
    ``reemplaza_id`` en ``recordar_evento`` en su lugar.
    """
    ctx = await _ctx_escritura(workspace)
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(f"event_id is not a valid UUID: {evento_id!r}") from e
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
            raise PermisoDenegadoError("only the owner can delete other members' memories")
        vinculos = await RelacionRepository(session).eliminar_de(eid)
        ok = await repo.eliminar(eid)
        await session.commit()
        _invalidar_recall()
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
        "evento_cierre_id": (str(o.evento_cierre_id) if o.evento_cierre_id else None),
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
        raise ErrorValidacion("goal title cannot be empty")
    if dimension is not None and dimension not in (
        "cognitiva",
        "somatica",
        "conductual",
    ):
        raise ErrorValidacion(
            f"invalid dimension: {dimension!r} " "(cognitive|somatic|behavioral)"
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
        _invalidar_recall()
    return _objetivo_a_dict(objetivo)


async def _actualizar_objetivo(objetivo_id: str, cambios: dict[str, Any]) -> dict[str, Any]:
    """Aplica cambios parciales a un objetivo existente."""
    ctx = await _ctx_escritura()
    try:
        oid = uuid.UUID(str(objetivo_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(f"goal_id is not a valid UUID: {objetivo_id!r}") from e
    async with get_session(obtener_engine()) as session:
        objetivo = await ObjetivoRepository(session, ctx).actualizar(oid, cambios)
        if objetivo is None:
            raise ErrorValidacion(f"goal not found: {objetivo_id}")
        await session.commit()
        _invalidar_recall()
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
        raise ErrorValidacion(f"invalid closing status: {estado!r} (fulfilled|abandoned)")
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
            (await session.execute(select(func.count(EventoModel.id)).where(*vis))).scalar_one()
        )
    vigentes = [e for e in recientes if e.expira_en is None or e.expira_en > ahora]
    nucleo = sorted(vigentes, key=lambda e: (-e.amplitud, -e.refuerzos))[:n]
    calientes = sorted(vigentes, key=lambda e: (-e.refuerzos, -e.timestamp_unix))[:n]
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
                    f"[{parsear_censor(e.contexto)[0].upper()}] " f"{e.contenido}" for e in censores
                ),
            ),
            SeccionBootstrap(
                "loops",
                "Compromisos abiertos",
                tuple(f"{loop['contenido']} ({loop['dias_abierto']}d)" for loop in loops_dicts),
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
        _invalidar_recall()
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
@ingles
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
    tipo_memoria: (Literal["episodica", "semantica", "procedimental"] | None) = None,
    estado_emocional: float | None = None,
    parametrica: bool = False,
) -> dict[str, Any]:
    """Deprecated alias — use ``remember`` instead."""
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
@ingles
async def compromisos_abiertos(
    rama: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``open_commitments`` instead."""
    return await _open_loops(rama, workspace)


@mcp_server.tool()
@ingles
async def cerrar_compromiso(
    compromiso_id: str,
    resolucion: str,
    fuente: str = "agente",
) -> dict[str, Any]:
    """Deprecated alias — use ``close_commitment`` instead."""
    return await _cerrar_loop(compromiso_id, resolucion, fuente)


@mcp_server.tool()
@ingles
async def contexto_inicial(
    top_n: int = 8,
    presupuesto: int | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``bootstrap_context`` instead."""
    return await _contexto_inicial(top_n, presupuesto, workspace)


@mcp_server.tool()
@ingles
async def listar_objetivos(
    estado: str = "activo",
    rama: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_goals`` instead."""
    return await _objetivos(None if estado == "*" else estado, rama, workspace)


@mcp_server.tool()
@ingles
async def crear_objetivo(
    titulo: str,
    fecha_objetivo: int,
    dimension: str | None = None,
    descripcion: str | None = None,
    rama: str = "main",
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``create_goal`` instead."""
    return await _crear_objetivo(
        titulo, fecha_objetivo, dimension, descripcion, rama, scope, workspace
    )


@mcp_server.tool()
@ingles
async def cerrar_objetivo(
    objetivo_id: str,
    estado: str,
    resolucion: str | None = None,
    fuente: str = "agente",
) -> dict[str, Any]:
    """Deprecated alias — use ``close_goal`` instead."""
    return await _cumplir_objetivo(objetivo_id, estado, resolucion, fuente)


@mcp_server.tool()
@ingles
async def consultar_memoria(
    consulta: str,
    top_n: int = 10,
    rama: str = "main",
    workspace: str | None = None,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``recall`` instead."""
    return await _consultar(
        consulta,
        top_n,
        None if rama == "*" else rama,
        workspace,
        max_tokens,
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
        dimensiones=[str(i["evento"].get("dimension", "")) for i in ev_items],
        confianzas=[float(i.get("confianza", 0.0)) for i in ev_items],
        n_parametricas=sum(1 for i in ev_items if i["evento"].get("parametrica")),
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
@ingles
async def mapa_lagunas(
    consulta: str,
    top_n: int = 10,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``gap_map`` instead."""
    return await _mapa_lagunas(consulta, top_n, workspace)


@mcp_server.tool()
@ingles
async def obtener_linea_de_universo(
    desde_unix: int,
    hasta_unix: int,
    limite: int = 100,
    rama: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``timeline`` instead."""
    return await _linea(
        desde_unix,
        hasta_unix,
        limite,
        None if rama == "*" else rama,
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
        raise ErrorValidacion("from_unix must be <= to_unix")
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
@ingles
async def obtener_episodios(
    desde_unix: int,
    hasta_unix: int,
    gap_max_segundos: int = GAP_MAX_DEFECTO_SEGUNDOS,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_episodes`` instead."""
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
@ingles
async def proyectar_trayectoria(
    horizonte_dias: int = 7,
    dias_historial: int = 30,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``project_trajectory`` instead."""
    return await _proyectar(horizonte_dias, dias_historial, workspace)


@mcp_server.tool()
@ingles
async def olvidar_evento(evento_id: str) -> dict[str, Any]:
    """Deprecated alias — use ``forget_event`` instead."""
    return await _olvidar(evento_id)


@mcp_server.tool()
@ingles
async def listar_tags(
    limite: int = 100,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_tags`` instead."""
    return await _tags(min(max(limite, 1), 500), workspace)


@mcp_server.tool()
@ingles
async def listar_fuentes(workspace: str | None = None) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_sources`` instead."""
    return await _fuentes(workspace)


@mcp_server.tool()
@ingles
async def ramificar_fuente(
    nombre: str,
    rama: str,
    namespace: str = "",
) -> dict[str, Any] | None:
    """Deprecated alias — use ``branch_source`` instead."""
    return await _ramificar_fuente(nombre, rama, namespace)


@mcp_server.tool()
@ingles
async def listar_relaciones(
    limite: int = 500,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_relations`` instead."""
    return await _relaciones(limite, workspace)


@mcp_server.tool()
@ingles
async def entidad(
    nombre: str,
    limite_menciones: int = 8,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``entity`` instead."""
    return await _entidad(nombre, limite_menciones, workspace)


@mcp_server.tool()
@ingles
async def leer_preferencia(clave: str) -> dict[str, Any]:
    """Deprecated alias — use ``read_preference`` instead."""
    return await _preferencia_leer(clave)


@mcp_server.tool()
@ingles
async def guardar_preferencia(clave: str, valor: dict[str, Any]) -> dict[str, Any]:
    """Deprecated alias — use ``save_preference`` instead."""
    await _preferencia_guardar(clave, valor)
    return {"clave": clave, "guardado": True}


@mcp_server.tool()
@ingles
async def render_persona(
    filename: str | None = None,
    archivo: str | None = None,
) -> dict[str, Any]:
    """Renders the agent persona files (SOUL.md, USER.md, ...).

    USE WHEN: producing the current persona documents from the stored
    answer bank — for inspection or exporting identity config.

    Args:
        filename: Specific file to render (e.g. "SOUL.md"); None
            renders the whole catalog with per-file completeness.
        archivo: Deprecated Spanish alias of ``filename``.

    Returns:
        Dict with "hash", rendered "files", "missing" required keys
        per file and the "catalog" of required keys.
    """
    elegido = filename or archivo
    banco = await _preferencia_leer(CLAVE_BANCO)
    objetivos = [elegido] if elegido else list(ARCHIVOS)
    for a in objetivos:
        if a not in ARCHIVOS:
            raise ErrorValidacion(
                f"unknown persona file: {a!r} — " f"valid: {', '.join(ARCHIVOS)}"
            )
    faltan = {a: list(faltantes(a, banco)) for a in objetivos}
    faltan = {a: f for a, f in faltan.items() if f}
    contenidos = {a: render(a, banco) for a in objetivos if a not in faltan}
    return {
        "hash": hash_banco(banco),
        "archivos": contenidos,
        "faltantes": faltan,
        "catalogo": {a: list(claves) for a, claves in catalogo().items()},
    }


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
        raise PermisoDenegadoError("owner role required in the workspace")
    return ctx


async def _miembro_por_handle(ctx: Contexto, handle: str) -> uuid.UUID:
    """Resuelve un handle de usuario a su UUID dentro del workspace."""
    engine = obtener_engine()
    miembros = await idn.listar_miembros(engine, ctx.workspace_id)
    for m in miembros:
        if m.handle == handle:
            return m.usuario_id
    raise ErrorValidacion(f"handle is not a workspace member: {handle!r}")


@mcp_server.tool()
@ingles
async def listar_workspaces() -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_workspaces`` instead."""
    engine = obtener_engine()
    ctx = await _ctx()
    return [
        {"id": str(w.id), "slug": w.slug, "nombre": w.nombre, "rol": w.rol}
        for w in await idn.listar_workspaces_con_rol(engine, ctx.usuario_id)
    ]


@mcp_server.tool()
@ingles
async def crear_workspace(slug: str, nombre: str) -> dict[str, Any]:
    """Deprecated alias — use ``create_workspace`` instead."""
    ctx = await _ctx()
    if ctx.usuario_id is None:
        raise PermisoDenegadoError("no user identity (development mode)")
    ws = await idn.crear_workspace(obtener_engine(), slug, nombre, ctx.usuario_id)
    return {
        "id": str(ws.id),
        "slug": ws.slug,
        "nombre": ws.nombre,
        "rol": "owner",
    }


@mcp_server.tool()
@ingles
async def listar_miembros(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_members`` instead."""
    ctx = await _ctx(workspace)
    return [
        {
            "usuario_id": str(m.usuario_id),
            "handle": m.handle,
            "nombre": m.nombre,
            "rol": m.rol,
        }
        for m in await idn.listar_miembros(obtener_engine(), ctx.workspace_id)
    ]


@mcp_server.tool()
@ingles
async def invitar_miembro(
    rol: str = "member",
    dias: int = 7,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``invite_member`` instead."""
    ctx = await _ctx_owner_en(workspace)
    token = await idn.crear_invitacion(
        obtener_engine(),
        ctx.workspace_id,
        rol,
        dias=dias,
        creada_por=ctx.usuario_id,
    )
    return {
        "token": token,
        "expira_dias": dias,
        "workspace_id": str(ctx.workspace_id),
        "rol": rol,
    }


@mcp_server.tool()
@ingles
async def cambiar_rol_miembro(
    handle: str,
    rol: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``change_member_role`` instead."""
    ctx = await _ctx_owner_en(workspace)
    uid = await _miembro_por_handle(ctx, handle)
    await idn.cambiar_rol(obtener_engine(), ctx.workspace_id, uid, rol)
    return {"handle": handle, "rol": rol}


@mcp_server.tool()
@ingles
async def quitar_miembro(
    handle: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``remove_member`` instead."""
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
        raise ErrorValidacion("intention cannot be empty")
    if (en is None) == (cuando is None):
        raise ErrorValidacion("declare exactly one trigger: 'at' (time) or 'when' (event)")
    if not 0.0 < umbral <= 1.0:
        raise ErrorValidacion("threshold must be in (0, 1]")
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    if en is not None:
        try:
            ts = parsear_ttl(en, ahora)
        except ValueError as e:
            raise ErrorValidacion(str(e)) from e
        if ts is None or ts <= ahora:
            raise ErrorValidacion("'at' must resolve to a future instant")
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
        _invalidar_recall()
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
        filas = await IntencionProspectivaRepository(session, ctx).listar(ejecutadas=ejecutadas)
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


async def _cancelar_intencion(intencion_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Cancela una intención prospectiva pendiente."""
    ctx = await _ctx_escritura(workspace)
    try:
        iid = uuid.UUID(str(intencion_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(f"intention_id is not a valid UUID: {intencion_id!r}") from e
    async with get_session(obtener_engine()) as session:
        ok = await IntencionProspectivaRepository(session, ctx).cancelar(iid, int(time.time()))
        await session.commit()
        _invalidar_recall()
    if not ok:
        raise ErrorValidacion(f"intention does not exist or is already resolved: {intencion_id}")
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
        tipo_memoria=(TipoMemoria(modelo.tipo_memoria) if modelo.tipo_memoria else None),
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
                await relaciones.vincular(evento_gist.id, uuid.UUID(miembro), "gist")
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
            if await relaciones.vincular(uuid.UUID(id_a), uuid.UUID(id_b), "rem"):
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
        _invalidar_recall()
    # Ritmo circadiano: sugiere la ventana más quieta — recomendación,
    # nunca un scheduler (el sueño de Nous lo decide el operador).
    ventana = ventana_optima_sueno([e.timestamp_unix for e in eventos])
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
        _invalidar_recall()
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


async def _restaurar_evento(evento_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Restaura una memoria archivada — deshace el olvido."""
    ctx = await _ctx_escritura(workspace)
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(f"event_id is not a valid UUID: {evento_id!r}") from e
    async with get_session(obtener_engine()) as session:
        ok = await EventoRepository(session, ctx).restaurar(eid)
        await session.commit()
        _invalidar_recall()
    if not ok:
        raise ErrorValidacion(f"event does not exist or is not archived: {evento_id}")
    return {"id": str(eid), "restaurado": True}


async def _archivar_evento(
    evento_id: str,
    razon: str = "manual",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Archiva una memoria a petición — el olvido manual, reversible."""
    ctx = await _ctx_escritura(workspace)
    try:
        eid = uuid.UUID(str(evento_id))
    except (ValueError, AttributeError) as e:
        raise ErrorValidacion(f"event_id is not a valid UUID: {evento_id!r}") from e
    async with get_session(obtener_engine()) as session:
        ok = await EventoRepository(session, ctx).archivar(
            eid, int(time.time()), razon[:200] or "manual"
        )
        await session.commit()
        _invalidar_recall()
    if not ok:
        raise ErrorValidacion(f"event does not exist or is already archived: {evento_id}")
    return {"id": str(eid), "archivado": True, "razon_archivo": razon}


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
        raise ErrorValidacion(f"event_id is not a valid UUID: {evento_id!r}") from e
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
        _invalidar_recall()
    if not ok:
        raise ErrorValidacion(f"event not found in your visibility: {evento_id}")
    return {"id": str(eid), "curado": curado}


@mcp_server.tool()
@ingles
async def crear_intencion(
    intencion: str,
    en: str | int | None = None,
    cuando: str | None = None,
    umbral: float = 0.6,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``create_intention`` instead."""
    return await _crear_intencion(intencion, en, cuando, umbral, workspace)


@mcp_server.tool()
@ingles
async def listar_intenciones(
    ejecutadas: bool | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_intentions`` instead."""
    return await _listar_intenciones(ejecutadas, workspace)


@mcp_server.tool()
@ingles
async def cancelar_intencion(
    intencion_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``cancel_intention`` instead."""
    return await _cancelar_intencion(intencion_id, workspace)


@mcp_server.tool()
@ingles
async def consolidar_memoria(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``consolidate_memory`` instead."""
    return await _consolidar(workspace)


@mcp_server.tool()
@ingles
async def listar_reportes_consolidacion(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_consolidation_reports`` instead."""
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
@ingles
async def minar_scripts(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``mine_scripts`` instead."""
    return await _minar_scripts(workspace)


@mcp_server.tool()
@ingles
async def listar_scripts(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_scripts`` instead."""
    return await _listar_scripts(workspace)


@mcp_server.tool()
@ingles
async def listar_archivados(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_archived`` instead."""
    return await _listar_archivados(workspace)


@mcp_server.tool()
@ingles
async def restaurar_evento(
    evento_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``restore_event`` instead."""
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
        lineas += [f"- [{e.dimension}] {e.contenido[:120]}" for e in recientes]
    if objetivos:
        lineas.append("Metas activas:")
        lineas += [f"- {o.titulo}" for o in objetivos[:8]]
    if loops:
        lineas.append("Loops abiertos:")
        lineas += [f"- {e.contenido[:100]} (con {e.con_quien or '?'})" for e in loops[:6]]
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
        raise ErrorValidacion("at least 2 distinct options are required")
    ctx = await _ctx(workspace)
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session, ctx).listar_por_rango(
            ahora - 30 * 86400, ahora, limite=40, rama="main"
        )
        objetivos = await ObjetivoRepository(session, ctx).listar(estado="activo")
        loops = await EventoRepository(session, ctx).listar_compromisos_abiertos("main", ahora)
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
                "no System One provider (NOUS_DECISION_PROVIDER): "
                "4D context assembled, calibration unavailable"
            ),
        }
    from src.core.decision.consejero import recomendar

    rec = await recomendar(provider, state, opciones)
    if rec is None:
        return {
            "disponible": False,
            "dilema": dilema,
            "opciones": opciones,
            "mensaje": "the provider returned no useful answer",
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
@ingles
async def consultar_decision(
    dilema: str,
    opciones: list[str] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``ask_decision`` instead."""
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
                f"working memory full ({CAPACIDAD_MAX} drafts) — "
                "rehearse one to promote it or discard"
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
        _invalidar_recall()
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
            raise ErrorValidacion(f"draft does not exist or is not visible: {borrador_id}")
        b = await repo.obtener(uuid.UUID(str(borrador_id)))
        if b is None or esta_expirado(b.creado_en, ahora, b.expira_en - b.creado_en):
            raise ErrorValidacion("draft already expired — it was lost")
        respuesta: dict[str, Any] = {"id": borrador_id, "ensayos": ensayos}
        if debe_promover(ensayos):
            contenido, dimension = b.contenido, b.dimension
            await repo.descartar(b.id)
            await session.commit()
            _invalidar_recall()
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
            _invalidar_recall()
            respuesta["promovido"] = False
    return respuesta


async def _descartar_borrador(
    borrador_id: str,
    workspace: str | None,
) -> dict[str, Any]:
    """Saca el borrador del buffer sin promoverlo."""
    ctx = await _ctx_escritura(workspace)
    async with get_session(obtener_engine()) as session:
        ok = await BorradorRepository(session, ctx).descartar(uuid.UUID(str(borrador_id)))
        await session.commit()
        _invalidar_recall()
    return {"id": borrador_id, "descartado": ok}


@mcp_server.tool()
@ingles
async def recordar_borrador(
    contenido: str,
    dimension: Literal["cognitiva", "somatica", "conductual"] = "cognitiva",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``save_draft`` instead."""
    return await _recordar_borrador(contenido, dimension, workspace)


@mcp_server.tool()
@ingles
async def listar_borrador(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_drafts`` instead."""
    return await _listar_borrador(workspace)


@mcp_server.tool()
@ingles
async def ensayar_borrador(
    borrador_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``rehearse_draft`` instead."""
    return await _ensayar_borrador(borrador_id, workspace)


@mcp_server.tool()
@ingles
async def descartar_borrador(
    borrador_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``discard_draft`` instead."""
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
        raise ErrorValidacion(f"event_id is not a valid UUID: {evento_id!r}") from e
    async with get_session(obtener_engine()) as session:
        repo = EventoRepository(session, ctx)
        if await repo.obtener(eid) is None:
            raise ErrorValidacion(f"event does not exist: {evento_id}")
        await repo.marcar_olvido_dirigido(eid, olvidar)
        await session.commit()
        _invalidar_recall()
    return {"id": str(eid), "olvido_dirigido": olvidar}


@mcp_server.tool()
@ingles
async def olvidar_gradual(
    evento_id: str,
    olvidar: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``forget_gradually`` instead."""
    return await _olvidar_gradual(evento_id, olvidar, workspace)


async def _repasos_pendientes(
    limite: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista las memorias cuyo repaso óptimo (SRS) ya venció."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        pendientes = await EventoRepository(session, ctx).pendientes_revision(
            int(time.time()), limite=limite
        )
        return [
            {
                "id": str(m.id),
                "contenido": m.contenido[:200],
                "timestamp_unix": m.timestamp_unix,
                "proxima_revision": m.proxima_revision,
                "dias_atraso": round(
                    (int(time.time()) - (m.proxima_revision or 0)) / 86400.0,
                    2,
                ),
                "dimension": m.dimension,
                "tipo_memoria": m.tipo_memoria,
            }
            for m in pendientes
        ]


@mcp_server.tool()
@ingles
async def repasos_pendientes(
    limite: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``pending_reviews`` instead."""
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
        raise ErrorValidacion(f"intention_id is not a valid UUID: {intencion_id!r}") from e
    hasta = int(time.time()) + max(0, segundos)
    async with get_session(obtener_engine()) as session:
        repo = IntencionProspectivaRepository(session, ctx)
        if not await repo.aplazar(iid, hasta):
            raise ErrorValidacion(f"intention is not pending or does not exist: {intencion_id}")
        await session.commit()
        _invalidar_recall()
    return {"id": str(iid), "aplazada_hasta": hasta, "segundos": segundos}


@mcp_server.tool()
@ingles
async def aplazar_intencion(
    intencion_id: str,
    segundos: int = 3600,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``snooze_intention`` instead."""
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
            raise ErrorValidacion(f"source does not exist: {nombre}")
        liberados = 0
        if confiable:
            # Aprobar la fuente saca del aislamiento a sus eventos —
            # ya indexados, ahora también evocables.
            liberados = await EventoRepository(session, ctx).aprobar_cuarentena_fuente(nombre)
        await session.commit()
        _invalidar_recall()
    return {"fuente": nombre, "confiable": confiable, "liberados": liberados}


@mcp_server.tool()
@ingles
async def confiar_fuente(
    nombre: str,
    confiable: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``trust_source`` instead."""
    return await _confiar_fuente(nombre, confiable, workspace)


async def _listar_cuarentena(
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista los eventos retenidos en cuarentena del workspace."""
    ctx = await _ctx(workspace)
    async with get_session(obtener_engine()) as session:
        cuarentena = await EventoRepository(session, ctx).listar_cuarentena(limite=limite)
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
@ingles
async def listar_cuarentena(
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_quarantine`` instead."""
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
        _invalidar_recall()
    pack = await _contexto_inicial(top_n=8, presupuesto=presupuesto, workspace=workspace)
    pack["sesion_id"] = str(sesion.id)
    pack["inicio"] = ahora
    if vencidas:
        pack["recordatorios"] = [{"id": v["id"], "intencion": v["intencion"]} for v in vencidas]
    return pack


@mcp_server.tool()
@ingles
async def iniciar_sesion(
    agente: str | None = None,
    presupuesto: int = 4000,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``start_session`` instead."""
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
                raise ErrorValidacion(f"session_id is not a valid UUID: {sesion_id!r}") from e
            sesion = await repo_s.obtener(sid)
        else:
            sesion = await repo_s.ultima_abierta()
        if sesion is None or sesion.fin is not None:
            raise ErrorValidacion(
                "no open session to close" + (f" ({sesion_id})" if sesion_id else "")
            )
        repo_e = EventoRepository(session, ctx)
        escritos = await repo_e.listar_por_rango(sesion.inicio, ahora, limite=40)
        loops = await repo_e.listar_compromisos_abiertos("main", ahora)
        curados = await repo_e.listar_curados(limite=16)
        censores = await repo_e.listar_censores()
        handoff = generar_handoff(
            agente=sesion.agente,
            inicio=sesion.inicio,
            fin=ahora,
            resumen=resumen,
            escritos=[{"contenido": e.contenido, "tipo": e.tipo} for e in escritos],
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
        _invalidar_recall()
    return {
        "id": str(sesion.id),
        "fin": ahora,
        "eventos": len(escritos),
        "handoff": handoff,
    }


@mcp_server.tool()
@ingles
async def cerrar_sesion(
    sesion_id: str | None = None,
    resumen: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``end_session`` instead."""
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
                raise ErrorValidacion(f"session_id is not a valid UUID: {sesion_id!r}") from e
            sesion = await repo_s.obtener(sid)
        else:
            sesion = await repo_s.ultima_cerrada()
        if sesion is None or not sesion.handoff:
            raise ErrorValidacion("no handoff available")
    return {
        "id": str(sesion.id),
        "agente": sesion.agente,
        "inicio": sesion.inicio,
        "fin": sesion.fin,
        "eventos": sesion.eventos,
        "handoff": sesion.handoff,
    }


@mcp_server.tool()
@ingles
async def obtener_handoff(
    sesion_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``get_handoff`` instead."""
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
@ingles
async def listar_sesiones(
    limite: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_sessions`` instead."""
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
        raise ErrorValidacion("severity must be 'block' or 'warn'")
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
@ingles
async def crear_censor(
    regla: str,
    severidad: str = "advertir",
    dominio: str | None = None,
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``create_censor`` instead."""
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
@ingles
async def listar_censores(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_censors`` instead."""
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
            "motivo": "no active censors — clear path",
        }
    try:
        vector = await _obtener_embeddings().generar(accion)
    except Exception:
        vector = None
    coincidencias: list[CensorCoincidente] = []
    for c in censores:
        if vector is not None and c.embedding is not None:
            sim = _coseno(vector, list(c.embedding))
        else:
            # Sin embedding disponible cae a cobertura léxica: ¿qué
            # fracción de los términos de la acción aparece en la
            # regla? — parafrasea mejor que la contención literal.
            terminos = {t for t in accion.lower().split() if len(t) > 2}
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
@ingles
async def verificar_accion(
    accion: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``verify_action`` instead."""
    return await _verificar_accion(accion, workspace)


async def _registrar_prediccion(
    afirmacion: str,
    confianza: float,
    evento_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Declara una confianza ANTES del resultado — materia del Brier."""
    if not 0.0 <= confianza <= 1.0:
        raise ErrorValidacion("confidence must be between 0.0 and 1.0")
    ctx = await _ctx_escritura(workspace)
    ahora = int(time.time())
    eid: uuid.UUID | None = None
    if evento_id:
        try:
            eid = uuid.UUID(str(evento_id))
        except (ValueError, AttributeError) as e:
            raise ErrorValidacion(f"event_id is not a valid UUID: {evento_id!r}") from e
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
        _invalidar_recall()
    return {
        "id": str(prediccion.id),
        "afirmacion": afirmacion,
        "confianza": confianza,
        "creado_en": ahora,
    }


@mcp_server.tool()
@ingles
async def registrar_prediccion(
    afirmacion: str,
    confianza: float,
    evento_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``register_prediction`` instead."""
    return await _registrar_prediccion(afirmacion, confianza, evento_id, workspace)


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
        raise ErrorValidacion(f"prediction_id is not a valid UUID: {prediccion_id!r}") from e
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        ok = await PrediccionRepository(session, ctx).resolver(pid, acierto, ahora)
        await session.commit()
        _invalidar_recall()
    if not ok:
        raise ErrorValidacion(
            "prediction does not exist or is already resolved: " f"{prediccion_id}"
        )
    return {"id": str(pid), "acierto": acierto, "resuelto_en": ahora}


@mcp_server.tool()
@ingles
async def resolver_prediccion(
    prediccion_id: str,
    acierto: bool,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``resolve_prediction`` instead."""
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
@ingles
async def listar_predicciones(
    pendientes: bool | None = None,
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``list_predictions`` instead."""
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
        [PrediccionResuelta(confianza=p.confianza, acierto=bool(p.acierto)) for p in resueltas],
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
@ingles
async def reporte_calibracion(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``calibration_report`` instead."""
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
@ingles
async def pendientes_aprobacion(
    limite: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Deprecated alias — use ``pending_approvals`` instead."""
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
        raise ErrorValidacion(f"event_id is not a valid UUID: {evento_id!r}") from e
    async with get_session(obtener_engine()) as session:
        aprobados = await EventoRepository(session, ctx).aprobar_staged([eid])
        await session.commit()
        _invalidar_recall()
    if not aprobados:
        raise ErrorValidacion(f"no staged proposal with that id: {evento_id}")
    return {"id": str(eid), "aprobado": True}


@mcp_server.tool()
@ingles
async def aprobar_evento(
    evento_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``approve_event`` instead."""
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
        raise ErrorValidacion(f"event_id is not a valid UUID: {evento_id!r}") from e
    async with get_session(obtener_engine()) as session:
        rechazados = await EventoRepository(session, ctx).rechazar_staged([eid])
        await session.commit()
        _invalidar_recall()
    if not rechazados:
        raise ErrorValidacion(f"no staged proposal with that id: {evento_id}")
    return {"id": str(eid), "rechazado": True}


@mcp_server.tool()
@ingles
async def rechazar_evento(
    evento_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``reject_event`` instead."""
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
        _invalidar_recall()
    return {"aprobados": aprobados}


@mcp_server.tool()
@ingles
async def aprobar_pendientes(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Deprecated alias — use ``approve_all_pending`` instead."""
    return await _aprobar_pendientes(workspace)
