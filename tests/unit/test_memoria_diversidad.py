"""Tests de diversidad (fan effect) y tip-of-the-tongue — Neuro-3."""

from src.core.memoria.diversidad import (
    UMBRAL_CASI,
    diversificar,
    es_casi_recuerdo,
)


def test_diversificar_penaliza_casi_duplicados() -> None:
    """Dos versiones del mismo recuerdo compiten: la segunda baja."""
    items = [
        ("a", "deploy producción viernes con docker compose", 0.9),
        ("b", "deploy producción viernes con docker compose v2", 0.85),
        ("c", "comprar leche y pan en el súper", 0.8),
    ]
    salida = diversificar(items)
    scores = dict((i, s) for i, _, s in salida)
    # El casi-duplicado pierde score; el distinto se queda igual.
    assert scores["b"] < 0.85
    assert scores["c"] == 0.8


def test_diversificar_no_penaliza_recuerdos_distintos() -> None:
    """Contenidos diferentes conservan su score original."""
    items = [
        ("a", "deploy producción viernes", 0.9),
        ("b", "comprar leche y pan", 0.85),
        ("c", "llamar al dentista mañana", 0.8),
    ]
    salida = diversificar(items)
    scores = dict((i, s) for i, _, s in salida)
    assert scores["a"] == 0.9
    assert scores["b"] == 0.85
    assert scores["c"] == 0.8


def test_diversificar_reordena_por_score_ajustado() -> None:
    """Un duplicado penalizado puede caer bajo un item más débil."""
    items = [
        ("a", "deploy producción viernes con docker", 0.9),
        ("b", "deploy producción viernes docker compose", 0.89),
        ("c", "idea completamente distinta sobre diseño", 0.5),
    ]
    salida = diversificar(items)
    # 'b' penalizado (~0.76) sigue sobre 'c' (0.5) pero perdió score.
    assert salida[0][0] == "a"
    assert salida[1][2] < 0.89


def test_es_casi_recuerdo_banda_honesta() -> None:
    """Solo bajo el umbral y con señal > 0 cuenta como 'casi'."""
    assert es_casi_recuerdo(UMBRAL_CASI - 0.01)
    assert not es_casi_recuerdo(UMBRAL_CASI)
    assert not es_casi_recuerdo(0.0)
    assert not es_casi_recuerdo(0.9)
