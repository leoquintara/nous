"""Tests de las operaciones de memoria tipadas (Neuro-7).

Cubiertas: router de ops temporales, parse de fecha asof,
primacía de fuente, conflictos del mismo día, certeza,
span de duración, recall débil, reformulación léxica y
clarificación estructurada — todo puro, sin I/O.
"""

import uuid
from datetime import UTC, datetime

from src.core.busqueda import _re_scored, _veredicto
from src.core.memoria.operaciones import (
    certeza_op,
    conflictos_empate,
    consulta_relajada,
    es_recall_debil,
    primacia_fuente,
    span_duracion,
    sugerir_clarificacion,
    variantes_morfologicas,
)
from src.core.modelos import ResultadoBusqueda
from src.core.routing import (
    OpTemporal,
    clasificar_op_temporal,
    consulta_nucleo,
    fecha_asof,
)
from src.storage.models import EventoModel

# -- Router de ops temporales ---------------------------------


def test_op_earliest_para_primera_aparicion() -> None:
    assert clasificar_op_temporal("cuando fue el primer mensaje de samay") == OpTemporal.EARLIEST


def test_op_latest_para_mas_reciente() -> None:
    assert clasificar_op_temporal("ultimo mensaje de samay") == OpTemporal.LATEST


def test_op_duration_para_cuanto_tiempo() -> None:
    assert clasificar_op_temporal("cuanto tiempo lleva samay en el proyecto") == OpTemporal.DURATION


def test_op_duration_para_desde_cuando() -> None:
    assert clasificar_op_temporal("desde cuando usamos nous") == OpTemporal.DURATION


def test_op_asof_para_fecha_explicita() -> None:
    assert clasificar_op_temporal("quien era el responsable en junio 2022") == OpTemporal.ASOF


def test_op_asof_para_anio_solo() -> None:
    assert clasificar_op_temporal("que sabiamos de la migration en 2023") == OpTemporal.ASOF


def test_op_change_para_cuando_cambio() -> None:
    assert clasificar_op_temporal("cuando cambio el owner del dashboard") == OpTemporal.CHANGE


def test_op_provenance_para_donde_se_confirmo() -> None:
    assert clasificar_op_temporal("donde se confirmo el deploy") == OpTemporal.PROVENANCE


def test_op_provenance_para_quien_lo_dijo() -> None:
    assert clasificar_op_temporal("quien lo dijo primero") == OpTemporal.PROVENANCE


def test_fecha_asof_mes_y_anio_cierra_el_mes() -> None:
    ts = fecha_asof("quien era el responsable en junio 2022")
    assert ts == int(datetime(2022, 7, 1, tzinfo=UTC).timestamp()) - 1


def test_fecha_asof_anio_cierra_el_anio() -> None:
    ts = fecha_asof("que sabiamos en 2023")
    assert ts == int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()) - 1


def test_fecha_asof_mes_sin_anio_usa_el_ultimo_cerrado() -> None:
    ahora = int(datetime(2025, 3, 15, tzinfo=UTC).timestamp())
    ts = fecha_asof("que paso en junio", ahora=ahora)
    # Marzo 2025: junio 2024 es el último junio ya cerrado.
    assert ts == int(datetime(2024, 7, 1, tzinfo=UTC).timestamp()) - 1


def test_fecha_asof_sin_fecha_devuelve_none() -> None:
    assert fecha_asof("cuando fue el primer mensaje") is None


def test_consulta_nucleo_descarta_fechas_en_asof() -> None:
    nucleo = consulta_nucleo("quien era el responsable en junio 2022", descartar_fechas=True)
    assert "junio" not in nucleo
    assert "2022" not in nucleo
    assert "responsable" in nucleo


# -- Primacía de fuente -----------------------------------------


def test_primacia_captura_directa_sobre_import() -> None:
    assert primacia_fuente("signal") > primacia_fuente("memlord")
    assert primacia_fuente("usuario") > primacia_fuente("gbrain")


