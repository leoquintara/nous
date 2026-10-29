"""Tests unitarios de los módulos puros de Neuro-5.

Cubre: bootstrap presupuestado + snapshot, veredicto de censores,
calibración Brier, handoff de continuidad y el grace floor de la
consolidación. Sin I/O — todo entra por parámetro.
"""

from src.core.memoria.bootstrap import (
    SeccionBootstrap,
    empaquetar_bootstrap,
    formatear_snapshot,
)
from src.core.memoria.calibracion import (
    PrediccionResuelta,
    brier_score,
    reporte_calibracion,
)
from src.core.memoria.consolidacion import (
    EventoConsolidable,
    decidir_consolidacion,
)
from src.core.memoria.handoff import generar_handoff
from src.core.memoria.tipos import TipoMemoria
from src.core.memoria.verificacion import (
    CensorCoincidente,
    parsear_censor,
    veredicto_censores,
)
from src.core.modelos import Dimension

_AHORA = 1_800_000_000


def _seccion(clave: str, n: int, largo: int = 20) -> SeccionBootstrap:
    """Sección con n líneas de largo fijo."""
    return SeccionBootstrap(
        clave=clave,
        titulo=clave.upper(),
        lineas=tuple("x" * largo + str(i) for i in range(n)),
    )


def _evento(
    id_: str,
    *,
    dias: float = 30.0,
    amplitud: float = 0.5,
    refuerzos: int = 0,
    weight: float = 1.0,
    tipo: str = "evento",
    tipo_memoria: TipoMemoria | None = TipoMemoria.EPISODICA,
    dimension: Dimension = Dimension.COGNITIVA,
    ultimo_acceso: int | None = None,
    archivado: bool = False,
    curado: bool = False,
) -> EventoConsolidable:
    """Fabrica un candidato de consolidación."""
    return EventoConsolidable(
        id=id_,
        timestamp_unix=int(_AHORA - dias * 86400),
        dimension=dimension,
        tipo=tipo,
        amplitud=amplitud,
        refuerzos=refuerzos,
        weight=weight,
        tipo_memoria=tipo_memoria,
        ultimo_acceso=ultimo_acceso,
        archivado=archivado,
        curado=curado,
    )


# ---------- bootstrap presupuestado ----------


def test_empaquetar_respeta_presupuesto_y_prioridad() -> None:
    """Con presupuesto corto solo entran las primeras secciones."""
    plan = empaquetar_bootstrap(
        [
            _seccion("censores", 2),
            _seccion("loops", 3),
            _seccion("calientes", 5),
        ],
        presupuesto=120,
    )
    claves = [s.clave for s in plan.secciones]
    assert claves[0] == "censores"
    assert plan.usado <= 120
    assert "calientes" in plan.omitidos or "calientes" not in claves


def test_empaquetar_presupuesto_cero_es_ilimitado() -> None:
    """presupuesto ≤ 0 = sin recorte, nada omitido."""
    plan = empaquetar_bootstrap(
        [_seccion("a", 5), _seccion("b", 5)], presupuesto=0
    )
    assert len(plan.secciones) == 2
    assert not plan.omitidos


def test_empaquetar_omite_lineas_enteras_no_corta() -> None:
    """Una línea que no cabe se omite entera — nunca truncada."""
    linea_larga = "y" * 500
    plan = empaquetar_bootstrap(
        [
            SeccionBootstrap(
                clave="nucleo",
                titulo="N",
                lineas=("corta", linea_larga),
            )
        ],
        presupuesto=100,
    )
    assert plan.secciones[0].lineas == ("corta",)
    assert plan.omitidos["nucleo"] == 1


def test_empaquetar_seccion_vacia_no_cuenta() -> None:
    """Secciones sin líneas no consumen presupuesto ni aparecen."""
    plan = empaquetar_bootstrap(
        [_seccion("vacia", 0), _seccion("llena", 2)], presupuesto=100
    )
    assert [s.clave for s in plan.secciones] == ["llena"]


def test_snapshot_incluye_medidor_y_omitidos() -> None:
    """El bloque frozen declara uso y lo que quedó fuera."""
    plan = empaquetar_bootstrap(
        [_seccion("censores", 1), _seccion("loops", 10)],
        presupuesto=80,
    )
    texto = formatear_snapshot(plan)
    assert "NOUS MEMORY" in texto
    assert "/" in texto  # usado/presupuesto
    assert "omitidos" in texto.lower() or "+" in texto


def test_snapshot_sin_omitidos_no_muestra_resto() -> None:
    """Si todo cupo, el bloque no menciona memoria adicional."""
    plan = empaquetar_bootstrap([_seccion("a", 2)], presupuesto=0)
    texto = formatear_snapshot(plan)
    assert "+" not in texto.split("NOUS MEMORY")[1]


# ---------- verificación pre-acción ----------


def _censor(
    severidad: str, similitud: float = 0.8
) -> CensorCoincidente:
    return CensorCoincidente(
        id="c1",
        contenido="nunca hagas push a main",
        severidad=severidad,
        dominio="git",
        similitud=similitud,
    )


def test_veredicto_sin_coincidencias_permite() -> None:
    """Sin censores activados la acción queda permitida."""
    v = veredicto_censores([])
    assert v.permitido
    assert not v.censores


def test_veredicto_bloquear_gana_sobre_advertir() -> None:
    """Un solo censor bloqueante veta aunque haya advertencias."""
    v = veredicto_censores([_censor("advertir"), _censor("bloquear")])
    assert not v.permitido
    assert len(v.censores) == 2


