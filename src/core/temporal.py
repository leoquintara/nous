"""Línea de universo: detección de trayectorias y proyección 4D.

Una trayectoria es una racha de eventos consecutivos de la misma
dimensión. La proyección pondera las dimensiones por recencia
(vida media exponencial) y las refuerza si hay una racha activa.
"""

import math
import time
from collections.abc import Sequence
from statistics import fmean

from src.core.modelos import Dimension, Evento, Proyeccion, Trayectoria

MIN_RACHA = 3
BOOST_RACHA_ACTIVA = 1.4
VIDA_MEDIA_DIAS = 7.0
SEGUNDOS_POR_DIA = 86_400
EVENTOS_BASE_PROYECCION = 10


def detectar_trayectorias(
    eventos: Sequence[Evento], min_longitud: int = MIN_RACHA
) -> list[Trayectoria]:
    """Detecta rachas consecutivas de la misma dimensión.

    Args:
        eventos: Eventos de la línea temporal (cualquier orden).
        min_longitud: Mínimo de eventos consecutivos para formar racha.

    Returns:
        Trayectorias detectadas en orden cronológico; la última lleva
        ``activa=True`` si su racha alcanza el evento más reciente.
    """
    if not eventos:
        return []
    ordenados = sorted(eventos, key=lambda e: e.timestamp_unix)
    trayectorias: list[Trayectoria] = []
    inicio = 0
    for i in range(1, len(ordenados) + 1):
        cierra = i == len(ordenados) or (
            ordenados[i].dimension != ordenados[inicio].dimension
        )
        if not cierra:
            continue
        grupo = ordenados[inicio:i]
        if len(grupo) >= min_longitud:
            es_ultima = i == len(ordenados)
            trayectorias.append(_a_trayectoria(grupo, activa=es_ultima))
        inicio = i
    return trayectorias


def proyectar(
    eventos: Sequence[Evento],
    horizonte_dias: int = 7,
    ahora: int | None = None,
    vida_media_dias: float = VIDA_MEDIA_DIAS,
) -> Proyeccion:
    """Proyecta la dimensión dominante probable en el horizonte dado.

    Pondera cada evento por recencia (decaimiento exponencial con la
    vida media dada) y refuerza la dimensión de cualquier racha activa.

    Args:
        eventos: Historial reciente del usuario.
        horizonte_dias: Días hacia adelante de la proyección.
        ahora: Timestamp Unix actual (inyectable para tests).
        vida_media_dias: Vida media del decaimiento por recencia.

    Returns:
        Proyeccion con la distribución por dimensión y la probabilidad
        de la dimensión más probable.

    Raises:
        ValueError: Si no hay eventos sobre los que proyectar.
    """
    if not eventos:
        raise ValueError("No hay eventos en el período para proyectar")
    ahora_real = ahora if ahora is not None else int(time.time())

    pesos = {dimension: 0.0 for dimension in Dimension}
    for evento in eventos:
        edad = max(0, ahora_real - evento.timestamp_unix)
        factor = math.pow(0.5, edad / (vida_media_dias * SEGUNDOS_POR_DIA))
        pesos[evento.dimension] += factor

    for trayectoria in detectar_trayectorias(eventos):
        if trayectoria.activa:
            dimension_activa = Dimension(trayectoria.tipo.split("_", 1)[1])
            pesos[dimension_activa] *= BOOST_RACHA_ACTIVA

    total = sum(pesos.values())
    distribucion = {
        dimension.value: round(pesos[dimension] / total, 4)
        for dimension in Dimension
    }
    dominante = max(distribucion, key=lambda d: distribucion[d])
    return Proyeccion(
        horizonte_dias=horizonte_dias,
        variable_objetivo="dimension_dominante",
        probabilidad=distribucion[dominante],
        vector_proyectado=distribucion,
        basada_en_eventos=tuple(
            e.id for e in eventos[-EVENTOS_BASE_PROYECCION:]
        ),
    )


def _a_trayectoria(grupo: Sequence[Evento], activa: bool) -> Trayectoria:
    """Construye la Trayectoria de un grupo de eventos consecutivos."""
    dimension = grupo[0].dimension
    return Trayectoria(
        tipo=f"racha_{dimension}",
        fecha_inicio=grupo[0].timestamp_unix,
        fecha_fin=None if activa else grupo[-1].timestamp_unix,
        descripcion=(
            f"Racha de {len(grupo)} eventos de dimensión {dimension}"
        ),
        eventos_ids=tuple(e.id for e in grupo),
        amplitud_promedio=round(fmean(e.amplitud for e in grupo), 3),
        confianza=round(min(1.0, len(grupo) / 6), 3),
        activa=activa,
    )
