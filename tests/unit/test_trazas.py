"""Tests del clasificador de origen y el buffer de llamadas."""

import pytest

from src.core import trazas


def test_origen_declarado_gana_sobre_user_agent():
    """El header x-nous-origen es la fuente de verdad del origen."""
    origen, _ = trazas.clasificar_origen("dashboard", "Mozilla/5.0")
    assert origen == "dashboard"


def test_origen_declarado_se_trunca_a_80():
    """Un header exagerado no puede inflar la columna."""
    origen, _ = trazas.clasificar_origen("x" * 200, "ua")
    assert len(origen) == 80


@pytest.mark.parametrize(
    ("ua", "esperado"),
    [
        ("Claude-Desktop/1.0", "agente:claude"),
        ("Cursor/1.2", "agente:cursor"),
        ("Windsurf", "agente:windsurf"),
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/120", "usuario:navegador"),
        ("python-httpx/0.28", "agente:programa"),
        ("algo-raro", "desconocido"),
    ],
)
def test_clasificacion_por_user_agent(ua: str, esperado: str):
    """La familia del UA clasifica agente de IA vs usuario humano."""
    origen, agente = trazas.clasificar_origen(None, ua)
    assert origen == esperado
    assert agente == ua[:200]


def test_registrar_encola_sin_bloquear():
    """registrar() solo encola en memoria — nunca toca DB ni async."""
    trazas.registrar("GET", "/api/eventos", "dashboard", "ua", 12, 200, False)
    assert trazas.pendientes() >= 1
    trazas._buffer.clear()


def test_registrar_descarta_las_mas_viejas_al_toparse():
    """El buffer acotado sacrifica lo más viejo antes de crecer."""
    for _ in range(trazas._MAX_BUFFER + 5):
        trazas.registrar("GET", "/x", "o", "a", 1, 200, False)
    assert trazas.pendientes() == trazas._MAX_BUFFER
    trazas._buffer.clear()
