"""Abstracción única sobre el wire protocol ``POST /v1/systemone``.

Los modelos System One (Laya, Tev1, Nimbus, Jev) no generan texto:
responden preguntas tipificadas ``choice``/``score``/``noul`` con
probabilidades calibradas en un solo forward pass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class QuestionType(StrEnum):
    """Primitivas tipificadas del protocolo System One."""

    CHOICE = "choice"  # elige una de K opciones nombradas (con criteria)
    SCORE = "score"  # asigna un nivel ordenado (con criteria)
    NOUL = "noul"  # sí/no → probabilidad calibrada 0.0-1.0


@dataclass(frozen=True, slots=True)
class DecisionAnswer:
    """Respuesta tipificada de un modelo System One.

    Attributes:
        question_type: Primitiva que se preguntó.
        value: ``str`` para choice, nivel para score, ``bool`` para noul.
        probability: Probabilidad calibrada de la respuesta (0-1).
        distribution: Distribución completa (choice/score) si el
            proveedor la expone.
    """

    question_type: QuestionType
    value: str | float | bool
    probability: float
    distribution: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """Resultado completo de una evaluación System One.

    Attributes:
        answers: Respuestas por nombre de pregunta.
        model_routed: Checkpoint/modelo que respondió (routers como
            Laya eligen el modelo por pregunta).
        input_tokens: Tokens de entrada consumidos (0 en local).
        output_tokens: Tokens de salida consumidos (0 en local).
    """

    answers: dict[str, DecisionAnswer]
    model_routed: str = "desconocido"
    input_tokens: int = 0
    output_tokens: int = 0


class DecisionProvider(ABC):
    """Interfaz sobre el wire protocol /v1/systemone.

    Implementaciones: ``LayaLocal`` (default), ``OllamaLocal``,
    ``JevCloud``. Todas comparten el schema choice/score/noul — un
    adaptador por transporte, no por modelo.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Identificador legible del proveedor."""
        ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True si NUNCA envía datos fuera de la máquina del usuario."""
        ...

    @abstractmethod
    async def predict(
        self, state: str, questions: dict[str, dict[str, Any]]
    ) -> DecisionResult:
        """Evalúa preguntas tipificadas sobre un estado.

        Args:
            state: Texto a evaluar (contenido de evento, contexto 4D…).
            questions: dict nombre → ``{"type", "instructions",
                "criteria"}``.

        Returns:
            DecisionResult con respuestas calibradas por pregunta.
        """
        ...


class ProveedorExternoBloqueadoError(RuntimeError):
    """Se intentó usar un proveedor que envía datos fuera sin opt-in."""


def guard_privacidad(provider: DecisionProvider, permitir_externos: bool) -> None:
    """Bloquea proveedores externos sin consentimiento explícito.

    Regla ADR-002: ``is_local=False`` solo se instancia si el usuario
    activó ``privacy.permitir_proveedores_externos`` a sabiendas.

    Args:
        provider: Proveedor a validar.
        permitir_externos: Flag de privacidad del usuario.

    Raises:
        ProveedorExternoBloqueadoError: Si el proveedor no es local y
            el usuario no dio consentimiento explícito.
    """
    if not provider.is_local and not permitir_externos:
        raise ProveedorExternoBloqueadoError(
            f"El proveedor '{provider.name}' enviaría datos de memoria "
            "fuera de la máquina. Para permitirlo: "
            "NOUS_PRIVACY_PERMITIR_EXTERNOS=true"
        )
