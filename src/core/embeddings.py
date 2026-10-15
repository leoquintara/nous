"""Generación local de embeddings con sentence-transformers.

Local-first: el modelo corre en CPU dentro de la máquina del usuario.
Ningún texto sale hacia servicios externos.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from src.config import get_settings
from src.core.exceptions import ProveedorNoDisponibleError

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer
    from transformers.tokenization_utils_base import (
        PreTrainedTokenizerBase,
    )

logger = logging.getLogger(__name__)


class _SesionOnnx(Protocol):
    """Contrato mínimo de onnxruntime.InferenceSession (sin stubs)."""

    def run(
        self, output_names: list[str] | None, inputs: dict[str, Any]
    ) -> list[Any]: ...

    def get_inputs(self) -> list[Any]: ...

    def get_outputs(self) -> list[Any]: ...


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
        self._sesion_onnx: _SesionOnnx | None = None
        self._tokenizer: PreTrainedTokenizerBase | None = None
        self._entradas_onnx: set[str] = set()
        self._dimension_cargada: int | None = None
        self._backend_preferido: Literal["auto", "onnx", "torch"] = (
            get_settings().embeddings_backend
        )
        self._lock = threading.Lock()

    @property
    def estadisticas_cache(self) -> dict[str, int]:
        """Contadores de aciertos y fallos del cache (para benchmarks)."""
        return {"hits": self._hits, "misses": self._misses}

    @property
    def dimension(self) -> int:
        """Dimensión del vector que produce el backend cargado."""
        self._cargar_modelo()
        assert self._dimension_cargada is not None
        return self._dimension_cargada

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
        """Codifica textos con el backend activo (corre en thread)."""
        self._cargar_modelo()
        if self._sesion_onnx is not None:
            return self._encode_onnx(textos)
        assert self._modelo is not None
        resultado = self._modelo.encode(
            textos, normalize_embeddings=True, convert_to_numpy=True
        )
        return resultado.tolist()  # type: ignore[no-any-return]

    def _encode_onnx(self, textos: list[str]) -> list[list[float]]:
        """Codifica con onnxruntime: tokenizer + mean-pooling + norma L2."""
        import numpy as np

        assert self._tokenizer is not None
        assert self._sesion_onnx is not None
        tokens = self._tokenizer(
            textos,
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=256,
        )
        entradas = {
            nombre: tokens[nombre]
            for nombre in self._entradas_onnx
            if nombre in tokens
        }
        salida = self._sesion_onnx.run(None, entradas)[0]
        mascara = entradas["attention_mask"][..., None].astype(np.float32)
        pooled = (salida * mascara).sum(axis=1) / np.clip(
            mascara.sum(axis=1), 1e-9, None
        )
        normas = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalizado: list[list[float]] = (
            pooled / np.clip(normas, 1e-9, None)
        ).tolist()
        return normalizado

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

    def _cargar_modelo(self) -> None:
        """Carga perezosa del backend; import pesado solo al primer uso.

        Prefiere onnxruntime (~3x más rápido en CPU) cuando está
        disponible y el modelo tiene pesos ONNX en caché; de lo
        contrario cae a sentence-transformers/torch.

        Raises:
            ProveedorNoDisponibleError: el modelo no está en caché y
                el modo offline de HF está activo.
        """
        if self._modelo is not None or self._sesion_onnx is not None:
            return
        with self._lock:
            if self._modelo is not None or self._sesion_onnx is not None:
                return
            activar_offline_hf()
            if self._backend_preferido != "torch" and self._intentar_onnx():
                return
            self._cargar_torch()

    def _intentar_onnx(self) -> bool:
        """Intenta cargar el backend ONNX; False si no es viable."""
        try:
            import onnxruntime
            from huggingface_hub import hf_hub_download
            from transformers import AutoTokenizer
        except ImportError:
            logger.info("onnxruntime no instalado: backend torch")
            if self._backend_preferido == "onnx":
                raise ProveedorNoDisponibleError(
                    "NOUS_EMBEDDINGS_BACKEND=onnx requiere "
                    "pip install onnxruntime"
                ) from None
            return False
        try:
            ruta = hf_hub_download(
                self._nombre_modelo, "onnx/model.onnx"
            )
            opciones = onnxruntime.SessionOptions()
            opciones.intra_op_num_threads = 4
            self._sesion_onnx = onnxruntime.InferenceSession(
                ruta, opciones, providers=["CPUExecutionProvider"]
            )
            auto_tokenizer: Any = AutoTokenizer
            self._tokenizer = cast(
                "PreTrainedTokenizerBase",
                auto_tokenizer.from_pretrained(self._nombre_modelo),
            )
            self._entradas_onnx = {
                entrada.name for entrada in self._sesion_onnx.get_inputs()
            }
            forma = self._sesion_onnx.get_outputs()[0].shape
            self._dimension_cargada = (
                forma[-1] if isinstance(forma[-1], int) else 384
            )
            logger.info("backend de embeddings: onnxruntime")
            return True
        except Exception as exc:  # noqa: BLE001 — fallback controlado
            if self._backend_preferido == "onnx":
                raise ProveedorNoDisponibleError(
                    f"Backend ONNX no disponible para "
                    f"'{self._nombre_modelo}': {exc}"
                ) from exc
            logger.info("ONNX no disponible (%s): backend torch", exc)
            self._sesion_onnx = None
            self._tokenizer = None
            return False

    def _cargar_torch(self) -> None:
        """Carga el modelo vía sentence-transformers/torch.

        Backend opcional: el paquete vive en el extra ``torch`` (~2GB)
        y solo se instala si se necesita un modelo sin pesos ONNX.
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ProveedorNoDisponibleError(
                "El backend torch no está instalado: "
                "pip install 'nous-memory[torch]'. "
                "El backend ONNX cubre los modelos por defecto."
            ) from exc

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
        self._dimension_cargada = (
            self._modelo.get_sentence_embedding_dimension()
        )
