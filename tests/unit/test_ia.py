"""Tests unitarios del módulo de proveedores de IA (sin red ni DB)."""

from src.core.ia import (
    _enmascarar,
    _parsear_prediccion,
    _prompt_prediccion,
    _resumen_contexto,
)

DIA = 86_400
AHORA = 1_800_000_000


def _evento(ts: int, dimension: str = "cognitiva", amplitud: float = 0.5) -> dict:
    """Evento mínimo para el resumen de contexto."""
    return {"ts": ts, "dimension": dimension, "amplitud": amplitud, "contenido": "x"}


def test_enmascarar_key_larga_muestra_extremos() -> None:
    """La máscara conserva prefijo y sufijo sin exponer el cuerpo."""
    mascara = _enmascarar("sk-proj-abc123def456ghi789")
    assert mascara.startswith("sk-p")
    assert mascara.endswith("i789")
    assert "abc123def456" not in mascara


def test_enmascarar_key_corta_no_rompe() -> None:
    """Keys muy cortas se enmascaran igual sin IndexError."""
    assert _enmascarar("abc").endswith("abc")


def test_parsear_prediccion_json_directo() -> None:
    """Un JSON limpio produce escenarios normalizados."""
    texto = (
        '{"escenarios": [{"nombre": "Continuidad", "probabilidad": 0.6,'
        ' "descripcion": "sigue igual", "sustrato": "cognitiva"}],'
        ' "razonamiento": "patrón estable"}'
    )
    r = _parsear_prediccion(texto)
    assert r is not None
    assert r["escenarios"][0]["probabilidad"] == 0.6
    assert r["escenarios"][0]["sustrato"] == "cognitiva"
    assert r["razonamiento"] == "patrón estable"


def test_parsear_prediccion_tolerante_a_fences_y_texto() -> None:
    """Fences ```json y texto alrededor no impiden el parseo."""
    texto = (
        'Aquí va:\n```json\n{"escenarios": [{"nombre": "A",'
        ' "probabilidad": 0.4}]}\n```\nListo.'
    )
    r = _parsear_prediccion(texto)
    assert r is not None
    assert r["escenarios"][0]["nombre"] == "A"


def test_parsear_prediccion_invalida_devuelve_none() -> None:
    """Texto sin JSON o sin lista 'escenarios' devuelve None."""
    assert _parsear_prediccion("no hay json") is None
    assert _parsear_prediccion('{"otra": "cosa"}') is None
    assert _parsear_prediccion('{"escenarios": "no-lista"}') is None


def test_parsear_prediccion_acota_probabilidades_y_sustrato() -> None:
    """Probabilidades se clampean a (0, 0.95]; sustrato inválido → None."""
    texto = (
        '{"escenarios": ['
        '{"nombre": "alta", "probabilidad": 1.7, "sustrato": "inventada"},'
        '{"nombre": "baja", "probabilidad": -2, "sustrato": null}]}'
    )
    r = _parsear_prediccion(texto)
    assert r is not None
    assert r["escenarios"][0]["probabilidad"] == 0.95
    assert r["escenarios"][0]["sustrato"] is None
    assert r["escenarios"][1]["probabilidad"] == 0.01


def test_resumen_contexto_filtra_futuros_y_decae() -> None:
    """Solo cuentan eventos <= origen y el peso decae con la edad."""
    eventos = [
        _evento(AHORA - 90 * DIA, "cognitiva"),
        _evento(AHORA - DIA, "conductual"),
        _evento(AHORA + DIA, "somatica"),  # futuro: no cuenta
    ]
    r = _resumen_contexto(eventos, AHORA, incluir_contenido=False)
    assert r["eventos_visibles"] == 2
    assert r["distribucion_dimensional"]["conductual"] > r[
        "distribucion_dimensional"
    ]["cognitiva"]
    assert r["dimension_dominante"] == "conductual"
    assert "muestras" not in r


def test_resumen_contexto_muestras_solo_con_optin() -> None:
    """El contenido de memorias viaja solo si incluir_contenido=True."""
    eventos = [_evento(AHORA - DIA)]
    sin = _resumen_contexto(eventos, AHORA, incluir_contenido=False)
    con = _resumen_contexto(eventos, AHORA, incluir_contenido=True)
    assert "muestras" not in sin
    assert con["muestras"][0]["contenido"] == "x"


def test_prompt_prediccion_incluye_candidatos_e_instruccion() -> None:
    """Con candidatos heurísticos el prompt pide re-ponderarlos."""
    contexto = _resumen_contexto([_evento(AHORA - DIA)], AHORA, False)
    candidatos = [{"id": "continuidad", "nombre": "Continuidad",
                   "probabilidad": 0.5, "descripcion": "x"}]
    msgs = _prompt_prediccion(contexto, [], 2, 30, candidatos)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "candidatos_heuristicos" in msgs[1]["content"]
    assert "Continuidad" in msgs[1]["content"]


def test_prompt_prediccion_sin_candidatos_no_pide_reponderado() -> None:
    """Sin candidatos, el prompt solo pide escenarios nuevos."""
    contexto = _resumen_contexto([_evento(AHORA - DIA)], AHORA, False)
    msgs = _prompt_prediccion(contexto, [], 0, 30, None)
    assert "candidatos_heuristicos" not in msgs[1]["content"]
