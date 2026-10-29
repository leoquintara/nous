"""Tests de la fusión RRF de búsqueda híbrida."""

import uuid

import pytest

from src.core.busqueda import (
    BOOST_TEMPORAL,
    BusquedaHibrida4D,
    _re_scored,
    fusion_rrf,
)
from src.core.embeddings import GeneradorEmbeddings
from src.core.modelos import ResultadoBusqueda
from src.storage.models import EventoModel


def test_fusion_rrf_con_dos_listas_ordena_por_score() -> None:
    """Verifica que RRF fusiona dos listas ordenando por score descendente."""
    lista_a = [
        ResultadoBusqueda(evento_id="1", score_bm25=0.9),
        ResultadoBusqueda(evento_id="2", score_bm25=0.7),
    ]
    lista_b = [
        ResultadoBusqueda(evento_id="2", score_vectorial=0.95),
        ResultadoBusqueda(evento_id="3", score_vectorial=0.5),
    ]

    resultado = fusion_rrf(lista_a, lista_b, k=60, top_n=10)

    assert len(resultado) == 3
    assert resultado[0].evento_id == "2"
    assert resultado[0].score_rrf > resultado[-1].score_rrf


def test_fusion_rrf_respeta_top_n() -> None:
    """Verifica que fusion_rrf no retorna más de top_n resultados."""
    lista = [ResultadoBusqueda(evento_id=str(i)) for i in range(20)]

    resultado = fusion_rrf(lista, top_n=5)

    assert len(resultado) == 5


def test_fusion_rrf_con_lista_vacia_lanza_value_error() -> None:
    """Verifica que fusion_rrf rechaza listas vacías."""
    with pytest.raises(ValueError):
        fusion_rrf([], [ResultadoBusqueda(evento_id="1")])


def test_fusion_rrf_ponderada_prioriza_lista_con_mayor_peso() -> None:
    """Verifica que los pesos inclinan el ranking hacia la lista favorecida."""
    lista_lexica = [
        ResultadoBusqueda(evento_id="a"),
        ResultadoBusqueda(evento_id="b"),
    ]
    lista_vectorial = [
        ResultadoBusqueda(evento_id="b"),
        ResultadoBusqueda(evento_id="c"),
    ]

    resultado = fusion_rrf(lista_lexica, lista_vectorial, pesos=[1.0, 3.0])

    assert resultado[0].evento_id == "b"
    assert resultado[1].evento_id == "c"


def test_fusion_rrf_pesos_desalineados_lanza_value_error() -> None:
    """Verifica que se rechaza un peso que no corresponde a una lista."""
    with pytest.raises(ValueError):
        fusion_rrf([ResultadoBusqueda(evento_id="1")], pesos=[1.0, 2.0])


# -- Pata temporal: extremo de la línea de universo ----------------


def _modelo(contenido: str, timestamp: int) -> EventoModel:
    return EventoModel(
        id=uuid.uuid4(),
        timestamp_unix=timestamp,
        dimension="cognitiva",
        fuente="t",
        contenido=contenido,
        embedding=[1.0] + [0.0] * 383,
        amplitud=0.0,
        refuerzos=0,
    )


class _RepoFalso:
    """Repo mínimo: adyacentes vacíos para el ancla temporal."""

    async def adyacentes_lote(
        self, marcas: list[int], cantidad: int
    ) -> dict[int, tuple[list, list]]:
        return {i: ([], []) for i in range(len(marcas))}


def test_re_scored_el_extremo_temporal_suma_boost() -> None:
    """El extremo pedido recibe BOOST_TEMPORAL aditivo sobre el blend."""
    ahora = 1_800_000_000
    viejo = _modelo("primer samay", ahora - 100_000_000)
    nuevo = _modelo("samay reciente", ahora - 100)
    fusionados = [
        ResultadoBusqueda(evento_id=str(nuevo.id), score_rrf=1.0),
        ResultadoBusqueda(evento_id=str(viejo.id), score_rrf=0.0),
    ]
    modelos = {str(viejo.id): viejo, str(nuevo.id): nuevo}
    vector = [1.0] + [0.0] * 383

    sin = _re_scored(fusionados, modelos, vector, ahora, 30.0)
    base_viejo = next(
        r for r in sin if r.evento_id == str(viejo.id)
    ).score_final
    con = _re_scored(
        fusionados,
        modelos,
        vector,
        ahora,
        30.0,
        temporal_scores={str(viejo.id): 1.0},
    )
    viejo_res = next(r for r in con if r.evento_id == str(viejo.id))

    assert viejo_res.score_final == pytest.approx(
        base_viejo + BOOST_TEMPORAL
    )


@pytest.mark.asyncio
async def test_anclar_extremo_reinserta_al_frente() -> None:
    """El extremo temporal corona aunque el rerank lo haya degradado."""
    b = BusquedaHibrida4D(GeneradorEmbeddings(modelo="x"))
    viejo = _modelo("primero", 1_700_000_000)
    hits = [(viejo, 1.0)]
    resultados = [ResultadoBusqueda(evento_id=str(viejo.id), score_final=0.3)]
    finales = [ResultadoBusqueda(evento_id="otro", score_final=0.8)]

    salida = await b._anclar_extremo(
        _RepoFalso(),  # type: ignore[arg-type]
        "asc",
        hits,
        resultados=resultados,
        finales=finales,
        top_n=5,
    )

    assert salida[0].evento_id == str(viejo.id)
    assert salida[0].score_final > finales[0].score_final
    assert len(salida) == 2


@pytest.mark.asyncio
async def test_anclar_extremo_no_aplica_sin_orden_temporal() -> None:
    """Sin «primero/último» la lista final se devuelve intacta."""
    b = BusquedaHibrida4D(GeneradorEmbeddings(modelo="x"))
    viejo = _modelo("primero", 1_700_000_000)
    finales = [ResultadoBusqueda(evento_id="otro", score_final=0.8)]

    salida = await b._anclar_extremo(
        _RepoFalso(),  # type: ignore[arg-type]
        None,
        [(viejo, 1.0)],
        resultados=[],
        finales=finales,
        top_n=5,
    )

    assert salida == finales


@pytest.mark.asyncio
async def test_anclar_extremo_no_duplica_si_ya_corona() -> None:
    """Si el extremo ya encabeza el resultado, no se reinserta."""
    b = BusquedaHibrida4D(GeneradorEmbeddings(modelo="x"))
    viejo = _modelo("primero", 1_700_000_000)
    finales = [ResultadoBusqueda(evento_id=str(viejo.id), score_final=0.9)]

    salida = await b._anclar_extremo(
        _RepoFalso(),  # type: ignore[arg-type]
        "asc",
        [(viejo, 1.0)],
        resultados=finales,
        finales=finales,
        top_n=5,
    )

    assert salida == finales
