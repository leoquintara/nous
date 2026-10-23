"""Tests unitarios de ritmos: primacía de época y ventana circadiana."""

import time

import pytest

from src.core.memoria.ritmos import (
    UMBRAL_EPOCA_SEG,
    VENTANA_SUENO_HORAS,
    es_primero_de_epoca,
    ventana_optima_sueno,
)


def test_es_primero_de_epoca_tras_hueco_largo() -> None:
    """Un gap ≥ 6h inaugura época — el evento es primacia."""
    assert es_primero_de_epoca(UMBRAL_EPOCA_SEG)
    assert es_primero_de_epoca(UMBRAL_EPOCA_SEG + 1)


def test_es_primero_de_epoca_falso_en_flujo_continuo() -> None:
    """Eventos en flujo continuo no inauguran nada."""
    assert not es_primero_de_epoca(UMBRAL_EPOCA_SEG - 1)
    assert not es_primero_de_epoca(0)


def test_primer_evento_de_la_historia_es_primacia() -> None:
    """Sin previo el gap es infinito — siempre inaugura época."""
    assert es_primero_de_epoca(10**12)


def test_ventana_sin_datos_devuelve_madrugada_sin_confianza() -> None:
    """Sin actividad observada la sugerencia es inocua."""
    v = ventana_optima_sueno([])
    assert v.hora_inicio == 2
    assert v.duracion_horas == VENTANA_SUENO_HORAS
    assert v.confianza == 0.0


def test_ventana_cae_en_el_valle_de_actividad() -> None:
    """La ventana evita las horas cargadas y elige las quietas."""
    base = int(time.mktime(time.strptime("2025-01-06", "%Y-%m-%d")))
    # Actividad concentrada a las 10h durante varias semanas.
    ocupado = [
        base + i * 86400 + 10 * 3600 + j * 60
        for i in range(30)
        for j in range(10)
    ]
    v = ventana_optima_sueno(ocupado)
    horas_ventana = {
        (v.hora_inicio + h) % 24 for h in range(v.duracion_horas)
    }
    assert 10 not in horas_ventana
    assert v.confianza > 0.0


def test_ventana_cubre_medianoche_circular() -> None:
    """El valle puede cruzar 23→0 — el histograma circular lo halla."""
    base = int(time.mktime(time.strptime("2025-01-06", "%Y-%m-%d")))
    # Toda la actividad de 08 a 20: la ventana de 4h debe cubrir 23h.
    diurno = [
        base + i * 86400 + h * 3600
        for i in range(20)
        for h in range(8, 21)
    ]
    v = ventana_optima_sueno(diurno)
    assert v.hora_inicio < 8 or v.hora_inicio >= 21


def test_confianza_satura_en_500_muestras() -> None:
    """La confianza escala con la muestra hasta 1.0."""
    base = int(time.time())
    muchos = [base - i * 3600 for i in range(600)]
    assert ventana_optima_sueno(muchos).confianza == pytest.approx(1.0)
