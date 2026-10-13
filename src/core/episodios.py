"""Segmentación episódica de la línea de universo.

Teoría de segmentación de eventos (Zacks et al., 2007): el cerebro
corta el flujo continuo de experiencia en episodios en los puntos de
inflexión temporal (boundaries). Nous reproduce ese corte agrupando
eventos cuya separación temporal no supera un hueco máximo: un gap
largo marca el cierre de un episodio y el inicio del siguiente.
"""

from collections import Counter
from dataclasses import dataclass

from src.core.modelos import Dimension, Evento

GAP_MAX_DEFECTO_SEGUNDOS = 14_400  # 4 horas


@dataclass(frozen=True, slots=True)
class Episodio:
    """Secuencia contigua de eventos en la línea de universo.

    Attributes:
        inicio: Timestamp Unix del primer evento del episodio.
        fin: Timestamp Unix del último evento.
        eventos: Eventos del episodio en orden cronológico.
        dimension_dominante: Dimensión más frecuente del episodio.
        amplitud_media: Media de amplitud de sus eventos.
    """

    inicio: int
    fin: int
    eventos: tuple[Evento, ...]
    dimension_dominante: Dimension
    amplitud_media: float


def segmentar(
    eventos: list[Evento],
    gap_max_segundos: int = GAP_MAX_DEFECTO_SEGUNDOS,
) -> list[Episodio]:
    """Corta la línea temporal en episodios por huecos de inactividad.

    Args:
        eventos: Eventos de la línea (se ordenan por timestamp).
        gap_max_segundos: Separación máxima entre eventos consecutivos
            del mismo episodio; un gap mayor abre un episodio nuevo.

    Returns:
        Episodios en orden cronológico. Lista vacía si no hay eventos.
    """
    if not eventos:
        return []
    ordenados = sorted(eventos, key=lambda e: e.timestamp_unix)
    grupos: list[list[Evento]] = [[ordenados[0]]]
    for evento in ordenados[1:]:
        if evento.timestamp_unix - grupos[-1][-1].timestamp_unix > gap_max_segundos:
            grupos.append([evento])
        else:
            grupos[-1].append(evento)
    return [_a_episodio(grupo) for grupo in grupos]


def _a_episodio(grupo: list[Evento]) -> Episodio:
    """Resume un grupo contiguo de eventos en un Episodio."""
    conteo = Counter(e.dimension for e in grupo)
    dominante = conteo.most_common(1)[0][0]
    return Episodio(
        inicio=grupo[0].timestamp_unix,
        fin=grupo[-1].timestamp_unix,
        eventos=tuple(grupo),
        dimension_dominante=dominante,
        amplitud_media=sum(e.amplitud for e in grupo) / len(grupo),
    )
