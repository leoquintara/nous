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


class CupoExcedidoError(NousError):
    """El plan libre self-hosted alcanzó su límite (sin licencia)."""


class PermisoDenegadoError(NousError):
    """El rol del llamante no permite la operación (viewer escribiendo, etc.)."""


class CupoCuradoError(NousError):
    """El tier curado llegó a su presupuesto — consolidar antes de seguir.

    Overflow-pressure: el núcleo no puede crecer sin costo; curar algo
    nuevo exige hacer espacio (descurar o consolidar) — la escasez es
    la higiene de la memoria.
    """
