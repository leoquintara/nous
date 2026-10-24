"""Tests unitarios de detección de interferencia (ADR-003)."""

from src.core.memoria.interferencia import (
    INTERFERENCIA_FACTOR,
    INTERFERENCIA_MIN,
    UMBRAL_SIMILITUD,
    detectar_conflicto,
    es_competidor_proactivo,
    marcar_superacion,
    tiene_negacion,
)


def test_marcador_superacion_detectado() -> None:
    """'ya no uso X' y 'ahora uso Y' son marcadores de cambio."""
    assert marcar_superacion("ahora uso Bun en vez de Node") == (
        "ahora uso"
    )
    assert marcar_superacion("migré a Bun en lugar de Node") is not None
    assert marcar_superacion("ya no trabajo con PHP") == "ya no"


def test_sin_marcador_no_hay_conflicto() -> None:
    """Similitud sin marcador de superación = relación, no conflicto."""
    assert detectar_conflicto(
        "trabajé con Node todo el día",
        [("e1", 0.95), ("e2", 0.8)],
    ) == []


def test_conflicto_solo_sobre_umbral() -> None:
    """Con marcador, solo los muy similares cuentan como contradichos."""
    conflictos = detectar_conflicto(
        "ahora uso Bun de ahora en adelante",
        [("e1", 0.9), ("e2", 0.5)],
    )
    assert [c.evento_id for c in conflictos] == ["e1"]
    assert conflictos[0].evidencia == "ahora uso"


def test_conflicto_ordenado_por_similitud() -> None:
    """Los conflictos salen del más parecido al menos."""
    conflictos = detectar_conflicto(
        "migré a Bun",
        [("e1", 0.75), ("e2", 0.95), ("e3", UMBRAL_SIMILITUD)],
    )
    assert [c.evento_id for c in conflictos] == ["e2", "e1", "e3"]


def test_tiene_negacion() -> None:
    """Detecta negaciones explícitas en el texto."""
    assert tiene_negacion("no quiero café") is True
    assert tiene_negacion("quiero café siempre") is False


def test_competidor_proactivo_banda_similar_distinto() -> None:
    """Similar-pero-distinto compite el slot; afín o duplicado, no."""
    dedup = 0.95
    assert es_competidor_proactivo(0.90, dedup)
    assert es_competidor_proactivo(INTERFERENCIA_MIN, dedup)
    # Por debajo de la banda es tema afín — no roba el recuerdo.
    assert not es_competidor_proactivo(0.70, dedup)
    # Por encima del dedup es el mismo evento — no compite, se fusiona.
    assert not es_competidor_proactivo(0.96, dedup)


def test_factor_proactivo_mas_fuerte_que_rif() -> None:
    """La codificación compite más que el recall: el golpe es mayor."""
    assert INTERFERENCIA_FACTOR < 0.97  # vs RIF_FACTOR en retrieval
