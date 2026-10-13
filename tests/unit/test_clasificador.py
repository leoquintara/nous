"""Tests unitarios del clasificador de dimensiones (embeddings mockeados)."""

from unittest.mock import AsyncMock

import pytest

from src.core.clasificador import ClasificadorDimensiones
from src.core.modelos import Dimension


def _embeddings_falso(vector_query: list[float]) -> AsyncMock:
    """Embeddings de mentira con prototipos = vectores base ortonormales."""
    embeddings = AsyncMock()
    embeddings.generar_lote.side_effect = [
        [[1.0, 0.0, 0.0]],
        [[0.0, 1.0, 0.0]],
        [[0.0, 0.0, 1.0]],
    ]
    embeddings.generar.return_value = vector_query
    return embeddings


async def test_clasifica_la_dimension_mas_cercana() -> None:
    """Verifica que gana la dimensión con mayor similitud coseno."""
    clasificador = ClasificadorDimensiones(_embeddings_falso([0.0, 1.0, 0.0]))

    resultado = await clasificador.clasificar("cualquier texto")

    assert resultado.dimension == Dimension.SOMATICA
    assert resultado.scores[Dimension.SOMATICA] > resultado.scores[
        Dimension.COGNITIVA
    ]


async def test_confianza_es_el_margen_top1_top2() -> None:
    """Verifica que confianza = score(top1) - score(top2) tras el blend."""
    clasificador = ClasificadorDimensiones(_embeddings_falso([1.0, 0.0, 0.0]))

    resultado = await clasificador.clasificar("texto")

    assert resultado.confianza == pytest.approx(0.65, abs=1e-4)
    assert resultado.dudoso is False


async def test_dudoso_cuando_el_margen_es_bajo() -> None:
    """Verifica que un empate casi perfecto marca dudoso=True."""
    clasificador = ClasificadorDimensiones(_embeddings_falso([1.0, 1.0, 0.0]))

    resultado = await clasificador.clasificar("texto ambiguo")

    assert resultado.dudoso is True
    assert resultado.confianza < 0.02


async def test_contenido_vacio_lanza_value_error() -> None:
    """Verifica que no se clasifica contenido vacío o solo espacios."""
    clasificador = ClasificadorDimensiones(_embeddings_falso([1.0, 0.0, 0.0]))

    with pytest.raises(ValueError):
        await clasificador.clasificar("   ")


async def test_prototipos_se_embeben_una_sola_vez() -> None:
    """Verifica que los prototipos se cargan perezosamente una única vez."""
    embeddings = _embeddings_falso([1.0, 0.0, 0.0])
    clasificador = ClasificadorDimensiones(embeddings)

    await clasificador.clasificar("primero")
    await clasificador.clasificar("segundo")

    assert embeddings.generar_lote.call_count == 3
