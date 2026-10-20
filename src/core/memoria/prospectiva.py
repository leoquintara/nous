"""Memoria prospectiva (ADR-003, patrón 2): recordar intenciones futuras.

No es predicción — es *recordar ejecutar algo*. Dos triggers:

- ``time``: se cumple una fecha/hora (``trigger_valor={"ts": <unix>}``).
- ``event``: se cumple una condición sobre eventos entrantes
  (``trigger_valor={"query": "<texto>", "umbral": 0.6}``) — evaluada por
  similitud coseno contra el embedding del evento recién registrado.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum


class TriggerTipo(StrEnum):
    """Tipo de condición que dispara la intención."""

    TIME = "time"
    EVENT = "event"


@dataclass(frozen=True, slots=True)
class Intencion:
    """Intención prospectiva pendiente o resuelta.

    Attributes:
        id: Identificador único.
        intencion: Qué recordar ("llamar a María").
        trigger_tipo: ``time`` o ``event``.
        trigger_valor: ``{"ts": int}`` o ``{"query": str, "umbral": float}``.
        creada_en: Timestamp de creación.
        ejecutada/cancelada: Flags de resolución.
    """

    id: str
    intencion: str
    trigger_tipo: TriggerTipo
    trigger_valor: dict[str, object]
    creada_en: int
    ejecutada: bool = False
    cancelada: bool = False


class IntencionInvalidaError(ValueError):
    """El trigger de la intención no tiene el schema esperado."""


def parsear_trigger(valor: dict[str, object]) -> TriggerTipo:
    """Valida y deduce el tipo de trigger desde su valor.

    Args:
        valor: dict con ``"ts"`` (time) o ``"query"`` (event).

    Returns:
        TriggerTipo correspondiente.

    Raises:
        IntencionInvalidaError: Si falta la clave esperada o es inválida.
    """
    if isinstance(valor.get("ts"), int | float):
        return TriggerTipo.TIME
    if isinstance(valor.get("query"), str) and str(valor["query"]).strip():
        return TriggerTipo.EVENT
    raise IntencionInvalidaError(
        "trigger_valor debe tener 'ts' (time) o 'query' (event)"
    )


def disparada_por_tiempo(intencion: Intencion, ahora_unix: int) -> bool:
    """True si una intención time-based ya venció.

    Args:
        intencion: Intención a evaluar (pendiente).
        ahora_unix: Timestamp actual.
    """
    if intencion.ejecutada or intencion.cancelada:
        return False
    if intencion.trigger_tipo != TriggerTipo.TIME:
        return False
    ts = intencion.trigger_valor.get("ts")
    return isinstance(ts, int | float) and ahora_unix >= int(ts)


def disparada_por_evento(
    intencion: Intencion,
    contenido_evento: str,
    similitud: float | None = None,
) -> bool:
    """True si un evento entrante dispara una intención event-based.

    Dos mecanismos: similitud coseno (si se pasa) contra ``query``, o
    fallback léxico — todas las palabras significativas del query
    presentes en el contenido.

    Args:
        intencion: Intención pendiente.
        contenido_evento: Texto del evento recién registrado.
        similitud: Similitud coseno precomputada con ``query`` (opcional;
            si None se usa el fallback léxico).

    Returns:
        True si la intención se dispara.
    """
    if intencion.ejecutada or intencion.cancelada:
        return False
    if intencion.trigger_tipo != TriggerTipo.EVENT:
        return False
    query = str(intencion.trigger_valor.get("query") or "")
    if not query.strip():
        return False
    umbral_raw = intencion.trigger_valor.get("umbral")
    umbral = (
        float(umbral_raw) if isinstance(umbral_raw, int | float) else 0.6
    )
    if similitud is not None:
        return similitud >= umbral
    return _cobertura_lexica(query, contenido_evento) >= 0.8


_STOPWORDS = frozenset(
    "el la los las un una de del en y o a al con por para que me se lo "
    "le mi tu su es son".split()
)


def _cobertura_lexica(query: str, contenido: str) -> float:
    """Fracción de palabras significativas del query presentes."""
    palabras = [
        p
        for p in re.findall(r"[a-záéíóúñü]+", query.lower())
        if p not in _STOPWORDS and len(p) > 2
    ]
    if not palabras:
        return 0.0
    texto = contenido.lower()
    return sum(1 for p in palabras if p in texto) / len(palabras)


def distancia_temporal(intencion: Intencion, ahora_unix: int) -> float:
    """Segundos hasta el trigger time (inf si no aplica/no cumple)."""
    if intencion.trigger_tipo != TriggerTipo.TIME:
        return math.inf
    ts = intencion.trigger_valor.get("ts")
    if not isinstance(ts, int | float):
        return math.inf
    return max(0.0, float(ts) - ahora_unix)
