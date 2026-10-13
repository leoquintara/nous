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
from src.core.modelos import Evento, ResultadoBusqueda
from src.core.reranker import ReordenadorLocal
from src.mcp.server import mcp_server
from src.storage.database import get_session, obtener_engine
from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository

_embeddings: GeneradorEmbeddings | None = None
_busqueda: BusquedaHibrida4D | None = None
_clasificador: ClasificadorDimensiones | None = None


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
    global _busqueda
    if _busqueda is None:
        settings = get_settings()
        reordenador = (
            ReordenadorLocal() if settings.reranker_habilitado else None
        )
        _busqueda = BusquedaHibrida4D(
            _obtener_embeddings(),
            reordenador=reordenador,
            vida_media_dias=settings.recencia_vida_media_dias,
        )
    return _busqueda


def _modelo_a_dict(modelo: EventoModel) -> dict[str, Any]:
    """Serializa un EventoModel ORM a dict JSON-compatible."""
    return {
        "id": str(modelo.id),
        "timestamp_unix": modelo.timestamp_unix,
        "dimension": modelo.dimension,
        "fuente": modelo.fuente,
        "contenido": modelo.contenido,
        "amplitud": modelo.amplitud,
    }


def _evento_a_dict(evento: Evento) -> dict[str, Any]:
    """Serializa el dataclass de dominio a dict JSON-compatible."""
    return {
        "id": evento.id,
        "timestamp_unix": evento.timestamp_unix,
        "dimension": str(evento.dimension),
        "contenido": evento.contenido,
        "amplitud": evento.amplitud,
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


@mcp_server.tool()
async def recordar_evento(
    contenido: str,
    dimension: Literal["cognitiva", "somatica", "conductual"] | None = None,
    fuente: str = "agente",
    amplitud: float = 0.5,
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
        timestamp_unix: Unix epoch en SEGUNDOS. Default: ahora.

    Returns:
        Dict con "id", "timestamp_unix", "dimension" usada y
        "confianza_clasificacion" si se clasificó automáticamente.
    """
    if not 0.0 <= amplitud <= 1.0:
        raise ValueError("amplitud debe estar entre 0.0 y 1.0")
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
    )
    async with get_session(obtener_engine()) as session:
        await EventoRepository(session).crear(evento)
        await session.commit()
    respuesta.update(
        {"id": str(evento.id), "timestamp_unix": ts, "dimension": dimension}
    )
    return respuesta


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
    async with get_session(obtener_engine()) as session:
        resultados = await _obtener_busqueda().buscar(
            session, consulta, top_n=max(1, min(top_n, 50))
        )
    return [_resultado_a_dict(r) for r in resultados]


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
        "amplitud"} ordenados cronológicamente.
    """
    if desde_unix > hasta_unix:
        raise ValueError("desde_unix debe ser <= hasta_unix")
    async with get_session(obtener_engine()) as session:
        eventos = await EventoRepository(session).listar_por_rango(
            desde_unix, hasta_unix, min(max(limite, 1), 500)
        )
    return [_modelo_a_dict(e) for e in eventos]
