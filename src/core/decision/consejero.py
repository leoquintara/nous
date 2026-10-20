"""Consejero prescriptivo — ADR-002 Nivel 2 (feature Pro).

Compone las tres primitivas System One en un solo veredicto:
``choice`` elige la opción, ``score`` estima la confianza del
veredicto y ``noul`` pregunta si hay riesgo no evidente. La línea
de universo 4D del usuario se serializa como ``state`` — el modelo
responde sobre SU historia, no en abstracto.

Módulo desacoplado: no importa ``src.storage`` ni ``src.mcp`` — el
caller construye el ``state`` (tools.py conoce la línea; la REST y
el CLI también pueden).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.decision.provider import (
    DecisionProvider,
    DecisionResult,
    QuestionType,
)
from src.core.decision.questions import choice, fusionar, noul, score

_NIVELES_CONFIANZA = ["baja", "media", "alta"]

_NIVELES_NUM = {"baja": 0.33, "media": 0.66, "alta": 0.95}


@dataclass(frozen=True, slots=True)
class Recomendacion:
    """Veredicto calibrado del consejero.

    Attributes:
        opcion: Opción ganadora del ``choice``.
        probabilidad: Probabilidad calibrada de la opción ganadora.
        distribucion: Probabilidad por opción (todas).
        confianza: Score 0-1 derivado del nivel ``score``.
        riesgo_oculto: Probabilidad noul de riesgo no evidente.
        motor: Provider que respondió (``model_routed``).
    """

    opcion: str
    probabilidad: float
    distribucion: dict[str, float]
    confianza: float
    riesgo_oculto: float
    motor: str


def construir_preguntas(opciones: list[str]) -> dict[str, dict[str, Any]]:
    """Arma las tres preguntas tipificadas para ``predict``.

    Args:
        opciones: Las opciones entre las que se decide (2-6).

    Returns:
        Dict de preguntas ``eleccion``/``confianza``/``riesgo``.
    """
    return fusionar(
        choice(
            "eleccion",
            "Elige la opción más alineada con la historia, metas "
            "y estado reciente descritos en el state.",
            {op: f"elegir '{op}' si mejor continúa la trayectoria" for op in opciones},
        ),
        score(
            "confianza",
            "¿Qué tan sólida es esa elección dados los datos del state?",
            _NIVELES_CONFIANZA,
        ),
        noul(
            "riesgo",
            "¿El state sugiere un riesgo material no evidente en la "
            "opción elegida (loop abierto, patrón adverso, sobreextensión)?",
        ),
    )


def _a_recomendacion(resultado: DecisionResult) -> Recomendacion:
    """Traduce las respuestas tipificadas al veredicto de dominio."""
    eleccion = resultado.answers.get("eleccion")
    confianza = resultado.answers.get("confianza")
    riesgo = resultado.answers.get("riesgo")
    opcion = str(eleccion.value) if eleccion else ""
    distribucion = dict(eleccion.distribution or {}) if eleccion else {}
    nivel = str(confianza.value) if confianza else "media"
    return Recomendacion(
        opcion=opcion,
        probabilidad=eleccion.probability if eleccion else 0.0,
        distribucion=distribucion,
        confianza=_NIVELES_NUM.get(nivel, 0.66),
        riesgo_oculto=riesgo.probability if riesgo else 0.0,
        motor=resultado.model_routed,
    )


async def recomendar(
    provider: DecisionProvider,
    state: str,
    opciones: list[str],
) -> Recomendacion | None:
    """Evalúa un dilema contra el state 4D con el provider.

    Args:
        provider: Proveedor System One ya construido.
        state: Contexto serializado (dilema + línea de universo).
        opciones: Opciones entre las que decidir.

    Returns:
        ``Recomendacion`` o None si el provider no respondió nada
        útil (el caller decide cómo degradar).

    Raises:
        ValueError: Menos de 2 opciones o state vacío.
    """
    if len(opciones) < 2:
        raise ValueError("consultar_decision requiere >= 2 opciones")
    if not state.strip():
        raise ValueError("el state del dilema no puede estar vacío")
    resultado = await provider.predict(state, construir_preguntas(opciones))
    rec = _a_recomendacion(resultado)
    if (
        not rec.opcion
        or all(
            a.question_type == QuestionType.CHOICE and a.probability == 0.0
            for a in resultado.answers.values()
        )
        and not rec.distribucion
    ):
        return None
    return rec
