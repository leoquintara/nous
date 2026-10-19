"""Tipos de memoria a largo plazo: episódica, semántica, procedimental.

La dimensión (cognitiva/somática/conductual) dice *de qué va* un evento;
el tipo de memoria dice *qué clase de recuerdo es*:

- **episódica**: eventos personales con contexto temporal/emocional
  ("ayer tuve una reunión tensa con el cliente").
- **semántica**: hechos y conocimiento sin contexto personal
  ("Python es un lenguaje interpretado").
- **procedimental**: habilidades y "cómo hacer" ("para depurar uso pdb
  con breakpoints condicionales").

Clasificación en dos vías: provider System One (pregunta ``choice``
calibrada) o heurístico léxico local como fallback.
"""

from __future__ import annotations

import logging
import re
from enum import StrEnum

from src.core.decision.provider import DecisionProvider
from src.core.decision.questions import choice


class TipoMemoria(StrEnum):
    """Tipo neurocognitivo de una memoria a largo plazo."""

    EPISODICA = "episodica"
    SEMANTICA = "semantica"
    PROCEDIMENTAL = "procedimental"


_CRITERIOS: dict[TipoMemoria, str] = {
    TipoMemoria.EPISODICA: (
        "evento personal con contexto temporal o emocional: algo que le "
        "pasó al usuario en un momento/lugar concreto"
    ),
    TipoMemoria.SEMANTICA: (
        "hecho o conocimiento abstracto sin contexto personal: "
        "definiciones, versiones, propiedades, relaciones"
    ),
    TipoMemoria.PROCEDIMENTAL: (
        "habilidad o procedimiento: cómo hacer algo, pasos, comandos, "
        "técnicas, 'sé hacer X'"
    ),
}

# Marcadores léxicos — raíces, no palabras completas (idioma-agnóstico
# en la raíz, español-inglés mixto porque los eventos son así).
_MARCADORES: dict[TipoMemoria, tuple[str, ...]] = {
    TipoMemoria.PROCEDIMENTAL: (
        "cómo ", "como hacer", "pasos para", "para depurar", "se usa ",
        "el comando", "la sintaxis", "sintaxis", "how to", "tutorial",
        "sé usar", "se usar", "procedimiento", "receta", "script para",
        "configur", "instal", "ejecuta", "lanza", "compil",
        "breakpoint", "shortcut", "atajo",
    ),
    TipoMemoria.SEMANTICA: (
        " es un", " es una", " son ", "significa", "se define",
        "definición", "propiedad", "versión", "version", "v1.", "v2.",
        "soporta", "requiere", "deprecated", "release", "la api",
        "el framework", "documentaci", "estándar", "rfc", "spec",
        "licencia", "paradigma",
    ),
}

# Episódica = marcadores temporales/personales (el default si nada pega).
_MARCADORES_EPISODICOS: tuple[str, ...] = (
    "ayer", "hoy", "esta mañana", "esta tarde", "anoche", "el lunes",
    "el martes", "el miércoles", "el jueves", "el viernes", "el sábado",
    "el domingo", "la semana pasada", "hace ", "tuve ", "fui ",
    "me reuní", "quedé", "me dijo", "hablé con", "en la reunión",
    "me pasó", "acabo de", "estuve",
)

_RE_FECHA = re.compile(r"\b\d{1,2}[/\-]\d{1,2}|\b20\d{2}\b|\bel \d+ de\b")


def clasificar_tipo_memoria_heuristico(contenido: str) -> TipoMemoria:
    """Clasifica el tipo de memoria con marcadores léxicos locales.

    Procedimental y semántica ganan por marcadores explícitos; episódica
    es el default (la memoria agéntica es primariamente episódica).

    Args:
        contenido: Texto del evento.

    Returns:
        TipoMemoria estimado por señales léxicas.
    """
    texto = contenido.lower()
    hits_proc = sum(1 for m in _MARCADORES[TipoMemoria.PROCEDIMENTAL] if m in texto)
    hits_sem = sum(1 for m in _MARCADORES[TipoMemoria.SEMANTICA] if m in texto)
    hits_epi = sum(1 for m in _MARCADORES_EPISODICOS if m in texto)
    if _RE_FECHA.search(texto):
        hits_epi += 2

    mejor = max(hits_proc, hits_sem, hits_epi)
    if mejor == 0:
        return TipoMemoria.EPISODICA
    if hits_proc == mejor:
        return TipoMemoria.PROCEDIMENTAL
    if hits_sem == mejor:
        return TipoMemoria.SEMANTICA
    return TipoMemoria.EPISODICA


async def clasificar_tipo_memoria(
    contenido: str,
    provider: DecisionProvider | None = None,
) -> TipoMemoria:
    """Clasifica el tipo de memoria; provider calibrado o heurístico.

    Args:
        contenido: Texto del evento.
        provider: DecisionProvider System One opcional — si está,
            responde una pregunta ``choice`` calibrada; si no, cae al
            heurístico léxico (nunca lanza por ausencia de provider).

    Returns:
        TipoMemoria del evento.
    """
    if provider is not None:
        try:
            resultado = await provider.predict(
                contenido,
                choice(
                    "tipo_memoria",
                    "¿Qué tipo de memoria representa este evento?",
                    {t.value: c for t, c in _CRITERIOS.items()},
                ),
            )
            respuesta = resultado.answers["tipo_memoria"]
            return TipoMemoria(str(respuesta.value))
        except (KeyError, ValueError, TypeError) as exc:
            # Provider respondió algo fuera de schema — degradar, no romper.
            logging.getLogger(__name__).warning(
                "Provider %s devolvió tipo_memoria inválido: %s",
                provider.name,
                exc,
            )
        except Exception as exc:  # noqa: BLE001 — provider no es fatal
            # Red caída, timeout, endpoint inexistente: la memoria no
            # depende del modelo de decisión.
            logging.getLogger(__name__).warning(
                "Provider %s falló clasificando tipo: %s",
                provider.name,
                exc,
            )
    return clasificar_tipo_memoria_heuristico(contenido)
