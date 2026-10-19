"""Builders de preguntas tipificadas ``choice``/``score``/``noul``.

Centraliza el schema del wire protocol para que los consumidores
(clasificador, amplitud, trayectorias) no armen dicts a mano.
"""

from __future__ import annotations

from typing import Any


def choice(nombre: str, instrucciones: str, opciones: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Construye una pregunta ``choice`` (elige entre opciones nombradas).

    Args:
        nombre: Clave de la pregunta en el dict de respuestas.
        instrucciones: Instrucción legible para el modelo.
        opciones: ``{opcion: criterio}`` — el criterio describe cuándo
            elegir esa opción.

    Returns:
        ``{nombre: {"type": "choice", "instructions": …, "criteria": …}}``.
    """
    return {
        nombre: {
            "type": "choice",
            "instructions": instrucciones,
            "criteria": dict(opciones),
        }
    }


def score(nombre: str, instrucciones: str, niveles: list[str]) -> dict[str, dict[str, Any]]:
    """Construye una pregunta ``score`` (nivel ordenado).

    Args:
        nombre: Clave de la pregunta.
        instrucciones: Instrucción legible para el modelo.
        niveles: Niveles ordenados de menor a mayor.

    Returns:
        ``{nombre: {"type": "score", "instructions": …, "criteria": […]}}``.
    """
    return {
        nombre: {
            "type": "score",
            "instructions": instrucciones,
            "criteria": list(niveles),
        }
    }


def noul(nombre: str, instrucciones: str) -> dict[str, dict[str, Any]]:
    """Construye una pregunta ``noul`` (sí/no → probabilidad calibrada).

    Args:
        nombre: Clave de la pregunta.
        instrucciones: La pregunta de sí/no a evaluar.

    Returns:
        ``{nombre: {"type": "noul", "instructions": …}}``.
    """
    return {nombre: {"type": "noul", "instructions": instrucciones}}


def fusionar(*preguntas: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Une varios builders en un solo dict de preguntas para predict()."""
    out: dict[str, dict[str, Any]] = {}
    for p in preguntas:
        out.update(p)
    return out
