"""Tests unitarios del generador de embeddings (modelo mockeado)."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.config import Settings
from src.core.embeddings import GeneradorEmbeddings, activar_offline_hf

DIM_FALSA = 384


def _modelo_falso() -> MagicMock:
    """SentenceTransformer de mentira que retorna vectores de unos."""
    modelo = MagicMock()
    modelo.get_sentence_embedding_dimension.return_value = DIM_FALSA
    modelo.encode.side_effect = lambda textos, **kw: np.ones(
        (len(textos), DIM_FALSA), dtype=np.float32
    )
    return modelo


async def test_generar_retorna_vector_de_dimension_del_modelo() -> None:
    """Verifica que generar() retorna un vector de la dimensión del modelo."""
    generador = GeneradorEmbeddings(modelo="modelo-falso")
    with patch(
        "sentence_transformers.SentenceTransformer", return_value=_modelo_falso()
    ):
        vector = await generador.generar("hola nous")

    assert len(vector) == DIM_FALSA


async def test_generar_lote_retorna_un_vector_por_texto() -> None:
    """Verifica que generar_lote() preserva orden y cantidad de textos."""
    generador = GeneradorEmbeddings(modelo="modelo-falso")
    with patch(
        "sentence_transformers.SentenceTransformer", return_value=_modelo_falso()
    ):
        vectores = await generador.generar_lote(["uno", "dos", "tres"])

    assert len(vectores) == 3
    assert all(len(v) == DIM_FALSA for v in vectores)


async def test_generar_lote_vacio_no_invoca_al_modelo() -> None:
    """Verifica que una lista vacía retorna [] sin tocar el modelo."""
    generador = GeneradorEmbeddings(modelo="modelo-falso")
    with patch("sentence_transformers.SentenceTransformer") as constructor:
        vectores = await generador.generar_lote([])

    assert vectores == []
    constructor.assert_not_called()


def _snapshot_hf(cache: Path, modelo: str) -> None:
    """Crea la estructura mínima de un modelo cacheado por HF."""
    carpeta = cache / f"models--{modelo.replace('/', '--')}" / "snapshots"
    (carpeta / "abc123").mkdir(parents=True)


def test_activar_offline_hf_sin_cache_permite_descarga_inicial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin modelos en caché no se fuerza offline (catch-22 de arranque)."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    cfg = Settings(
        hf_offline=True,
        embedding_model="org/emb",
        reranker_modelo="org/rerank",
        reranker_habilitado=True,
    )
    with patch("src.core.embeddings.get_settings", return_value=cfg):
        activar_offline_hf()

    assert "HF_HUB_OFFLINE" not in os.environ


def test_activar_offline_hf_con_cache_fuerza_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con los modelos en caché se activa el offline estricto."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot_hf(tmp_path, "org/emb")
    _snapshot_hf(tmp_path, "org/rerank")
    cfg = Settings(
        hf_offline=True,
        embedding_model="org/emb",
        reranker_modelo="org/rerank",
        reranker_habilitado=True,
    )
    with patch("src.core.embeddings.get_settings", return_value=cfg):
        activar_offline_hf()

    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_activar_offline_hf_sin_reranker_solo_chequea_embeddings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reranker deshabilitado: su modelo no impide el modo offline."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot_hf(tmp_path, "org/emb")
    cfg = Settings(
        hf_offline=True,
        embedding_model="org/emb",
        reranker_modelo="org/rerank-ausente",
        reranker_habilitado=False,
    )
    with patch("src.core.embeddings.get_settings", return_value=cfg):
        activar_offline_hf()

    assert os.environ["HF_HUB_OFFLINE"] == "1"
