"""Memoria dependiente de contexto/estado (ADR-003, patrón 6).

Recuerdas mejor en el mismo contexto (proyecto, lugar, hora) y estado
emocional en que codificaste la memoria. La búsqueda re-rankea por
similitud con el contexto/estado actual — "la última vez que estuve
estresado, hice X y funcionó" gana relevancia cuando estás estresado.
"""

from __future__ import annotations

from dataclasses import dataclass

# Pesos del boost contextual sobre el score_final.
PESO_CONTEXTO = 0.3
PESO_ESTADO = 0.2
# El boost se aplica sobre la mitad variable del score (base 0.5
# garantiza que sin contexto no cambie nada — no penaliza, solo premia).
BASE_BOOST = 0.5


@dataclass(frozen=True, slots=True)
class ContextoActual:
    """Contexto/estado del usuario en el momento de la consulta.

    Attributes:
        proyecto: Proyecto activo (slug).
        hora_dia: Hora 0-23.
        fuente: Fuente/origen de la consulta actual — el mismo agente
            o canal desde el que se codificó es un ancla contextual.
        estado_emocional: 0-1 derivado de amplitud somática reciente.
    """

    proyecto: str | None = None
    hora_dia: int | None = None
    fuente: str | None = None
    estado_emocional: float | None = None


def similitud_contexto(
    contexto_evento: dict[str, object] | None, actual: ContextoActual
) -> float:
    """Similitud [0,1] entre el contexto codificado y el actual.

    Compara claves presentes en ambos: proyecto igual, misma fuente,
    hora cercana (±3h). Vacío = 0 (sin señal, no penaliza).
    """
    if not contexto_evento:
        return 0.0
    puntos = 0.0
    posibles = 0.0
    for clave, esperado in (
        ("proyecto", actual.proyecto),
        ("fuente", actual.fuente),
    ):
        if esperado and contexto_evento.get(clave):
            posibles += 1.0
            if str(contexto_evento[clave]) == esperado:
                puntos += 1.0
    hora_ev = contexto_evento.get("hora_dia")
    if actual.hora_dia is not None and isinstance(hora_ev, (int, float)):
        posibles += 1.0
        distancia = min(
            abs(actual.hora_dia - hora_ev),
            24 - abs(actual.hora_dia - hora_ev),
        )
        puntos += max(0.0, 1.0 - distancia / 6.0)
    if posibles == 0.0:
        return 0.0
    return puntos / posibles


def similitud_estado(
    estado_evento: float | None, estado_actual: float | None
) -> float:
    """Similitud [0,1] entre estado emocional codificado y actual."""
    if estado_evento is None or estado_actual is None:
        return 0.0
    return 1.0 - abs(estado_evento - estado_actual)


def boost_contextual(
    score_final: float,
    contexto_evento: dict[str, object] | None,
    estado_evento: float | None,
    actual: ContextoActual,
) -> float:
    """Aplica el boost de contexto/estado sobre un score de búsqueda.

    ``score * (0.5 + 0.3*sim_contexto + 0.2*sim_estado)`` — sin señal
    el factor queda 0.5..1.0; con contexto idéntico llega a 1.0.

    Returns:
        Score ajustado, nunca menor que ``score_final * 0.5``.
    """
    sim_ctx = similitud_contexto(contexto_evento, actual)
    sim_est = similitud_estado(estado_evento, actual.estado_emocional)
    factor = BASE_BOOST + PESO_CONTEXTO * sim_ctx + PESO_ESTADO * sim_est
    return score_final * factor
