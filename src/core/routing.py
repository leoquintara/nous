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


_RE_ENTIDAD = re.compile(
    r"^\s*(?:[¿¡]?\s*)"
    r"(?:qui[eé]n(?:es)?\s+(?:es|son|era|fue|fueron)|"
    r"qu[eé]\s+(?:es|son|significa)|"
    r"dime\s+(?:de|sobre)|"
    r"h[áa]blame\s+de)\s+(.+?)\s*[?¿!.,;:]*\s*$",
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


def _limpiar_nombre(texto: str) -> str:
    """Extrae el nombre propio de la cola de la pregunta."""
    nombre = _RE_CORTE_NOMBRE.split(texto.strip(), maxsplit=1)[0]
    return _RE_ARTICULOS.sub("", nombre).strip()


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
