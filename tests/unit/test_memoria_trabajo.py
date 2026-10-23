"""Tests unitarios del buffer de trabajo (Baddeley)."""

from src.core.memoria.trabajo import (
    CAPACIDAD_MAX,
    ENSAYOS_PARA_PROMOVER,
    TTL_BORRADOR_SEG,
    debe_promover,
    esta_expirado,
    hay_cupo,
)

AHORA = 1_800_000_000


def test_borrador_expira_pasado_su_ttl() -> None:
    """Sin ensayo el pensamiento se pierde — el olvido es la función."""
    creado = AHORA - TTL_BORRADOR_SEG - 1
    assert esta_expirado(creado, AHORA)
    assert not esta_expirado(AHORA - 60, AHORA)


def test_borrador_promueve_con_los_ensayos_justos() -> None:
    """La repetición es la señal: alcanza el umbral y se gradúa."""
    assert not debe_promover(ENSAYOS_PARA_PROMOVER - 1)
    assert debe_promover(ENSAYOS_PARA_PROMOVER)
    assert debe_promover(ENSAYOS_PARA_PROMOVER + 5)


def test_buffer_respeta_capacidad() -> None:
    """El buffer es finito — lleno no admite más pensamientos."""
    assert hay_cupo(CAPACIDAD_MAX - 1)
    assert not hay_cupo(CAPACIDAD_MAX)
    assert not hay_cupo(CAPACIDAD_MAX + 10)


def test_ttl_default_es_media_hora() -> None:
    """Constante documentada: 30 minutos de vida sin ensayo."""
    assert TTL_BORRADOR_SEG == 1800
