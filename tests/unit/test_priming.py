"""Tests de activación difusa / priming (Ronda 4 — Collins & Loftus)."""

import uuid

from src.core.busqueda import BOOST_PRIMING, BusquedaHibrida4D, _re_scored
from src.core.embeddings import GeneradorEmbeddings
from src.core.modelos import (
    Contexto4D,
    Dimension,
    Evento,
    ResultadoBusqueda,
)
from src.storage.models import EventoModel


def _busqueda(ttl: int = 300) -> BusquedaHibrida4D:
    """Instancia sin modelo cargado (generador lazy, sin inferencia)."""
    return BusquedaHibrida4D(
        GeneradorEmbeddings(modelo="x"), priming_segundos=ttl
    )


def _evento(eid: str) -> Evento:
    return Evento(
        id=eid,
        timestamp_unix=1_800_000_000,
        dimension=Dimension.COGNITIVA,
        contenido="c",
    )


def test_primar_activa_resultados_y_vecinos_con_atenuacion() -> None:
    """Hits a fuerza 1.0; el contexto temporal queda a 0.5."""
    b = _busqueda()
    resultado = ResultadoBusqueda(
        evento_id="hit",
        contexto=Contexto4D(
            previos=(_evento("vecino1"),), siguientes=(_evento("vecino2"),)
        ),
    )
    b._primar([resultado], ahora=1000.0)

    act = b._activaciones(ahora=1001.0)
    assert act["hit"] == 1.0
    assert act["vecino1"] == 0.5
    assert act["vecino2"] == 0.5


def test_activaciones_expiran_tras_el_ttl() -> None:
    """El priming es de corto plazo: vencido el TTL no queda rastro."""
    b = _busqueda(ttl=60)
    b._primar([ResultadoBusqueda(evento_id="hit")], ahora=1000.0)

    assert b._activaciones(ahora=1061.0) == {}


def test_hit_prima_sobre_vecino_previamente_activado() -> None:
    """Ser resultado directo eleva la activación de un vecino a 1.0."""
    b = _busqueda()
    vecino = _evento("x")
    b._primar(
        [
            ResultadoBusqueda(
                evento_id="h1",
                contexto=Contexto4D(previos=(vecino,)),
            )
        ],
        ahora=1000.0,
    )
    b._primar([ResultadoBusqueda(evento_id="x")], ahora=1001.0)

    assert b._activaciones(ahora=1002.0)["x"] == 1.0


def test_priming_desactivado_no_retiene_nada() -> None:
    """priming_segundos=0 apaga el mecanismo por completo."""
    b = _busqueda(ttl=0)
    b._primar([ResultadoBusqueda(evento_id="hit")], ahora=1000.0)
    assert b._activaciones(ahora=1001.0) == {}


def test_re_scored_la_activacion_eleva_el_score() -> None:
    """Un candidato primado puntúa BOOST_PRIMING por encima de uno idéntico."""
    ahora = 1_800_000_000
    modelo = EventoModel(
        id=uuid.uuid4(),
        timestamp_unix=ahora,
        dimension="cognitiva",
        fuente="t",
        contenido="e",
        embedding=[1.0] + [0.0] * 383,
        amplitud=0.0,
        refuerzos=0,
    )
    fusionados = [ResultadoBusqueda(evento_id=str(modelo.id), score_rrf=1.0)]
    modelos = {str(modelo.id): modelo}
    vector = [1.0] + [0.0] * 383

    base = _re_scored(fusionados, modelos, vector, ahora, 30.0)[0].score_final
    primado = _re_scored(
        fusionados, modelos, vector, ahora, 30.0, {str(modelo.id): 1.0}
    )[0].score_final

    assert primado > base
    assert primado == base * (1.0 + BOOST_PRIMING)
