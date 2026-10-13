"""Tests unitarios del reordenador local (cross-encoder mockeado)."""

from unittest.mock import MagicMock, patch

import numpy as np

from src.core.reranker import ReordenadorLocal


def _cross_encoder_falso() -> MagicMock:
    """CrossEncoder de mentira que puntúa por longitud del texto."""
    modelo = MagicMock()
    modelo.predict.side_effect = lambda pares, **kw: np.array(
        [float(len(par[1])) for par in pares], dtype=np.float32
    )
    return modelo


async def test_puntuar_retorna_un_logit_por_texto() -> None:
    """Verifica que puntuar() retorna un logit por cada candidato."""
    reordenador = ReordenadorLocal(modelo="modelo-falso")
    with patch(
        "sentence_transformers.CrossEncoder", return_value=_cross_encoder_falso()
    ):
        logits = await reordenador.puntuar("query", ["corto", "texto largo"])

    assert len(logits) == 2
    assert logits[1] > logits[0]


async def test_puntuar_lista_vacia_no_invoca_al_modelo() -> None:
    """Verifica que una lista vacía retorna [] sin cargar el modelo."""
    reordenador = ReordenadorLocal(modelo="modelo-falso")
    with patch("sentence_transformers.CrossEncoder") as constructor:
        logits = await reordenador.puntuar("query", [])

    assert logits == []
    constructor.assert_not_called()
