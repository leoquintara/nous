"""Dataclasses y enums del dominio de Nous."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.storage.models import EventoModel


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
    refuerzos: int = 0
    contexto: dict[str, Any] | None = None
    estado_emocional: float | None = None
    weight: float = 1.0
    archivado: bool = False
    tags: list[str] | None = None
    tipo_memoria: str | None = None
    tipo: str = "evento"
    fuente: str | None = None
    olvido_dirigido: bool = False
    cuarentena: bool = False
    debilitados: int = 0
    staged: bool = False
    parametrica: bool = False

    @classmethod
    def desde_modelo(cls, modelo: EventoModel) -> Evento:
        """Convierte un EventoModel ORM en el dataclass de dominio."""
        return cls(
            id=str(modelo.id),
            timestamp_unix=modelo.timestamp_unix,
            dimension=Dimension(modelo.dimension),
            contenido=modelo.contenido,
            amplitud=modelo.amplitud,
            refuerzos=modelo.refuerzos,
            contexto=modelo.contexto or None,
            estado_emocional=modelo.estado_emocional,
            weight=modelo.weight,
            archivado=modelo.archivado,
            tags=list(modelo.tags) if modelo.tags else None,
            tipo_memoria=modelo.tipo_memoria,
            tipo=modelo.tipo or "evento",
            fuente=modelo.fuente,
            olvido_dirigido=bool(getattr(modelo, "olvido_dirigido", False)),
            cuarentena=bool(getattr(modelo, "cuarentena", False)),
            debilitados=int(getattr(modelo, "debilitados", 0) or 0),
            staged=bool(getattr(modelo, "staged", False)),
            parametrica=bool(getattr(modelo, "parametrica", False)),
        )


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
class Trayectoria:
    """Patrón detectado en la línea de universo del usuario.

    Attributes:
        tipo: Categoría del patrón (p. ej. ``racha_somatica``).
        fecha_inicio: Timestamp Unix del primer evento del patrón.
        fecha_fin: Timestamp del último evento; None si sigue activa.
        descripcion: Resumen legible del patrón.
        eventos_ids: Ids de los eventos que la componen.
        amplitud_promedio: Media de amplitud de sus eventos.
        confianza: Fuerza del patrón en [0, 1].
        activa: True si la racha alcanza el evento más reciente.
    """

    tipo: str
    fecha_inicio: int
    fecha_fin: int | None
    descripcion: str
    eventos_ids: tuple[str, ...]
    amplitud_promedio: float
    confianza: float
    activa: bool


@dataclass(frozen=True, slots=True)
class Proyeccion:
    """Futuro probable proyectado desde la trayectoria del usuario.

    Attributes:
        horizonte_dias: Horizonte de la proyección en días.
        variable_objetivo: Qué se proyecta (p. ej. ``dimension_dominante``).
        probabilidad: Probabilidad del valor más probable (0-1).
        vector_proyectado: Distribución de probabilidad por dimensión.
        basada_en_eventos: Ids de los eventos que fundamentan la proyección.
    """

    horizonte_dias: int
    variable_objetivo: str
    probabilidad: float
    vector_proyectado: dict[str, float]
    basada_en_eventos: tuple[str, ...]


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
