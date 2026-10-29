"""Nous MCP resources: read-only views over 4D memory.

Resources do not modify state: they expose aggregated summaries,
detected patterns and the current projection. Unlike tools (agent
actions), a resource is context the client can attach to its prompt.

Available URIs (canonical English):

- ``memory://summary/daily`` — last-24-hours aggregate.
- ``memory://summary/weekly`` — last-7-days aggregate.
- ``memory://patterns/flow`` — dimensional streaks of the last 30 days.
- ``memory://projection/current`` — latest persisted projection.

The ``memoria://`` URIs remain registered as deprecated aliases —
same payloads, legacy addresses.
"""

import json
import time
from statistics import fmean
from typing import Any

from src.core.modelos import Dimension, Evento, Trayectoria
from src.core.temporal import SEGUNDOS_POR_DIA, detectar_trayectorias
from src.mcp.contract import traducir_payload
from src.mcp.server import mcp_server
from src.storage.database import get_session, obtener_engine
from src.storage.repositories import EventoRepository, ProyeccionRepository

DIAS_RESUMEN: dict[str, int] = {"daily": 1, "weekly": 7}
_PERIODOS_ES = {"diario": "daily", "semanal": "weekly"}
DIAS_PATRONES = 30
MAX_EVENTOS_RESUMEN = 500
TOP_EVENTOS_DESTACADOS = 5


@mcp_server.resource("memory://summary/{period}")
async def summary(period: str) -> str:
    """Summarizes recent universe-line activity (read-only).

    Context resource: the agent reads it to get situated, not to act.
    To search a concrete topic use the ``recall`` tool; to record
    something, ``remember``.

    Args:
        period: ``daily`` (24 h) or ``weekly`` (7 days).

    Returns:
        JSON with the period, total events, per-dimension count and
        mean amplitude, and the highest-amplitude events of the period.
    """
    if period not in DIAS_RESUMEN:
        return json.dumps(
            {
                "error": f"Period '{period}' not supported",
                "valid_periods": sorted(DIAS_RESUMEN),
            },
            ensure_ascii=False,
        )
    eventos = await _eventos_recientes(DIAS_RESUMEN[period])
    return json.dumps(
        traducir_payload(_construir_resumen(period, eventos)),
        ensure_ascii=False,
        default=str,
    )


@mcp_server.resource("memory://patterns/flow")
async def flow_patterns() -> str:
    """Exposes recent dimensional streaks/trajectories (read-only).

    Context resource: describes patterns already detected over the
    last 30 days. To extrapolate futures use the
    ``project_trajectory`` tool.

    Returns:
        JSON with the detected trajectories (kind, temporal range,
        mean amplitude, confidence and whether still active).
    """
    eventos = await _eventos_recientes(DIAS_PATRONES)
    trayectorias = detectar_trayectorias(eventos)
    salida = {
        "ventana_dias": DIAS_PATRONES,
        "eventos_analizados": len(eventos),
        "trayectorias": [_trayectoria_a_dict(t) for t in trayectorias],
    }
    return json.dumps(traducir_payload(salida), ensure_ascii=False)


@mcp_server.resource("memory://projection/current")
async def current_projection() -> str:
    """Exposes the latest persisted projection (read-only).

    Context resource: reflects what the ``project_trajectory`` tool
    last computed; it does not recalculate. When nothing was ever
    projected, ``status`` says so.

    Returns:
        JSON with horizon, per-dimension distribution, probability
        and computation date, or ``status: no_projections``.
    """
    async with get_session(obtener_engine()) as session:
        proyeccion = await ProyeccionRepository(session).ultima()
    if proyeccion is None:
        return json.dumps(
            {
                "status": "no_projections",
                "suggestion": "Invoke the project_trajectory tool first",
            },
            ensure_ascii=False,
        )
    salida = {
        "id": str(proyeccion.id),
        "creado_en": proyeccion.creado_en.isoformat(),
        "horizonte_dias": proyeccion.horizonte_dias,
        "variable_objetivo": proyeccion.variable_objetivo,
        "probabilidad": proyeccion.probabilidad,
        "vector_proyectado": proyeccion.vector_proyectado,
        "rama_decision": proyeccion.rama_decision,
        "eventos_base": len(proyeccion.basada_en_eventos),
        "aviso": "Probabilistic extrapolation, not a certain prediction",
    }
    return json.dumps(traducir_payload(salida), ensure_ascii=False)


# ---------------------------------------------------------------------
# Deprecated Spanish aliases — same payloads, legacy URIs.
# ---------------------------------------------------------------------


@mcp_server.resource("memoria://resumen/{periodo}")
async def resumen(periodo: str) -> str:
    """Deprecated alias — use ``memory://summary/{period}``."""
    salida: str = await summary(_PERIODOS_ES.get(periodo, periodo))
    return salida


@mcp_server.resource("memoria://patrones/flow")
async def patrones_flow() -> str:
    """Deprecated alias — use ``memory://patterns/flow``."""
    salida: str = await flow_patterns()
    return salida


@mcp_server.resource("memoria://proyeccion/actual")
async def proyeccion_actual() -> str:
    """Deprecated alias — use ``memory://projection/current``."""
    salida: str = await current_projection()
    return salida


# ---------------------------------------------------------------------
# Helpers internos (payload en español — se traduce en la frontera).
# ---------------------------------------------------------------------


async def _eventos_recientes(dias: int) -> list[Evento]:
    """Carga los eventos de los últimos ``dias`` días como dataclasses."""
    ahora = int(time.time())
    async with get_session(obtener_engine()) as session:
        modelos = await EventoRepository(session).listar_por_rango(
            desde=ahora - dias * SEGUNDOS_POR_DIA,
            hasta=ahora,
            limite=MAX_EVENTOS_RESUMEN,
        )
    return [Evento.desde_modelo(m) for m in modelos]


def _construir_resumen(periodo: str, eventos: list[Evento]) -> dict[str, Any]:
    """Agrega conteos, amplitud media y destacados por dimensión."""
    por_dimension: dict[str, dict[str, Any]] = {}
    for dimension in Dimension:
        grupo = [e for e in eventos if e.dimension == dimension]
        por_dimension[dimension.value] = {
            "total": len(grupo),
            "amplitud_media": (round(fmean(e.amplitud for e in grupo), 3) if grupo else None),
        }
    destacados = sorted(eventos, key=lambda e: e.amplitud, reverse=True)[:TOP_EVENTOS_DESTACADOS]
    return {
        "periodo": periodo,
        "generado_en": int(time.time()),
        "total_eventos": len(eventos),
        "por_dimension": por_dimension,
        "eventos_destacados": [_evento_breve(e) for e in destacados],
    }


def _evento_breve(evento: Evento) -> dict[str, Any]:
    """Serializa un evento de forma compacta para el resumen."""
    return {
        "id": evento.id,
        "timestamp_unix": evento.timestamp_unix,
        "dimension": evento.dimension.value,
        "amplitud": evento.amplitud,
        "contenido": evento.contenido[:200],
    }


def _trayectoria_a_dict(trayectoria: Trayectoria) -> dict[str, Any]:
    """Serializa una Trayectoria de dominio a dict JSON-compatible."""
    return {
        "tipo": trayectoria.tipo,
        "fecha_inicio": trayectoria.fecha_inicio,
        "fecha_fin": trayectoria.fecha_fin,
        "descripcion": trayectoria.descripcion,
        "eventos": len(trayectoria.eventos_ids),
        "amplitud_promedio": trayectoria.amplitud_promedio,
        "confianza": trayectoria.confianza,
        "activa": trayectoria.activa,
    }
