"""Tests de detección de trayectorias y proyección temporal."""

import pytest

from src.core.modelos import Dimension, Evento
from src.core.temporal import detectar_trayectorias, proyectar

AHORA = 1_800_000_000
DIA = 86_400


def _e(i: int, dim: Dimension, ts: int, amplitud: float = 0.5) -> Evento:
    """Factory de eventos de dominio para tests."""
    return Evento(
        id=str(i), timestamp_unix=ts, dimension=dim,
        contenido=f"evento {i}", amplitud=amplitud,
    )


def test_detecta_racha_consecutiva_de_la_misma_dimension() -> None:
    """Verifica que 4 eventos somáticos seguidos forman una racha."""
    eventos = [_e(i, Dimension.SOMATICA, 100 + i) for i in range(4)]
    eventos.append(_e(9, Dimension.COGNITIVA, 200))

    trayectorias = detectar_trayectorias(eventos)

    assert len(trayectorias) == 1
    assert trayectorias[0].tipo == "racha_somatica"
    assert trayectorias[0].fecha_fin == 103
    assert trayectorias[0].activa is False


def test_racha_es_activa_si_alcanza_el_ultimo_evento() -> None:
    """Verifica que una racha abierta queda marcada activa y sin fin."""
    eventos = [_e(0, Dimension.COGNITIVA, 1)] + [
        _e(i, Dimension.SOMATICA, 100 + i) for i in range(1, 4)
    ]

    trayectorias = detectar_trayectorias(eventos)

    assert trayectorias[0].activa is True
    assert trayectorias[0].fecha_fin is None


def test_racha_mas_corta_que_el_minimo_no_cuenta() -> None:
    """Verifica que 2 eventos seguidos no forman trayectoria."""
    eventos = [_e(i, Dimension.SOMATICA, i) for i in range(2)]
    assert detectar_trayectorias(eventos) == []


def test_proyectar_distribucion_suma_uno() -> None:
    """Verifica que el vector proyectado es una distribución válida."""
    eventos = [
        _e(i, Dimension.COGNITIVA if i % 2 else Dimension.CONDUCTUAL,
           AHORA - i * DIA)
        for i in range(10)
    ]

    proyeccion = proyectar(eventos, ahora=AHORA)

    assert sum(proyeccion.vector_proyectado.values()) == pytest.approx(
        1.0, abs=1e-3
    )


def test_proyectar_favorece_la_dimension_reciente() -> None:
    """Verifica que el decaimiento por recencia prioriza lo nuevo."""
    eventos = [
        _e(i, Dimension.COGNITIVA, AHORA - (20 + i) * DIA) for i in range(5)
    ] + [_e(10 + i, Dimension.SOMATICA, AHORA - i * DIA) for i in range(4)]

    proyeccion = proyectar(eventos, ahora=AHORA)

    assert proyeccion.vector_proyectado["somatica"] == max(
        proyeccion.vector_proyectado.values()
    )


def test_racha_activa_impulsa_su_dimension() -> None:
    """Verifica que una racha activa inclina la proyección a su favor."""
    eventos = [
        _e(i, Dimension.COGNITIVA, AHORA - DIA - i * 3600)
        for i in range(5)
    ] + [
        _e(10 + i, Dimension.SOMATICA, AHORA - (3 - i) * 3600)
        for i in range(4)
    ]

    proyeccion = proyectar(eventos, ahora=AHORA)

    assert proyeccion.vector_proyectado["somatica"] > proyeccion.vector_proyectado[
        "cognitiva"
    ]


def test_proyectar_sin_eventos_lanza_value_error() -> None:
    """Verifica que no se proyecta sin historial."""
    with pytest.raises(ValueError):
        proyectar([], ahora=AHORA)
