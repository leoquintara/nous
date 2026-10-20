"""Tests unitarios de la capa de decisión System One (ADR-002)."""

from unittest.mock import AsyncMock

import pytest

from src.core.decision.adapters import _normalizar_respuesta
from src.core.decision.engine import DecisionEngine
from src.core.decision.provider import (
    DecisionAnswer,
    DecisionProvider,
    DecisionResult,
    ProveedorExternoBloqueadoError,
    QuestionType,
    guard_privacidad,
)
from src.core.decision.questions import choice, fusionar, noul, score


def test_choice_construye_schema_del_wire() -> None:
    """La pregunta choice lleva type/instructions/criteria."""
    q = choice("dim", "¿Cuál?", {"a": "criterio a"})
    assert q["dim"]["type"] == "choice"
    assert q["dim"]["criteria"]["a"] == "criterio a"


def test_score_y_noul_construyen_schema() -> None:
    """score lleva criteria-ordenada; noul solo instrucción."""
    s = score("nivel", "¿Qué tan alto?", ["bajo", "alto"])
    assert s["nivel"]["type"] == "score"
    assert s["nivel"]["criteria"] == ["bajo", "alto"]
    n = noul("si", "¿Aplica?")
    assert n["si"]["type"] == "noul"


def test_fusionar_une_preguntas() -> None:
    """fusionar combina builders sin colisionar claves."""
    q = fusionar(choice("a", "?", {"x": "c"}), noul("b", "¿?"))
    assert set(q) == {"a", "b"}


def test_normalizar_respuesta_wire() -> None:
    """El payload del wire se traduce a DecisionResult tipado."""
    data = {
        "answers": {
            "dimension": {
                "type": "choice",
                "answer": "cognitiva",
                "probability": 0.87,
            }
        },
        "routing": {"model": "laya-421m"},
        "input_tokens": 12,
        "output_tokens": 3,
    }
    r = _normalizar_respuesta(data, "fallback")
    assert r.answers["dimension"].value == "cognitiva"
    assert r.answers["dimension"].probability == 0.87
    assert r.model_routed == "laya-421m"
    assert r.input_tokens == 12


def test_normalizar_respuesta_tolerante() -> None:
    """Campos ausentes caen a defaults sin romper."""
    r = _normalizar_respuesta({}, "m")
    assert r.answers == {}
    assert r.model_routed == "m"


class _ProviderFalso(DecisionProvider):
    """Provider de mentira local para tests del engine."""

    def __init__(self, nombre: str, local: bool = True) -> None:
        self._nombre = nombre
        self._local = local

    @property
    def name(self) -> str:
        return self._nombre

    @property
    def is_local(self) -> bool:
        return self._local

    async def predict(
        self, state: str, questions: dict[str, dict[str, object]]
    ) -> DecisionResult:
        return DecisionResult(
            answers={
                "q": DecisionAnswer(
                    question_type=QuestionType.CHOICE,
                    value="x",
                    probability=0.9,
                )
            },
            model_routed=self._nombre,
        )


def test_guard_privacidad_bloquea_externo() -> None:
    """Un provider no-local sin opt-in lanza el error de privacidad."""
    with pytest.raises(ProveedorExternoBloqueadoError):
        guard_privacidad(_ProviderFalso("cloud", local=False), False)


def test_guard_privacidad_permite_con_opt_in() -> None:
    """Con el flag activo el provider externo pasa."""
    guard_privacidad(_ProviderFalso("cloud", local=False), True)


def test_guard_privacidad_local_siempre_pasa() -> None:
    """Un provider local nunca se bloquea."""
    guard_privacidad(_ProviderFalso("local"), False)


async def test_engine_prefiere_local_por_defecto() -> None:
    """El engine elige el primer provider local registrado."""
    engine = DecisionEngine()
    engine.register(_ProviderFalso("cloud", local=False))
    engine.register(_ProviderFalso("local"))
    resultado = await engine.predict("s", {"q": {"type": "choice"}})
    assert resultado is not None
    assert resultado.model_routed == "local"


async def test_engine_sin_provider_devuelve_none() -> None:
    """Sin providers el engine degrada a None — nunca lanza."""
    assert await DecisionEngine().predict("s", {}) is None


async def test_engine_comparar_aisla_fallos() -> None:
    """Un provider que falla no tira la comparación de los demás."""
    roto = _ProviderFalso("roto")
    roto.predict = AsyncMock(side_effect=RuntimeError("caído"))
    engine = DecisionEngine()
    engine.register(roto)
    engine.register(_ProviderFalso("sano"))
    resultados = await engine.comparar("s", {})
    assert set(resultados) == {"sano"}
