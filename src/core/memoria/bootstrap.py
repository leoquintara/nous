"""Bootstrap presupuestado — el pack de arranque medido en caracteres.

``contexto_inicial`` siempre trajo el mismo volumen de memoria a cada
sesión (``top_n`` por conteo): con contextos de modelo que cuestan
tokens, el número correcto no es "cuántos eventos" sino "cuántos
caracteres caben". Este módulo decide qué secciones caben en el
presupuesto — es lógica pura: la capa MCP/REST le entrega las
secciones ya consultadas y él las empaqueta por prioridad.

Reglas de empaquetado (inspiradas en el bounded-memory de Hermes,
pero sobre el curado real del usuario):

1. Secciones en orden de prioridad — los guardrails (censores) van
   primero: vetan acciones, no son negociables.
2. Un ítem que no cabe se omite entero — nunca se corta una memoria
   a la mitad (una huella truncada es una alucinación esperando).
3. ``omitidos`` queda auditable por sección — el agente sabe qué no
   cupo y puede pedirlo con ``consultar_memoria`` si lo necesita.

El ``snapshot`` resultante es el patrón frozen-snapshot: un bloque
estable pensado para inyectarse UNA VEZ al abrir la sesión (en el
system prompt o el primer mensaje) — no muta mid-session, lo que
además preserva el prefix-cache del modelo en hosts que lo soportan.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SeccionBootstrap:
    """Una sección candidata del pack, en unidad de empaquetado.

    Attributes:
        clave: Identificador estable (``"censores"``, ``"loops"``…).
        titulo: Cabecera legible del bloque final.
        lineas: Ítems de la sección, cada uno una línea del snapshot.
    """

    clave: str
    titulo: str
    lineas: tuple[str, ...]


@dataclass(slots=True)
class PlanBootstrap:
    """Resultado del empaquetado bajo presupuesto.

    Attributes:
        secciones: Secciones que entraron, con sus líneas visibles.
        omitidos: ``clave → líneas que no cupieron`` — lo auditable.
        usado: Caracteres ocupados (header + secciones + separadores).
        presupuesto: Presupuesto total declarado.
    """

    secciones: list[SeccionBootstrap] = field(default_factory=list)
    omitidos: dict[str, int] = field(default_factory=dict)
    usado: int = 0
    presupuesto: int = 0


# Overhead de presentación por sección (título + línea en blanco).
_OVERHEAD_SECCION = 32


def empaquetar_bootstrap(
    secciones: list[SeccionBootstrap], presupuesto: int
) -> PlanBootstrap:
    """Empaqueta secciones por prioridad dentro de un presupuesto.

    Las secciones ya vienen ordenadas de mayor a menor prioridad por
    el llamador; dentro de cada una las líneas entran en su orden
    hasta agotar el presupuesto. Una sección sin espacio completo se
    recorta por líneas enteras y reporta cuántas quedaron fuera.

    Args:
        secciones: Secciones candidatas en orden de prioridad.
        presupuesto: Máximo de caracteres del bloque final (≤0 = sin
            límite — todo entra).

    Returns:
        PlanBootstrap con las líneas visibles y los omitidos por
        sección.
    """
    plan = PlanBootstrap(presupuesto=presupuesto)
    ilimitado = presupuesto <= 0
    for seccion in secciones:
        if not seccion.lineas:
            continue
        caben: list[str] = []
        for linea in seccion.lineas:
            costo = len(linea) + 1
            if not caben:
                costo += _OVERHEAD_SECCION
            if not ilimitado and plan.usado + costo > presupuesto:
                break
            caben.append(linea)
            plan.usado += costo
        if caben:
            plan.secciones.append(
                SeccionBootstrap(
                    clave=seccion.clave,
                    titulo=seccion.titulo,
                    lineas=tuple(caben),
                )
            )
        omitidas = len(seccion.lineas) - len(caben)
        if omitidas:
            plan.omitidos[seccion.clave] = omitidas
    return plan


def formatear_snapshot(plan: PlanBootstrap) -> str:
    """Renderiza el plan como bloque frozen-snapshot inyectable.

    El formato sigue el patrón de memoria acotada: cabecera con el
    medidor de uso (para que el agente sepa cuánta capacidad queda),
    secciones delimitadas y líneas planas — sin markdown pesado, el
    bloque va directo al system prompt.

    Args:
        plan: Plan ya empaquetado.

    Returns:
        Texto listo para inyectar una vez al abrir la sesión.
    """
    if plan.presupuesto > 0:
        pct = min(100, round(plan.usado / plan.presupuesto * 100))
        medidor = f"{pct}% — {plan.usado}/{plan.presupuesto} chars"
    else:
        medidor = f"{plan.usado} chars"
    lineas = [
        "═" * 46,
        f"NOUS MEMORY [{medidor}]",
        "═" * 46,
    ]
    for seccion in plan.secciones:
        lineas.append(f"── {seccion.titulo} ──")
        lineas.extend(seccion.lineas)
    if plan.omitidos:
        resto = ", ".join(
            f"{clave}: +{n}" for clave, n in plan.omitidos.items()
        )
        lineas.append(f"── (más en Nous — {resto}) ──")
    return "\n".join(lineas)
