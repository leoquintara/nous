"""Semanticización — durante el sueño, los episodios se vuelven ideas.

La memoria humana no guarda cada episodio literal: destila el gist —
"los lunes deployo de mañana" nace de muchos lunes concretos. En la
corrida de consolidación, los clusters de episodios semánticamente
próximos generan un evento ``semantica`` nuevo (fuente ``sueno``)
vinculado a sus miembros por aristas ``gist``.

La abstracción es **extractiva** por defecto — sin LLM: el medoide
del cluster (el episodio más cercano al centroide) hace de
representante. Los episodios originales quedan intactos — el gist
no borra la evidencia que lo sostiene.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# Similitud mínima para agrupar episodios en un mismo gist.
UMBRAL_CLUSTER = 0.82
# Un gist exige al menos N episodios — 2 es anécdota, 3 es patrón.
MIN_CLUSTER = 3
# Máximo de gists por corrida de sueño — abstraer es trabajo, no spam.
GISTS_MAX_POR_CORRIDA = 5


@dataclass(frozen=True, slots=True)
class EpisodioGist:
    """Vista mínima de un episodio candidato a semanticizarse."""

    id: str
    contenido: str
    weight: float
    embedding: Sequence[float] | None


@dataclass(slots=True)
class PlanGist:
    """Un evento semántico propuesto y los episodios que lo sostienen."""

    contenido: str
    miembros: list[str] = field(default_factory=list)


def _coseno(a: Sequence[float], b: Sequence[float]) -> float:
    """Similitud coseno entre vectores normalizados."""
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


def _centroide(vectores: list[Sequence[float]]) -> list[float]:
    """Centroide de un grupo de vectores (promedio por dimensión)."""
    n = len(vectores[0])
    return [sum(v[d] for v in vectores) / len(vectores) for d in range(n)]


def _medoide(
    cluster: list[EpisodioGist], centroide: Sequence[float]
) -> EpisodioGist:
    """El episodio más cercano al centroide — la esencia del grupo."""
    assert all(e.embedding is not None for e in cluster)
    return max(
        cluster, key=lambda e: _coseno(e.embedding or (), centroide)
    )


def agrupar_episodios(
    episodios: list[EpisodioGist], umbral: float = UMBRAL_CLUSTER
) -> list[list[EpisodioGist]]:
    """Clusters voraces por similitud de embedding (≥ umbral).

    Cada episodio entra en el primer cluster cuyo semilla supera el
    umbral; si no cabe en ninguno, semilla uno nuevo. Greedy y
    determinista — suficiente para destilar gists sin k-means.

    Returns:
        Clusters con al menos un miembro (los pequeños se filtran
        fuera en :func:`plan_gists`).
    """
    elegibles = [e for e in episodios if e.embedding]
    clusters: list[list[EpisodioGist]] = []
    for e in elegibles:
        for cluster in clusters:
            semilla = cluster[0].embedding or ()
            if _coseno(e.embedding or (), semilla) >= umbral:
                cluster.append(e)
                break
        else:
            clusters.append([e])
    return clusters


def plan_gists(
    episodios: list[EpisodioGist],
    umbral: float = UMBRAL_CLUSTER,
    min_cluster: int = MIN_CLUSTER,
    max_gists: int = GISTS_MAX_POR_CORRIDA,
) -> list[PlanGist]:
    """Propone los gists semánticos de la corrida de sueño.

    Solo clusters de ``min_cluster``+ miembros: el contenido del gist
    es el medoide (el episodio más representativo) — abstracción
    extractiva honesta, sin inventar texto que el usuario no dijo.

    Returns:
        Hasta ``max_gists`` planes, del cluster más grande al menor.
    """
    clusters = [
        c for c in agrupar_episodios(episodios, umbral) if len(c) >= min_cluster
    ]
    clusters.sort(key=len, reverse=True)
    planes = []
    for cluster in clusters[:max_gists]:
        centro = _centroide([e.embedding or () for e in cluster])
        medoide = _medoide(cluster, centro)
        planes.append(
            PlanGist(
                contenido=(
                    f"Gist de {len(cluster)} episodios: "
                    f"{medoide.contenido}"
                ),
                miembros=[e.id for e in cluster],
            )
        )
    return planes
