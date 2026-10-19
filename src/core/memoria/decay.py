"""Curvas de olvido adaptativas (ADR-003, patrón 4).

La retención de una memoria decae con el tiempo, pero **no todas las
memorias decaen igual**: un episodio emocionalmente intenso persiste
más que un hecho semántico trivial, y un estado somático es transitorio
por naturaleza.

Modelo: ``retencion = 0.5 ** (dias / media_vida)`` — decaimiento
exponencial con media-vida real (a ``dias = media_vida`` retiene 0.5).
La media-vida se ajusta por dimensión, tipo de memoria y amplitud. Los
refuerzos (evocaciones) actúan como "replay" — cada acceso reinicia
parcialmente el reloj, y los eventos ``curados`` nunca decaen.
"""

from __future__ import annotations

import math

from src.core.memoria.tipos import TipoMemoria
from src.core.modelos import Dimension

# Media-vida base por dimensión (días). La somática es transitoria: el
# cuerpo cambia rápido. La conductual (hábitos) es intermedia.
_MEDIA_VIDA_DIMENSION: dict[Dimension, float] = {
    Dimension.COGNITIVA: 14.0,
    Dimension.SOMATICA: 3.0,
    Dimension.CONDUCTUAL: 9.0,
}

# Multiplicador por tipo de memoria — el conocimiento semántico
# persiste más que un episodio ordinario; lo procedimental más aún
# (las habilidades se consolidan con el uso).
_FACTOR_TIPO: dict[TipoMemoria, float] = {
    TipoMemoria.EPISODICA: 1.0,
    TipoMemoria.SEMANTICA: 2.0,
    TipoMemoria.PROCEDIMENTAL: 2.5,
}

# La amplitud modula el episodio: lo emocionalmente intenso se recuerda
# más (flashbulb memory atenuada — un factor, no inmortalidad).
_FACTOR_AMPLITUD_MAX = 3.0
# Cada refuerzo (evocación) añade ~20% de media-vida, hasta +100%.
_FACTOR_REFUERZO = 0.2


def media_vida_dias(
    dimension: Dimension,
    tipo_memoria: TipoMemoria | None = None,
    amplitud: float = 0.5,
    refuerzos: int = 0,
    curado: bool = False,
) -> float:
    """Media-vida efectiva de una memoria en días.

    Args:
        dimension: Dimensión del evento.
        tipo_memoria: Tipo neurocognitivo (None = sin clasificar → 1.0).
        amplitud: Intensidad 0-1 — modula episódicas y cognitivas.
        refuerzos: Veces que la memoria fue evocada (replay).
        curado: True = tier protegido → media-vida infinita simbólica.

    Returns:
        Días hasta que la retención cae a 0.5. Con ``curado`` retorna
        ``math.inf``.
    """
    if curado:
        return math.inf
    base = _MEDIA_VIDA_DIMENSION[dimension]
    if tipo_memoria is not None:
        base *= _FACTOR_TIPO[tipo_memoria]
    if dimension in (Dimension.COGNITIVA, Dimension.CONDUCTUAL):
        base *= 1.0 + amplitud * (_FACTOR_AMPLITUD_MAX - 1.0)
    base *= 1.0 + min(refuerzos, 5) * _FACTOR_REFUERZO
    return base


def retencion(
    dias_transcurridos: float,
    media_vida: float,
) -> float:
    """Retención exponencial de una memoria tras N días.

    Args:
        dias_transcurridos: Días desde el último acceso o creación.
        media_vida: Resultado de :func:`media_vida_dias`.

    Returns:
        Retención en [0, 1]. Media-vida infinita → siempre 1.0.
    """
    if math.isinf(media_vida):
        return 1.0
    return 0.5 ** (dias_transcurridos / media_vida)


def retencion_actual(
    ahora_unix: int,
    timestamp_unix: int,
    ultimo_acceso: int | None,
    dimension: Dimension,
    tipo_memoria: TipoMemoria | None = None,
    amplitud: float = 0.5,
    refuerzos: int = 0,
    curado: bool = False,
) -> float:
    """Retención completa de un evento ahora (helper de una llamada).

    El último acceso cuenta como "replay" — el reloj corre desde la
    evocación más reciente, no desde la creación.
    """
    desde = max(timestamp_unix, ultimo_acceso or 0)
    dias = max(0.0, (ahora_unix - desde) / 86400.0)
    return retencion(
        dias,
        media_vida_dias(dimension, tipo_memoria, amplitud, refuerzos, curado),
    )
