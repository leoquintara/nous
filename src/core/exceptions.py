"""Excepciones del dominio de Nous."""


class NousError(Exception):
    """Excepción base de todas las de Nous."""


class EventoNoEncontradoError(NousError):
    """El evento solicitado no existe en la línea de universo."""


class ProveedorExternoError(NousError):
    """Fallo al contactar un proveedor externo configurado por el usuario."""


class ConfiguracionError(NousError):
    """La configuración proporcionada es inválida o incompleta."""
