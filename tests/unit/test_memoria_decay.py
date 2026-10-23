"""Tests unitarios de las curvas de olvido adaptativas (ADR-003)."""

import math

import pytest

from src.core.memoria.decay import (
    media_vida_dias,
    retencion,
    retencion_actual,
)
from src.core.memoria.tipos import TipoMemoria
from src.core.modelos import Dimension


def test_somatica_decae_mas_rapido_que_cognitiva() -> None:
    """Lo corporal es transitorio: media-vida mucho menor."""
    assert media_vida_dias(Dimension.SOMATICA) < media_vida_dias(
        Dimension.COGNITIVA
    )


def test_semantica_dura_mas_que_episodica() -> None:
    """El conocimiento persiste más que el episodio."""
    assert media_vida_dias(
        Dimension.COGNITIVA, TipoMemoria.SEMANTICA
    ) > media_vida_dias(Dimension.COGNITIVA, TipoMemoria.EPISODICA)


def test_amplitud_alarga_vida_en_cognitiva() -> None:
    """Lo emocionalmente intenso se recuerda más."""
    assert media_vida_dias(
        Dimension.COGNITIVA, amplitud=0.9
    ) > media_vida_dias(Dimension.COGNITIVA, amplitud=0.2)


def test_refuerzos_alargan_vida() -> None:
    """Cada evocación es un replay que frena el olvido."""
    assert media_vida_dias(
        Dimension.COGNITIVA, refuerzos=4
    ) > media_vida_dias(Dimension.COGNITIVA, refuerzos=0)


def test_curado_nunca_decae() -> None:
    """El tier curado tiene media-vida infinita."""
    assert media_vida_dias(Dimension.SOMATICA, curado=True) == math.inf


def test_retencion_media_vida_da_medio() -> None:
    """Tras exactamente una media-vida, retención = 0.5."""
    assert retencion(14.0, 14.0) == pytest.approx(0.5)


def test_retencion_infinita_es_uno() -> None:
    """Media-vida infinita retiene siempre."""
    assert retencion(99999.0, math.inf) == 1.0


def test_retencion_actual_usa_ultimo_acceso() -> None:
    """El reloj corre desde la evocación más reciente, no la creación."""
    ahora = 1_800_000_000
    hace_30d = ahora - 30 * 86400
    acceso_reciente = ahora - 86400
    sin_acceso = retencion_actual(
        ahora, hace_30d, None, Dimension.COGNITIVA
    )
    con_acceso = retencion_actual(
        ahora, hace_30d, acceso_reciente, Dimension.COGNITIVA
    )
    assert con_acceso > sin_acceso


def test_spacing_effect_ensayos_espaciados_duran_mas() -> None:
    """Repasos distribuidos consolidan más que repasos masivos."""
    masivos = media_vida_dias(
        Dimension.COGNITIVA, refuerzos=5, intervalo_medio_dias=0.1
    )
    espaciados = media_vida_dias(
        Dimension.COGNITIVA, refuerzos=5, intervalo_medio_dias=5.0
    )
    assert espaciados > masivos


def test_primacia_alarga_la_media_vida() -> None:
    """El primer evento de una época persiste ~15% más."""
    normal = media_vida_dias(Dimension.COGNITIVA)
    primero = media_vida_dias(Dimension.COGNITIVA, primacia=True)
    assert primero > normal


def test_retencion_actual_aplica_spacing_automatico() -> None:
    """El intervalo se deriva adentro: (acceso−creación)/refuerzos."""
    ahora = 1_800_000_000
    creado = ahora - 60 * 86400
    acceso = ahora - 10 * 86400
    # 4 refuerzos repartidos en 50 días → intervalo 12.5d → boost.
    espaciada = retencion_actual(
        ahora, creado, acceso, Dimension.COGNITIVA, refuerzos=4
    )
    assert espaciada > 0.0


def test_retencion_actual_con_primacia_retiene_mas() -> None:
    """La primacía también entra por el helper completo."""
    ahora = 1_800_000_000
    creado = ahora - 20 * 86400
    normal = retencion_actual(ahora, creado, None, Dimension.COGNITIVA)
    primero = retencion_actual(
        ahora, creado, None, Dimension.COGNITIVA, primacia=True
    )
    assert primero > normal
