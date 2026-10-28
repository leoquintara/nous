"""Estilos ANSI para la CLI de Nous — sin dependencias externas.

Respeta la convención ``NO_COLOR`` y solo emite secuencias cuando la
salida es una TTY. En Windows activa ENABLE_VIRTUAL_TERMINAL_PROCESSING
sobre stdout/stderr para que las consolas legacy interpreten ANSI.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any

_HABILITADO: bool | None = None


def _habilitar_vt_windows() -> None:
    """Activa el procesamiento de secuencias ANSI en la consola."""
    windll = getattr(ctypes, "windll", None)  # solo existe en Windows
    kernel32: Any = windll.kernel32 if windll is not None else None
    for identificador in (-11, -12):  # STD_OUTPUT_HANDLE / STD_ERROR_HANDLE
        manija = kernel32.GetStdHandle(identificador)
        modo = ctypes.c_uint32()
        if kernel32.GetConsoleMode(manija, ctypes.byref(modo)):
            kernel32.SetConsoleMode(manija, modo.value | 0x0004)


def habilitado() -> bool:
    """Indica si la salida acepta colores ANSI."""
    global _HABILITADO
    if _HABILITADO is None:
        _HABILITADO = (
            "NO_COLOR" not in os.environ
            and not os.environ.get("NOUS_SIN_COLOR")
            and sys.stdout.isatty()
        )
        if _HABILITADO and sys.platform == "win32":
            _habilitar_vt_windows()
    return _HABILITADO


def estilo(texto: str, *codigos: str) -> str:
    """Envuelve ``texto`` en códigos ANSI si la salida los soporta.

    Args:
        texto: Texto a decorar.
        *codigos: Códigos SGR (``"1"`` negrita, ``"32"`` verde…).

    Returns:
        Texto con escapes ANSI, o el texto plano si no hay TTY.
    """
    if not habilitado() or not codigos:
        return texto
    return f"\x1b[{';'.join(codigos)}m{texto}\x1b[0m"


def ok(texto: str) -> str:
    """Verde — éxito."""
    return estilo(texto, "32")


def error(texto: str) -> str:
    """Rojo — fallo."""
    return estilo(texto, "31")


def aviso(texto: str) -> str:
    """Amarillo — advertencia."""
    return estilo(texto, "33")


def info(texto: str) -> str:
    """Cian — dato operativo."""
    return estilo(texto, "36")


def suave(texto: str) -> str:
    """Tenue — detalle secundario."""
    return estilo(texto, "2")


def negrita(texto: str) -> str:
    """Negrita — énfasis estructural."""
    return estilo(texto, "1")


def acento(texto: str) -> str:
    """Magenta en negrita — identidad de marca (Nous)."""
    return estilo(texto, "35", "1")
