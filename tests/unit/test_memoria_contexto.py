"""Tests unitarios de memoria dependiente de contexto (ADR-003)."""

import pytest

from src.core.memoria.contexto import (
    ContextoActual,
    boost_contextual,
    similitud_contexto,
    similitud_estado,
)


def test_contexto_vacio_no_penaliza() -> None:
    """Sin contexto codificado la similitud es 0 — no hay boost."""
    actual = ContextoActual(proyecto="nous", hora_dia=10)
    assert similitud_contexto(None, actual) == 0.0
    assert similitud_contexto({}, actual) == 0.0


def test_mismo_proyecto_suma() -> None:
    """Mismo proyecto codificado y actual → similitud alta."""
    actual = ContextoActual(proyecto="nous")
    assert similitud_contexto({"proyecto": "nous"}, actual) == 1.0
    assert similitud_contexto({"proyecto": "ruba"}, actual) == 0.0


def test_hora_cercana_aproxima() -> None:
    """Horas cercanas (±3h) puntúan; lejanas no."""
    actual = ContextoActual(hora_dia=10)
    assert similitud_contexto({"hora_dia": 10}, actual) == 1.0
    assert 0.4 < similitud_contexto({"hora_dia": 13}, actual) < 1.0
    assert similitud_contexto({"hora_dia": 22}, actual) == 0.0


def test_estado_similar_suma() -> None:
    """Estados emocionales cercanos similitud alta."""
    assert similitud_estado(0.8, 0.7) == pytest.approx(0.9)
    assert similitud_estado(None, 0.5) == 0.0


def test_boost_con_contexto_identico_maximiza() -> None:
    """Contexto idéntico lleva el factor a 1.0 — score intacto."""
    actual = ContextoActual(proyecto="nous", estado_emocional=0.8)
    boosted = boost_contextual(
        0.9, {"proyecto": "nous"}, 0.8, actual
    )
    assert boosted == pytest.approx(0.9)


def test_boost_nunca_corta_mas_de_mitad() -> None:
    """Sin señal el score se atenúa a la mitad, nunca desaparece."""
    actual = ContextoActual(proyecto="otro")
    assert boost_contextual(1.0, {"proyecto": "x"}, None, actual) == 0.5