def test_veredicto_solo_advertir_permite_con_cautela() -> None:
    """Advertir nunca bloquea — informa."""
    v = veredicto_censores([_censor("advertir")])
    assert v.permitido
    assert "warning" in v.motivo


def test_parsear_censor_defaults_advierten() -> None:
    """Sin metadatos el censor advierte — nunca bloquea por defecto."""
    severidad, dominio = parsear_censor(None)
    assert severidad == "advertir"
    assert dominio is None
    severidad, dominio = parsear_censor(
        {"severidad": "bloquear", "dominio": "db"}
    )
    assert severidad == "bloquear"
    assert dominio == "db"


# ---------- calibración Brier ----------


def test_brier_oraculo_es_cero() -> None:
    """Confianza 1.0 + acierto = score perfecto."""
    assert brier_score(1.0, True) == 0.0
    assert brier_score(0.0, False) == 0.0


def test_brier_peor_caso_es_uno() -> None:
    """Certeza total equivocada paga el máximo."""
    assert brier_score(1.0, False) == 1.0
    assert brier_score(0.0, True) == 1.0


def test_reporte_sin_resueltas_no_inventa_metrica() -> None:
    """Sin datos brier es None — honestidad primero."""
    r = reporte_calibracion([], n_pendientes=3)
    assert r.brier is None
    assert r.n_resueltas == 0


def test_reporte_agrega_curva_y_sesgo() -> None:
    """La curva agrupa por decil y el sesgo refleja sobreconfianza."""
    resueltas = [
        PrediccionResuelta(confianza=0.9, acierto=i % 2 == 0)
        for i in range(10)
    ]
    r = reporte_calibracion(resueltas)
    assert r.n_resueltas == 10
    assert r.brier is not None and 0 < r.brier < 1
    assert r.acierto_empirico == 0.5
    # Sobreconfianza: declaró 0.9, acertó 0.5.
    assert r.sesgo is not None and r.sesgo > 0
    assert len(r.curva) == 1
    assert r.curva[0].desde == 0.9


def test_reporte_confianza_uno_cae_en_ultimo_decil() -> None:
    """confianza=1.0 pertenece al bucket 0.9–1.0, no se pierde."""
    r = reporte_calibracion(
        [PrediccionResuelta(confianza=1.0, acierto=True)]
    )
    assert len(r.curva) == 1
    assert r.curva[0].desde == 0.9


# ---------- handoff ----------


def test_handoff_incluye_secciones_con_datos() -> None:
    """Cada fuente poblada produce su sección en el documento."""
    md = generar_handoff(
        agente="devin",
        inicio=_AHORA - 3600,
        fin=_AHORA,
        resumen="se cerró la migración 0014",
        escritos=[{"tipo": "hecho", "contenido": "bootstrap quedó"}],
        loops=[{"contenido": "pagar dominio", "dias_abierto": 3}],
        nucleo=[{"contenido": "preferencia: siempre vim"}],
        censores=[{"contenido": "no push a main", "severidad": "bloquear"}],
    )
    for seccion in (
        "Guardrails",
        "Loops abiertos",
        "esta sesión dejó",
        "Núcleo curado",
        "pagar dominio",
        "BLOQUEAR",
    ):
        assert seccion in md


def test_handoff_sin_datos_no_imprime_secciones_vacias() -> None:
    """Un handoff mínimo solo trae encabezado — sin ruido."""
    md = generar_handoff(
        agente="x",
        inicio=_AHORA,
        fin=_AHORA,
        resumen=None,
        escritos=[],
        loops=[],
        nucleo=[],
        censores=[],
    )
    assert "##" not in md
    assert "Handoff" in md


# ---------- grace floor ----------


def test_grace_floor_nunca_evocado_joven_sobrevive() -> None:
    """Nunca usada, retención muerta y < 30d: la gracia la salva."""
    plan = decidir_consolidacion(
        [
            _evento(
                "joven",
                dias=20,
                dimension=Dimension.SOMATICA,
                amplitud=0.3,
                refuerzos=0,
                ultimo_acceso=None,
                weight=0.5,
            )
        ],
        ahora_unix=_AHORA,
    )
    assert "joven" not in plan.archivar


def test_grace_floor_tras_gracia_uso_cero_es_elegible() -> None:
    """Pasada la gracia, cero usos SÍ es evidencia de desechable."""
    plan = decidir_consolidacion(
        [
            _evento(
                "vieja",
                dias=45,
                dimension=Dimension.SOMATICA,
                amplitud=0.3,
                refuerzos=0,
                ultimo_acceso=None,
                weight=0.5,
            )
        ],
        ahora_unix=_AHORA,
    )
    assert "vieja" in plan.archivar


def test_grace_floor_no_aplica_a_evocadas() -> None:
    """Una memoria usada decae por retención normal, sin piso extra."""
    plan = decidir_consolidacion(
        [
            _evento(
                "usada",
                dias=20,
                dimension=Dimension.SOMATICA,
                amplitud=0.3,
                refuerzos=1,
                ultimo_acceso=_AHORA - 18 * 86400,
                weight=0.5,
            )
        ],
        ahora_unix=_AHORA,
    )
    # Somática decae rápido; ya fue evocada → sin protección de gracia.
    assert "usada" in plan.archivar


def test_censor_es_accionable_nunca_se_archiva() -> None:
    """Los censores viven en TIPOS_ACCIONABLES — el sueño no los toca."""
    plan = decidir_consolidacion(
        [
            _evento(
                "c1",
                dias=200,
                amplitud=0.2,
                refuerzos=0,
                tipo="censor",
                weight=0.4,
            )
        ],
        ahora_unix=_AHORA,
    )
    assert "c1" not in plan.archivar
    assert "c1" not in plan.fortalecer
