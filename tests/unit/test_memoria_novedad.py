"""Tests unitarios de novedad — prediction-error como saliencia."""

import math

import pytest

from src.core.memoria.novedad import (
    BOOST_SORPRESA_MAX,
    amplitud_con_sorpresa,
    sorpresa,
)


def _vec(dominante: int, n: int = 8) -> list[float]:
    """Vector one-hot con ruido cero: similitudes controladas."""
    return [1.0 if i == dominante else 0.0 for i in range(n)]


def test_sorpresa_cero_si_es_identico_a_la_trayectoria() -> None:
    """Lo idéntico a lo reciente no sorprende nada."""
    e = _vec(0)
    assert sorpresa(e, [_vec(0), _vec(0), _vec(1)]) == pytest.approx(0.0)


def test_sorpresa_alta_si_es_ortogonal_a_todo() -> None:
    """Un evento aislado conceptualmente sorprende ~1."""
    recientes = [_vec(0), _vec(1), _vec(2)]
    assert sorpresa(_vec(7), recientes) == pytest.approx(1.0)


def test_sorpresa_intermedia_con_vecinos_parciales() -> None:
    """Distancia parcial → sorpresa parcial, siempre en [0,1]."""
    # coseno(medio, e0) = 0.5 → sorpresa = 0.5.
    medio = [0.5, math.sqrt(0.75), 0, 0, 0, 0, 0, 0]
    s = sorpresa(medio, [_vec(0)])
    assert s == pytest.approx(0.5)


def test_sorpresa_sin_trayectoria_es_incerteza() -> None:
    """La primera huella no engaña: sin previos devuelve 0.5."""
    assert sorpresa(_vec(0), []) == pytest.approx(0.5)


def test_amplitud_solo_premia_nunca_penaliza() -> None:
    """Lo mundano conserva su amplitud; lo inesperado sube."""
    assert amplitud_con_sorpresa(0.5, 0.0) == 0.5
    assert amplitud_con_sorpresa(0.5, 1.0) == pytest.approx(
        0.5 * (1 + BOOST_SORPRESA_MAX)
    )


def test_amplitud_respeta_lo_trivial_declarado() -> None:
    """Multiplicativo: un 0.05 deliberado sigue siendo débil."""
    assert amplitud_con_sorpresa(0.05, 1.0) < 0.1


def test_amplitud_respeta_el_techo_uno() -> None:
    """Ni la mayor sorpresa supera amplitud 1.0."""
    assert amplitud_con_sorpresa(0.9, 1.0) == 1.0
