"""Reordenamiento local de candidatos con un cross-encoder.

A diferencia del reranker de gbrain (API externa de Voyage), Nous
reordena con un modelo que corre 100% en la máquina del usuario:
``cross-encoder/ms-marco-MiniLM-L12-v2`` (~120MB, CPU). Prefiere el
backend onnxruntime cuando está disponible (~3x más rápido).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.config import get_settings
from src.core.embeddings import activar_offline_hf
from src.core.exceptions import ProveedorNoDisponibleError

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder
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
        self._sesion_onnx: _SesionOnnx | None = None
        self._tokenizer: PreTrainedTokenizerBase | None = None
        self._entradas_onnx: set[str] = set()
        self._lock = threading.Lock()

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
        """Ejecuta la predicción con el backend activo (bloqueante)."""
        self._cargar_modelo()
        if self._sesion_onnx is not None:
            return self._predecir_onnx(pares)
        assert self._modelo is not None
        resultado = self._modelo.predict(pares, convert_to_numpy=True)
        return resultado.tolist()  # type: ignore[no-any-return]

    def _predecir_onnx(self, pares: list[list[str]]) -> list[float]:
        """Predice logits con onnxruntime sobre el lote de pares."""
        assert self._tokenizer is not None
        assert self._sesion_onnx is not None
        tokens = self._tokenizer(
            pares,
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=512,
        )
        entradas = {
            nombre: tokens[nombre]
            for nombre in self._entradas_onnx
            if nombre in tokens
        }
        salida = self._sesion_onnx.run(None, entradas)[0]
        logits: list[float] = salida.reshape(-1).tolist()
        return logits

    def precalentar(self) -> None:
        """Carga el modelo en memoria si aún no está cargado.

        Pensado para el warm-up en segundo plano al arrancar el
        servidor; es seguro llamarlo concurrentemente.
        """
        self._cargar_modelo()

    def _cargar_modelo(self) -> None:
        """Carga perezosa del backend; import pesado solo al primer uso.

        Prefiere onnxruntime cuando el modelo tiene pesos ONNX en
        caché; de lo contrario cae a sentence-transformers/torch.

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
            if self._intentar_onnx():
                return
            self._cargar_torch()

    def _intentar_onnx(self) -> bool:
        """Intenta cargar el backend ONNX; False si no es viable."""
        try:
            import onnxruntime
            from huggingface_hub import hf_hub_download
            from transformers import AutoTokenizer
        except ImportError:
            logger.info("onnxruntime no instalado: reranker en torch")
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
            logger.info("backend del reranker: onnxruntime")
            return True
        except Exception as exc:  # noqa: BLE001 — fallback controlado
            logger.info(
                "ONNX del reranker no disponible (%s): backend torch", exc
            )
            self._sesion_onnx = None
            self._tokenizer = None
            return False

    def _cargar_torch(self) -> None:
        """Carga el cross-encoder vía sentence-transformers/torch."""
        from sentence_transformers import CrossEncoder

        try:
            self._modelo = CrossEncoder(
                self._nombre_modelo, device="cpu"
            )
        except OSError as exc:
            raise ProveedorNoDisponibleError(
                f"Modelo '{self._nombre_modelo}' no está en la "
                "caché local y NOUS_HF_OFFLINE=true. Descárgalo "
                "una vez con NOUS_HF_OFFLINE=false."
            ) from exc
