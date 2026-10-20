"""Tests del consejero prescriptivo (ADR-002 Nivel 2)."""

from __future__ import annotations

import pytest

from src.core.decision.consejero import (
    construir_preguntas,
    recomendar,
)
from src.core.decision.provider import (
    DecisionAnswer,
    DecisionProvider,
    DecisionResult,
    QuestionType,
)


class _ProviderFalso(DecisionProvider):
    """Provider de test: responde calibrado sin red ni modelo."""

    def __init__(self, resultado: DecisionResult | None) -> None:
        self._resultado = resultado

    @property
    def name(self) -> str:
        return "falso"

    @property
    def is_local(self) -> bool:
        return True

    async def predict(self, state, questions):  # noqa: ANN001, ANN202
        if self._resultado is None:
            raise RuntimeError("provider caído")
        return self._resultado


def _resultado_opcion(opcion: str, prob: float = 0.8) -> DecisionResult:
    return DecisionResult(
        answers={
            "eleccion": DecisionAnswer(
                question_type=QuestionType.CHOICE,
                value=opcion,
                probability=prob,
                distribution={opcion: prob, "otro": 1 - prob},
            ),
            "confianza": DecisionAnswer(
                question_type=QuestionType.SCORE,
                value="alta",
                probability=0.9,
            ),
            "riesgo": DecisionAnswer(
                question_type=QuestionType.NOUL,
                value=True,
                probability=0.3,
            ),
        },
        model_routed="falso-1",
    )


def test_construir_preguntas_tres_primitivas():
    qs = construir_preguntas(["aceptar", "rechazar"])
    assert set(qs) == {"eleccion", "confianza", "riesgo"}
    assert qs["eleccion"]["type"] == "choice"
    assert set(qs["eleccion"]["criteria"]) == {"aceptar", "rechazar"}
    assert qs["confianza"]["type"] == "score"
    assert qs["riesgo"]["type"] == "noul"


@pytest.mark.asyncio
async def test_recomendar_mapea_veredicto():
    rec = await recomendar(
        _ProviderFalso(_resultado_opcion("aceptar", 0.8)),
        state="dilema: ¿aceptar?",
        opciones=["aceptar", "rechazar"],
    )
    assert rec is not None
    assert rec.opcion == "aceptar"
    assert rec.probabilidad == 0.8
    assert rec.distribucion["otro"] == pytest.approx(0.2)
    assert rec.confianza == pytest.approx(0.95)
    assert rec.riesgo_oculto == pytest.approx(0.3)
    assert rec.motor == "falso-1"


@pytest.mark.asyncio
async def test_recomendar_respuesta_vacia_devuelve_none():
    vacio = DecisionResult(answers={}, model_routed="falso-1")
    rec = await recomendar(
        _ProviderFalso(vacio), state="x", opciones=["a", "b"]
    )
    assert rec is None


@pytest.mark.asyncio
async def test_recomendar_menos_de_dos_opciones_rechaza():
    with pytest.raises(ValueError):
        await recomendar(
            _ProviderFalso(None), state="x", opciones=["una"]
        )


@pytest.mark.asyncio
async def test_recomendar_state_vacio_rechaza():
    with pytest.raises(ValueError):
        await recomendar(
            _ProviderFalso(None), state="   ", opciones=["a", "b"]
        )
