"""Tests unitarios de los efectos de evocación (ADR-003, tanda 2).

Cubre co-activación hebbiana, extinción emocional, ventana lábil de
reconsolidación, metamemoria y spacing effect — toda lógica pura.
"""

import uuid

import pytest

from src.core.memoria.retrieval import (
    COACTIVA_DELTA,
    COACTIVA_PESO_MAX,
    COACTIVA_TOP_K,
    EXTINCION_FACTOR,
    confianza,
    confianza_de_evento,
    esta_labil,
    extincion_valencia,
    factor_espaciado,
    intervalo_medio,
    pares_coactivos,
    peso_coactiva,
    ventana_labil,
)

AHORA = 1_800_000_000


def _ids(n: int) -> list[str]:
    return [str(uuid.uuid4()) for _ in range(n)]


def test_pares_coactivos_genera_combinaciones_unicas() -> None:
    """Tres co-evocados producen C(3,2)=3 pares sin repetidos."""
    pares = pares_coactivos(_ids(3))
    assert len(pares) == 3
    assert len({frozenset(p) for p in pares}) == 3


def test_pares_coactivos_respeta_top_k() -> None:
    """Solo los top-K encendidos cuentan — el resto no asocia."""
    ids = _ids(COACTIVA_TOP_K + 2)
    pares = pares_coactivos(ids)
    usados = {x for par in pares for x in par}
    assert usados == set(ids[:COACTIVA_TOP_K])


def test_peso_coactiva_suma_hasta_el_tope() -> None:
    """La arista crece con cada co-recall pero tiene cap fisiológico."""
    assert peso_coactiva(1.0) == pytest.approx(1.0 + COACTIVA_DELTA)
    assert peso_coactiva(COACTIVA_PESO_MAX) == COACTIVA_PESO_MAX


def test_extincion_atenua_valencia_negativa_en_neutro() -> None:
    """Recuerdo negativo evocado en calma pierde ~12% de carga."""
    nueva = extincion_valencia(-0.8, estado_actual=0.0)
    assert nueva == pytest.approx(-0.8 * (1 - EXTINCION_FACTOR))


def test_extincion_tambien_atenua_valencia_positiva() -> None:
    """La exposición neutra desactiva carga en cualquier polo."""
    nueva = extincion_valencia(0.9, estado_actual=None)
    assert nueva is not None and 0 < nueva < 0.9


def test_extincion_no_aplica_a_memoria_neutra() -> None:
    """Una valencia tenue no tiene nada que extinguir."""
    assert extincion_valencia(0.1, estado_actual=0.0) is None


def test_extincion_no_aplica_si_el_usuario_esta_emocional() -> None:
    """Revivir un recuerdo negativo estando mal lo refuerza, no lo apaga."""
    assert extincion_valencia(-0.8, estado_actual=-0.5) is None
    assert extincion_valencia(-0.8, estado_actual=0.6) is None


def test_extincion_converge_bajo_el_umbral_emocional() -> None:
    """La exposición repetida baja la valencia hasta dejar de ser
    emocional (< UMBRAL_VALENCIA_EMOCIONAL) — ahí ya no hay nada
    que extinguir y la serie se detiene."""
    from src.core.memoria.retrieval import UMBRAL_VALENCIA_EMOCIONAL

    valencia = 0.9
    pasos = 0
    while True:
        nueva = extincion_valencia(valencia, estado_actual=None)
        if nueva is None:
            break
        valencia = nueva
        pasos += 1
    assert pasos > 0
    assert abs(valencia) < UMBRAL_VALENCIA_EMOCIONAL + 0.02


def test_ventana_labil_marca_horas_de_reconsolidacion() -> None:
    """La ventana abre 6h desde la evocación."""
    assert ventana_labil(AHORA) == AHORA + 6 * 3600
    assert esta_labil(ventana_labil(AHORA), AHORA + 3600)
    assert not esta_labil(ventana_labil(AHORA), AHORA + 7 * 3600)


def test_esta_labil_sin_ventana_es_falso() -> None:
    """Una memoria nunca evocada no tiene ventana abierta."""
    assert not esta_labil(None, AHORA)


def test_confianza_sube_con_refuerzos_y_peso() -> None:
    """La evidencia acumulada hace el recuerdo más fiable."""
    tenue = confianza(0, 1.0, 0.5, 1.0)
    solida = confianza(8, 2.5, 0.9, 1.0)
    assert solida > tenue


def test_confianza_baja_con_contradicciones_y_archivo() -> None:
    """Contradicciones abiertas y archivo bajan la fiabilidad."""
    base = confianza(4, 1.5, 0.7, 10.0)
    contradictoria = confianza(4, 1.5, 0.7, 10.0, contradicciones=3)
    archivada = confianza(4, 1.5, 0.7, 10.0, archivado=True)
    assert contradictoria < base
    assert archivada <= 0.05


def test_confianza_nunca_es_certeza_total() -> None:
    """Es memoria, no verdad verificada: tope 0.98, piso 0.05."""
    assert confianza(99, 3.0, 1.0, 0.0) <= 0.98
    assert confianza(0, 0.0, 0.0, 400.0) >= 0.05


def test_confianza_de_evento_deriva_la_edad() -> None:
    """El atajo convierte timestamps a días sin salirse del rango."""
    viejo = AHORA - 300 * 86400
    c = confianza_de_evento(2, 1.0, 0.6, viejo, AHORA)
    assert 0.05 <= c <= 0.98
    nuevo = confianza_de_evento(2, 1.0, 0.6, AHORA - 86400, AHORA)
    assert nuevo > c


def test_factor_espaciado_premia_ensayos_distribuidos() -> None:
    """Un repaso por día vale más que cinco el mismo día."""
    assert factor_espaciado(None) == 1.0
    assert factor_espaciado(0.0) == 1.0
    assert factor_espaciado(3.5) > factor_espaciado(0.2)
    # Cap a +35%: un intervalo absurdo no rompe la curva.
    assert factor_espaciado(365.0) == factor_espaciado(7.0)


def test_intervalo_medio_divide_el_span_entre_ensayos() -> None:
    """(último acceso − creación) / refuerzos = intervalo medio."""
    creado = AHORA - 30 * 86400
    ultimo = AHORA - 10 * 86400
    assert intervalo_medio(creado, ultimo, 4) == pytest.approx(5.0)
    assert intervalo_medio(creado, None, 4) is None
    assert intervalo_medio(creado, ultimo, 0) is None
