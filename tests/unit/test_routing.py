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
