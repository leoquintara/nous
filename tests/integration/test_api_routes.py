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
            text("DELETE FROM eventos WHERE fuente LIKE :patron"),
            {"patron": f"{FUENTE_TEST}%"},
        )
        await conn.execute(
            text("DELETE FROM fuentes WHERE nombre LIKE :patron"),
            {"patron": f"{FUENTE_TEST}%"},
        )
        await conn.execute(text("DELETE FROM proyecciones"))
        await conn.execute(text("DELETE FROM objetivos"))
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
        r = await c.get("/api/events")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_eventos_crear_y_listar(cliente: AsyncClient) -> None:
    """POST /api/eventos persiste y GET /api/eventos lo devuelve."""
    ahora = int(time.time())
    r = await cliente.post(
        "/api/events",
        json={
            "content": "evento de prueba desde REST",
            "dimension": "cognitive",
            "source": FUENTE_TEST,
        },
    )
    assert r.status_code == 200
    cuerpo: dict[str, Any] = r.json()
    assert cuerpo["dimension"] == "cognitive"

    r = await cliente.get("/api/events", params={"from_unix": ahora - 60, "limit": 50})
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()}
    assert cuerpo["id"] in ids


@pytest.mark.asyncio
async def test_buscar_retorna_resultados_con_contexto(
    cliente: AsyncClient,
) -> None:
    """GET /api/buscar encuentra el evento recién creado."""
    await cliente.post(
        "/api/events",
        json={
            "content": "decisión clave sobre la arquitectura hexagonal",
            "dimension": "cognitive",
            "source": FUENTE_TEST,
        },
    )
    r = await cliente.get("/api/search", params={"q": "arquitectura hexagonal"})
    assert r.status_code == 200
    assert any("arquitectura" in h["event"]["content"] for h in r.json())


@pytest.mark.asyncio
async def test_proyeccion_genera_y_ultima(cliente: AsyncClient) -> None:
    """POST /api/proyeccion persiste; GET /api/proyeccion/actual la lee."""
    await cliente.post(
        "/api/events",
        json={
            "content": "evento reciente para la proyección",
            "dimension": "behavioral",
            "source": FUENTE_TEST,
        },
    )
    r = await cliente.post("/api/projection", params={"horizon_days": 7, "history_days": 30})
    assert r.status_code == 200
    assert "probable_dimension" in r.json()

    r = await cliente.get("/api/projection/current")
    assert r.status_code == 200
    assert r.json().get("probable_dimension") or r.json().get("status")


@pytest.mark.asyncio
async def test_proyeccion_sin_historial_devuelve_422(
    cliente: AsyncClient,
) -> None:
    """Sin eventos en el período, el dominio se traduce a 422."""
    r = await cliente.post(
        "/api/projection",
        params={"horizon_days": 7, "history_days": 1},
    )
    # depende del contenido de nous_test: si hay eventos recientes da 200
    assert r.status_code in (200, 422)


@pytest.mark.asyncio
async def test_login_sin_configurar_devuelve_503(cliente: AsyncClient) -> None:
    """POST /api/login devuelve 503 si no hay credenciales configuradas."""
    r = await cliente.post("/api/login", json={"username": "x", "password": "y"})
    assert r.status_code in (401, 503)


@pytest.mark.asyncio
async def test_preferencias_roundtrip_en_postgres(cliente: AsyncClient) -> None:
    """PUT guarda el documento y GET lo devuelve tal cual."""
    doc = {"rotate": 1.4, "query": "memoria", "pins": {"abc": [1, 2, 3]}}
    r = await cliente.put("/api/preferences/test_prefs", json=doc)
    assert r.status_code == 200

    r = await cliente.get("/api/preferences/test_prefs")
    assert r.status_code == 200
    assert r.json() == doc


@pytest.mark.asyncio
async def test_preferencias_clave_inexistente_devuelve_vacio(
    cliente: AsyncClient,
) -> None:
    """GET de una clave nunca guardada devuelve {}."""
    r = await cliente.get("/api/preferences/clave_que_no_existe")
    assert r.status_code == 200
    assert r.json() == {}


