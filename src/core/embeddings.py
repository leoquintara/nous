"""Generación local de embeddings con sentence-transformers.

Local-first: el modelo corre en CPU dentro de la máquina del usuario.
Ningún texto sale hacia servicios externos.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

from src.config import get_settings
from src.core.exceptions import ProveedorNoDisponibleError

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


def activar_offline_hf() -> None:
    """Fuerza el modo offline de HuggingFace cuando ``hf_offline`` es True.

    Local-first: sin esta bandera ``huggingface_hub`` consulta la red en
    cada carga de modelo (incluso estando cacheado), lo que añade ~30s de
    latencia a la primera consulta y envía tráfico a huggingface.co sin
    necesidad. Los modelos deben existir en la caché local; para la
    descarga inicial usar ``NOUS_HF_OFFLINE=false`` una sola vez.
    """
    if not get_settings().hf_offline:
        return
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class GeneradorEmbeddings:
    """Genera embeddings localmente (MiniLM-L12 por defecto, 384 dims).

    El modelo se carga de forma diferida en el primer uso para no pagar
    el costo de importar torch al arrancar el proceso.
    """

    def __init__(
        self, modelo: str | None = None, *, cache_max: int | None = None
    ) -> None:
        """Inicializa el generador sin cargar el modelo todavía.

        Args:
            modelo: Nombre del modelo de sentence-transformers.
                Por defecto ``settings.embedding_model``.
            cache_max: Entradas del cache LRU de embeddings por texto
                (None = ``settings.cache_embeddings_max``, 0 = apagado).
        """
        self._nombre_modelo = modelo or get_settings().embedding_model
        self._cache_max = (
            get_settings().cache_embeddings_max
            if cache_max is None
            else cache_max
        )
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._modelo: SentenceTransformer | None = None
        self._lock = threading.Lock()

    @property
    def estadisticas_cache(self) -> dict[str, int]:
        """Contadores de aciertos y fallos del cache (para benchmarks)."""
        return {"hits": self._hits, "misses": self._misses}

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
        if self._cache_max <= 0:
            return await asyncio.to_thread(self._encode, textos)
        vectores: list[list[float] | None] = [
            self._cache_get(t) for t in textos
        ]
        pendientes = [t for t, v in zip(textos, vectores, strict=True) if v is None]
        if pendientes:
            nuevos = await asyncio.to_thread(self._encode, pendientes)
            for texto, vector in zip(pendientes, nuevos, strict=True):
                self._cache_put(texto, vector)
            it = iter(nuevos)
            vectores = [v if v is not None else next(it) for v in vectores]
        return [v for v in vectores if v is not None]

    def _encode(self, textos: list[str]) -> list[list[float]]:
        """Codifica textos con el modelo (bloqueante, corre en thread)."""
        modelo = self._cargar_modelo()
        resultado = modelo.encode(
            textos, normalize_embeddings=True, convert_to_numpy=True
        )
        return resultado.tolist()  # type: ignore[no-any-return]

    def _cache_get(self, texto: str) -> list[float] | None:
        """Busca el embedding cacheado por clave normalizada del texto."""
        clave = " ".join(texto.lower().split())
        vector = self._cache.get(clave)
        if vector is None:
            self._misses += 1
            return None
        self._cache.move_to_end(clave)
        self._hits += 1
        return vector

    def _cache_put(self, texto: str, vector: list[float]) -> None:
        """Inserta un embedding en el cache LRU desalojando el más viejo."""
        clave = " ".join(texto.lower().split())
        self._cache[clave] = vector
        self._cache.move_to_end(clave)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    def precalentar(self) -> None:
        """Carga el modelo en memoria si aún no está cargado.

        Pensado para el warm-up en segundo plano al arrancar el
        servidor; es seguro llamarlo concurrentemente.
        """
        self._cargar_modelo()

    def _cargar_modelo(self) -> SentenceTransformer:
        """Carga perezosa del modelo; import pesado solo al primer uso.

        Raises:
            ProveedorNoDisponibleError: el modelo no está en caché y
                el modo offline de HF está activo.
        """
        if self._modelo is None:
            with self._lock:
                if self._modelo is not None:
                    return self._modelo
                activar_offline_hf()
                from sentence_transformers import SentenceTransformer

                try:
                    self._modelo = SentenceTransformer(
                        self._nombre_modelo, device="cpu"
                    )
                except OSError as exc:
                    raise ProveedorNoDisponibleError(
                        f"Modelo '{self._nombre_modelo}' no está en la "
                        "caché local y NOUS_HF_OFFLINE=true. Descárgalo "
                        "una vez con NOUS_HF_OFFLINE=false."
                    ) from exc
        return self._modelo
