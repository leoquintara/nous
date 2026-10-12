"""Generación local de embeddings con sentence-transformers.

Local-first: el modelo corre en CPU dentro de la máquina del usuario.
Ningún texto sale hacia servicios externos.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class GeneradorEmbeddings:
    """Genera embeddings localmente (MiniLM-L12 por defecto, 384 dims).

    El modelo se carga de forma diferida en el primer uso para no pagar
    el costo de importar torch al arrancar el proceso.
    """

    def __init__(self, modelo: str | None = None) -> None:
        """Inicializa el generador sin cargar el modelo todavía.

        Args:
            modelo: Nombre del modelo de sentence-transformers.
                Por defecto ``settings.embedding_model``.
        """
        self._nombre_modelo = modelo or get_settings().embedding_model
        self._modelo: SentenceTransformer | None = None

    @property
    def dimension(self) -> int:
        """Dimensión del vector que produce el modelo cargado."""
        dimension = self._cargar_modelo().get_sentence_embedding_dimension()
        assert dimension is not None
        return dimension

    async def generar(self, texto: str) -> list[float]:
        """Genera el embedding normalizado de un texto.

        Args:
            texto: Contenido a vectorizar.

        Returns:
            Vector de ``dimension`` floats (norma 1).
        """
        vectores = await self.generar_lote([texto])
        return vectores[0]

    async def generar_lote(self, textos: list[str]) -> list[list[float]]:
        """Genera embeddings normalizados para un lote de textos.

        Args:
            textos: Lista de contenidos a vectorizar.

        Returns:
            Un vector por texto de entrada, en el mismo orden.
        """
        if not textos:
            return []
        return await asyncio.to_thread(self._encode, textos)

    def _encode(self, textos: list[str]) -> list[list[float]]:
        """Codifica textos con el modelo (bloqueante, corre en thread)."""
        modelo = self._cargar_modelo()
        resultado = modelo.encode(
            textos, normalize_embeddings=True, convert_to_numpy=True
        )
        return resultado.tolist()  # type: ignore[no-any-return]

    def _cargar_modelo(self) -> SentenceTransformer:
        """Carga perezosa del modelo; import pesado solo al primer uso."""
        if self._modelo is None:
            from sentence_transformers import SentenceTransformer

            self._modelo = SentenceTransformer(self._nombre_modelo, device="cpu")
        return self._modelo
