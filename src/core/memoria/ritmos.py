"""Ritmos de la memoria: primacía de época y ventana circadiana.

Dos patrones temporales complementarios:

- **Efecto primacía** (posición serial): el primer evento de una época
  se recuerda mejor que los que le siguen. Cuando un evento llega tras
  un hueco de inactividad (default 6h) inaugura época — el decay lo
  premia con una media-vida extra (~15%).
- **Ritmo circadiano**: la consolidación-sueño es manual por diseño,
  pero la hora importa — dormir la memoria en el valle de actividad
  consolida sin competir con el uso. ``ventana_optima_sueno`` sugiere
  la ventana más quieta desde el histograma real de actividad; la
  recomendación es solo eso — nunca un scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass

# Hueco de inactividad que cierra una época y abre otra (6 horas).
UMBRAL_EPOCA_SEG = 6 * 3600
# Bonus de media-vida por inaugurar época (efecto primacía).
FACTOR_PRIMACIA = 1.15
# Duración de la ventana de sueño sugerida.
VENTANA_SUENO_HORAS = 4


def es_primero_de_epoca(
    gap_segundos: int, umbral_seg: int = UMBRAL_EPOCA_SEG
) -> bool:
    """True si el evento inaugura una época (hueco previo ≥ umbral).

    Args:
        gap_segundos: Segundos desde el evento anterior (``inf`` vía
            un número grande cuando no hay previo — el primer evento
            de la historia siempre es primacia).
        umbral_seg: Hueco mínimo que separa épocas.

    Returns:
        True si el gap supera el umbral.
    """
    return gap_segundos >= umbral_seg


@dataclass(frozen=True, slots=True)
class VentanaSueno:
    """Ventana de consolidación sugerida por el ritmo del usuario.

    Attributes:
        hora_inicio: Hora local 0-23 donde empieza el valle.
        duracion_horas: Largo de la ventana en horas.
        eventos_en_ventana: Actividad observada dentro (debería ser
            la mínima del día).
        confianza: 0-1 — sube con la cantidad de actividad muestral;
            con pocos datos la sugerencia es solo una intuición.
    """

    hora_inicio: int
    duracion_horas: int
    eventos_en_ventana: int
    confianza: float


def ventana_optima_sueno(
    timestamps: list[int],
    *,
    duracion_horas: int = VENTANA_SUENO_HORAS,
) -> VentanaSueno:
    """Encuentra el valle de actividad local: la mejor hora para dormir.

    Histograma por hora local de todos los timestamps; la ventana
    deslizante de ``duracion_horas`` con menos actividad gana. Sin
    datos devuelve la ventana canónica de madrugada (2-6h) con
    confianza 0 — sugerencia inocua, no una medición.

    Args:
        timestamps: Timestamps unix de actividad (eventos, llamadas).
        duracion_horas: Largo de la ventana a buscar (1-12).

    Returns:
        VentanaSueno con la hora de inicio más quieta.
    """
    duracion = max(1, min(duracion_horas, 12))
    if not timestamps:
        return VentanaSueno(2, duracion, 0, 0.0)

    import time as _time

    hist = [0] * 24
    for ts in timestamps:
        hist[_time.localtime(ts).tm_hour] += 1
    # Ventana circular: duplicar el histograma evita casos de borde.
    doble = hist + hist
    mejor_hora, menor_carga = 0, sum(doble[:duracion])
    for h in range(1, 24):
        carga = sum(doble[h : h + duracion])
        if carga < menor_carga:
            mejor_hora, menor_carga = h, carga
    confianza = min(1.0, len(timestamps) / 500.0)
    return VentanaSueno(mejor_hora, duracion, menor_carga, confianza)
