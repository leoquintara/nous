"""Tests unitarios del generador de embeddings (modelo mockeado)."""

from unittest.mock import MagicMock, patch

import numpy as np

from src.core.embeddings import GeneradorEmbeddings

DIM_FALSA = 384


def _modelo_falso() -> MagicMock:
    """SentenceTransformer de mentira que retorna vectores de unos."""
    modelo = MagicMock()
    modelo.get_sentence_embedding_dimension.return_value = DIM_FALSA
    modelo.encode.side_effect = lambda textos, **kw: np.ones(
        (len(textos), DIM_FALSA), dtype=np.float32
    )
    return modelo


async def test_generar_retorna_vector_de_dimension_del_modelo() -> None:
    """Verifica que generar() retorna un vector de la dimensión del modelo."""
    generador = GeneradorEmbeddings(modelo="modelo-falso")
    with patch(
        "sentence_transformers.SentenceTransformer", return_value=_modelo_falso()
    ):
        vector = await generador.generar("hola nous")

    assert len(vector) == DIM_FALSA


async def test_generar_lote_retorna_un_vector_por_texto() -> None:
    """Verifica que generar_lote() preserva orden y cantidad de textos."""
    generador = GeneradorEmbeddings(modelo="modelo-falso")
    with patch(
        "sentence_transformers.SentenceTransformer", return_value=_modelo_falso()
    ):
        vectores = await generador.generar_lote(["uno", "dos", "tres"])

    assert len(vectores) == 3
    assert all(len(v) == DIM_FALSA for v in vectores)


async def test_generar_lote_vacio_no_invoca_al_modelo() -> None:
    """Verifica que una lista vacía retorna [] sin tocar el modelo."""
    generador = GeneradorEmbeddings(modelo="modelo-falso")
    with patch("sentence_transformers.SentenceTransformer") as constructor:
        vectores = await generador.generar_lote([])

    assert vectores == []
    constructor.assert_not_called()
