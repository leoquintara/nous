"""Dataclasses y enums del dominio de Nous."""

from dataclasses import dataclass
from enum import StrEnum


class Dimension(StrEnum):
    """Dimensión de un evento en la línea de universo."""

    COGNITIVA = "cognitiva"
    SOMATICA = "somatica"
    CONDUCTUAL = "conductual"


@dataclass(frozen=True, slots=True)
class Evento:
    """Evento atómico en la línea de universo 4D del usuario.

    Attributes:
        id: Identificador único del evento.
        timestamp_unix: Marca temporal en formato Unix epoch.
        dimension: Dimensión a la que pertenece el evento.
        contenido: Texto del evento (nunca se loggea).
        amplitud: Intensidad del evento en [0.0, 1.0].
    """

    id: str
    timestamp_unix: int
    dimension: Dimension
    contenido: str
    amplitud: float = 0.5


@dataclass(frozen=True, slots=True)
class ResultadoClasificacion:
    """Resultado de clasificar un evento en una dimensión.

    Attributes:
        dimension: Dimensión ganadora por similitud con prototipos.
        confianza: Margen entre la mejor y segunda similitud (0-1).
        scores: Similitud coseno del contenido con cada dimensión.
        dudoso: True si el margen quedó bajo el umbral de confianza.
    """

    dimension: Dimension
    confianza: float
    scores: dict[Dimension, float]
    dudoso: bool


@dataclass(frozen=True, slots=True)
class Contexto4D:
    """Contexto temporal alrededor de un evento en la línea de universo.

    Attributes:
        previos: Eventos anteriores, del más cercano al más lejano.
        siguientes: Eventos posteriores, del más cercano al más lejano.
    """

    previos: tuple[Evento, ...] = ()
    siguientes: tuple[Evento, ...] = ()


@dataclass(frozen=True, slots=True)
class ResultadoBusqueda:
    """Resultado de búsqueda híbrida con scores parciales y contexto 4D.

    Attributes:
        evento_id: Identificador del evento encontrado.
        score_bm25: Score de la búsqueda lexical (ts_rank).
        score_vectorial: Similitud coseno de la búsqueda semántica.
        score_rrf: Score fusionado por Reciprocal Rank Fusion.
        score_final: Score tras re-score coseno y boost por amplitud.
        evento: Evento completo (poblado tras la fusión).
        contexto: Vecindad temporal 4D (poblada en la contextualización).
    """

    evento_id: str
    score_bm25: float = 0.0
    score_vectorial: float = 0.0
    score_rrf: float = 0.0
    score_final: float = 0.0
    evento: Evento | None = None
    contexto: Contexto4D | None = None
