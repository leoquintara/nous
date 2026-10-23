"""Detección de novedad — error de predicción como señal de saliencia.

La dopamina no marca placer: marca *sorpresa* — la distancia entre
lo esperado y lo observado. En Nous la expectativa es la trayectoria
reciente de embeddings: un evento nuevo cuyo vector queda lejos de
ella es inesperado y merece codificarse más fuerte (amplitud extra),
aunque el llamador la haya declarado baja.
"""

from __future__ import annotations

from collections.abc import Sequence

# Tope del boost multiplicativo de sorpresa (×1.25 a lo declarado).
BOOST_SORPRESA_MAX = 0.25
# Similitud mínima para considerar un vecino "esperado".
_VECINOS_MAX = 20


def _coseno(a: Sequence[float], b: Sequence[float]) -> float:
    """Similitud coseno entre dos vectores ya normalizados."""
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


def sorpresa(
    embedding_nuevo: Sequence[float],
    embeddings_recientes: Sequence[Sequence[float]],
) -> float:
    """Distancia del evento a la trayectoria reciente (0-1).

    1 − max_coseno contra los recientes: un evento idéntico a la
    actividad actual sorprende 0; uno aislado conceptualmente, ~1.

    Args:
        embedding_nuevo: Vector del evento entrante (normalizado).
        embeddings_recientes: Vectores de la trayectoria reciente
            (máx. ``_VECINOS_MAX`` primeros se consideran).

    Returns:
        Sorpresa en [0, 1]. Sin trayectoria previa → 0.5 (incerteza,
        ni esperado ni sorprendente — la primera huella no engaña).
    """
    vecinos = embeddings_recientes[:_VECINOS_MAX]
    if not vecinos:
        return 0.5
    maximo = max(_coseno(embedding_nuevo, v) for v in vecinos)
    return max(0.0, min(1.0, 1.0 - maximo))


def amplitud_con_sorpresa(amplitud_base: float, sorpresa_val: float) -> float:
    """Amplitud efectiva: la declarada amplificada por lo inesperado.

    La sorpresa solo *premia* — la dopamina amplifica la señal, no la
    sustituye: un evento marcado trivial (0.05) sigue siendo débil
    aunque sea raro, y uno declarado intenso sube aún más. Nunca baja
    de lo declarado ni supera 1.0.
    """
    return min(1.0, amplitud_base * (1.0 + BOOST_SORPRESA_MAX * sorpresa_val))