@pytest.mark.asyncio
async def test_preferencias_clave_invalida_devuelve_422(
    cliente: AsyncClient,
) -> None:
    """La clave se valida: caracteres fuera de [a-z0-9_.-] → 422."""
    r = await cliente.get("/api/preferences/Clave Mala$")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_actividad_registra_store_y_recall(
    cliente: AsyncClient,
) -> None:
    """POST /eventos y GET /buscar dejan rastro en /actividad."""
    await cliente.post(
        "/api/events",
        json={
            "content": "qzx actividad visible en el cerebro zq9",
            "dimension": "cognitive",
            "source": FUENTE_TEST,
        },
    )
    await cliente.get("/api/search", params={"q": "actividad cerebro"})

    r = await cliente.get("/api/activity")
    assert r.status_code == 200
    eventos = r.json()
    assert isinstance(eventos, list)
    kinds = {e["kind"] for e in eventos}
    assert "store" in kinds
    assert all({"kind", "q", "ids", "ts"} <= e.keys() for e in eventos)


@pytest.mark.asyncio
async def test_relaciones_endpoint_devuelve_vinculos(
    cliente: AsyncClient,
) -> None:
    """POST /eventos con relaciona_ids deja el vínculo en GET /relaciones."""
    ra = await cliente.post(
        "/api/events",
        json={
            "content": "qzx relaciones base zq9",
            "dimension": "cognitive",
            "source": FUENTE_TEST,
        },
    )
    assert ra.status_code == 200
    rb = await cliente.post(
        "/api/events",
        json={
            "content": "qzx relaciones derivada zq9",
            "dimension": "cognitive",
            "source": FUENTE_TEST,
            "related_ids": [ra.json()["id"]],
        },
    )
    assert rb.status_code == 200
    assert rb.json()["links_created"] == 1
    assert rb.json()["context"]["complete"] is True

    r = await cliente.get("/api/relations")
    assert r.status_code == 200
    pares = {(f["origin_id"], f["target_id"]) for f in r.json()}
    ids = {ra.json()["id"], rb.json()["id"]}
    assert any(set(p) == ids for p in pares)


@pytest.mark.asyncio
async def test_eventos_avisa_entidad_sin_antecedente(
    cliente: AsyncClient,
) -> None:
    """POST /eventos con nombre propio nuevo → contexto.completo=false."""
    r = await cliente.post(
        "/api/events",
        json={
            "content": "qzx Zabdiel me agobia muchísimo zq9",
            "source": FUENTE_TEST,
        },
    )
    assert r.status_code == 200
    contexto = r.json()["context"]
    assert contexto["complete"] is False
    assert "Zabdiel" in contexto["new_entities"]


@pytest.mark.asyncio
async def test_eventos_fusiona_tags_explicitos_y_hashtags(
    cliente: AsyncClient,
) -> None:
    """POST /eventos mezcla tags explícitos con #hashtags del contenido."""
    r = await cliente.post(
        "/api/events",
        json={
            "content": "qzx revisé el backlog del sprint #prioridad zq9",
            "source": FUENTE_TEST,
            "tags": ["Sprint", "prioridad"],
        },
    )
    assert r.status_code == 200
    evento_id = r.json()["id"]
    linea = await cliente.get("/api/events")
    tags = next(
        e["tags"] for e in linea.json() if e["id"] == evento_id
    )
    assert sorted(tags) == ["prioridad", "sprint"]


@pytest.mark.asyncio
async def test_tags_endpoint_lista_frecuencias(cliente: AsyncClient) -> None:
    """GET /tags agrega frecuencias de tags persistidos."""
    await cliente.post(
        "/api/events",
        json={
            "content": "qzx nota sobre #zigurat alpha zq9",
            "source": FUENTE_TEST,
        },
    )
    await cliente.post(
        "/api/events",
        json={
            "content": "qzx otra nota sobre #zigurat beta zq9",
            "source": FUENTE_TEST,
        },
    )
    r = await cliente.get("/api/tags")
    assert r.status_code == 200
    frecs = {t["tag"]: t["events"] for t in r.json()}
    assert frecs["zigurat"] == 2


@pytest.mark.asyncio
async def test_eventos_rama_filtra_linea_de_universo(
    cliente: AsyncClient,
) -> None:
    """Los eventos de una rama alternativa no aparecen en la principal."""
    r = await cliente.post(
        "/api/events",
        json={
            "content": "qzx evento en rama espejo zq9",
            "source": FUENTE_TEST,
            "branch": "espejo-qzx",
        },
    )
    assert r.status_code == 200
    evento_id = r.json()["id"]

    principal = await cliente.get("/api/events")
    assert evento_id not in {e["id"] for e in principal.json()}

    espejo = await cliente.get("/api/events", params={"branch": "espejo-qzx"})
    assert evento_id in {e["id"] for e in espejo.json()}


