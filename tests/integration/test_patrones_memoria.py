"""Tests E2E de los patrones neurocognitivos (ADR-003) contra PG real.

Cubre: intenciones prospectivas (time/event), consolidación-sueño con
archivado reversible, restauración, minería de scripts y el campo
tipo_memoria post-indexación.
"""

import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp import tools
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_patrones"


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Redirige las tools a nous_test y limpia las tablas nuevas."""
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
                "DELETE FROM eventos WHERE fuente IN (:f, 'prospectiva')"
            ),
            {"f": FUENTE_TEST},
        )
        await conn.execute(text("DELETE FROM intenciones_prospectivas"))
        await conn.execute(text("DELETE FROM reportes_consolidacion"))
        await conn.execute(text("DELETE FROM scripts_detectados"))
    await engine.dispose()


async def test_crear_intencion_temporal(motor: AsyncEngine) -> None:
    """Una intención time-based persiste con su trigger futuro."""
    respuesta = await tools.crear_intencion(
        "regar las plantas", en="3d"
    )
    assert respuesta["trigger_tipo"] == "time"
    assert respuesta["dispara_en"] > int(time.time())
    pendientes = await tools.listar_intenciones(ejecutadas=False)
    assert any(p["id"] == respuesta["id"] for p in pendientes)


async def test_crear_intencion_exige_un_solo_trigger(
    motor: AsyncEngine,
) -> None:
    """Sin trigger o con los dos, la validación rechaza."""
    with pytest.raises(Exception, match="trigger"):
        await tools.crear_intencion("sin trigger")
    with pytest.raises(Exception, match="trigger"):
        await tools.crear_intencion("doble", en="1d", cuando="x")


async def test_intencion_vencida_dispara_en_consulta(
    motor: AsyncEngine,
) -> None:
    """Una intención temporal madura y aparece como recordatorio."""
    creada = await tools.crear_intencion("pagar el servidor", en="1d")
    # Maduración forzada: el trigger vence "ayer" para el test.
    async with motor.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intenciones_prospectivas "
                "SET trigger_valor = "
                "jsonb_build_object('ts', CAST(:ts AS bigint)) "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"ts": int(time.time()) - 10, "id": str(creada["id"])},
        )
    resultados = await tools.consultar_memoria("estado del servidor")
    assert resultados
    assert resultados[0].get("recordatorio") is True
    assert "pagar el servidor" in resultados[0]["evento"]["contenido"]
    ejecutadas = await tools.listar_intenciones(ejecutadas=True)
    assert any(e["id"] == creada["id"] for e in ejecutadas)


async def test_intencion_evento_dispara_al_registrar(
    motor: AsyncEngine,
) -> None:
    """Un evento entrante que calza dispara la intención pendiente."""
    creada = await tools.crear_intencion(
        "avisar del bug crítico", cuando="bug crítico producción"
    )
    await tools.recordar_evento(
        "llegó reporte del bug crítico de producción del cliente",
        dimension="conductual",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    ejecutadas = await tools.listar_intenciones(ejecutadas=True)
    assert any(e["id"] == creada["id"] for e in ejecutadas)
    async with motor.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM eventos WHERE fuente = "
                    "'prospectiva'"
                )
            )
        ).scalar_one()
    assert n >= 1


async def test_consolidar_archiva_debil_y_promueve_fuerte(
    motor: AsyncEngine,
) -> None:
    """La corrida archiva lo viejo/débil y promueve lo destacado."""
    sesenta_dias = int(time.time()) - 60 * 86400
    debil = await tools.recordar_evento(
        "detalle trivial ya sin relevancia",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.05,
        timestamp_unix=sesenta_dias,
    )
    fuerte = await tools.recordar_evento(
        "decisión crucial del proyecto",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.95,
        timestamp_unix=sesenta_dias,
    )
    await tools._drenar_fondo()

    reporte = await tools.consolidar_memoria()

    assert reporte["evaluados"] >= 2
    archivados = await tools.listar_archivados()
    assert any(a["id"] == debil["id"] for a in archivados)
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text("SELECT curado FROM eventos WHERE id = :id"),
                {"id": fuerte["id"]},
            )
        ).one()
    assert fila[0] is True


async def test_restaurar_deshace_el_archivado(motor: AsyncEngine) -> None:
    """restaurar_evento vuelve evocable una memoria archivada."""
    viejo = int(time.time()) - 90 * 86400
    evento = await tools.recordar_evento(
        "recuerdo antiguo de baja intensidad",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.05,
        timestamp_unix=viejo,
    )
    await tools._drenar_fondo()
    await tools.consolidar_memoria()
    assert any(
        a["id"] == evento["id"] for a in await tools.listar_archivados()
    )

    restaurado = await tools.restaurar_evento(evento["id"])
    assert restaurado["restaurado"] is True
    assert not any(
        a["id"] == evento["id"] for a in await tools.listar_archivados()
    )


async def test_minar_y_listar_scripts(motor: AsyncEngine) -> None:
    """La minería detecta la secuencia repetida y la persiste."""
    base = int(time.time()) - 3 * 86400
    for i in range(3):
        t = base + i * 50_000
        await tools.recordar_evento(
            "reproduje el error crítico del build",
            dimension="conductual",
            fuente=FUENTE_TEST,
            timestamp_unix=t,
        )
        await tools.recordar_evento(
            "arreglé el test fallido de la suite",
            dimension="conductual",
            fuente=FUENTE_TEST,
            timestamp_unix=t + 600,
        )
    reporte = await tools.minar_scripts()
    assert reporte["detectados"] >= 1
    scripts = await tools.listar_scripts()
    assert scripts
    assert "confianza" in scripts[0]


async def test_indexacion_asigna_tipo_memoria(motor: AsyncEngine) -> None:
    """Tras indexar, el evento lleva su tipo_memoria heurístico."""
    evento = await tools.recordar_evento(
        "el comando para migrar es alembic upgrade head",
        dimension="conductual",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        tipo = (
            await conn.execute(
                text(
                    "SELECT tipo_memoria FROM eventos WHERE id = :id"
                ),
                {"id": evento["id"]},
            )
        ).scalar_one()
    assert tipo == "procedimental"
