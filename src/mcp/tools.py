"""Tools MCP de dominio de Nous: memoria persistente 4D.

Cada tool declara explícitamente cuándo usarla y cuándo NO, para que
el modelo que la invoque no confunda sus responsabilidades.
"""

import time
from typing import Any, Literal

from src.config import get_settings
from src.core.busqueda import BusquedaHibrida4D
from src.core.clasificador import ClasificadorDimensiones
from src.core.embeddings import GeneradorEmbeddings
from src.core.exceptions import ErrorValidacion
from src.core.modelos import Evento, ResultadoBusqueda, Trayectoria
from src.core.reranker import ReordenadorLocal
from src.core.temporal import detectar_trayectorias, proyectar
from src.mcp.server import mcp_server
from src.storage.database import get_session, obtener_engine
from src.storage.models import EventoModel, ProyeccionModel
from src.storage.repositories import EventoRepository, ProyeccionRepository

_embeddings: GeneradorEmbeddings | None = None
_busqueda: BusquedaHibrida4D | None = None
_clasificador: ClasificadorDimensiones | None = None
_reordenador: ReordenadorLocal | None = None


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
        _reordenador = (
            ReordenadorLocal() if settings.reranker_habilitado else None
        )
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


async def _recordar(
    contenido: str,
    dimension: Literal["cognitiva", "somatica", "conductual"] | None,
    fuente: str,
    amplitud: float,
    frecuencia: float | None,
    timestamp_unix: int | None,
) -> dict[str, Any]:
    """Persiste un evento: valida, clasifica si hace falta, embebe y guarda."""
    if not 0.0 <= amplitud <= 1.0:
        raise ErrorValidacion("amplitud debe estar entre 0.0 y 1.0")
    if frecuencia is not None and frecuencia <= 0.0:
        raise ErrorValidacion("frecuencia debe ser positiva (Hz)")
    respuesta: dict[str, Any] = {}
    if dimension is None:
        resultado = await _obtener_clasificador().clasificar(contenido)
        dimension = resultado.dimension.value
        respuesta["confianza_clasificacion"] = resultado.confianza
        respuesta["dimension_dudosa"] = resultado.dudoso
    ts = timestamp_unix if timestamp_unix is not None else int(time.time())
    vector = await _obtener_embeddings().generar(contenido)
    evento = EventoModel(
        timestamp_unix=ts,
        dimension=dimension,
        fuente=fuente,
        contenido=contenido,
        embedding=vector,
        amplitud=amplitud,
        frecuencia=frecuencia,
    )
    async with get_session(obtener_engine()) as session:
        await EventoRepository(session).crear(evento)
        await session.commit()
    respuesta.update(
        {"id": str(evento.id), "timestamp_unix": ts, "dimension": dimension}
    )
    return respuesta


async def _consultar(consulta: str, top_n: int) -> list[dict[str, Any]]:
    """Ejecuta la búsqueda híbrida 4D y serializa los resultados."""
    async with get_session(obtener_engine()) as session:
        resultados = await _obtener_busqueda().buscar(
            session, consulta, top_n=max(1, min(top_n, 50))
        )
        await session.commit()  # persiste los refuerzos de consolidación
    return [_resultado_a_dict(r) for r in resultados]


async def _linea(desde_unix: int, hasta_unix: int, limite: int) -> list[dict[str, Any]]:
    """Devuelve los eventos del rango temporal en orden cronológico."""
    if desde_unix > hasta_unix:
        raise ErrorValidacion("desde_unix debe ser <= hasta_unix")
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session).listar_por_rango(
            desde_unix, hasta_unix, min(max(limite, 1), 500)
        )
    return [_modelo_a_dict(e) for e in eventos]


async def _proyectar(horizonte_dias: int, dias_historial: int) -> dict[str, Any]:
    """Detecta trayectorias, proyecta la dimensión futura y la persiste."""
    ahora = int(time.time())
    desde = ahora - min(max(dias_historial, 1), 365) * 86_400
    horizonte = min(max(horizonte_dias, 1), 90)
    async with get_session(obtener_engine()) as session:
        modelos = await EventoRepository(session).listar_por_rango(
            desde, ahora, 500
        )
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
) -> dict[str, Any]:
    """Guarda un evento nuevo en la memoria persistente 4D del usuario.

    USAR CUANDO: haya que registrar un pensamiento, decisión, emoción,
    estado corporal o acción para consultarlo en el futuro.
    NO usar para buscar memorias existentes: eso es consultar_memoria.
    NO usar para listar un rango de fechas: obtener_linea_de_universo.

    El contenido se vectoriza localmente (nada sale de la máquina) y
    queda indexado para búsqueda semántica y textual. Si no indicas
    dimension, se clasifica automáticamente por semejanza semántica.

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

    Returns:
        Dict con "id", "timestamp_unix", "dimension" usada y
        "confianza_clasificacion" si se clasificó automáticamente.
    """
    return await _recordar(
        contenido, dimension, fuente, amplitud, frecuencia, timestamp_unix
    )


@mcp_server.tool()
async def consultar_memoria(
    consulta: str,
    top_n: int = 10,
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

    Returns:
        Resultados ordenados por relevancia; cada uno trae el "evento"
        completo, sus scores y "contexto_temporal": los eventos que
        ocurrieron justo antes y después en la línea de tiempo.
    """
    return await _consultar(consulta, top_n)


@mcp_server.tool()
async def obtener_linea_de_universo(
    desde_unix: int,
    hasta_unix: int,
    limite: int = 100,
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

    Returns:
        Eventos {"id","timestamp_unix","dimension","fuente","contenido",
        "amplitud","frecuencia"} ordenados cronológicamente.
    """
    return await _linea(desde_unix, hasta_unix, limite)


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
