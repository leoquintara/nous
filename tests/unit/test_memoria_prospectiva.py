"""Tests unitarios de memoria prospectiva (ADR-003)."""

import pytest

from src.core.memoria.prospectiva import (
    Intencion,
    IntencionInvalidaError,
    TriggerTipo,
    aplazada,
    aplazar,
    disparada_por_evento,
    disparada_por_tiempo,
    distancia_temporal,
    parsear_trigger,
)


def _intencion(
    trigger_valor: dict[str, object], **kw: object
) -> Intencion:
    """Fabrica una intención pendiente con el trigger dado."""
    return Intencion(
        id="i1",
        intencion="llamar a María",
        trigger_tipo=kw.get("trigger_tipo", TriggerTipo.TIME),
        trigger_valor=trigger_valor,
        creada_en=1_000_000,
        ejecutada=kw.get("ejecutada", False),
        cancelada=kw.get("cancelada", False),
        aplazada_hasta=kw.get("aplazada_hasta"),
    )


def test_parsear_trigger_time() -> None:
    """Un dict con 'ts' es un trigger temporal."""
    assert parsear_trigger({"ts": 1_800_000_000}) == TriggerTipo.TIME


def test_parsear_trigger_event() -> None:
    """Un dict con 'query' es un trigger de evento."""
    assert parsear_trigger({"query": "reunión con cliente"}) == (
        TriggerTipo.EVENT
    )


def test_parsear_trigger_invalido_lanza() -> None:
    """Sin 'ts' ni 'query' válidos, el trigger es inválido."""
    with pytest.raises(IntencionInvalidaError):
        parsear_trigger({"foo": 1})


def test_time_dispara_al_vencer() -> None:
    """Vencido el timestamp, la intención dispara."""
    i = _intencion({"ts": 1_800_000_000})
    assert disparada_por_tiempo(i, 1_800_000_001) is True
    assert disparada_por_tiempo(i, 1_799_999_999) is False


def test_ejecutada_o_cancelada_no_dispara() -> None:
    """Una intención resuelta nunca vuelve a disparar."""
    assert disparada_por_tiempo(
        _intencion({"ts": 1}, ejecutada=True), 9_999_999_999
    ) is False
    assert disparada_por_tiempo(
        _intencion({"ts": 1}, cancelada=True), 9_999_999_999
    ) is False


def test_event_dispara_por_similitud() -> None:
    """Con similitud coseno ≥ umbral, el evento dispara."""
    i = _intencion(
        {"query": "reunión con cliente", "umbral": 0.6},
        trigger_tipo=TriggerTipo.EVENT,
    )
    assert disparada_por_evento(i, "cualquier texto", 0.7) is True
    assert disparada_por_evento(i, "cualquier texto", 0.5) is False


def test_event_fallback_lexico() -> None:
    """Sin embedding, la cobertura léxica decide el disparo."""
    i = _intencion(
        {"query": "reunión cliente proyecto"}, trigger_tipo=TriggerTipo.EVENT
    )
    assert disparada_por_evento(
        i, "hoy tuve reunión con el cliente del proyecto"
    ) is True
    assert disparada_por_evento(i, "algo sin relación") is False


def test_distancia_temporal() -> None:
    """Segundos hasta el trigger; infinito para event-based."""
    i = _intencion({"ts": 1_000_100})
    assert distancia_temporal(i, 1_000_000) == 100.0
    assert (
        distancia_temporal(
            _intencion({"query": "x"}, trigger_tipo=TriggerTipo.EVENT), 0
        )
        == float("inf")
    )


def test_snooze_silencia_el_disparo_temporal() -> None:
    """Una intención aplazada no dispara aunque su ts ya venció."""
    i = _intencion({"ts": 900}, aplazada_hasta=2_000)
    assert aplazada(i, 1_000) is True
    assert disparada_por_tiempo(i, 1_000) is False
    # Pasado el snooze vuelve a disparar.
    assert disparada_por_tiempo(i, 2_500) is True


def test_aplazar_calcula_el_instante_destino() -> None:
    """aplazar devuelve ahora+segundos (nunca en el pasado)."""
    assert aplazar(_intencion({"ts": 0}), 3600, 1_000) == 4_600
    assert aplazar(_intencion({"ts": 0}), -50, 1_000) == 1_000
