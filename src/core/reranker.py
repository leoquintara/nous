"""Reordenamiento local de candidatos con un cross-encoder.

A diferencia del reranker de gbrain (API externa de Voyage), Nous
reordena con un modelo que corre 100% en la máquina del usuario:
``cross-encoder/ms-marco-MiniLM-L-6-v2`` (~90MB, CPU).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


class ReordenadorLocal:
    """Puntúa pares (query, documento) con un cross-encoder local.

    El modelo se carga de forma diferida en el primer uso. Los logits
    que produce se interpretan como relevancia: mayor es mejor.
    """

    def __init__(self, modelo: str | None = None) -> None:
        """Inicializa el reordenador sin cargar el modelo todavía.

        Args:
            modelo: Nombre del cross-encoder. Por defecto
                ``settings.reranker_modelo``.
        """
        self._nombre_modelo = modelo or get_settings().reranker_modelo
        self._modelo: CrossEncoder | None = None

    async def puntuar(self, query: str, textos: list[str]) -> list[float]:
        """Puntúa la relevancia de cada texto respecto al query.

        Args:
            query: Consulta original del usuario.
            textos: Candidatos a evaluar.

        Returns:
            Un logit de relevancia por texto, en el mismo orden.
        """
        if not textos:
            return []
        pares = [[query, texto] for texto in textos]
        logits = await asyncio.to_thread(self._predecir, pares)
        return [float(logit) for logit in logits]

    def _predecir(self, pares: list[list[str]]) -> list[float]:
        """Ejecuta la predicción del cross-encoder (bloqueante)."""
        modelo = self._cargar_modelo()
        resultado = modelo.predict(pares, convert_to_numpy=True)
        return resultado.tolist()  # type: ignore[no-any-return]

    def _cargar_modelo(self) -> CrossEncoder:
        """Carga perezosa del modelo; import pesado solo al primer uso."""
        if self._modelo is None:
            from sentence_transformers import CrossEncoder

            self._modelo = CrossEncoder(self._nombre_modelo, device="cpu")
        return self._modelo
