"""Tests unitarios de minería de scripts secuenciales (ADR-003)."""

from src.core.memoria.scripts import (
    VENTANA_GAP_SEGUNDOS,
    EventoSecuencia,
    minar_scripts,
    token_tema,
)


def _ev(i: int, contenido: str, ts: int) -> EventoSecuencia:
    """Fabrica un evento secuenciable."""
    return EventoSecuencia(
        id=f"e{i}", timestamp_unix=ts, contenido=contenido, dimension="conductual"
    )


def test_token_tema_extrae_palabras_significativas() -> None:
    """El token reduce el contenido a sus palabras más largas."""
    assert token_tema("reproduje el error crítico del build") == (
        "critico+reproduje"
    )
    assert token_tema("el la de") == "misc"


def test_secuencia_repetida_se_detecta() -> None:
    """Tres veces el mismo bigrama temporal → script candidato."""
    base = 1_700_000_000
    eventos = []
    for i in range(3):
        t = base + i * 100_000
        eventos.append(_ev(i * 2, "reproduje error crítico", t))
        eventos.append(_ev(i * 2 + 1, "arreglé el test fallido", t + 600))
    scripts = minar_scripts(eventos, min_soporte=3)
    assert scripts
    assert scripts[0].soporte >= 3
    assert scripts[0].confianza > 0.0


def test_soporte_minimo_filtra_ruido() -> None:
    """Secuencias únicas no llegan a ser script."""
    base = 1_700_000_000
    eventos = [
        _ev(1, "reproduje error crítico", base),
        _ev(2, "arreglé test", base + 600),
    ]
    assert minar_scripts(eventos, min_soporte=3) == []


def test_gap_largo_rompe_la_secuencia() -> None:
    """Eventos separados por >4h no forman una misma secuencia."""
    base = 1_700_000_000
    eventos = [
        _ev(1, "reproduje error crítico", base),
        _ev(2, "arreglé test", base + VENTANA_GAP_SEGUNDOS + 1),
    ]
    assert minar_scripts(eventos, min_soporte=1) == []


def test_confianza_mide_predictibilidad() -> None:
    """Un prefijo que siempre continúa igual da confianza 1.0."""
    base = 1_700_000_000
    eventos = []
    for i in range(4):
        t = base + i * 50_000
        eventos += [
            _ev(i * 3, "reproduje error crítico", t),
            _ev(i * 3 + 1, "arreglé test fallido", t + 300),
            _ev(i * 3 + 2, "commit final verde", t + 600),
        ]
    scripts = minar_scripts(eventos, min_soporte=2)
    trigrama = [s for s in scripts if len(s.pasos) == 3]
    assert trigrama and trigrama[0].confianza == 1.0
