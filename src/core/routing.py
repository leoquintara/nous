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

# Una cola de «quién es X» que describe una RELACIÓN en vez de
# nombrar a alguien — «el dev más nuevo», «crítico para gatsby»,
# «el responsable del deploy» — pide comparar la línea de
# universo, no abrir una ficha. Señal 1: marcadores comparativos
# o temporales dentro del propio nombre extraído.
_RE_RELACIONAL = re.compile(
    r"\b(?:m[aá]s|menos|mejores?|peores?|mayor(?:es)?|menor(?:es)?|"
    r"primer[ao]?s?|segund[ao]?s?|tercer[ao]?s?|[uú]ltim[ao]s?|"
    r"reciente[ns]?|nuevo[as]?|viej[ao]s?|antiguo[as]?|"
    r"pr[oó]xim[ao]s?|anterior(?:es)?|siguiente[s]?)\b",
    re.IGNORECASE,
)

# Señal 2: la cola entera son roles o adjetivos predicados —
# «crítico», «el responsable», «dev junior». Si TODOS los
# tokens del nombre son vocabulario relacional, lo que sigue a
# «quién es» describe un criterio de filtro, no una entidad.
_ROLES_GENERICOS = frozenset(
    {
        "dev", "devs", "developer", "developers", "programador",
        "programadora", "ingeniero", "ingeniera", "arquitecto",
        "arquitecta", "diseñador", "diseñadora", "coordinador",
        "coordinadora", "qa", "tester", "lider", "líder", "jefe",
        "jefa", "dueño", "dueña", "fundador", "fundadora",
        "responsable", "encargado", "encargada", "critico", "crítico",
        "importante", "principal", "senior", "junior", "mid",
        "cliente", "prospecto", "equipo", "persona",
    }
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
    if primer in _NO_ENTIDAD:
        return ""
    # Un superlativo o comparador dentro del nombre delata una
    # pregunta relacional: «quién es el dev más nuevo» pide el
    # extremo de una serie temporal, no la ficha de «más nuevo».
    if _RE_RELACIONAL.search(limpio):
        return ""
    # Si cada token es un rol o adjetivo predicado («dev junior»,
    # «responsable») tampoco hay entidad que nombrar.
    tokens = limpio.lower().split()
    if tokens and all(t in _ROLES_GENERICOS for t in tokens):
        return ""
    return limpio


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
        r"|reciente[ns]?|nuevo[as]?)\b",
        consulta,
        re.IGNORECASE,
    ):
        return "desc"
    return "asc"


# Andamiaje interrogativo: palabras que dan FORMA a la pregunta
# pero no aportan evidencia al contenido — «primer», «cuándo»,
# «mensaje», «vez», artículos y cortesías. La pata temporal
# (extremo de la línea de universo) solo debe filtrar por los
# lexemas que nombran la evidencia («samay» en «primer mensaje
# de samay»); con el andamiaje incluido, el OR de tsquery deja
# entrar eventos viejos que mencionan «mensaje» pero no al actor
# y el verdadero extremo se diluye en la fusión.
_RE_SCAFFOLD = re.compile(
    r"\b(?:cu[aá]ndo|qu[eé]|cu[aá]l(?:es)?|qui[eé]n(?:es)?|"
    r"primer[ao]?s?|debut|[uú]ltim[ao]s?|m[aá]s|menos|mejores?|"
    r"peores?|mayor(?:es)?|menor(?:es)?|reciente[ns]?|nuevo[as]?|"
    r"viej[ao]s?|antiguo[as]?|apareci[oó]|empez[oó]|comenz[oó]|"
    r"inici[oó]|fue|era|es|son|est[áa]|hay|tiene|"
    r"mensaje[s]?|vez|fecha[s]?|d[ií]a[s]?|registro[s]?|evento[s]?|"
    r"escribi[oó]|dij[oó]|dijiste|pregunt[oó]|cont[oó]|"
    r"el|la|los|las|de|del|en|a|al|un|una|unos|unas|y|o|ni|"
    r"para|por|con|sin|sobre|entre|lo|se|su|sus|le|les|me|nos|"
    r"mi|tu|te|que|como|cuando|donde|"
    r"dime|cu[eé]ntame|sabes|decir(?:me)?)\b",
    re.IGNORECASE,
)


def consulta_nucleo(consulta: str) -> str:
    """Devuelve los lexemas de evidencia sin el andamiaje interrogativo.

    «primer mensaje de samay» → «samay»: la pata temporal ordena
    por ``timestamp_unix`` los eventos que mencionan a samay y su
    extremo ES la respuesta. Si la consulta no deja contenido
    («¿cuándo fue la última vez?») devuelve cadena vacía y el
    llamante usa la consulta original.

    Returns:
        Lexemas de contenido separados por espacio, o "" si la
        consulta era puro andamiaje.
    """
    limpia = _RE_SCAFFOLD.sub(" ", consulta)
    limpia = re.sub(r"[¿?¡!.,;:]", " ", limpia)
    tokens = [t for t in limpia.split() if len(t) > 1]
    return " ".join(tokens)
