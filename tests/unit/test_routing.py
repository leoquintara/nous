"""Tests del clasificador de intención de consulta (puro, sin I/O)."""

from src.core.routing import (
    IntencionConsulta,
    clasificar_intencion,
    direccion_temporal,
)


def test_entidad_quien_es_extrae_nombre() -> None:
    intencion, nombre = clasificar_intencion("quien es oscar y que hace")
    assert intencion == IntencionConsulta.ENTIDAD
    assert nombre == "oscar"


def test_entidad_que_es_con_articulo() -> None:
    intencion, nombre = clasificar_intencion("que es el react template")
    assert intencion == IntencionConsulta.ENTIDAD
    assert nombre == "react template"


def test_entidad_dime_sobre() -> None:
    intencion, nombre = clasificar_intencion("dime de samay")
    assert intencion == IntencionConsulta.ENTIDAD
    assert nombre == "samay"


def test_entidad_dime_quien_es_con_prefijo() -> None:
    intencion, nombre = clasificar_intencion(
        "dime quien es samay y cual es su rol"
    )
    assert intencion == IntencionConsulta.ENTIDAD
    assert nombre == "samay"


def test_entidad_sabes_quien_es() -> None:
    intencion, nombre = clasificar_intencion("sabes quien es carlos")
    assert intencion == IntencionConsulta.ENTIDAD
    assert nombre == "carlos"


def test_entidad_cual_es_el_rol_de() -> None:
    intencion, nombre = clasificar_intencion("cual es el rol de samay")
    assert intencion == IntencionConsulta.ENTIDAD
    assert nombre == "samay"


def test_que_es_lo_pronombre_es_tema() -> None:
    intencion, nombre = clasificar_intencion("que es eso")
    assert intencion == IntencionConsulta.TEMA
    assert nombre is None
    intencion, nombre = clasificar_intencion("que es lo que falla")
    assert intencion == IntencionConsulta.TEMA
    assert nombre is None


def test_que_es_lo_adjetivo_no_es_entidad() -> None:
    intencion, nombre = clasificar_intencion(
        "que es lo mas delicado de la arquitectura gatsby"
    )
    assert intencion == IntencionConsulta.TEMA
    assert nombre is None


def test_temporal_cuando() -> None:
    intencion, nombre = clasificar_intencion(
        "cuando fue el primer mensaje de samay"
    )
    assert intencion == IntencionConsulta.TEMPORAL
    assert nombre is None


def test_temporal_mas_reciente() -> None:
    intencion, _ = clasificar_intencion(
        "cual es el dev mas nuevo en digitalbooting"
    )
    assert intencion == IntencionConsulta.TEMPORAL


def test_temporal_primero() -> None:
    intencion, _ = clasificar_intencion("el primer proyecto de ricardo")
    assert intencion == IntencionConsulta.TEMPORAL


def test_tema_pregunta_generica() -> None:
    intencion, nombre = clasificar_intencion(
        "como diferencias un proyecto react v1 de v2"
    )
    assert intencion == IntencionConsulta.TEMA
    assert nombre is None


def test_direccion_primero_es_ascendente() -> None:
    assert direccion_temporal("el primer proyecto de ricardo") == "asc"
    assert direccion_temporal("cuando aparecio samay") == "asc"


def test_direccion_reciente_es_descendente() -> None:
    assert (
        direccion_temporal("cual es el dev mas nuevo") == "desc"
    )
    assert (
        direccion_temporal("el ultimo proyecto desplegado") == "desc"
    )


def test_consulta_vacia_es_tema() -> None:
    intencion, nombre = clasificar_intencion("   ")
    assert intencion == IntencionConsulta.TEMA
    assert nombre is None