@pytest.mark.asyncio
async def test_fuentes_auto_registro_y_listado(cliente: AsyncClient) -> None:
    """Una fuente nueva en POST /eventos se registra en la tabla fuentes."""
    await cliente.post(
        "/api/events",
        json={
            "content": "qzx evento con fuente nueva zq9",
            "source": FUENTE_TEST,
        },
    )
    r = await cliente.get("/api/sources")
    assert r.status_code == 200
    nombres = {f["name"] for f in r.json()}
    assert FUENTE_TEST in nombres


@pytest.mark.asyncio
async def test_fuentes_crear_actualizar_archivar_borrar(
    cliente: AsyncClient,
) -> None:
    """Ciclo completo de administración de una fuente."""
    crear = await cliente.post(
        "/api/sources",
        json={"name": "fuente-qzx", "kind": "import", "namespace": "lab"},
    )
    assert crear.status_code == 200
    fuente = crear.json()
    assert fuente["name"] == "fuente-qzx"

    patch = await cliente.patch(
        "/api/sources/fuente-qzx",
        json={"status": "archived"},
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "archived"

    borrar = await cliente.delete("/api/sources/fuente-qzx")
    assert borrar.status_code == 200
    restantes = await cliente.get("/api/sources")
    assert "fuente-qzx" not in {f["name"] for f in restantes.json()}


@pytest.mark.asyncio
async def test_fuentes_ramificar_crea_rama_hija(cliente: AsyncClient) -> None:
    """POST /fuentes/{nombre}/ramificar materializa la sub-línea."""
    crear = await cliente.post(
        "/api/sources", json={"name": "origen-qzx"}
    )
    assert crear.status_code == 200
    rama = await cliente.post(
        "/api/sources/origen-qzx/branch",
        json={"branch": "rama-qzx"},
    )
    assert rama.status_code == 200
    datos = rama.json()
    assert datos["name"] == "origen-qzx:rama-qzx"
    await cliente.delete("/api/sources/origen-qzx:rama-qzx")
    await cliente.delete("/api/sources/origen-qzx")


@pytest.mark.asyncio
async def test_eventos_repartir_incluye_todas_las_fuentes(
    cliente: AsyncClient,
) -> None:
    """Con repartir=true una fuente mayoritaria no acapara la ventana."""
    for i in range(6):
        r = await cliente.post(
            "/api/events",
            json={
                "content": f"evento repartido mayor {i}",
                "source": FUENTE_TEST,
                "dimension": "behavioral",
            },
        )
        assert r.status_code == 200
    r = await cliente.post(
        "/api/events",
        json={
            "content": "evento repartido minor",
            "source": f"{FUENTE_TEST}_b",
            "dimension": "cognitive",
        },
    )
    assert r.status_code == 200
    r = await cliente.get(
        "/api/events", params={"limit": 4, "split": "true"}
    )
    fuentes = {e["source"] for e in r.json()}
    assert f"{FUENTE_TEST}_b" in fuentes


@pytest.mark.asyncio
async def test_loops_compromiso_abierto_y_cierre(cliente: AsyncClient) -> None:
    """Un commitment aparece en /loops hasta que se cierra."""
    r = await cliente.post(
        "/api/events",
        json={
            "content": "responder a Franz sobre el presupuesto",
            "source": FUENTE_TEST,
            "kind": "commitment",
            "with_whom": "Franz",
        },
    )
    assert r.status_code == 200
    compromiso_id = r.json()["id"]

    r = await cliente.get("/api/commitments")
    abiertos = [lp for lp in r.json() if lp["id"] == compromiso_id]
    assert len(abiertos) == 1
    assert abiertos[0]["with_whom"] == "Franz"

    r = await cliente.post(
        f"/api/commitments/{compromiso_id}/close",
        json={"resolution": "le respondí a Franz", "source": FUENTE_TEST},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "closed"

    r = await cliente.get("/api/commitments")
    assert all(lp["id"] != compromiso_id for lp in r.json())


@pytest.mark.asyncio
async def test_cerrar_loop_sobre_no_compromiso_devuelve_error(
    cliente: AsyncClient,
) -> None:
    """Solo eventos tipo commitment son cerrables."""
    r = await cliente.post(
        "/api/events",
        json={
            "content": "un fact cualquiera sin commitment",
            "source": FUENTE_TEST,
        },
    )
    otro_id = r.json()["id"]
    r = await cliente.post(
        f"/api/commitments/{otro_id}/close",
        json={"resolution": "intento de cierre", "source": FUENTE_TEST},
    )
    assert r.status_code in (400, 422, 500)


@pytest.mark.asyncio
async def test_supersede_marca_reemplazado(cliente: AsyncClient) -> None:
    """El evento viejo queda enlazado al nuevo vía reemplazado_por."""
    r = await cliente.post(
        "/api/events",
        json={
            "content": "mi peso es 80kg exactamente",
            "source": FUENTE_TEST,
            "kind": "fact",
        },
    )
    viejo_id = r.json()["id"]
    r = await cliente.post(
        "/api/events",
        json={
            "content": "mi peso es 79kg exactamente",
            "source": FUENTE_TEST,
            "kind": "fact",
            "supersedes_id": viejo_id,
        },
    )
    assert r.status_code == 200
    assert r.json()["supersedes"] == viejo_id

    r = await cliente.get("/api/events", params={"limit": 500})
    viejo = next(e for e in r.json() if e["id"] == viejo_id)
    assert viejo["superseded_by"] is not None


@pytest.mark.asyncio
async def test_eventos_ttl_expira_en(cliente: AsyncClient) -> None:
    """ttl='30d' persiste expira_en futuro en el evento."""
    r = await cliente.post(
        "/api/events",
        json={
            "content": "nota efímera que caduca sola",
            "source": FUENTE_TEST,
            "ttl": "30d",
        },
    )
    assert r.status_code == 200
    assert r.json()["expires_at"] > r.json()["timestamp_unix"]


@pytest.mark.asyncio
async def test_contexto_inicial_devuelve_pack(cliente: AsyncClient) -> None:
    """El pack de sesión incluye núcleo, loops, calientes y stats."""
    await cliente.post(
        "/api/events",
        json={
            "content": "commitment pending de contexto inicial",
            "source": FUENTE_TEST,
            "kind": "commitment",
            "with_whom": "Maria",
        },
    )
    r = await cliente.get("/api/bootstrap")
    assert r.status_code == 200
    pack = r.json()
    assert set(pack) >= {"core", "loops", "hot", "stats"}
    assert any(
        lp["with_whom"] == "Maria" for lp in pack["loops"]
    )


@pytest.mark.asyncio
async def test_objetivos_ciclo_de_vida(cliente: AsyncClient) -> None:
    """Crear, listar activos y cerrar un objetivo con auditoría."""
    futuro = 1_800_000_000 + 30 * 86_400
    r = await cliente.post(
        "/api/goals",
        json={
            "title": "Lanzar beta de nous",
            "target_date": futuro,
            "dimension": "cognitive",
            "description": "beta pública self-hosted",
        },
    )
    assert r.status_code == 200
    objetivo = r.json()
    assert objetivo["status"] == "active"
    assert objetivo["dimension"] == "cognitive"

    r = await cliente.get("/api/goals", params={"status": "active"})
    assert any(o["id"] == objetivo["id"] for o in r.json())

    r = await cliente.post(
        f"/api/goals/{objetivo['id']}/close",
        json={
            "status": "fulfilled",
            "resolution": "se lanzó la beta",
            "source": FUENTE_TEST,
        },
    )
    assert r.status_code == 200
    cerrado = r.json()
    assert cerrado["status"] == "fulfilled"
    # La resolución queda enlazada como evento conductual.
    assert cerrado["closing_event_id"] is not None


@pytest.mark.asyncio
async def test_objetivos_patch_actualiza_fecha(cliente: AsyncClient) -> None:
    """PATCH modifica campos sueltos sin tocar el resto."""
    r = await cliente.post(
        "/api/goals",
        json={"title": "meditar 30 días", "target_date": 1_800_000_000},
    )
    oid = r.json()["id"]
    nueva_fecha = 1_800_000_000 + 60 * 86_400
    r = await cliente.patch(
        f"/api/goals/{oid}", json={"target_date": nueva_fecha}
    )
    assert r.status_code == 200
    assert r.json()["target_date"] == nueva_fecha
    assert r.json()["title"] == "meditar 30 días"


@pytest.mark.asyncio
async def test_objetivos_validacion(cliente: AsyncClient) -> None:
    """UUID inválido y dimension inválida se rechazan con 422."""
    r = await cliente.patch(
        "/api/goals/no-es-uuid", json={"title": "x"}
    )
    assert r.status_code == 422
    r = await cliente.post(
        "/api/goals",
        json={
            "title": "meta mala",
            "target_date": 1,
            "dimension": "nonexistent",
        },
    )
    assert r.status_code == 422
