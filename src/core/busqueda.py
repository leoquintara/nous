"""Búsqueda híbrida de eventos: BM25 + vectorial + Reciprocal Rank Fusion."""

from dataclasses import replace

from src.core.modelos import ResultadoBusqueda

K_RRF_DEFAULT = 60


def fusion_rrf(
    *listas_resultados: list[ResultadoBusqueda],
    k: int = K_RRF_DEFAULT,
    top_n: int = 10,
) -> list[ResultadoBusqueda]:
    """Fusiona múltiples listas de resultados usando Reciprocal Rank Fusion.

    RRF(d) = Σ 1 / (k + rank_i(d)) donde rank_i es el ranking
    del documento d en la lista i (1-indexed).

    Args:
        *listas_resultados: Listas de resultados a fusionar.
        k: Constante de suavizado (default 60, según paper original RRF).
        top_n: Número máximo de resultados a retornar.

    Returns:
        Lista fusionada ordenada por score RRF descendente.

    Raises:
        ValueError: Si alguna lista está vacía.
    """
    if not listas_resultados or any(not lista for lista in listas_resultados):
        raise ValueError("Todas las listas de resultados deben ser no vacías")

    scores: dict[str, float] = {}
    representantes: dict[str, ResultadoBusqueda] = {}
    for lista in listas_resultados:
        for rank, resultado in enumerate(lista, start=1):
            scores[resultado.evento_id] = (
                scores.get(resultado.evento_id, 0.0) + 1.0 / (k + rank)
            )
            representantes.setdefault(resultado.evento_id, resultado)

    ordenados = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        replace(representantes[evento_id], score_rrf=score)
        for evento_id, score in ordenados[:top_n]
    ]
