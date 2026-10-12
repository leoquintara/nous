"""Tests de la fusión RRF de búsqueda híbrida."""

import pytest

from src.core.busqueda import fusion_rrf
from src.core.modelos import ResultadoBusqueda


def test_fusion_rrf_con_dos_listas_ordena_por_score() -> None:
    """Verifica que RRF fusiona dos listas ordenando por score descendente."""
    lista_a = [
        ResultadoBusqueda(evento_id="1", score_bm25=0.9),
        ResultadoBusqueda(evento_id="2", score_bm25=0.7),
    ]
    lista_b = [
        ResultadoBusqueda(evento_id="2", score_vectorial=0.95),
        ResultadoBusqueda(evento_id="3", score_vectorial=0.5),
    ]

    resultado = fusion_rrf(lista_a, lista_b, k=60, top_n=10)

    assert len(resultado) == 3
    assert resultado[0].evento_id == "2"
    assert resultado[0].score_rrf > resultado[-1].score_rrf


def test_fusion_rrf_respeta_top_n() -> None:
    """Verifica que fusion_rrf no retorna más de top_n resultados."""
    lista = [ResultadoBusqueda(evento_id=str(i)) for i in range(20)]

    resultado = fusion_rrf(lista, top_n=5)

    assert len(resultado) == 5


def test_fusion_rrf_con_lista_vacia_lanza_value_error() -> None:
    """Verifica que fusion_rrf rechaza listas vacías."""
    with pytest.raises(ValueError):
        fusion_rrf([], [ResultadoBusqueda(evento_id="1")])
