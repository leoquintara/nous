"""Clasificador de intención de consulta — puro, sin I/O (ADR-003).

Tres intenciones que cambian el camino de recuperación:

- ``entidad``: «quién es oscar», «qué es kinam» → ficha de entidad
  (lookup de título + grafo + span), no ranking difuso.
- ``temporal``: «cuándo», «primero», «más reciente» → pata ordenada
  por ``timestamp_unix`` fusionada con el RAG normal — la pregunta
  pide posición en la línea de universo, no solo relevancia.
- ``tema``: todo lo demás → pipeline híbrido estándar.
"""

import re
from enum import StrEnum


class IntencionConsulta(StrEnum):
    """Intención detectada de una consulta en lenguaje natural."""

    ENTIDAD = "entidad"
    TEMPORAL = "temporal"
    TEMA = "tema"


# Las formas «quién es X» admiten un prefijo de cortesía
# («dime», «sabes», «cuéntame», «me puedes decir») — la
# ancla ^ sigue protegiendo oraciones donde «qué es» va
# embebido («no sé qué es lo que falla» → tema).
_RE_ENTIDAD = re.compile(
    r"^\s*(?:[¿¡]?\s*)"
    r"(?:"
    r"(?:(?:dime|cu[eé]ntame|sabes|me\s+puedes?\s+decir(?:me)?|"
    r"me\s+interesa|quiero\s+saber)\s+)?"
    r"qui[eé]n(?:es)?\s+(?:es|son|era|fue|fueron)"
    r"|qu[eé]\s+(?:es|son|significa)"
    r"|cu[aá]l\s+es\s+(?:el|la)\s+(?:rol|trabajo|puesto|funci[oó]n)"
    r"\s+(?:de|del|de\s+(?:el|la|los|las))"
    r"|(?:dime|cu[eé]ntame|h[áa]blame)\s+(?:de|sobre|del)"
    r")\s+(.+?)\s*[?¿!.,;:]*\s*$",
    re.IGNORECASE,
)

_RE_TEMPORAL = re.compile(
    r"\b(?:cu[aá]ndo|primer[ao]?s?|debut|[uú]ltim[ao]s?|"
    r"m[aá]s\s+(?:reciente[ns]?|nuevo[as]?|viejo[as]?|antiguo[as]?)"
    r"|apareci[oó]|empez[oó]|comenz[oó]|inici[oó])\b",
    re.IGNORECASE,
)

# Corta el nombre propio en la primera conjunción/preposición:
# «oscar y que hace» → «oscar».
_RE_CORTE_NOMBRE = re.compile(
    r"\s+(?:y|que|de|del|en|para|con|el|la|los|las)\s+", re.IGNORECASE
)

_RE_ARTICULOS = re.compile(
    r"^(?:el|la|los|las|un|una|unos|unas|del|de)\s+", re.IGNORECASE
)

# Si la cola de «qué es X» no es un nombre sino un pronombre
# («qué es eso», «qué es lo que falla»), no hay entidad que
# buscar — la consulta es de tema.
_NO_ENTIDAD = frozenset(
    {"lo", "eso", "esto", "ello", "algo", "aquello", "tal", "nada"}
)


def _limpiar_nombre(texto: str) -> str:
    """Extrae el nombre propio de la cola de la pregunta."""
    nombre = _RE_CORTE_NOMBRE.split(texto.strip(), maxsplit=1)[0]
    limpio = _RE_ARTICULOS.sub("", nombre).strip()
    # El nombre no puede EMPEZAR por pronombre: «lo mas delicado»,
    # «eso del sync» no son entidades aunque traigan más texto.
    if limpio.lower() in _NO_ENTIDAD:
        return ""
    primer = limpio.split(maxsplit=1)[0].lower() if limpio else ""
    return "" if primer in _NO_ENTIDAD else limpio


def clasificar_intencion(consulta: str) -> tuple[IntencionConsulta, str | None]:
    """Clasifica la consulta y extrae el nombre si es de entidad.

    Args:
        consulta: Pregunta en lenguaje natural.

    Returns:
        (intención, nombre extraído o None). El nombre solo viene
        cuando la intención es ``entidad`` — para temporal/tema la
        consulta entera alimenta las patas normales.
    """
    if not consulta.strip():
        return IntencionConsulta.TEMA, None
    match = _RE_ENTIDAD.match(consulta)
    if match:
        nombre = _limpiar_nombre(match.group(1))
        if nombre:
            return IntencionConsulta.ENTIDAD, nombre
    if _RE_TEMPORAL.search(consulta):
        return IntencionConsulta.TEMPORAL, None
    return IntencionConsulta.TEMA, None


def direccion_temporal(consulta: str) -> str:
    """Sentido del orden temporal que pide la consulta.

    «primero», «cuándo», «apareció» preguntan por el inicio de la
    línea (ascendente); «último», «más reciente», «nuevo» por el
    extremo (descendente).

    Returns:
        ``"asc"`` o ``"desc"``.
    """
    if re.search(
        r"\b(?:[uú]ltim[ao]s?|m[aá]s\s+(?:reciente[ns]?|nuevo[as]?)"
        r"|reciente[ns]?)\b",
        consulta,
        re.IGNORECASE,
    ):
        return "desc"
    return "asc"
