"""Memoria de trabajo — el buffer de Baddeley.

No todo lo que el agente piensa merece largo plazo: borradores,
hipótesis, contexto transitorio. La memoria de trabajo de Nous es
una tabla volátil con TTL corto:

- **Expira** si nadie la ensaya — el olvido del buffer no es un bug,
  es su función (igual que las intenciones: se evalúa al consultar,
  sin scheduler).
- **Promueve** a la línea de universo si se ensaya lo bastante — la
  repetición es la señal de que el pensamiento importa.
- **Nunca contamina** la búsqueda principal: el buffer es un espacio
  aparte; lo que sobrevive entra como evento real.
"""

from __future__ import annotations

# TTL default de un borrador: media hora sin ensayo → se pierde.
TTL_BORRADOR_SEG = 1800
# Ensayos necesarios para promover a largo plazo (repetición → señal).
ENSAYOS_PARA_PROMOVER = 2
# Capacidad visible por workspace — el buffer humano ~7±2; el de un
# agente puede ser mayor pero sigue acotado.
CAPACIDAD_MAX = 15


def ttl_default() -> int:
    """TTL en segundos de un borrador nuevo."""
    return TTL_BORRADOR_SEG


def esta_expirado(
    creado_en: int, ahora_unix: int, ttl_seg: int = TTL_BORRADOR_SEG
) -> bool:
    """True si el borrador venció sin ensayos suficientes."""
    return (ahora_unix - creado_en) >= ttl_seg


def debe_promover(ensayos: int) -> bool:
    """True si el borrador acumuló la repetición que lo gradúa."""
    return ensayos >= ENSAYOS_PARA_PROMOVER


def hay_cupo(activos: int) -> bool:
    """True si el buffer aún admite un borrador más."""
    return activos < CAPACIDAD_MAX
