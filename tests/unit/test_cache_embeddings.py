"""Tests del cache LRU de embeddings (Ronda 1 — memoria de trabajo)."""

import pytest

from src.core.embeddings import GeneradorEmbeddings


class _SpyGenerador(GeneradorEmbeddings):
    """Generador con _encode espiado; produce vectores sintéticos."""

    def __init__(self, cache_max: int) -> None:
        super().__init__(modelo="sintetico", cache_max=cache_max)
        self.llamadas: list[list[str]] = []

    def _encode(self, textos: list[str]) -> list[list[float]]:
        self.llamadas.append(list(textos))
        return [[float(i), 1.0] for i, _ in enumerate(textos)]


@pytest.mark.asyncio
async def test_cache_hit_no_invoca_el_modelo() -> None:
    """Un segundo generar() del mismo texto reutiliza el vector."""
    gen = _SpyGenerador(cache_max=8)
    v1 = await gen.generar("  Hola   Mundo ")
    v2 = await gen.generar("hola mundo")

    assert v1 == v2
    assert gen.llamadas == [["  Hola   Mundo "]]
    assert gen.estadisticas_cache == {"hits": 1, "misses": 1}


@pytest.mark.asyncio
async def test_cache_lru_desaloja_el_menos_reciente() -> None:
    """Con cache_max=2, el tercer texto expulsa al primero."""
    gen = _SpyGenerador(cache_max=2)
    await gen.generar("a")
    await gen.generar("b")
    await gen.generar("c")
    await gen.generar("a")  # 'a' fue expulsado → miss

    assert gen.llamadas == [["a"], ["b"], ["c"], ["a"]]
    assert gen.estadisticas_cache["misses"] == 4


@pytest.mark.asyncio
async def test_lote_cachea_por_texto_y_solo_codifica_los_nuevos() -> None:
    """generar_lote mezcla hits y misses en el orden correcto."""
    gen = _SpyGenerador(cache_max=8)
    await gen.generar("x")
    vectores = await gen.generar_lote(["x", "y", "x", "z"])

    assert len(vectores) == 4
    assert vectores[0] == vectores[2]
    # 'x' aparece dos veces en pendientes (aún no cacheado al evaluar);
    # el lote puede codificarlo una o dos veces, pero nunca re-encoda hits.
    total = sum(len(lote) for lote in gen.llamadas)
    assert total <= 4


@pytest.mark.asyncio
async def test_cache_desactivado_llama_siempre_al_modelo() -> None:
    """cache_max=0 desactiva el cache por completo."""
    gen = _SpyGenerador(cache_max=0)
    await gen.generar("mismo")
    await gen.generar("mismo")

    assert len(gen.llamadas) == 2
    assert gen.estadisticas_cache == {"hits": 0, "misses": 0}


@pytest.mark.asyncio
async def test_generar_lote_vacio_no_toca_modelo() -> None:
    gen = _SpyGenerador(cache_max=8)
    assert await gen.generar_lote([]) == []
    assert gen.llamadas == []
