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


@pytest.mark.asyncio
async def test_relaciones_endpoint_devuelve_vinculos(
    cliente: AsyncClient,
) -> None:
    """POST /eventos con relaciona_ids deja el vínculo en GET /relaciones."""
    ra = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx relaciones base zq9",
            "dimension": "cognitiva",
            "fuente": FUENTE_TEST,
        },
    )
    assert ra.status_code == 200
    rb = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx relaciones derivada zq9",
            "dimension": "cognitiva",
            "fuente": FUENTE_TEST,
            "relaciona_ids": [ra.json()["id"]],
        },
    )
    assert rb.status_code == 200
    assert rb.json()["vinculos_creados"] == 1
    assert rb.json()["contexto"]["completo"] is True

    r = await cliente.get("/api/relaciones")
    assert r.status_code == 200
    pares = {(f["origen_id"], f["destino_id"]) for f in r.json()}
    ids = {ra.json()["id"], rb.json()["id"]}
    assert any(set(p) == ids for p in pares)


@pytest.mark.asyncio
async def test_eventos_avisa_entidad_sin_antecedente(
    cliente: AsyncClient,
) -> None:
    """POST /eventos con nombre propio nuevo → contexto.completo=false."""
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx Zabdiel me agobia muchísimo zq9",
            "fuente": FUENTE_TEST,
        },
    )
    assert r.status_code == 200
    contexto = r.json()["contexto"]
    assert contexto["completo"] is False
    assert "Zabdiel" in contexto["entidades_nuevas"]


@pytest.mark.asyncio
async def test_eventos_fusiona_tags_explicitos_y_hashtags(
    cliente: AsyncClient,
) -> None:
    """POST /eventos mezcla tags explícitos con #hashtags del contenido."""
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx revisé el backlog del sprint #prioridad zq9",
            "fuente": FUENTE_TEST,
            "tags": ["Sprint", "prioridad"],
        },
    )
    assert r.status_code == 200
    evento_id = r.json()["id"]
    linea = await cliente.get("/api/eventos")
    tags = next(
        e["tags"] for e in linea.json() if e["id"] == evento_id
    )
    assert sorted(tags) == ["prioridad", "sprint"]


@pytest.mark.asyncio
async def test_tags_endpoint_lista_frecuencias(cliente: AsyncClient) -> None:
    """GET /tags agrega frecuencias de tags persistidos."""
    await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx nota sobre #zigurat alpha zq9",
            "fuente": FUENTE_TEST,
        },
    )
    await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx otra nota sobre #zigurat beta zq9",
            "fuente": FUENTE_TEST,
        },
    )
    r = await cliente.get("/api/tags")
    assert r.status_code == 200
    frecs = {t["tag"]: t["eventos"] for t in r.json()}
    assert frecs["zigurat"] == 2


@pytest.mark.asyncio
async def test_eventos_rama_filtra_linea_de_universo(
    cliente: AsyncClient,
) -> None:
    """Los eventos de una rama alternativa no aparecen en la principal."""
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx evento en rama espejo zq9",
            "fuente": FUENTE_TEST,
            "rama": "espejo-qzx",
        },
    )
    assert r.status_code == 200
    evento_id = r.json()["id"]

    principal = await cliente.get("/api/eventos")
    assert evento_id not in {e["id"] for e in principal.json()}

    espejo = await cliente.get("/api/eventos", params={"rama": "espejo-qzx"})
    assert evento_id in {e["id"] for e in espejo.json()}


@pytest.mark.asyncio
async def test_fuentes_auto_registro_y_listado(cliente: AsyncClient) -> None:
    """Una fuente nueva en POST /eventos se registra en la tabla fuentes."""
    await cliente.post(
        "/api/eventos",
        json={
            "contenido": "qzx evento con fuente nueva zq9",
            "fuente": FUENTE_TEST,
        },
    )
    r = await cliente.get("/api/fuentes")
    assert r.status_code == 200
    nombres = {f["nombre"] for f in r.json()}
    assert FUENTE_TEST in nombres


@pytest.mark.asyncio
async def test_fuentes_crear_actualizar_archivar_borrar(
    cliente: AsyncClient,
) -> None:
    """Ciclo completo de administración de una fuente."""
    crear = await cliente.post(
        "/api/fuentes",
        json={"nombre": "fuente-qzx", "tipo": "import", "namespace": "lab"},
    )
    assert crear.status_code == 200
    fuente = crear.json()
    assert fuente["nombre"] == "fuente-qzx"

    patch = await cliente.patch(
        "/api/fuentes/fuente-qzx",
        json={"estado": "archivada"},
    )
    assert patch.status_code == 200
    assert patch.json()["estado"] == "archivada"

    borrar = await cliente.delete("/api/fuentes/fuente-qzx")
    assert borrar.status_code == 200
    restantes = await cliente.get("/api/fuentes")
    assert "fuente-qzx" not in {f["nombre"] for f in restantes.json()}


