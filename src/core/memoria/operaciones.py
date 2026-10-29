"""Operaciones de memoria tipadas — verificación y reformulación.

Neuro-7: trae al recall las semánticas del verificador de
dsh-plugin-memory — la pregunta de superficie no es la operación
de memoria, y la respuesta debe declarar su propia solidez.

Lógica pura (ADR-003): sin I/O, sin reloj implícito — timestamps
y conteos entran por parámetro.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

SEGUNDOS_POR_DIA = 86_400

# Primacía de fuentes: qué tan cerca del origen está el registro.
# Capturas y dicho-directo del usuario son evidencia primaria;
# los imports derivados (memlord/gbrain) y las síntesis son
# evidencia secundaria — en una op ``provenance`` pesan menos.
_PRIMACIA_FUENTE: dict[str, float] = {
    "usuario": 1.0,
    "mcp": 1.0,
    "signal": 0.95,
    "whatsapp": 0.95,
    "telegram": 0.95,
    "devin": 0.8,
    "cli": 0.8,
    "dashboard": 0.6,
    "cron": 0.6,
    "memlord": 0.4,
    "gbrain": 0.4,
    "import": 0.35,
}
_PRIMACIA_DEFAULT = 0.5


def primacia_fuente(fuente: str | None) -> float:
    """Qué tan primaria es una fuente para la op ``provenance``.

    «¿Dónde se confirmó X?» se responde con la evidencia más
    cercana al hecho — un mensaje capturado vale más que la nota
    derivada que lo resumió.

    Args:
        fuente: Slug de la fuente registrada en el evento.

    Returns:
        Primacía en [0, 1] — alta = evidencia directa.
    """
    if not fuente:
        return _PRIMACIA_DEFAULT
    return _PRIMACIA_FUENTE.get(fuente.lower(), _PRIMACIA_DEFAULT)


# Dos candidatos cuyos timestamps difieren menos que esta ventana
# no se pueden ordenar con certeza — el empate de posición es un
# conflicto real (dsh: ``conflicts: true``). Un margen mayor
# sería ruido: dos mensajes el mismo día sí se ordenan.
EMPATE_SEGUNDOS = 60


def conflictos_empate(ganador_ts: int, otras_marcas: list[int]) -> int:
    """Cuenta candidatos empatados al ganador en posición temporal.

    El empate es real cuando los timestamps difieren menos de
    ``EMPATE_SEGUNDOS`` — típico de imports masivos que comparten
    marca o de dos registros del mismo hecho capturados a la vez.

    Args:
        ganador_ts: Timestamp del extremo coronado.
        otras_marcas: Timestamps de los demás candidatos temporales.

    Returns:
        Número de candidatos empatados con el ganador.
    """
    return sum(1 for ts in otras_marcas if abs(ts - ganador_ts) <= EMPATE_SEGUNDOS)


def certeza_op(hay_pata_temporal: bool, hay_resultados: bool, conflictos: int) -> str:
    """Certeza declarada de una op temporal.

    Args:
        hay_pata_temporal: La pata cronológica trajo evidencia.
        hay_resultados: El recall devolvió algo.
        conflictos: Candidatos empatados en posición con el ganador.

    Returns:
        ``"alta"`` | ``"media"`` | ``"baja"`` | ``"nula"``.
    """
    if not hay_resultados:
        return "nula"
    if not hay_pata_temporal:
        return "baja"
    return "media" if conflictos > 0 else "alta"


def span_duracion(primero_ts: int, ultimo_ts: int) -> dict[str, int]:
    """Span de una op ``duration``: desde el primer recuerdo al último.

    Args:
        primero_ts: Evidencia más antigua que menciona el tema.
        ultimo_ts: Evidencia más reciente que lo menciona.

    Returns:
        ``{desde_unix, hasta_unix, dias}`` — ``desde`` es cota
        inferior, no necesariamente el verdadero comienzo (la
        honestidad del verificador: es lo primero *registrado*).
    """
    return {
        "desde_unix": primero_ts,
        "hasta_unix": ultimo_ts,
        "dias": max(0, (ultimo_ts - primero_ts) // SEGUNDOS_POR_DIA),
    }


@dataclass(frozen=True, slots=True)
class VeredictoOp:
    """La op declarada en la respuesta — solidez, no solo hits.

    Attributes:
        op: Operación ejecutada (``earliest``…``provenance``).
        ganador: Evento coronado por la verificación.
        certeza: ``alta``/``media``/``baja``/``nula``.
        falsado: La sonda temporal corrigió al ranking de
            relevancia (el extremo real no encabezaba).
        conflictos: Ids de candidatos empatados en posición.
        duracion: Span ``{desde,hasta,dias}`` en op ``duration``.
        estado_en: Corte unix pedido en op ``asof``.
        fuente_ganadora: Fuente del ganador en op ``provenance``.
        reformulada: La consulta necesitó segunda pasada léxica.
    """

    op: str
    ganador: str | None = None
    certeza: str = "nula"
    falsado: bool = False
    conflictos: list[str] = field(default_factory=list)
    duracion: dict[str, int] | None = None
    estado_en: int | None = None
    fuente_ganadora: str | None = None
    reformulada: bool = False


@dataclass(frozen=True, slots=True)
class Clarificacion:
    """Sugerencia estructurada de clarificación (selectiva).

    Solo dos casos disparan (igual que en dsh): ``conflicto``
    —los registros discrepan, conviene presentar ambos— y
    ``sin_evidencia`` —target concreto sin nada que lo cubra,
    conviene pedir contexto—. Las lagunas de cobertura nunca
    disparan: el usuario no puede ayudar con lo que nadie capturó.

    Attributes:
        razon: ``"conflicto"`` o ``"sin_evidencia"``.
        accion: Qué le sugerimos al modelo (``presentar_ambos`` /
            ``preguntar_contexto``).
        pregunta: Pregunta exacta que cerraría la ambigüedad.
        opciones: Ids de los candidatos rivales (en conflicto).
    """

    razon: str
    accion: str
    pregunta: str | None = None
    opciones: list[str] = field(default_factory=list)


def sugerir_clarificacion(
    n_resultados: int,
    conflicto_rival: str | None,
    tema: str,
) -> Clarificacion | None:
    """Decide si el recall amerita una clarificación sugerida.

    Args:
        n_resultados: Hits devueltos tras la búsqueda.
        conflicto_rival: Id del candidato rival empatado al
            ganador (empate de posición o delta de score ínfimo), si hay.
        tema: El núcleo pedido — para armar la pregunta exacta.

    Returns:
        :class:`Clarificacion` o ``None`` si el resultado habla solo.
    """
    if conflicto_rival is not None:
        return Clarificacion(
            razon="conflicto",
            accion="presentar_ambos",
            pregunta=(
                f"Two equally strong candidates exist about "
                f"«{tema}» in the same period — showing both is "
                "safer than asserting."
            ),
            opciones=[conflicto_rival],
        )
    if n_resultados == 0:
        return Clarificacion(
            razon="sin_evidencia",
            accion="preguntar_contexto",
            pregunta=(
                f"No memory at all about «{tema}» — "
                "is there missing context (name, date, project)?"
            ),
        )
    return None


def es_recall_debil(n_resultados: int, top_score: float, top_n: int) -> bool:
    """Señal de recall débil que amerita reformulación interna.

    Débil = pocos candidatos o un ganador de score bajo — la
    primera pasada no cubrió el tema y la segunda (léxico
    relajado) puede rescatar lo que el léxico estricto perdió.

    Args:
        n_resultados: Candidatos tras fusión/dedupe.
        top_score: ``score_final`` del mejor candidato.
        top_n: Resultados pedidos.

    Returns:
        True si conviene la segunda pasada reformulada.
    """
    if n_resultados == 0:
        return True
    if n_resultados < 3:
        return True
    return top_score < UMBRAL_RECALL_DEBIL


UMBRAL_RECALL_DEBIL = 0.30


def _sin_tildes(token: str) -> str:
    """Colapsa diacríticos — el tsv español no los pliega."""
    return "".join(c for c in unicodedata.normalize("NFKD", token) if not unicodedata.combining(c))


def variantes_morfologicas(token: str) -> list[str]:
    """Variantes léxicas de un término para la segunda pasada.

    La reformulación determinista: el término sin tildes y un
    singular/plural ingenuo — «reunión» ↔ «reunion», «chats» ↔
    «chat». No inventa sinónimos; solo relaja la forma.

    Args:
        token: Lexema de la consulta original.

    Returns:
        Variantes distintas del token (puede ser vacía).
    """
    base = _sin_tildes(token.lower())
    variantes = {base}
    if len(base) > 4 and base.endswith("es"):
        variantes.add(base[:-2])
    elif len(base) > 3 and base.endswith("s"):
        variantes.add(base[:-1])
    elif len(base) > 3:
        variantes.add(base + "s")
    return [v for v in variantes if v != token.lower()]


def consulta_relajada(nucleo: str) -> str:
    """Query de segunda pasada: núcleo + variantes morfológicas.

    «reuniones con samay» → «samay reuniones reunion reuniones»:
    la OR de lexemas ya normalizados recupera lo que la forma
    exacta perdía. Vacía = no hay con qué reformular.

    Args:
        nucleo: Lexemas de evidencia (``consulta_nucleo``).

    Returns:
        Texto OR-ready, o "" si no había núcleo.
    """
    tokens = [t for t in nucleo.split() if len(t) > 1]
    if not tokens:
        return ""
    extra = [v for t in tokens for v in variantes_morfologicas(t) if len(v) > 1]
    return " ".join(tokens + extra)
