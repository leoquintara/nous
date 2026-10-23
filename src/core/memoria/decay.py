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

from src.core.memoria.retrieval import factor_espaciado
from src.core.memoria.ritmos import FACTOR_PRIMACIA
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
# Olvido dirigido: el usuario pidió olvidar — la curva se acelera ~3x.
# No es archivo (eso es decisión del sueño, reversible por restaurar):
# es dejar que la huella se disuelva antes de tiempo.
FACTOR_OLVIDO_DIRIGIDO = 0.33
# Spaced repetition: cuando la retención cae a este nivel, toca repaso.
# 0.6 está sobre el umbral de archivo (0.15) — se repasa aún siendo
# evocable, que es cuando el repaso más consolidación produce.
RETENCION_REVISION = 0.6


def media_vida_dias(
    dimension: Dimension,
    tipo_memoria: TipoMemoria | None = None,
    amplitud: float = 0.5,
    refuerzos: int = 0,
    curado: bool = False,
    intervalo_medio_dias: float | None = None,
    primacia: bool = False,
    olvido_dirigido: bool = False,
) -> float:
    """Media-vida efectiva de una memoria en días.

    Args:
        dimension: Dimensión del evento.
        tipo_memoria: Tipo neurocognitivo (None = sin clasificar → 1.0).
        amplitud: Intensidad 0-1 — modula episódicas y cognitivas.
        refuerzos: Veces que la memoria fue evocada (replay).
        curado: True = tier protegido → media-vida infinita simbólica.
        intervalo_medio_dias: Espacio medio entre evocaciones —
            el spacing effect: ensayos distribuidos consolidan más
            que ensayos masivos (None = sin accesos registrados).
        primacia: True si el evento inaugura época (efecto
            primacía — el primer evento de un periodo persiste más).
        olvido_dirigido: True si el usuario pidió olvidar —
            la media-vida se reduce a ~1/3 (olvido acelerado).

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
    base *= factor_espaciado(intervalo_medio_dias)
    if primacia:
        base *= FACTOR_PRIMACIA
    if olvido_dirigido:
        base *= FACTOR_OLVIDO_DIRIGIDO
    return base


def proxima_revision(
    desde_unix: int,
    media_vida: float,
    umbral: float = RETENCION_REVISION,
) -> int | None:
    """Timestamp del repaso óptimo — cuándo la retención cruzará el umbral.

    Spaced repetition (SRS): la curva exponencial cruza ``umbral``
    en ``media_vida · log2(1/umbral)`` días desde el último acceso.
    Repasar justo antes de olvidar es cuando la evocación más
    consolida (desirable difficulty).

    Args:
        desde_unix: Último acceso o creación (base del reloj).
        media_vida: Media-vida efectiva en días (de
            :func:`media_vida_dias`).
        umbral: Retención que dispara el repaso (default 0.6).

    Returns:
        Timestamp unix de la revisión óptima, o ``None`` si la
        media-vida es infinita (curado — nunca necesita repaso).
    """
    if math.isinf(media_vida) or media_vida <= 0:
        return None
    dias = media_vida * math.log2(1.0 / umbral)
    return desde_unix + int(dias * 86400)


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
    return float(0.5 ** (dias_transcurridos / media_vida))


def retencion_actual(
    ahora_unix: int,
    timestamp_unix: int,
    ultimo_acceso: int | None,
    dimension: Dimension,
    tipo_memoria: TipoMemoria | None = None,
    amplitud: float = 0.5,
    refuerzos: int = 0,
    curado: bool = False,
    primacia: bool = False,
    olvido_dirigido: bool = False,
) -> float:
    """Retención completa de un evento ahora (helper de una llamada).

    El último acceso cuenta como "replay" — el reloj corre desde la
    evocación más reciente, no desde la creación. El spacing effect
    se deriva adentro: (ultimo_acceso − creación) / refuerzos es el
    intervalo medio entre ensayos.
    """
    desde = max(timestamp_unix, ultimo_acceso or 0)
    dias = max(0.0, (ahora_unix - desde) / 86400.0)
    intervalo = (
        (ultimo_acceso - timestamp_unix) / 86400.0 / refuerzos
        if refuerzos > 0 and ultimo_acceso is not None
        and ultimo_acceso > timestamp_unix
        else None
    )
    return retencion(
        dias,
        media_vida_dias(
            dimension,
            tipo_memoria,
            amplitud,
            refuerzos,
            curado,
            intervalo,
            primacia,
            olvido_dirigido,
        ),
    )