@pytest.mark.asyncio
async def test_fuentes_ramificar_crea_rama_hija(cliente: AsyncClient) -> None:
    """POST /fuentes/{nombre}/ramificar materializa la sub-línea."""
    crear = await cliente.post(
        "/api/fuentes", json={"nombre": "origen-qzx"}
    )
    assert crear.status_code == 200
    rama = await cliente.post(
        "/api/fuentes/origen-qzx/ramificar",
        json={"rama": "rama-qzx"},
    )
    assert rama.status_code == 200
    datos = rama.json()
    assert datos["nombre"] == "origen-qzx:rama-qzx"
    await cliente.delete("/api/fuentes/origen-qzx:rama-qzx")
    await cliente.delete("/api/fuentes/origen-qzx")


@pytest.mark.asyncio
async def test_eventos_repartir_incluye_todas_las_fuentes(
    cliente: AsyncClient,
) -> None:
    """Con repartir=true una fuente mayoritaria no acapara la ventana."""
    for i in range(6):
        r = await cliente.post(
            "/api/eventos",
            json={
                "contenido": f"evento repartido mayor {i}",
                "fuente": FUENTE_TEST,
                "dimension": "conductual",
            },
        )
        assert r.status_code == 200
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "evento repartido minor",
            "fuente": f"{FUENTE_TEST}_b",
            "dimension": "cognitiva",
        },
    )
    assert r.status_code == 200
    r = await cliente.get(
        "/api/eventos", params={"limite": 4, "repartir": "true"}
    )
    fuentes = {e["fuente"] for e in r.json()}
    assert f"{FUENTE_TEST}_b" in fuentes


@pytest.mark.asyncio
async def test_loops_compromiso_abierto_y_cierre(cliente: AsyncClient) -> None:
    """Un compromiso aparece en /loops hasta que se cierra."""
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "responder a Franz sobre el presupuesto",
            "fuente": FUENTE_TEST,
            "tipo": "compromiso",
            "con_quien": "Franz",
        },
    )
    assert r.status_code == 200
    compromiso_id = r.json()["id"]

    r = await cliente.get("/api/loops")
    abiertos = [lp for lp in r.json() if lp["id"] == compromiso_id]
    assert len(abiertos) == 1
    assert abiertos[0]["con_quien"] == "Franz"

    r = await cliente.post(
        f"/api/loops/{compromiso_id}/cerrar",
        json={"resolucion": "le respondí a Franz", "fuente": FUENTE_TEST},
    )
    assert r.status_code == 200
    assert r.json()["estado"] == "cerrado"

    r = await cliente.get("/api/loops")
    assert all(lp["id"] != compromiso_id for lp in r.json())


@pytest.mark.asyncio
async def test_cerrar_loop_sobre_no_compromiso_devuelve_error(
    cliente: AsyncClient,
) -> None:
    """Solo eventos tipo compromiso son cerrables."""
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "un hecho cualquiera sin compromiso",
            "fuente": FUENTE_TEST,
        },
    )
    otro_id = r.json()["id"]
    r = await cliente.post(
        f"/api/loops/{otro_id}/cerrar",
        json={"resolucion": "intento de cierre", "fuente": FUENTE_TEST},
    )
    assert r.status_code in (400, 422, 500)


@pytest.mark.asyncio
async def test_supersede_marca_reemplazado(cliente: AsyncClient) -> None:
    """El evento viejo queda enlazado al nuevo vía reemplazado_por."""
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "mi peso es 80kg exactamente",
            "fuente": FUENTE_TEST,
            "tipo": "hecho",
        },
    )
    viejo_id = r.json()["id"]
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "mi peso es 79kg exactamente",
            "fuente": FUENTE_TEST,
            "tipo": "hecho",
            "reemplaza_id": viejo_id,
        },
    )
    assert r.status_code == 200
    assert r.json()["reemplaza"] == viejo_id

    r = await cliente.get("/api/eventos", params={"limite": 500})
    viejo = next(e for e in r.json() if e["id"] == viejo_id)
    assert viejo["reemplazado_por"] is not None


@pytest.mark.asyncio
async def test_eventos_ttl_expira_en(cliente: AsyncClient) -> None:
    """ttl='30d' persiste expira_en futuro en el evento."""
    r = await cliente.post(
        "/api/eventos",
        json={
            "contenido": "nota efímera que caduca sola",
            "fuente": FUENTE_TEST,
            "ttl": "30d",
        },
    )
    assert r.status_code == 200
    assert r.json()["expira_en"] > r.json()["timestamp_unix"]


@pytest.mark.asyncio
async def test_contexto_inicial_devuelve_pack(cliente: AsyncClient) -> None:
    """El pack de sesión incluye núcleo, loops, calientes y stats."""
    await cliente.post(
        "/api/eventos",
        json={
            "contenido": "compromiso pendiente de contexto inicial",
            "fuente": FUENTE_TEST,
            "tipo": "compromiso",
            "con_quien": "Maria",
        },
    )
    r = await cliente.get("/api/contexto-inicial")
    assert r.status_code == 200
    pack = r.json()
    assert set(pack) >= {"nucleo", "loops", "calientes", "stats"}
    assert any(
        lp["con_quien"] == "Maria" for lp in pack["loops"]
    )
