"""Tests del sistema de identidad renderizable (módulo puro).

Cubre el catálogo de claves, el gate de faltantes, el render con
sustitución segura y la huella del banco para el read-back ritual.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import ErrorValidacion
from src.core.persona import (
    ARCHIVOS,
    catalogo,
    faltantes,
    hash_banco,
    render,
)

_BANCO_COMPLETO = {
    "persona.nombre": "Qwen",
    "persona.mision": "Acompañar a Rubén en su trabajo.",
    "persona.voz": "Directo, técnico, sin relleno.",
    "persona.valores": "Precisión, memoria durable, local-first.",
    "persona.limites": "No ejecutar acciones destructivas sin confirmar.",
    "usuario.nombre": "Rubén",
    "usuario.rol": "Fundador de Quintara AI.",
    "usuario.contexto": "CDMX, equipo Ruba, stack Windows+Docker.",
    "usuario.comunicacion": "Español, conciso, tablas cuando comparan.",
    "politica.autonomia": "Leer, buscar, recordar, renderizar archivos.",
    "politica.confirmar": "Borrar, olvidar, escribir a workspaces.",
    "politica.prohibido": "Nunca guardar tokens ni passwords.",
    "ritmo.checkin": "Al iniciar cada sesión y tras 30 min de silencio.",
    "ritmo.revisiones": "Semanal: compromisos; mensual: banco completo.",
}


def test_catalogo_cubre_los_cuatro_archivos() -> None:
    assert set(catalogo()) == {
        "SOUL.md",
        "USER.md",
        "ACCESS_POLICY.md",
        "HEARTBEAT.md",
    }
    assert tuple(catalogo()) == ARCHIVOS


def test_faltantes_vacio_con_banco_completo() -> None:
    for archivo in ARCHIVOS:
        assert faltantes(archivo, _BANCO_COMPLETO) == ()


def test_faltantes_reporta_solo_lo_que_le_toca_al_archivo() -> None:
    banco = {"persona.nombre": "Qwen", "usuario.nombre": "Rubén"}
    assert faltantes("SOUL.md", banco) == (
        "persona.mision",
        "persona.voz",
        "persona.valores",
        "persona.limites",
    )
    # USER.md solo reclama sus propias claves, no las de SOUL.
    assert faltantes("USER.md", banco) == (
        "usuario.rol",
        "usuario.contexto",
        "usuario.comunicacion",
    )


def test_faltantes_ignora_valores_vacios_o_no_texto() -> None:
    banco = {
        "persona.nombre": "   ",
        "persona.mision": None,
        "persona.voz": "ok",
        "persona.valores": "ok",
        "persona.limites": "ok",
    }
    assert faltantes("SOUL.md", banco) == (
        "persona.nombre",
        "persona.mision",
    )


def test_faltantes_archivo_desconocido_rechaza() -> None:
    with pytest.raises(ErrorValidacion, match="desconocido"):
        faltantes("NOPE.md", _BANCO_COMPLETO)


def test_render_sustituye_y_averigua_sobre_el_banco() -> None:
    soul = render("SOUL.md", _BANCO_COMPLETO)
    assert "SOUL — Qwen" in soul
    assert "Directo, técnico" in soul
    # El aviso anti-edición manual encabeza el archivo.
    assert "nous persona set" in soul
    # Las claves de otros archivos no se filtran.
    assert "Fundador de Quintara" not in soul


def test_render_rechaza_con_faltantes_listados() -> None:
    banco = dict(_BANCO_COMPLETO)
    del banco["ritmo.checkin"]
    with pytest.raises(ErrorValidacion, match="ritmo.checkin"):
        render("HEARTBEAT.md", banco)


def test_render_no_rompe_con_signos_de_dolar_en_respuestas() -> None:
    banco = dict(_BANCO_COMPLETO)
    banco["politica.prohibido"] = "Nunca transferir $USD sin confirmar."
    policy = render("ACCESS_POLICY.md", banco)
    assert "$USD" in policy


def test_hash_estable_e_independiente_del_orden() -> None:
    invertido = dict(reversed(list(_BANCO_COMPLETO.items())))
    assert hash_banco(_BANCO_COMPLETO) == hash_banco(invertido)
    assert len(hash_banco(_BANCO_COMPLETO)) == 12


def test_hash_cambia_al_cambiar_una_respuesta() -> None:
    otro = dict(_BANCO_COMPLETO, **{"persona.nombre": "Otro"})
    assert hash_banco(otro) != hash_banco(_BANCO_COMPLETO)
