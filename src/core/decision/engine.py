"""Orquestador de proveedores System One.

El engine permite consultar varios proveedores y comparar sus
respuestas — útil para validar un modelo local contra otro o buscar
consenso antes de una decisión importante.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.decision.provider import (
    DecisionProvider,
    DecisionResult,
)


class DecisionEngine:
    """Registro y orquestación de DecisionProviders."""

    def __init__(self) -> None:
        self._providers: dict[str, DecisionProvider] = {}

    def register(self, provider: DecisionProvider) -> None:
        """Registra un proveedor bajo su ``name``."""
        self._providers[provider.name] = provider

    def get(self, name: str) -> DecisionProvider | None:
        """Devuelve el proveedor registrado o None."""
        return self._providers.get(name)

    def por_defecto(self) -> DecisionProvider | None:
        """Primer proveedor local registrado (preferencia local-first)."""
        for p in self._providers.values():
            if p.is_local:
                return p
        return next(iter(self._providers.values()), None)

    async def predict(
        self,
        state: str,
        questions: dict[str, dict[str, Any]],
        provider: str | None = None,
    ) -> DecisionResult | None:
        """Evalúa con un proveedor concreto o el de defecto.

        Args:
            state: Texto a evaluar.
            questions: Preguntas tipificadas.
            provider: Nombre del proveedor; None → el de defecto.

        Returns:
            DecisionResult o None si no hay proveedor disponible.
        """
        prov = (
            self._providers[provider] if provider else self.por_defecto()
        )
        if prov is None:
            return None
        return await prov.predict(state, questions)

    async def comparar(
        self, state: str, questions: dict[str, dict[str, Any]]
    ) -> dict[str, DecisionResult]:
        """Consulta todos los proveedores en paralelo y compara.

        Returns:
            ``{nombre_proveedor: DecisionResult}`` — proveedores que
            fallaron quedan fuera del dict (aislamiento de fallos).
        """
        resultados = await asyncio.gather(
            *(p.predict(state, questions) for p in self._providers.values()),
            return_exceptions=True,
        )
        return {
            nombre: r
            for nombre, r in zip(
                self._providers.keys(), resultados, strict=True
            )
            if isinstance(r, DecisionResult)
        }
