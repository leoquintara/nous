"""Tests de la fase REM — asociaciones creativas del sueño (Neuro-3)."""

import math

from src.core.memoria.rem import (
    REM_MAX_CANDIDATOS,
    REM_MAX_POR_CORRIDA,
    UMBRAL_REM_MAX,
    UMBRAL_REM_MIN,
    _muestrear,
    pares_rem,
)
from src.core.memoria.semanticizacion import EpisodioGist


def _ep(id_: str, vec: list[float]) -> EpisodioGist:
    return EpisodioGist(
        id=id_, contenido=f"contenido {id_}", weight=1.0, embedding=vec
    )


def _vec(angulo: float) -> list[float]:
    return [math.cos(angulo), math.sin(angulo)]


def test_pares_rem_solo_banda_media() -> None:
    """Solo pares con coseno en [UMBRAL_REM_MIN, UMBRAL_REM_MAX) califican."""
    a = _vec(0.0)
    b = _vec(0.2)   # cos ~0.98 → duplicado, excluido (>= max)
    c = _vec(0.85)  # cos ~0.66 → banda rem ✓
    d = _vec(1.4)   # cos ~0.17 → sin relación, excluido (< min)
    pares = pares_rem(
        [_ep("a", a), _ep("b", b), _ep("c", c), _ep("d", d)]
    )
    assert ("a", "c") in pares
    plano = {p for par in pares for p in par}
    assert "b" not in plano  # demasiado parecido a 'a'
    assert "d" not in plano  # sin relación


def test_pares_rem_un_sueno_por_evento() -> None:
    """Cada evento participa a lo sumo en un par soñado."""
    a = _vec(0.0)
    b = _vec(0.85)  # cos~0.66 con a
    c = _vec(0.80)  # cos~0.70 con a — rival de b por el sueño de a
    pares = pares_rem([_ep("a", a), _ep("b", b), _ep("c", c)])
    usados: list[str] = [p for par in pares for p in par]
    # 'a' no puede soñar dos veces — el mejor par gana.
    assert usados.count("a") <= 1


def test_pares_rem_respeta_tope_por_corrida() -> None:
    """REM_MAX_POR_CORRIDA limita los vínculos por pasada de sueño."""
    # 12 vectores espaciados ~0.85 rad entre adyacentes: todos en banda
    # con sus vecinos → abundan los pares elegibles.
    eps = [_ep(f"e{i}", _vec(i * 0.85)) for i in range(12)]
    pares = pares_rem(eps)
    assert len(pares) <= REM_MAX_POR_CORRIDA


def test_pares_rem_umbrales_coherentes() -> None:
    """La banda rem no toca ni dedup (alto) ni ruido (bajo)."""
    assert UMBRAL_REM_MIN < UMBRAL_REM_MAX
    assert UMBRAL_REM_MAX <= 0.82  # por debajo del cluster de gist


def test_muestrear_respeta_el_cap() -> None:
    """Pasado el tope de candidatos, la muestra queda acotada."""
    eps = [_ep(f"e{i:04}", _vec(i * 0.01)) for i in range(REM_MAX_CANDIDATOS + 50)]
    muestra = _muestrear(eps, REM_MAX_CANDIDATOS)
    assert len(muestra) == REM_MAX_CANDIDATOS


def test_muestrear_es_determinista_y_estratificado() -> None:
    """Misma entrada → misma muestra, repartida por todo el corpus."""
    eps = [_ep(f"e{i:04}", _vec(0.0)) for i in range(100)]
    a = _muestrear(eps, 10)
    b = _muestrear(eps, 10)
    assert [e.id for e in a] == [e.id for e in b]
    # Estratos: cubre principio, medio y final — no solo lo reciente.
    ids = {e.id for e in a}
    assert "e0000" in ids and any(i > "e0080" for i in ids)


def test_muestrear_bajo_el_cap_devuelve_todo() -> None:
    """Sin exceso no hay muestreo: el sueño ve el corpus completo."""
    eps = [_ep("a", _vec(0.0)), _ep("b", _vec(0.5))]
    assert _muestrear(eps, 10) == eps
