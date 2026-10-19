"""Interferencia de memoria (ADR-003, patrón 5).

Cuando un evento nuevo contradice memorias viejas ("ahora uso Bun en
lugar de Node" vs eventos previos de Node), la memoria nueva interfiere
con la antigua — interferencia retroactiva. Detectarla permite separar
contextos y alertar al usuario cuando cambia de opinión o práctica.

Señal: alta similitud semántica + marcadores de superación/negación.
Sin marcador, la similitud sola no implica contradicción.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Conflicto:
    """Evento previo potencialmente contradicho por uno nuevo.

    Attributes:
        evento_id: Id del evento anterior.
        similitud: Similitud coseno con el evento nuevo.
        evidencia: Marcador que disparó la sospecha ("ya no", "en lugar").
    """

    evento_id: str
    similitud: float
    evidencia: str


# Marcadores de superación/cambio — el contenido nuevo "sustituye" al viejo.
_MARCADORES_SUPERACION: tuple[str, ...] = (
    "ya no", "en lugar de", "en vez de", "cambié a", "cambie a",
    "ahora uso", "ahora es", "migré a", "migre a", "dejé de",
    "deje de", "sustitu", "reemplaz", "antes usaba", "antes era",
    "obsoleto", "deprecad", "abandoné", "abandone",
)

# Negación directa sobre el mismo sujeto.
_RE_NEGACION = re.compile(
    r"\b(no|nunca|jamás|jamas|sin)\s+\w+", re.IGNORECASE
)

UMBRAL_SIMILITUD = 0.72


def marcar_superacion(contenido_nuevo: str) -> str | None:
    """Devuelve el marcador de superación más temprano del texto.

    La evidencia es la señal de cambio que el usuario expresó primero
    — entre varios marcadores gana el de menor posición, no el de la
    lista estática.
    """
    texto = contenido_nuevo.lower()
    mejor: tuple[int, str] | None = None
    for marcador in _MARCADORES_SUPERACION:
        pos = texto.find(marcador)
        if pos >= 0 and (mejor is None or pos < mejor[0]):
            mejor = (pos, marcador)
    return mejor[1] if mejor else None


def detectar_conflicto(
    contenido_nuevo: str,
    candidatos_similares: list[tuple[str, float]],
) -> list[Conflicto]:
    """Filtra candidatos similares que podrían ser contradichos.

    Solo cuenta si el evento nuevo lleva un marcador de superación —
    la similitud sin marcador es solo relación semántica, no
    interferencia. Con marcador, cada candidato por encima del umbral
    es un Conflicto.

    Args:
        contenido_nuevo: Texto del evento recién registrado.
        candidatos_similares: ``[(evento_id, coseno)]`` previos.

    Returns:
        Lista de conflictos ordenada por similitud descendente.
    """
    marcador = marcar_superacion(contenido_nuevo)
    if marcador is None:
        return []
    return [
        Conflicto(evento_id=eid, similitud=s, evidencia=marcador)
        for eid, s in sorted(
            candidatos_similares, key=lambda c: -c[1]
        )
        if s >= UMBRAL_SIMILITUD
    ]


def tiene_negacion(texto: str) -> bool:
    """True si el texto contiene una negación explícita."""
    return bool(_RE_NEGACION.search(texto))
