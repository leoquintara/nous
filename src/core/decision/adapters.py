"""Adaptadores concretos del wire protocol ``/v1/systemone``.

Todos los modelos System One comparten el schema — la diferencia es el
transporte: SDK local (Laya), HTTP a Ollama/vLLM/LiteLLM, o la API
hosted de TypeSafe (Jev, solo con opt-in explícito).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.core.decision.provider import (
    DecisionAnswer,
    DecisionProvider,
    DecisionResult,
    QuestionType,
)

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _normalizar_respuesta(data: dict[str, Any], routed: str) -> DecisionResult:
    """Normaliza el payload JSON de /v1/systemone a DecisionResult.

    El wire devuelve ``{"answers": {nombre: {"answer": …,
    "probability": …, "distribution": …}}}`` más metadata de routing y
    tokens. Los campos ausentes caen a defaults tolerantes.
    """
    answers: dict[str, DecisionAnswer] = {}
    for nombre, raw in (data.get("answers") or {}).items():
        qtype_raw = (data.get("questions") or {}).get(nombre, {}).get(
            "type", raw.get("type", "choice")
        )
        answers[nombre] = DecisionAnswer(
            question_type=QuestionType(qtype_raw),
            value=raw.get("answer"),
            probability=float(raw.get("probability", 0.0)),
            distribution=raw.get("distribution"),
        )
    return DecisionResult(
        answers=answers,
        model_routed=str(
            (data.get("routing") or {}).get("model", routed)
        ),
        input_tokens=int(data.get("input_tokens", 0)),
        output_tokens=int(data.get("output_tokens", 0)),
    )


class LayaLocal(DecisionProvider):
    """Proveedor por defecto: Laya open-weights local (SDK ``laya``).

    33ms por forward pass, multilingüe, probabilidades calibradas por
    RLCD. Dependencia opcional — sin el paquete, el pipeline degrada a
    los heurísticos existentes.
    """

    def __init__(self, preload: bool = False) -> None:
        """Inicializa el router de Laya (descarga perezosa de pesos).

        Args:
            preload: True precarga los checkpoints en memoria.

        Raises:
            ImportError: Si el extra ``decision`` no está instalado.
        """
        try:
            from laya import Router  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "Laya no está instalado. Extra opcional: "
                "pip install 'nous-memory[decision]'"
            ) from exc
        self._router = Router(preload=preload)

    @property
    def name(self) -> str:
        return "laya-local"

    @property
    def is_local(self) -> bool:
        return True

    async def predict(
        self, state: str, questions: dict[str, dict[str, Any]]
    ) -> DecisionResult:
        """Evalúa preguntas con el router local (sync → thread)."""
        raw = await asyncio.to_thread(self._router.predict, state, questions)
        return _normalizar_respuesta(raw, routed="laya")


class HttpSystemOneProvider(DecisionProvider):
    """Base para proveedores HTTP del wire /v1/systemone.

    Cubre Ollama (Tev1/Nimbus), vLLM, LiteLLM, DigitalOcean — todos
    exponen el mismo endpoint con el mismo schema.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Configura el endpoint HTTP del proveedor.

        Args:
            endpoint: URL base del servicio (p. ej. http://localhost:11434).
            model: Identificador del modelo a enrutar.
            headers: Headers extra (p. ej. Authorization para hosted).
        """
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._headers = headers or {}

    @property
    def model(self) -> str:
        return self._model

    async def predict(
        self, state: str, questions: dict[str, dict[str, Any]]
    ) -> DecisionResult:
        """POSTea {state, questions} al endpoint /v1/systemone."""
        if not self.is_local:
            # Capa media entre bloquear y permitir: si el operador
            # optó por un proveedor externo, ningún secreto con forma
            # reconocible cruza la frontera dentro del state.
            from src.core.decision.scrub import redactar_secretos

            state = redactar_secretos(state)
        payload = {"state": state, "questions": questions, "model": self._model}
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=self._headers
        ) as http:
            resp = await http.post(f"{self._endpoint}/v1/systemone", json=payload)
            resp.raise_for_status()
            return _normalizar_respuesta(resp.json(), routed=self._model)


class OllamaLocal(HttpSystemOneProvider):
    """Tev1/Nimbus vía Ollama local (Jev-style, open weights, gratis)."""

    def __init__(
        self, model: str = "tev1:0.8b", endpoint: str = "http://localhost:11434"
    ) -> None:
        super().__init__(endpoint=endpoint, model=model)

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    @property
    def is_local(self) -> bool:
        return True


class JevCloud(HttpSystemOneProvider):
    """Jev hosted de TypeSafe — SOLO con opt-in explícito.

    ``is_local=False``: el guard de privacidad de ADR-002 lo bloquea
    salvo ``NOUS_PRIVACY_PERMITIR_EXTERNOS=true``.
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://api.typesafe.ai",
    ) -> None:
        super().__init__(
            endpoint=endpoint,
            model="jev",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @property
    def name(self) -> str:
        return "jev-cloud"

    @property
    def is_local(self) -> bool:
        return False
