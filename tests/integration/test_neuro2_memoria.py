"""Tests E2E de la segunda tanda neurocognitiva contra PG real.

Cubre: primacía de época, metamemoria en resultados, ventana lábil +
reconsolidación, co-activación hebbiana, extinción emocional,
buffer de trabajo (TTL + ensayo → promoción) y semanticización.
"""

import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp import tools
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_neuro2"


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Redirige las tools a nous_test y limpia las tablas tocadas."""
    engine = create_async_engine(TEST_DB_URL)
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
    yield engine
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM eventos WHERE fuente IN "
                "(:f, 'sueno', 'trabajo')"
            ),
            {"f": FUENTE_TEST},
        )
        await conn.execute(text("DELETE FROM borradores"))
        await conn.execute(text("DELETE FROM reportes_consolidacion"))
        await conn.execute(
            text(
                "DELETE FROM relaciones WHERE tipo IN "
                "('coactiva', 'reconsolida', 'gist')"
            )
        )
    await engine.dispose()


async def test_primero_de_epoca_marca_inaugura(motor: AsyncEngine) -> None:
    """Tras un hueco ≥6h el evento inaugura época (primacía)."""
    ayer = int(time.time()) - 86400
    await tools.recordar_evento(
        "evento previo de época",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        timestamp_unix=ayer - 10 * 3600,
    )
    nuevo = await tools.recordar_evento(
        "evento que inaugura nueva época",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        timestamp_unix=ayer,
    )
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text(
                    "SELECT inaugura_epoca FROM eventos WHERE id = :id"
                ),
                {"id": nuevo["id"]},
            )
        ).one()
    assert fila[0] is True


async def test_evento_en_flujo_no_inaugura(motor: AsyncEngine) -> None:
    """Eventos seguidos en el tiempo no son primacia."""
    ahora = int(time.time())
    await tools.recordar_evento(
        "primero del flujo continuo",
        dimension="conductual",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 60,
    )
    segundo = await tools.recordar_evento(
        "segundo del flujo continuo",
        dimension="conductual",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora,
    )
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text(
                    "SELECT inaugura_epoca FROM eventos WHERE id = :id"
                ),
                {"id": segundo["id"]},
            )
        ).one()
    assert fila[0] is False


async def test_consultar_devuelve_confianza_y_estado(
    motor: AsyncEngine,
) -> None:
    """La metamemoria viaja en cada resultado: confianza, weight."""
    await tools.recordar_evento(
        "decisión arquitectónica: PostgreSQL con pgvector",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.9,
    )
    await tools._drenar_fondo()
    resultados = await tools._consultar("pgvector postgres", 10)
    assert resultados
    r = resultados[0]
    assert 0.0 <= r["confianza"] <= 1.0
    assert "weight" in r["evento"]
    assert r["evento"]["archivado"] is False


async def test_recall_abre_ventana_labil(motor: AsyncEngine) -> None:
    """Tras evocarse, el evento queda lábil unas horas."""
    e = await tools.recordar_evento(
        "hecho evocable para ventana labil",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    await tools._consultar("ventana labil", 10)
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        hasta = (
            await conn.execute(
                text("SELECT labil_hasta FROM eventos WHERE id = :id"),
                {"id": e["id"]},
            )
        ).scalar_one()
    assert hasta is not None and hasta > int(time.time())


async def test_supersede_en_ventana_es_reconsolida(motor: AsyncEngine) -> None:
    """Un reemplazo dentro de la ventana lábil crea arista reconsolida."""
    viejo = await tools.recordar_evento(
        "dato original que luego se corrige",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    await tools._consultar("dato original corrige", 10)
    await tools._drenar_fondo()
    nuevo = await tools.recordar_evento(
        "dato corregido tras releerlo",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        reemplaza_id=viejo["id"],
    )
    assert nuevo.get("reconsolidated") is True
    async with motor.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM relaciones "
                    "WHERE tipo = 'reconsolida'"
                )
            )
        ).scalar_one()
    assert n >= 1


async def test_co_recall_crea_arista_coactiva(motor: AsyncEngine) -> None:
    """Dos memorias evocadas juntas tejen un vínculo hebbiano."""
    # Dos memorias distintas unidas por un token único — la dedup
    # semántica no las fusiona y la consulta trae ambas juntas.
    await tools.recordar_evento(
        "zxq marcador — decisión de arquitectura de microservicios",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.9,
    )
    await tools.recordar_evento(
        "zxq marcador — receta de pasta con setas al ajillo",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.9,
    )
    await tools._drenar_fondo()
    await tools._consultar("zxq marcador", 10)
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text(
                    "SELECT count(*), max(peso) FROM relaciones "
                    "WHERE tipo = 'coactiva'"
                )
            )
        ).one()
    assert fila[0] >= 1
    assert fila[1] is not None and fila[1] > 0


async def test_borrador_ciclo_completo_promocion(
    motor: AsyncEngine,
) -> None:
    """Borrador → ensayos → se gradúa a evento de la línea."""
    b = await tools._recordar_borrador(
        "hipótesis: el bug es un race condition",
        "cognitiva",
        None,
    )
    assert b["capacidad"].endswith("/15")
    vivos = await tools._listar_borrador(None)
    assert any(v["id"] == b["id"] for v in vivos)

    r1 = await tools._ensayar_borrador(b["id"], None)
    assert r1["promovido"] is False
    r2 = await tools._ensayar_borrador(b["id"], None)
    assert r2["promovido"] is True
    assert r2["evento_id"]
    # Salió del buffer.
    assert not any(v["id"] == b["id"] for v in await tools._listar_borrador(None))
    async with motor.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM eventos "
                    "WHERE fuente = 'trabajo'"
                )
            )
        ).scalar_one()
    assert n >= 1


async def test_borrador_descartado_desaparece(motor: AsyncEngine) -> None:
    """Descartar saca el pensamiento sin promoverlo."""
    b = await tools._recordar_borrador("pensamiento desechable", "cognitiva", None)
    out = await tools._descartar_borrador(b["id"], None)
    assert out["descartado"] is True
    assert not any(v["id"] == b["id"] for v in await tools._listar_borrador(None))


async def test_borrador_expirado_no_se_ensaya(motor: AsyncEngine) -> None:
    """Un borrador vencido ya no admite repaso — se perdió."""
    b = await tools._recordar_borrador("idea que se va a perder", "cognitiva", None)
    async with motor.begin() as conn:
        await conn.execute(
            text(
                "UPDATE borradores SET expira_en = :pasado "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {
                "pasado": int(time.time()) - 10,
                "id": b["id"],
            },
        )
    with pytest.raises(Exception, match="expired"):
        await tools._ensayar_borrador(b["id"], None)


async def test_extincion_atenua_valencia_en_recall_neutro(
    motor: AsyncEngine,
) -> None:
    """Un recuerdo negativo evocado en calma pierde carga poco a poco."""
    # El recuerdo negativo va como cognitiva: si fuera somática sería
    # el último estado del usuario y la extinción —correctamente— no
    # aplicaría ("revivirlo estando mal refuerza, no extingue").
    e = await tools.recordar_evento(
        "discusión muy tensa del martes con el cliente",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        estado_emocional=-0.9,
    )
    await tools._drenar_fondo()
    await tools._consultar("discusión tensa cliente", 10)
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        valencia = (
            await conn.execute(
                text(
                    "SELECT estado_emocional FROM eventos "
                    "WHERE id = :id"
                ),
                {"id": e["id"]},
            )
        ).scalar_one()
    assert valencia is not None
    assert -0.9 < valencia < 0.0


async def test_sueno_semanticiza_episodios_afines(
    motor: AsyncEngine,
) -> None:
    """La consolidación crea el gist semántico y sus aristas."""
    ws = "00000000-0000-0000-0000-000000000002"
    emb = "[" + ",".join(["1" if i == 0 else "0" for i in range(384)]) + "]"
    ahora = int(time.time())
    async with motor.begin() as conn:
        for i in range(4):
            await conn.execute(
                text(
                    "INSERT INTO eventos (timestamp_unix, dimension, "
                    "fuente, contenido, amplitud, tipo, tipo_memoria, "
                    "weight, embedding, workspace_id, scope) VALUES "
                    "(:ts, 'cognitiva', :f, :c, 0.6, 'hecho', "
                    "'episodica', 1.0, CAST(:e AS vector), "
                    "CAST(:ws AS uuid), 'personal')"
                ),
                {
                    "ts": ahora - i * 100,
                    "f": FUENTE_TEST,
                    "c": f"deploy del lunes variante {i}",
                    "e": emb,
                    "ws": ws,
                },
            )
    reporte = await tools._consolidar()
    assert reporte["semanticos"] >= 1
    assert reporte["ventana_sugerida"]["hora_inicio"] >= 0
    async with motor.connect() as conn:
        gist = (
            await conn.execute(
                text(
                    "SELECT tipo_memoria, fuente FROM eventos "
                    "WHERE fuente = 'sueno' LIMIT 1"
                )
            )
        ).one()
        assert gist[0] == "semantica"
        aristas = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM relaciones WHERE tipo = 'gist'"
                )
            )
        ).scalar_one()
    assert aristas >= 4
