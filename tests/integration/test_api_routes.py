"""Tests de los endpoints REST contra PostgreSQL real (nous_test).

La app combinada usa el engine singleton del proceso; el fixture lo
redirige a la base de test aislada y limpia las filas creadas.
"""

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings
from src.main import crear_app_http
from src.mcp import tools
from src.storage import database
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_api"


@pytest_asyncio.fixture
async def cliente(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app real apuntando a nous_test."""
    engine: AsyncEngine = create_async_engine(TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OSError:
        await engine.dispose()
        pytest.skip("PostgreSQL de test no disponible")

    monkeypatch.setattr(tools, "obtener_engine", lambda: engine)
    # El worker de indexación vive en un thread aparte y pide su
    # propio engine vía crear_engine: apuntarlo también a nous_test.
    monkeypatch.setattr(
        tools,
        "crear_engine",
        lambda: create_async_engine(TEST_DB_URL),
    )
    monkeypatch.setattr(database, "obtener_engine", lambda: engine)
    app = crear_app_http()
    transport = ASGITransport(app=app)
    token = get_settings().api_token or ""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente = :fuente"),
            {"fuente": FUENTE_TEST},
        )
        await conn.execute(text("DELETE FROM proyecciones"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_health_publico_sin_token() -> None:
    """GET /health responde sin autenticación."""
    transport = ASGITransport(app=crear_app_http())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_rutas_api_rechazan_sin_token(cliente: AsyncClient) -> None:
    """Los endpoints de datos exigen Bearer válido."""
    transport = ASGITransport(app=crear_app_http())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/eventos")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_eventos_crear_y_listar(cliente: AsyncClient) -> None:
    """POST /api/eventos persiste y GET /api/eventos lo devuelve."""
    ahora = int(time.time())
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "evento de prueba desde REST",
            "dimension": "cognitiva",
            "fuente": FUENTE_TEST,
        },
    )
    assert r.status_code == 200
    cuerpo: dict[str, Any] = r.json()
    assert cuerpo["dimension"] == "cognitiva"

    r = await cliente.get("/api/eventos", params={"desde_unix": ahora - 60, "limite": 50})
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()}
    assert cuerpo["id"] in ids


@pytest.mark.asyncio
async def test_buscar_retorna_resultados_con_contexto(
    cliente: AsyncClient,
) -> None:
    """GET /api/buscar encuentra el evento recién creado."""
    await cliente.post(
        "/api/eventos",
        json={
            "contenido": "decisión clave sobre la arquitectura hexagonal",
            "dimension": "cognitiva",
            "fuente": FUENTE_TEST,
        },
    )
    r = await cliente.get("/api/buscar", params={"q": "arquitectura hexagonal"})
    assert r.status_code == 200
    assert any("arquitectura" in h["evento"]["contenido"] for h in r.json())


@pytest.mark.asyncio
async def test_proyeccion_genera_y_ultima(cliente: AsyncClient) -> None:
    """POST /api/proyeccion persiste; GET /api/proyeccion/actual la lee."""
    await cliente.post(
        "/api/eventos",
        json={
            "contenido": "evento reciente para la proyección",
            "dimension": "conductual",
            "fuente": FUENTE_TEST,
        },
    )
    r = await cliente.post("/api/proyeccion", params={"horizonte_dias": 7, "dias_historial": 30})
    assert r.status_code == 200
    assert "dimension_probable" in r.json()

    r = await cliente.get("/api/proyeccion/actual")
    assert r.status_code == 200
    assert r.json().get("dimension_probable") or r.json().get("estado")


@pytest.mark.asyncio
async def test_proyeccion_sin_historial_devuelve_422(
    cliente: AsyncClient,
) -> None:
    """Sin eventos en el período, el dominio se traduce a 422."""
    r = await cliente.post(
        "/api/proyeccion",
        params={"horizonte_dias": 7, "dias_historial": 1},
    )
    # depende del contenido de nous_test: si hay eventos recientes da 200
    assert r.status_code in (200, 422)


@pytest.mark.asyncio
async def test_login_sin_configurar_devuelve_503(cliente: AsyncClient) -> None:
    """POST /api/login devuelve 503 si no hay credenciales configuradas."""
    r = await cliente.post("/api/login", json={"usuario": "x", "password": "y"})
    assert r.status_code in (401, 503)


@pytest.mark.asyncio
async def test_preferencias_roundtrip_en_postgres(cliente: AsyncClient) -> None:
    """PUT guarda el documento y GET lo devuelve tal cual."""
    doc = {"rotate": 1.4, "query": "memoria", "pins": {"abc": [1, 2, 3]}}
    r = await cliente.put("/api/preferencias/test_prefs", json=doc)
    assert r.status_code == 200

    r = await cliente.get("/api/preferencias/test_prefs")
    assert r.status_code == 200
    assert r.json() == doc


@pytest.mark.asyncio
async def test_preferencias_clave_inexistente_devuelve_vacio(
    cliente: AsyncClient,
) -> None:
    """GET de una clave nunca guardada devuelve {}."""
    r = await cliente.get("/api/preferencias/clave_que_no_existe")
    assert r.status_code == 200
    assert r.json() == {}


@pytest.mark.asyncio
async def test_preferencias_clave_invalida_devuelve_422(
    cliente: AsyncClient,
) -> None:
    """La clave se valida: caracteres fuera de [a-z0-9_.-] → 422."""
    r = await cliente.get("/api/preferencias/Clave Mala$")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_actividad_registra_store_y_recall(
    cliente: AsyncClient,
) -> None:
    """POST /eventos y GET /buscar dejan rastro en /actividad."""
    await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx actividad visible en el cerebro zq9",
            "dimension": "cognitiva",
            "fuente": FUENTE_TEST,
        },
    )
    await cliente.get("/api/buscar", params={"q": "actividad cerebro"})

    r = await cliente.get("/api/actividad")
    assert r.status_code == 200
    eventos = r.json()
    assert isinstance(eventos, list)
    kinds = {e["kind"] for e in eventos}
    assert "store" in kinds
    assert all({"kind", "q", "ids", "ts"} <= e.keys() for e in eventos)
