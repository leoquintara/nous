"""Verificación pre-acción — los censores como guardrails.

Una corrección que madura a prohibición: cuando el usuario enseña
"nunca hagas X", esa regla debe pesar ANTES de actuar, no solo
aparecer en el recall cuando alguien pregunta. ``tipo='censor'`` es
la huella de esa regla; ``verificar_accion`` consulta los censores
del workspace contra la acción propuesta y devuelve un veredicto.

Lógica pura — el veredicto decide sobre los matches que la capa
MCP/REST ya buscó (texto + vector restringido a censores). Sin I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

# Umbral de similitud mínimo para que un censor se active en la
# verificación — bajo él la regla no aplica a esta acción (evita
# vetos por ruido semántico). MiniLM en español puntúa oraciones
# no relacionadas ~0.45-0.50; paráfrasis reales quedan ~0.65+.
UMBRAL_ACTIVACION = 0.55


@dataclass(frozen=True, slots=True)
class CensorCoincidente:
    """Un censor que la acción propuesta activó."""

    id: str
    contenido: str
    severidad: str
    dominio: str | None
    similitud: float


@dataclass(frozen=True, slots=True)
class Veredicto:
    """Resultado de verificar una acción contra los censores.

    ``permitido`` es False solo si algún censor de severidad
    ``bloquear`` se activó — advertir nunca bloquea (informa).
    """

    permitido: bool
    censores: tuple[CensorCoincidente, ...]
    motivo: str


def veredicto_censores(
    coincidencias: list[CensorCoincidente],
) -> Veredicto:
    """Decide el veredicto de una acción frente a los censores.

    Args:
        coincidencias: Censores activados por la acción, ya filtrados
            por similitud suficiente.

    Returns:
        Veredicto con la regla de decisión aplicada: bloquear gana
        sobre advertir; sin coincidencias la acción queda permitida.
    """
    bloqueos = [c for c in coincidencias if c.severidad == "bloquear"]
    if bloqueos:
        return Veredicto(
            permitido=False,
            censores=tuple(coincidencias),
            motivo=(
                f"{len(bloqueos)} censor(es) bloquean esta acción — "
                "reglas aprendidas, no sugerencias"
            ),
        )
    if coincidencias:
        return Veredicto(
            permitido=True,
            censores=tuple(coincidencias),
            motivo=(
                f"{len(coincidencias)} advertencia(s) — la acción "
                "puede continuar con cautela"
            ),
        )
    return Veredicto(
        permitido=True,
        censores=(),
        motivo="sin censores aplicables",
    )


def parsear_censor(contexto: dict[str, object] | None) -> tuple[str, str | None]:
    """Extrae severidad y dominio del contexto de un evento censor.

    El censor es un evento normal — sus metadatos de guardrail viven
    en ``contexto``: ``{"severidad": "bloquear"|"advertir",
    "dominio": "..."}``. Defaults tolerantes: sin severidad declarada
    el censor advierte (nunca bloquea sin haberlo pedido).
    """
    if not contexto:
        return "advertir", None
    severidad = str(contexto.get("severidad") or "advertir")
    dominio_raw = contexto.get("dominio")
    return severidad, str(dominio_raw) if dominio_raw else None