def test_primacia_desconocida_cae_en_default() -> None:
    assert 0.0 < primacia_fuente("agente-x") < 1.0


# -- Conflictos y certeza ----------------------------------------


def test_conflictos_empate_detecta_marca_casi_igual() -> None:
    dia = 1_700_000_000
    # Empate real = timestamps a menos de EMPATE_SEGUNDOS (imports
    # masivos que comparten marca); una hora después ya no empata.
    assert conflictos_empate(dia, [dia + 30, dia + 200_000]) == 1
    assert conflictos_empate(dia, [dia + 3600]) == 0


def test_certeza_baja_por_conflictos() -> None:
    assert certeza_op(True, True, 0) == "alta"
    assert certeza_op(True, True, 2) == "media"
    assert certeza_op(False, True, 0) == "baja"
    assert certeza_op(False, False, 0) == "nula"


def test_span_duracion_desde_hasta_dias() -> None:
    span = span_duracion(1_600_000_000, 1_600_000_000 + 10 * 86_400)
    assert span["dias"] == 10
    assert span["desde_unix"] == 1_600_000_000


# -- Recall débil y reformulación --------------------------------


def test_recall_debil_por_vacio_pocos_hits_o_score_bajo() -> None:
    assert es_recall_debil(0, 0.0, 10)
    assert es_recall_debil(2, 0.9, 10)
    assert es_recall_debil(5, 0.10, 10)
    assert not es_recall_debil(5, 0.9, 10)


def test_variantes_morfologicas_tildes_y_plural() -> None:
    assert "reunion" in variantes_morfologicas("reunión")
    assert "chat" in variantes_morfologicas("chats")


def test_consulta_relajada_anade_variantes() -> None:
    relajada = consulta_relajada("reuniones samay")
    assert "samay" in relajada
    assert "reunion" in relajada
    assert consulta_relajada("") == ""


# -- Clarificación estructurada ----------------------------------


def test_clarificacion_conflicto_sugiere_presentar_ambos() -> None:
    rival = str(uuid.uuid4())
    clarif = sugerir_clarificacion(4, rival, "samay")
    assert clarif is not None
    assert clarif.razon == "conflicto"
    assert clarif.accion == "presentar_ambos"
    assert clarif.opciones == [rival]


def test_clarificacion_sin_evidencia_sugiere_preguntar() -> None:
    clarif = sugerir_clarificacion(0, None, "proyecto-z")
    assert clarif is not None
    assert clarif.razon == "sin_evidencia"
    assert clarif.accion == "preguntar_contexto"


def test_clarificacion_resultado_sano_no_dispara() -> None:
    assert sugerir_clarificacion(5, None, "samay") is None


# -- Veredicto de la op ------------------------------------------


def _modelo(contenido: str, timestamp: int) -> EventoModel:
    return EventoModel(
        id=uuid.uuid4(),
        timestamp_unix=timestamp,
        dimension="cognitiva",
        fuente="t",
        contenido=contenido,
        embedding=[1.0] + [0.0] * 383,
        amplitud=0.0,
        refuerzos=0,
    )


def test_veredicto_declara_falsado_cuando_sonda_corrige() -> None:
    """La sonda temporal coronó a otro → falsed declarado."""
    viejo = _modelo("primer samay", 1_600_000_000)
    nuevo = _modelo("samay reciente", 1_700_000_000)
    hits = [(viejo, 1.0), (nuevo, 0.5)]
    finales = [ResultadoBusqueda(evento_id=str(viejo.id), score_final=1.0)]
    modelos = {str(viejo.id): viejo, str(nuevo.id): nuevo}
    v = _veredicto(
        OpTemporal.EARLIEST,
        "asc",
        hits,
        [],
        finales=finales,
        modelos=modelos,
        cabeza_previa=str(nuevo.id),
        asof_unix=None,
        reformulada=False,
    )
    assert v["op"] == "earliest"
    assert v["ganador"] == str(viejo.id)
    assert v["falsado"] is True
    assert v["certeza"] == "alta"


