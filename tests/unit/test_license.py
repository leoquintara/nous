"""Tests del módulo de licencia local (ADR-001) — parseo JWT."""

from __future__ import annotations

import base64
import json
import time

import pytest

from src.core.license import licencia_actual, parsear_licencia


def _jwt(claims: dict) -> str:
    """Arma un JWT no firmado con los claims dados."""

    def enc(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{enc({'alg': 'RS256', 'typ': 'JWT'})}.{enc(claims)}.Zm9v"


def test_key_vacia_no_licencia():
    assert not parsear_licencia(None).valida
    assert not parsear_licencia("").valida
    assert not parsear_licencia("   ").valida


def test_key_opaca_licencia_pro_honor():
    """Keys opacas legacy licencian por presencia (sistema de honor)."""
    lic = parsear_licencia("nslic_abc123")
    assert lic.valida
    assert lic.tier == "pro"
    assert lic.tiene_feature("consultar_decision")


def test_jwt_valido_sin_pubkey_parsea_claims():
    futuro = int(time.time()) + 86400
    lic = parsear_licencia(
        _jwt({"sub": "leo", "tier": "pro", "exp": futuro})
    )
    assert lic.valida
    assert lic.tier == "pro"
    assert lic.sujeto == "leo"
    assert lic.expira == futuro
    assert not lic.firma_verificada


def test_jwt_expirado_no_licencia():
    pasado = int(time.time()) - 60
    lic = parsear_licencia(_jwt({"tier": "pro", "exp": pasado}))
    assert not lic.valida
    assert lic.expira == pasado


def test_jwt_malformado_no_licencia():
    # 3 partes con payload ilegible → inválida:
    assert not parsear_licencia("xxx.!!!.yyy").valida
    # payload que decodifica pero no es dict:
    lic_lista = _jwt([1, 2])  # claims como lista JSON
    lic_lista = lic_lista.split(".")
    lic_lista[1] = base64.urlsafe_b64encode(b"[1,2]").rstrip(b"=").decode()
    assert not parsear_licencia(".".join(lic_lista)).valida


def test_key_de_dos_partes_cuenta_como_opaca():
    # "a.b" no es JWT — entra como key opaca legacy (honor).
    assert parsear_licencia("a.b").valida


def test_jwt_features_restringen():
    futuro = int(time.time()) + 86400
    lic = parsear_licencia(
        _jwt({"tier": "pro", "exp": futuro, "features": ["brain-3d"]})
    )
    assert lic.valida
    assert lic.tiene_feature("brain-3d")
    assert not lic.tiene_feature("consultar_decision")


def test_licencia_actual_sin_key_no_valida(monkeypatch, tmp_path):
    """Sin NOUS_LICENSE_KEY ni archivo, la licencia no es válida."""
    from src.config import get_settings

    monkeypatch.setenv("NOUS_LICENSE_KEY", "")
    # Aísla ~/.nous/license.jwt — si existe en el host rompería el test.
    monkeypatch.setattr(
        "src.core.license.ruta_licencia",
        lambda: tmp_path / "license.jwt",
    )
    get_settings.cache_clear()
    assert not licencia_actual().valida
    get_settings.cache_clear()


def test_licencia_actual_cae_al_archivo(monkeypatch, tmp_path):
    """Sin env, licencia_actual() lee ~/.nous/license.jwt (fallback)."""
    from src.config import get_settings

    futuro = int(time.time()) + 86400
    ruta = tmp_path / "license.jwt"
    ruta.write_text(_jwt({"tier": "pro", "exp": futuro}) + "\n")
    monkeypatch.setenv("NOUS_LICENSE_KEY", "")
    monkeypatch.setattr("src.core.license.ruta_licencia", lambda: ruta)
    get_settings.cache_clear()
    lic = licencia_actual()
    assert lic.valida
    assert lic.tier == "pro"
    get_settings.cache_clear()


def test_licencia_actual_env_gana_sobre_archivo(monkeypatch, tmp_path):
    """NOUS_LICENSE_KEY tiene prioridad sobre el archivo (deploy)."""
    from src.config import get_settings

    ruta = tmp_path / "license.jwt"
    ruta.write_text(_jwt({"tier": "enterprise", "exp": 9999999999}))
    monkeypatch.setenv("NOUS_LICENSE_KEY", "key_opaca_env")
    monkeypatch.setattr("src.core.license.ruta_licencia", lambda: ruta)
    get_settings.cache_clear()
    lic = licencia_actual()
    assert lic.valida
    assert lic.tier == "pro"  # opaca = pro honor, no enterprise del archivo
    get_settings.cache_clear()


def test_leer_licencia_archivo_ausente_devuelve_none(tmp_path, monkeypatch):
    """Sin archivo persistido, el fallback no aporta licencia."""
    from src.core.license import leer_licencia_archivo

    monkeypatch.setattr(
        "src.core.license.ruta_licencia",
        lambda: tmp_path / "license.jwt",
    )
    assert leer_licencia_archivo() is None


@pytest.mark.asyncio
async def test_consultar_decision_sin_licencia_rechaza(
    monkeypatch, tmp_path
):
    """El consejero es Pro: sin licencia → CupoExcedidoError (402).

    El gate corre antes de resolver contexto o tocar la DB — el test
    es unitario aunque la tool sea async.
    """
    from src.config import get_settings
    from src.core.exceptions import CupoExcedidoError
    from src.mcp.tools import _consultar_decision

    monkeypatch.setenv("NOUS_LICENSE_KEY", "")
    monkeypatch.setattr(
        "src.core.license.ruta_licencia",
        lambda: tmp_path / "license.jwt",
    )
    get_settings.cache_clear()
    try:
        with pytest.raises(CupoExcedidoError):
            await _consultar_decision("¿acepto X?", None)
    finally:
        get_settings.cache_clear()
