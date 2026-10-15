"""Excepciones del dominio de Nous.

Toda excepción del dominio hereda de :class:`NousError`. Las de
validación también heredan de ``ValueError`` para mantener
compatibilidad con quien capture el tipo builtin.
"""


class NousError(Exception):
    """Base de todas las excepciones del dominio de Nous."""


class ErrorValidacion(NousError, ValueError):
    """Entrada inválida (amplitud fuera de rango, rango invertido, etc.)."""


class EventoNoEncontradoError(NousError):
    """El evento solicitado no existe en la línea de universo."""


class HistorialInsuficienteError(NousError, ValueError):
    """No hay eventos suficientes para la operación (p. ej. proyectar)."""


class ProveedorNoDisponibleError(NousError):
    """Un modelo o servicio local no pudo inicializarse o responder."""


class PermisoDenegadoError(NousError):
    """El rol del llamante no permite la operación (viewer escribiendo, etc.)."""
