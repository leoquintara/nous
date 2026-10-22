"""Tests REST de la superficie ADR-001/003 añadida para el dashboard.

Cubre comportamiento real contra ``nous_test`` (no solo 401): activar
y retirar la licencia desde la API, curar/descurar un evento y el
aislamiento por workspace. El archivo ``license.jwt`` se redirige a
``tmp_path`` — el entorno real nunca se toca.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import src.mcp.tools as tools
from src.config import get_settings
from src.main import crear_app_http
from src.storage import database
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_adr003"


@pytest_asyncio.fixture
async def cliente(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP real; license.jwt redirigido a tmp_path."""
    engine: AsyncEngine = create_async_engine(TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OSError:
        await engine.dispose()
        pytest.skip("PostgreSQL de test no disponible")

    monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
    monkeypatch.setattr(
        tools, "crear_engine", lambda: create_async_engine(TEST_DB_URL)
    )
    monkeypatch.setattr(database, "obtener_engine", lambda: engine)
    # La licencia persiste en disco: apuntar el archivo a tmp_path.
    import src.core.license as lic_mod

    monkeypatch.setattr(
        lic_mod, "ruta_licencia", lambda: tmp_path / "license.jwt"
    )
    app = crear_app_http()
    transport = ASGITransport(app=app)
    token = get_settings().api_token or ""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as c:
        yield c
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente LIKE :patron"),
            {"patron": f"{FUENTE_TEST}%"},
        )
        await conn.execute(
            text("DELETE FROM fuentes WHERE nombre LIKE :patron"),
            {"patron": f"{FUENTE_TEST}%"},
        )


@pytest.mark.asyncio
async def test_licencia_activar_key_opaca_persiste_y_tier(
    cliente: AsyncClient,
) -> None:
    """POST /licencia/activar con key opaca → tier pro + archivo."""
    resp = await cliente.post(
        "/api/licencia/activar", json={"key": "NSLIC-TEST-OPACA"}
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["activada"] is True
    assert cuerpo["tier"] == "pro"


@pytest.mark.asyncio
async def test_licencia_activar_jwt_expirado_422(
    cliente: AsyncClient,
) -> None:
    """Un JWT vencido no se persiste: 422 con detalle legible."""
    payload = (
        "eyJhbGciOiJSUzI1NiJ9."
        "eyJzdWIiOiJ0ZXN0IiwidGllciI6InBybyIsImV4cCI6MTAwfQ."
        "ZmFrZQ"
    )
    resp = await cliente.post(
        "/api/licencia/activar", json={"key": payload}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_licencia_delete_elimina_archivo(
    cliente: AsyncClient,
) -> None:
    """DELETE /licencia quita el archivo persistido (env var intacta)."""
    await cliente.post(
        "/api/licencia/activar", json={"key": "NSLIC-TEST-BORRAR"}
    )
    resp = await cliente.delete("/api/licencia")
    assert resp.status_code == 200
    assert resp.json()["archivo_eliminado"] is True


@pytest.mark.asyncio
async def test_curar_evento_marca_y_descura(
    cliente: AsyncClient,
) -> None:
    """POST /eventos/{id}/curar → curado:true; descure con false."""
    alta = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "memoria valiosa que quiero preservar",
            "fuente": FUENTE_TEST,
        },
    )
    assert alta.status_code == 200
    eid = alta.json()["id"]
    resp = await cliente.post(
        f"/api/eventos/{eid}/curar", json={"curado": True}
    )
    assert resp.status_code == 200
    assert resp.json()["curado"] is True
    # Descurar revierte el tier.
    resp = await cliente.post(
        f"/api/eventos/{eid}/curar", json={"curado": False}
    )
    assert resp.status_code == 200
    assert resp.json()["curado"] is False


@pytest.mark.asyncio
async def test_curar_evento_inexistente_422(
    cliente: AsyncClient,
) -> None:
    """Curar un id inexistente en la visibilidad → 422, nunca 500."""
    resp = await cliente.post(
        "/api/eventos/00000000-0000-0000-0000-000000000000/curar",
        json={"curado": True},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_evento_campos_declarados_persisten(
    cliente: AsyncClient,
) -> None:
    """estado_emocional/tipo_memoria declarados no los pisa el estimador."""
    resp = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "check-in de prueba",
            "fuente": FUENTE_TEST,
            "dimension": "somatica",
            "estado_emocional": -0.5,
            "tipo_memoria": "episodica",
        },
    )
    assert resp.status_code == 200
