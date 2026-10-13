"""Resources MCP de Nous: vistas de solo lectura sobre la memoria 4D.

Los resources no modifican estado: exponen resúmenes agregados,
patrones detectados y la proyección vigente. A diferencia de las
tools (acciones del agente), un resource es contexto que el cliente
puede adjuntar a su prompt.

URIs disponibles:

- ``memoria://resumen/diario`` — agregado de las últimas 24 horas.
- ``memoria://resumen/semanal`` — agregado de los últimos 7 días.
- ``memoria://patrones/flow`` — rachas dimensionales de los últimos 30 días.
- ``memoria://proyeccion/actual`` — última proyección persistida.
"""

import json
import time
from statistics import fmean
from typing import Any

from src.core.modelos import Dimension, Evento, Trayectoria
from src.core.temporal import SEGUNDOS_POR_DIA, detectar_trayectorias
from src.mcp.server import mcp_server
from src.storage.database import get_session, obtener_engine
from src.storage.repositories import EventoRepository, ProyeccionRepository

DIAS_RESUMEN: dict[str, int] = {"diario": 1, "semanal": 7}
DIAS_PATRONES = 30
MAX_EVENTOS_RESUMEN = 500
TOP_EVENTOS_DESTACADOS = 5


@mcp_server.resource("memoria://resumen/{periodo}")
async def resumen(periodo: str) -> str:
    """Resume la actividad reciente de la línea de universo (solo lectura).

    Resource de contexto: el agente lo lee para situarse, no para
    actuar. Para buscar un tema concreto usar la tool
    ``consultar_memoria``; para registrar algo, ``recordar_evento``.

    Args:
        periodo: ``diario`` (24 h) o ``semanal`` (7 días).

    Returns:
        JSON con el período, total de eventos, conteo y amplitud media
        por dimensión, y los eventos de mayor amplitud del período.
    """
    if periodo not in DIAS_RESUMEN:
        return json.dumps(
            {
                "error": f"Período '{periodo}' no soportado",
                "periodos_validos": sorted(DIAS_RESUMEN),
            },
            ensure_ascii=False,
        )
    eventos = await _eventos_recientes(DIAS_RESUMEN[periodo])
    return json.dumps(
        _construir_resumen(periodo, eventos), ensure_ascii=False, default=str
    )


@mcp_server.resource("memoria://patrones/flow")
async def patrones_flow() -> str:
    """Expone las trayectorias (rachas dimensionales) recientes (solo lectura).

    Resource de contexto: describe patrones ya detectados en los
    últimos 30 días. Para generar una extrapolación futura usar la
    tool ``proyectar_trayectoria``.

    Returns:
        JSON con las trayectorias detectadas (tipo, rango temporal,
        amplitud media, confianza y si sigue activa).
    """
    eventos = await _eventos_recientes(DIAS_PATRONES)
    trayectorias = detectar_trayectorias(eventos)
    salida = {
        "ventana_dias": DIAS_PATRONES,
        "eventos_analizados": len(eventos),
        "trayectorias": [_trayectoria_a_dict(t) for t in trayectorias],
    }
    return json.dumps(salida, ensure_ascii=False)


@mcp_server.resource("memoria://proyeccion/actual")
async def proyeccion_actual() -> str:
    """Expone la última proyección persistida (solo lectura).

    Resource de contexto: refleja lo que la tool
    ``proyectar_trayectoria`` calculó por última vez; no recalcula.
    Si nunca se ha proyectado, lo indica en ``estado``.

    Returns:
        JSON con horizonte, distribución por dimensión, probabilidad
        y fecha de cálculo, o ``estado: sin_proyecciones``.
    """
    async with get_session(obtener_engine()) as session:
        proyeccion = await ProyeccionRepository(session).ultima()
    if proyeccion is None:
        return json.dumps(
            {
                "estado": "sin_proyecciones",
                "sugerencia": "Invoca la tool proyectar_trayectoria primero",
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
        "aviso": "Extrapolación probabilística, no una predicción cierta",
    }
    return json.dumps(salida, ensure_ascii=False)


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
            "amplitud_media": (
                round(fmean(e.amplitud for e in grupo), 3) if grupo else None
            ),
        }
    destacados = sorted(eventos, key=lambda e: e.amplitud, reverse=True)[
        :TOP_EVENTOS_DESTACADOS
    ]
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