def test_veredicto_conflictos_empate() -> None:
    """Dos candidatos empatados en posición → conflicto declarado."""
    base = 1_700_000_000
    a = _modelo("ev a", base)
    b = _modelo("ev b", base + 30)
    hits = [(a, 1.0), (b, 0.9)]
    finales = [ResultadoBusqueda(evento_id=str(a.id), score_final=1.0)]
    modelos = {str(a.id): a, str(b.id): b}
    v = _veredicto(
        OpTemporal.LATEST,
        "desc",
        hits,
        [],
        finales=finales,
        modelos=modelos,
        cabeza_previa=str(a.id),
        asof_unix=None,
        reformulada=False,
    )
    assert v["conflictos"] == [str(b.id)]
    assert v["certeza"] == "media"


def test_veredicto_duration_reporta_span() -> None:
    """La op duration anexa el span primero→último."""
    primero = _modelo("arranque", 1_600_000_000)
    ultimo = _modelo("cierre", 1_600_000_000 + 5 * 86_400)
    v = _veredicto(
        OpTemporal.DURATION,
        "asc",
        [(primero, 1.0)],
        [(ultimo, 1.0)],
        finales=[ResultadoBusqueda(evento_id=str(primero.id), score_final=1.0)],
        modelos={str(primero.id): primero, str(ultimo.id): ultimo},
        cabeza_previa=str(primero.id),
        asof_unix=None,
        reformulada=False,
    )
    assert v["duracion"]["dias"] == 5


def test_veredicto_asof_declara_el_corte() -> None:
    """La op asof anexa el corte pedido."""
    v = _veredicto(
        OpTemporal.ASOF,
        "desc",
        [],
        [],
        finales=[],
        modelos={},
        cabeza_previa=None,
        asof_unix=1_656_633_599,
        reformulada=False,
    )
    assert v["estado_en"] == 1_656_633_599
    assert v["certeza"] == "nula"


def test_veredicto_provenance_declara_fuente() -> None:
    """La op provenance anexa la fuente del ganador."""
    m = _modelo("deploy confirmado", 1_700_000_000)
    v = _veredicto(
        OpTemporal.PROVENANCE,
        None,
        [],
        [],
        finales=[ResultadoBusqueda(evento_id=str(m.id), score_final=1.0)],
        modelos={str(m.id): m},
        cabeza_previa=str(m.id),
        asof_unix=None,
        reformulada=False,
    )
    assert v["fuente"] == "t"


def test_re_scored_provenance_premia_fuente_primaria() -> None:
    """A igualdad de señal, la captura directa supera al import."""
    ahora = 1_800_000_000
    captura = _modelo("dato de signal", ahora - 100)
    derivado = _modelo("dato de signal", ahora - 100)
    captura.fuente = "signal"
    derivado.fuente = "memlord"
    fusionados = [
        ResultadoBusqueda(evento_id=str(captura.id), score_rrf=1.0),
        ResultadoBusqueda(evento_id=str(derivado.id), score_rrf=1.0),
    ]
    modelos = {str(captura.id): captura, str(derivado.id): derivado}
    vector = [1.0] + [0.0] * 383
    boosts = {
        str(captura.id): primacia_fuente("signal"),
        str(derivado.id): primacia_fuente("memlord"),
    }
    sin_boost = _re_scored(fusionados, modelos, vector, ahora, 30.0)
    con_boost = _re_scored(
        fusionados,
        modelos,
        vector,
        ahora,
        30.0,
        fuente_boosts=boosts,
    )
    assert con_boost[0].evento_id == str(captura.id)
    base_c = next(r.score_final for r in sin_boost if r.evento_id == str(captura.id))
    top_c = con_boost[0].score_final
    assert top_c > base_c
