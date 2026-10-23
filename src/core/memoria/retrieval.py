"""Efectos de la evocación (recall) — la memoria cambia al usarse.

Cuatro patrones neurocognitivos del lado de la lectura:

- **Co-activación hebbiana**: "neurons that fire together wire
  together" — los resultados devueltos juntos ganan una arista
  ``coactiva`` que se refuerza con cada co-evocación.
- **Extinción emocional**: una memoria negativa evocada en un estado
  neutro pierde valencia poco a poco (la exposición sin refuerzo
  extingue — nunca en un solo recall).
- **Ventana lábil / reconsolidación**: la memoria evocada queda
  lábil unas horas — una actualización dentro de la ventana cuenta
  como reconsolidación (arista ``reconsolida``), no solo supersesión.
- **Metamemoria**: ``confianza`` estima qué tan fiable es el
  recuerdo devuelto — el agente puede ponderar la respuesta en vez
  de tratar toda memoria como igualmente cierta.
"""

from __future__ import annotations

import itertools

# --- Co-activación hebbiana ---
COACTIVA_TOP_K = 6
COACTIVA_DELTA = 0.15
COACTIVA_PESO_MAX = 3.0

# --- Extinción emocional ---
# Cada recall neutro atenúa la valencia un ~12%; |valencia| < 0.05
# se considera extinta del todo.
EXTINCION_FACTOR = 0.12
EXTINCION_MINIMA = 0.05
# El recuerdo debe ser emocional y el estado actual (casi) neutro
# para que la exposición extinga — revivirlo estando mal refuerza.
UMBRAL_VALENCIA_EMOCIONAL = 0.3
UMBRAL_ESTADO_NEUTRO = 0.2

# --- Ventana lábil ---
# Horas tras la evocación en que la memoria admite reconsolidación.
VENTANA_LABIL_SEG = 6 * 3600


def pares_coactivos(evento_ids: list[str]) -> list[tuple[str, str]]:
    """Pares únicos de los top-K resultados co-evocados.

    El co-recall es la señal hebbiana: si dos memorias se encienden
    juntas para la misma consulta, su asociación merece un vínculo
    propio — no necesariamente son semánticamente iguales, son
    *funcionalmente* coactivas.
    """
    ids = evento_ids[:COACTIVA_TOP_K]
    return list(itertools.combinations(ids, 2))


def peso_coactiva(peso_actual: float) -> float:
    """Nuevo peso de una arista coactiva re-encendida (cap fisiológico)."""
    return min(peso_actual + COACTIVA_DELTA, COACTIVA_PESO_MAX)


def extincion_valencia(
    valencia_memoria: float | None,
    estado_actual: float | None,
) -> float | None:
    """Valencia tras un recall en estado neutro — extinción gradual.

    Args:
        valencia_memoria: ``estado_emocional`` codificado (−1..1).
        estado_actual: Valencia somática actual; ``None`` también
            cuenta como neutra (sin dato no hay "estando mal").

    Returns:
        Nueva valencia atenuada, o ``None`` si este recall no
        extingue (memoria neutra o usuario emocional).
    """
    if valencia_memoria is None:
        return None
    if abs(valencia_memoria) < UMBRAL_VALENCIA_EMOCIONAL:
        return None
    if estado_actual is not None and abs(estado_actual) >= UMBRAL_ESTADO_NEUTRO:
        return None
    nueva = valencia_memoria * (1.0 - EXTINCION_FACTOR)
    return 0.0 if abs(nueva) < EXTINCION_MINIMA else nueva


def ventana_labil(ahora_unix: int, segundos: int = VENTANA_LABIL_SEG) -> int:
    """Hasta cuándo la memoria evocada admite reconsolidación."""
    return ahora_unix + segundos


def esta_labil(labil_hasta: int | None, ahora_unix: int) -> bool:
    """True si el evento sigue dentro de su ventana de reconsolidación."""
    return labil_hasta is not None and ahora_unix <= labil_hasta


def confianza(
    refuerzos: int,
    weight: float,
    amplitud: float,
    edad_dias: float,
    contradicciones: int = 0,
    archivado: bool = False,
) -> float:
    """Metamemoria: qué tan fiable es este recuerdo devuelto (0-1).

    No es el score de la búsqueda — es cuánto debería fiarse el
    agente del contenido una vez recuperado:

    - Sube con refuerzos (evocaciones correctas previas), ``weight``
      (consolidación) y amplitud (codificación intensa).
    - Baja con antigüedad extrema (el pasado se vuelve anecdótico),
      contradicciones abiertas y estar archivado.

    Returns:
        Confianza en [0.05, 0.98] — nunca certeza total: es una
        memoria, no un hecho verificado.
    """
    if archivado:
        return 0.05
    evidencia = (
        0.45
        + min(refuerzos, 8) * 0.045
        + min(max(weight, 0.0), 3.0) * 0.08
        + amplitud * 0.1
    )
    senectud = 1.0 - min(edad_dias / 365.0, 0.25)
    penal = 1.0 - min(contradicciones, 3) * 0.15
    return round(max(0.05, min(0.98, evidencia * senectud * penal)), 3)


def confianza_de_evento(
    refuerzos: int,
    weight: float,
    amplitud: float,
    timestamp_unix: int,
    ahora_unix: int,
    archivado: bool = False,
) -> float:
    """Atajo sobre campos del evento: deriva edad y delega."""
    edad = max(0.0, (ahora_unix - timestamp_unix) / 86400.0)
    return confianza(refuerzos, weight, amplitud, edad, archivado=archivado)


# --- Spacing effect (se consume desde decay) ---
def factor_espaciado(intervalo_medio_dias: float | None) -> float:
    """Multiplicador de media-vida por ensayos *espaciados*.

    El spacing effect: repasos distribuidos en el tiempo consolidan
    mejor que repasos masivos. ``intervalo_medio_dias`` = (último
    acceso − creación) / refuerzos — una evocación por día vale más
    que cinco el mismo día. Cap a +35%.

    Returns:
        Factor ≥1.0; ``None`` (sin accesos) → 1.0.
    """
    if intervalo_medio_dias is None or intervalo_medio_dias <= 0.0:
        return 1.0
    return 1.0 + min(intervalo_medio_dias, 7.0) * 0.05


def intervalo_medio(
    timestamp_unix: int, ultimo_acceso: int | None, refuerzos: int
) -> float | None:
    """Intervalo medio entre ensayos en días, o None si no hubo."""
    if refuerzos <= 0 or ultimo_acceso is None:
        return None
    gap = max(0, ultimo_acceso - timestamp_unix) / 86400.0
    return gap / refuerzos if gap > 0 else 0.0
