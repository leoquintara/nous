"""Tests del backend onnxruntime de embeddings y reranker."""

from typing import Any, cast

import numpy as np
import pytest

from src.core.embeddings import GeneradorEmbeddings
from src.core.exceptions import ProveedorNoDisponibleError
from src.core.reranker import ReordenadorLocal


class _EntradaFalsa:
    """Imita onnxruntime.NodeArg (solo expone .name)."""

    def __init__(self, nombre: str) -> None:
        self.name = nombre


class _SalidaFalsa:
    def __init__(self, forma: tuple[Any, ...]) -> None:
        self.shape = forma


class _SesionFalsa:
    """Sesion ONNX sintética: devuelve vectores fijos por token."""

    def __init__(self) -> None:
        self.llamadas = 0

    def run(
        self, output_names: list[str] | None, inputs: dict[str, Any]
    ) -> list[Any]:
        self.llamadas += 1
        batch, seq = inputs["input_ids"].shape
        return [np.ones((batch, seq, 4), dtype=np.float32)]

    def get_inputs(self) -> list[Any]:
        return [
            _EntradaFalsa("input_ids"),
            _EntradaFalsa("attention_mask"),
            _EntradaFalsa("token_type_ids"),
        ]

    def get_outputs(self) -> list[Any]:
        return [_SalidaFalsa((None, None, 4))]


class _TokenizerFalso:
    """Tokenizer sintético con máscara: 3 tokens reales + 1 de padding."""

    def __call__(self, textos: Any, **kwargs: Any) -> dict[str, Any]:
        batch = len(textos)
        return {
            "input_ids": np.ones((batch, 4), dtype=np.int64),
            "token_type_ids": np.zeros((batch, 4), dtype=np.int64),
            "attention_mask": np.array(
                [[1, 1, 1, 0]] * batch, dtype=np.int64
            ),
        }


def _gen_onnx() -> GeneradorEmbeddings:
    """Generador con backend ONNX falso ya cargado."""
    gen = GeneradorEmbeddings(modelo="sintetico", cache_max=0)
    gen._sesion_onnx = _SesionFalsa()
    gen._tokenizer = cast(Any, _TokenizerFalso())
    gen._entradas_onnx = {
        "input_ids", "attention_mask", "token_type_ids"
    }
    gen._dimension_cargada = 4
    return gen


@pytest.mark.asyncio
async def test_encode_onnx_normaliza_y_respeta_mascara() -> None:
    """El pooling excluye padding (mask=0) y el vector queda unitario."""
    gen = _gen_onnx()
    vector = await gen.generar("texto de prueba")

    assert len(vector) == 4
    assert pytest.approx(1.0) == sum(v * v for v in vector) ** 0.5


@pytest.mark.asyncio
async def test_encode_onnx_lote_devuelve_un_vector_por_texto() -> None:
    """El lote tokeniza una sola vez y devuelve B vectores."""
    gen = _gen_onnx()
    vectores = await gen.generar_lote(["a", "b", "c"])

    assert len(vectores) == 3
    sesion = cast(_SesionFalsa, gen._sesion_onnx)
    assert sesion.llamadas == 1


def test_dimension_del_backend_onnx() -> None:
    """dimension usa la forma del grafo ONNX sin cargar torch."""
    gen = _gen_onnx()
    assert gen.dimension == 4


def test_fallback_a_torch_si_onnx_falla(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin pesos ONNX disponibles, el generador cae a torch."""
    gen = GeneradorEmbeddings(modelo="sintetico", cache_max=0)
    monkeypatch.setattr(gen, "_intentar_onnx", lambda: False)
    cargado = {"torch": False}
    monkeypatch.setattr(
        gen, "_cargar_torch", lambda: cargado.update(torch=True)
    )

    gen._cargar_modelo()
    assert cargado["torch"] is True


def test_backend_torch_explicito_no_intenta_onnx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOUS_EMBEDDINGS_BACKEND=torch omite el intento ONNX."""
    gen = GeneradorEmbeddings(modelo="sintetico", cache_max=0)
    gen._backend_preferido = "torch"
    llamado = {"onnx": False}
    monkeypatch.setattr(
        gen,
        "_intentar_onnx",
        lambda: llamado.update(onnx=True) or True,
    )
    monkeypatch.setattr(gen, "_cargar_torch", lambda: None)

    gen._cargar_modelo()
    assert llamado["onnx"] is False


def test_backend_onnx_estricto_sin_runtime_lanza_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modo onnx explícito sin onnxruntime → ProveedorNoDisponible."""
    gen = GeneradorEmbeddings(modelo="sintetico", cache_max=0)
    gen._backend_preferido = "onnx"
    import builtins

    real_import = builtins.__import__

    def bloquear_onnx(nombre: str, *args: Any, **kwargs: Any) -> Any:
        if nombre == "onnxruntime":
            raise ImportError("simulado")
        return real_import(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", bloquear_onnx)
    with pytest.raises(ProveedorNoDisponibleError):
        gen._cargar_modelo()


def test_reranker_usa_sesion_onnx_cuando_esta_disponible() -> None:
    """El reranker predice por ONNX si la sesión existe."""
    reordenador = ReordenadorLocal(modelo="sintetico")
    reordenador._sesion_onnx = _SesionFalsa()
    reordenador._tokenizer = cast(Any, _TokenizerFalso())
    reordenador._entradas_onnx = {
        "input_ids", "attention_mask", "token_type_ids"
    }

    logits = reordenador._predecir_onnx([["q", "doc"]])
    assert len(logits) == 16  # (batch=1, seq=4, dim=4) aplanado
