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


# --- ADR-002: augment por provider System One -------------------------


def _provider_falso(
    valor: str = "somatica", probabilidad: float = 0.9
) -> AsyncMock:
    """Provider System One de mentira con una respuesta choice."""
    from src.core.decision.provider import (
        DecisionAnswer,
        DecisionResult,
        QuestionType,
    )

    provider = AsyncMock()
    provider.name = "falso"
    provider.predict.return_value = DecisionResult(
        answers={
            "dimension": DecisionAnswer(
                question_type=QuestionType.CHOICE,
                value=valor,
                probability=probabilidad,
            )
        }
    )
    return provider


async def test_provider_con_probabilidad_alta_gana() -> None:
    """Con probabilidad >= 0.5, la respuesta calibrada decide."""
    embeddings = _embeddings_falso([1.0, 0.0, 0.0])  # dice COGNITIVA
    clasificador = ClasificadorDimensiones(
        embeddings, provider=_provider_falso("somatica", 0.9)
    )

    resultado = await clasificador.clasificar("texto")

    assert resultado.dimension == Dimension.SOMATICA
    assert resultado.confianza == pytest.approx(0.9)
    assert resultado.dudoso is False


async def test_provider_debilitado_degrada_al_heuristico() -> None:
    """Probabilidad < 0.5 → gana el pipeline semántico+léxico."""
    embeddings = _embeddings_falso([1.0, 0.0, 0.0])
    clasificador = ClasificadorDimensiones(
        embeddings, provider=_provider_falso("somatica", 0.3)
    )

    resultado = await clasificador.clasificar("texto")

    assert resultado.dimension == Dimension.COGNITIVA


async def test_provider_roto_degrada_sin_romper() -> None:
    """Un provider que lanza no rompe la clasificación."""
    embeddings = _embeddings_falso([1.0, 0.0, 0.0])
    provider = AsyncMock()
    provider.name = "roto"
    provider.predict.side_effect = RuntimeError("caído")
    clasificador = ClasificadorDimensiones(embeddings, provider=provider)

    resultado = await clasificador.clasificar("texto")

    assert resultado.dimension == Dimension.COGNITIVA
