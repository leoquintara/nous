"""Consolidación-sueño (ADR-003, patrón 3 + mejoras OpenClaw).

Proceso periódico que simula la consolidación nocturna del cerebro:

- **Replay**: eventos de alta amplitud o muy evocados se fortalecen
  (``weight`` sube) — el hipocampo reactiva lo importante.
- **Olvido selectivo**: eventos débiles, sin acceso reciente y con baja
  retención se **archivan** (reversible — nunca se borran solos).
- **Promoción a curado** (mejora OpenClaw "save before compact"): antes
  de archivar, los eventos salientes se promueven al tier curado —
  equivalente a destilar ``memory/*.md`` en ``MEMORY.md``.
- **Accionables protegidos**: compromisos/preferencias nunca se archivan
  — afectan conducta futura, no son solo hechos.
- **Reporte auditable** (≈ DREAMS.md): cada corrida persiste un resumen
  con ids y razones — revisable por el usuario.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from src.core.memoria.decay import media_vida_dias, retencion
from src.core.memoria.tipos import TipoMemoria
from src.core.modelos import Dimension

# Umbrales de la noche de consolidación.
UMBRAL_FORTALEZCA_AMPLITUD = 0.7
UMBRAL_FORTALEZCA_REFUERZOS = 3
UMBRAL_PROMOCION_AMPLITUD = 0.85
UMBRAL_PROMOCION_REFUERZOS = 4
UMBRAL_ARCHIVO_RETENCION = 0.15
DIAS_MINIMOS_PARA_ARCHIVAR = 14
# Tipos que nunca se archivan — afectan conducta futura, no son hechos.
TIPOS_ACCIONABLES = frozenset({"compromiso", "preferencia"})


@dataclass(frozen=True, slots=True)
class EventoConsolidable:
    """Vista mínima de un evento para la corrida de consolidación."""

    id: str
    timestamp_unix: int
    dimension: Dimension
    tipo: str
    amplitud: float
    refuerzos: int
    weight: float
    tipo_memoria: TipoMemoria | None
    ultimo_acceso: int | None
    archivado: bool
    curado: bool


@dataclass(slots=True)
class ResultadoConsolidacion:
    """Plan de una corrida — la capa de persistencia lo aplica.

    Separar decisión de ejecución mantiene la lógica pura y testeable:
    el repositorio traduce estas listas a UPDATEs por lote.
    """

    fortalecer: dict[str, float] = field(default_factory=dict)
    promover: list[str] = field(default_factory=list)
    archivar: dict[str, str] = field(default_factory=dict)
    ejecutado_en: int = 0


def _factor_replay(evento: EventoConsolidable) -> float:
    """Factor de fortalecimiento según qué tan "repetida" fue la memoria."""
    base = 1.1
    if evento.amplitud >= UMBRAL_PROMOCION_AMPLITUD:
        base += 0.1
    if evento.refuerzos >= UMBRAL_FORTALEZCA_REFUERZOS:
        base += 0.05
    return base


def decidir_consolidacion(
    eventos: list[EventoConsolidable], ahora_unix: int | None = None
) -> ResultadoConsolidacion:
    """Decide qué hacer con cada evento en la corrida de sueño.

    Reglas (por orden — la primera que aplica gana):
      1. Archivado/curado/accionable → no se toca.
      2. Saliente (amplitud ≥ 0.85 o refuerzos ≥ 4) → promover a curado.
      3. Fuerte (amplitud ≥ 0.7 o refuerzos ≥ 3) → fortalecer weight.
      4. Retención < 0.15 tras ≥14 días → archivar (reversible).

    Args:
        eventos: Candidatos del workspace.
        ahora_unix: Timestamp de la corrida (default: ahora real).

    Returns:
        ResultadoConsolidacion con el plan completo.
    """
    ahora = ahora_unix if ahora_unix is not None else int(time.time())
    plan = ResultadoConsolidacion(ejecutado_en=ahora)
    for ev in eventos:
        if ev.archivado or ev.curado or ev.tipo in TIPOS_ACCIONABLES:
            continue
        if (
            ev.amplitud >= UMBRAL_PROMOCION_AMPLITUD
            or ev.refuerzos >= UMBRAL_PROMOCION_REFUERZOS
        ):
            plan.promover.append(ev.id)
            continue
        if (
            ev.amplitud >= UMBRAL_FORTALEZCA_AMPLITUD
            or ev.refuerzos >= UMBRAL_FORTALEZCA_REFUERZOS
        ):
            plan.fortalecer[ev.id] = round(
                min(ev.weight * _factor_replay(ev), 3.0), 4
            )
            continue
        dias = (ahora - ev.timestamp_unix) / 86400.0
        if dias < DIAS_MINIMOS_PARA_ARCHIVAR:
            continue
        vida = media_vida_dias(
            ev.dimension, ev.tipo_memoria, ev.amplitud, ev.refuerzos
        )
        desde = max(ev.timestamp_unix, ev.ultimo_acceso or 0)
        ret = retencion((ahora - desde) / 86400.0, vida)
        if ret < UMBRAL_ARCHIVO_RETENCION:
            plan.archivar[ev.id] = (
                f"retencion={ret:.3f} tras {dias:.0f}d "
                f"(vida={vida:.1f}d, sin acceso reciente)"
            )
    return plan


def resumen_legible(plan: ResultadoConsolidacion) -> str:
    """Resumen del reporte para MCP/dashboard — el dream diary."""
    if not (plan.fortalecer or plan.promover or plan.archivar):
        return "Consolidación sin cambios — la memoria está equilibrada."
    partes = []
    if plan.promover:
        partes.append(f"{len(plan.promover)} promovidos a curado")
    if plan.fortalecer:
        partes.append(f"{len(plan.fortalecer)} fortalecidos")
    if plan.archivar:
        partes.append(f"{len(plan.archivar)} archivados (recuperables)")
    return "Consolidación: " + ", ".join(partes) + "."


def retencion_minima_teorica() -> float:
    """El umbral más bajo posible de retención para archivar."""
    return UMBRAL_ARCHIVO_RETENCION


def es_infinita(vida: float) -> bool:
    """True si la media-vida es infinita (curado)."""
    return math.isinf(vida)
