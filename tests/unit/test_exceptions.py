"""Tests de la jerarquía de excepciones del dominio."""

import pytest

from src.core.exceptions import (
    ErrorValidacion,
    EventoNoEncontradoError,
    HistorialInsuficienteError,
    NousError,
    ProveedorNoDisponibleError,
)


def test_todas_heredan_de_nous_error() -> None:
    """Verifica que toda excepción del dominio deriva de NousError."""
    for clase in (
        ErrorValidacion,
        EventoNoEncontradoError,
        HistorialInsuficienteError,
        ProveedorNoDisponibleError,
    ):
        assert issubclass(clase, NousError)


def test_errores_de_validacion_son_value_error() -> None:
    """Verifica compatibilidad con quien capture ValueError builtin."""
    assert issubclass(ErrorValidacion, ValueError)
    assert issubclass(HistorialInsuficienteError, ValueError)
    with pytest.raises(ValueError):
        raise ErrorValidacion("amplitud fuera de rango")
    with pytest.raises(ValueError):
        raise HistorialInsuficienteError("sin historial")


def test_proyectar_lanza_historial_insuficiente() -> None:
    """Verifica que temporal.py usa la excepción del dominio."""
    from src.core.temporal import proyectar

    with pytest.raises(HistorialInsuficienteError):
        proyectar([], ahora=1)
