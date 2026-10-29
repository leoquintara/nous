"""Tests E2E del consejero prescriptivo (ADR-002 Nivel 2).

Contra PostgreSQL real: el gate de licencia Pro y el ensamblaje del
``state`` 4D (eventos + metas + loops). El provider se falsifica —
ningún test toca modelos ni redes externas.
"""

import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings
from src.mcp import tools
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_decision"


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Redirige las tools a nous_test y fuerza licencia Pro."""
    engine = create_async_engine(TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OSError:
        await engine.dispose()
        pytest.skip("PostgreSQL de test no disponible")

    monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
    monkeypatch.setattr(
        tools, "crear_engine", lambda: create_async_engine(TEST_DB_URL)
    )
    monkeypatch.setenv("NOUS_LICENSE_KEY", "nslic_test")
    get_settings.cache_clear()
    yield engine
    get_settings.cache_clear()
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente = :f"),
            {"f": FUENTE_TEST},
        )
    await engine.dispose()


async def test_sin_provider_devuelve_contexto_ensamblado(
    motor: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con licencia pero sin modelo System One: contexto, no veredicto.

    El consejero nunca inventa una recomendación — devuelve el
    contexto 4D que ensambló y ``disponible=False``.
    """
    monkeypatch.setattr(tools, "_obtener_provider_decision", lambda: None)
    ahora = int(time.time())
    await tools.recordar_evento(
        "evalué propuestas de consultoría por margen",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora,
    )
    respuesta = await tools._consultar_decision(
        "¿acepto el proyecto de migración?", ["sí", "no"]
    )
    assert respuesta["disponible"] is False
    assert respuesta["dilema"] == "¿acepto el proyecto de migración?"
    assert respuesta["opciones"] == ["sí", "no"]
    assert respuesta["contexto"]["eventos_considerados"] >= 1
    assert "metas_activas" in respuesta["contexto"]


async def test_con_provider_falso_devuelve_veredicto(
    motor: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con provider el consejero devuelve recomendación calibrada."""
    from src.core.decision.provider import (
        DecisionAnswer,
        DecisionProvider,
        DecisionResult,
        QuestionType,
    )

    class Falso(DecisionProvider):
        @property
        def name(self) -> str:
            return "falso"

        @property
        def is_local(self) -> bool:
            return True

        async def predict(self, state, questions):  # noqa: ANN001, ANN202
            return DecisionResult(
                answers={
                    "eleccion": DecisionAnswer(
                        question_type=QuestionType.CHOICE,
                        value="aceptar",
                        probability=0.71,
                        distribution={"aceptar": 0.71, "rechazar": 0.29},
                    ),
                    "confianza": DecisionAnswer(
                        question_type=QuestionType.SCORE,
                        value="media",
                        probability=0.8,
                    ),
                    "riesgo": DecisionAnswer(
                        question_type=QuestionType.NOUL,
                        value=True,
                        probability=0.4,
                    ),
                },
                model_routed="falso-e2e",
            )

    monkeypatch.setattr(
        tools, "_obtener_provider_decision", lambda: Falso()
    )
    respuesta = await tools._consultar_decision(
        "¿acepto el proyecto?", ["aceptar", "rechazar"]
    )
    assert respuesta["disponible"] is True
    assert respuesta["recomendacion"] == "aceptar"
    assert respuesta["probabilidad"] == pytest.approx(0.71)
    assert respuesta["riesgo_oculto"] == pytest.approx(0.4)
    assert respuesta["motor"] == "falso-e2e"


async def test_opciones_invalidas_rechaza(motor: AsyncEngine) -> None:
    """Menos de 2 opciones útiles → error de validación, no veredicto."""
    with pytest.raises(Exception, match="options"):
        await tools._consultar_decision("¿X?", ["solouna"])
