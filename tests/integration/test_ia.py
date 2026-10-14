"""Tests de integración del módulo de proveedores de IA.

Verifican el cifrado pgcrypto real y el CRUD enmascarado sobre
PostgreSQL — sin llamadas de red a proveedores externos.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import ia
from src.storage.repositories import CifradoRepository

pytestmark = pytest.mark.asyncio

DATOS_BASE = {
    "nombre": "OpenAI",
    "base_url": "https://api.openai.com/v1",
    "modelo": "gpt-4o-mini",
    "habilitado": True,
    "incluir_contenido": False,
}


async def test_cifrado_roundtrip(session: AsyncSession) -> None:
    """pgcrypto cifra y descifra el secreto simétricamente."""
    repo = CifradoRepository(session)
    b64 = await repo.cifrar("sk-secreto-de-prueba", "pass-test")
    assert "sk-secreto" not in b64
    assert await repo.descifrar(b64, "pass-test") == "sk-secreto-de-prueba"


async def test_guardar_proveedor_enmascara_la_key(
    session: AsyncSession,
) -> None:
    """El proveedor guardado expone máscara y api_key_set, nunca la key."""
    publico = await ia.guardar_proveedor(
        session, "openai", {**DATOS_BASE, "api_key": "sk-test-1234567890"}
    )
    assert publico["api_key_set"] is True
    assert publico["api_key_mascara"] == "sk-t…7890"
    assert "api_key" not in publico
    assert "api_key_enc" not in publico
    lista = await ia.listar_proveedores(session)
    assert len(lista) == 1
    assert lista[0]["id"] == "openai"


async def test_actualizar_sin_key_conserva_la_cifrada(
    session: AsyncSession,
) -> None:
    """Un PUT sin api_key no borra la ya almacenada."""
    await ia.guardar_proveedor(
        session, "p1", {**DATOS_BASE, "api_key": "sk-vieja-aaaa1111"}
    )
    publico = await ia.guardar_proveedor(
        session, "p1", {**DATOS_BASE, "modelo": "otro-modelo"}
    )
    assert publico["modelo"] == "otro-modelo"
    assert publico["api_key_mascara"] == "sk-v…1111"
    # El cambio persiste en el documento, no solo en la respuesta.
    lista = await ia.listar_proveedores(session)
    assert lista[0]["modelo"] == "otro-modelo"
    # La key sigue descifrable: el proveedor activo la recupera.
    activo = await ia._proveedor_activo(session, "p1")
    assert activo is not None
    assert activo["api_key"] == "sk-vieja-aaaa1111"


async def test_proveedor_inactivo_no_se_elige(
    session: AsyncSession,
) -> None:
    """Un proveedor deshabilitado no participa en la predicción."""
    await ia.guardar_proveedor(
        session,
        "off",
        {**DATOS_BASE, "habilitado": False, "api_key": "sk-x-aaaa1111"},
    )
    assert await ia._proveedor_activo(session, None) is None


async def test_validaciones_de_entrada(session: AsyncSession) -> None:
    """id no seguro y URL no-http son rechazados."""
    with pytest.raises(ValueError):
        await ia.guardar_proveedor(session, "malo/../x", DATOS_BASE)
    with pytest.raises(ValueError):
        await ia.guardar_proveedor(
            session, "p2", {**DATOS_BASE, "base_url": "ftp://x"}
        )


async def test_eliminar_proveedor(session: AsyncSession) -> None:
    """Eliminar borra el registro; repetir devuelve False."""
    await ia.guardar_proveedor(session, "p3", DATOS_BASE)
    assert await ia.eliminar_proveedor(session, "p3") is True
    assert await ia.eliminar_proveedor(session, "p3") is False


async def test_predecir_sin_proveedor_devuelve_motivo(
    session: AsyncSession,
) -> None:
    """Sin proveedor habilitado la predicción degrada, nunca explota."""
    r = await ia.predecir_escenarios(
        session,
        origen=1_800_000_000,
        horizonte_dias=30,
        eventos=[{"ts": 1_800_000_000, "dimension": "cognitiva",
                  "amplitud": 0.5, "contenido": "x"}],
        metas=[],
        loops_abiertos=0,
    )
    assert r["ok"] is False
    assert r["motivo"] == "sin_proveedor_habilitado"
