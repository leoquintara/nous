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
class ResultadoBusqueda:
    """Resultado de búsqueda híbrida con scores parciales y score RRF.

    Attributes:
        evento_id: Identificador del evento encontrado.
        score_bm25: Score de la búsqueda lexical (BM25).
        score_vectorial: Score de la búsqueda semántica (embeddings).
        score_rrf: Score fusionado por Reciprocal Rank Fusion.
    """

    evento_id: str
    score_bm25: float = 0.0
    score_vectorial: float = 0.0
    score_rrf: float = 0.0
