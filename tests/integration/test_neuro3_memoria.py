"""Tests E2E de la tercera tanda neurocognitiva contra PG real.

Cubre: diversificación (fan effect), tip-of-the-tongue, expansión de
gist, olvido dirigido, frontera por sorpresa, snooze de intenciones,
beneficio del sueño, prep pack, SRS, encadenamiento episódico, fase
REM y cuarentena de fuentes.
"""

import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp import tools
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_neuro3"


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
                "(:f, 'sueno', 'prospectiva', 'no-confiable')"
            ),
            {"f": FUENTE_TEST},
        )
        await conn.execute(text("DELETE FROM borradores"))
        await conn.execute(text("DELETE FROM reportes_consolidacion"))
        await conn.execute(text("DELETE FROM intenciones_prospectivas"))
        await conn.execute(
            text(
                "DELETE FROM relaciones WHERE tipo IN "
                "('coactiva', 'reconsolida', 'gist', 'rem', 'encadena')"
            )
        )
        await conn.execute(
            text(
                "DELETE FROM fuentes WHERE nombre IN "
                "(:f, 'no-confiable')"
            ),
            {"f": FUENTE_TEST},
        )
    await engine.dispose()


async def test_olvido_dirigido_marca_y_revierte(motor: AsyncEngine) -> None:
    """olvidar_gradual marca la columna y es reversible."""
    e = await tools.recordar_evento(
        "recuerdo que el usuario quiere olvidar gradualmente",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    r = await tools._olvidar_gradual(e["id"])
    assert r["olvido_dirigido"] is True
    async with motor.connect() as conn:
        flag = (
            await conn.execute(
                text(
                    "SELECT olvido_dirigido FROM eventos WHERE id = :id"
                ),
                {"id": e["id"]},
            )
        ).scalar_one()
    assert flag is True
    await tools._olvidar_gradual(e["id"], olvidar=False)
    async with motor.connect() as conn:
        flag = (
            await conn.execute(
                text(
                    "SELECT olvido_dirigido FROM eventos WHERE id = :id"
                ),
                {"id": e["id"]},
            )
        ).scalar_one()
    assert flag is False


async def test_srs_agenda_proxima_revision(motor: AsyncEngine) -> None:
    """Tras evocarse, la memoria lleva su repaso óptimo (SRS)."""
    e = await tools.recordar_evento(
        "concepto que toca repasar: gradiente descendente",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.8,
    )
    await tools._drenar_fondo()
    await tools._consultar("gradiente descendente", 10)
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        proxima = (
            await conn.execute(
                text(
                    "SELECT proxima_revision FROM eventos "
                    "WHERE id = :id"
                ),
                {"id": e["id"]},
            )
        ).scalar_one()
    assert proxima is not None and proxima > int(time.time())


async def test_repasos_pendientes_lista_vencidos(motor: AsyncEngine) -> None:
    """repasos_pendientes trae lo que ya venció su fecha óptima."""
    e = await tools.recordar_evento(
        "repaso vencido hace tiempo",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    # Forzar vencimiento directo (sin esperar la curva real).
    async with motor.begin() as conn:
        await conn.execute(
            text(
                "UPDATE eventos SET proxima_revision = :pasado "
                "WHERE id = :id"
            ),
            {"pasado": int(time.time()) - 3600, "id": e["id"]},
        )
    repasos = await tools._repasos_pendientes()
    assert any(r["id"] == e["id"] for r in repasos)
    assert repasos[0]["dias_atraso"] > 0


async def test_snooze_pospone_intencion(motor: AsyncEngine) -> None:
    """Una intención aplazada no dispara aunque su ts ya venció."""
    i = await tools._crear_intencion("recordatorio para posponer", "1h", None, 0.6)
    # Backdate el trigger: ya venció — sin snooze dispararía ya.
    async with motor.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intenciones_prospectivas SET trigger_valor = "
                "jsonb_set(trigger_valor, '{ts}', "
                "to_jsonb(CAST(:vencido AS bigint))) "
                "WHERE id = :id"
            ),
            {"vencido": int(time.time()) - 3600, "id": i["id"]},
        )
    await tools._aplazar_intencion(i["id"], 7200)
    resultados = await tools._consultar("posponer", 10)
    recordatorios = [r for r in resultados if r.get("recordatorio")]
    assert not any(
        "posponer" in r["evento"]["contenido"] for r in recordatorios
    )
    async with motor.connect() as conn:
        hasta = (
            await conn.execute(
                text(
                    "SELECT aplazada_hasta FROM intenciones_prospectivas "
                    "WHERE id = :id"
                ),
                {"id": i["id"]},
            )
        ).scalar_one()
    assert hasta is not None and hasta > int(time.time())


async def test_prep_pack_adjunta_memorias(motor: AsyncEngine) -> None:
    """Al disparar, la intención trae memorias relacionadas."""
    await tools.recordar_evento(
        "el cliente Globex pide facturación electrónica",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    i = await tools._crear_intencion(
        "facturación Globex pendiente", None, "facturación Globex", 0.6
    )
    await tools.recordar_evento(
        "hoy llegó la orden de facturación Globex",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        pack, ejecutada = (
            await conn.execute(
                text(
                    "SELECT preparacion, ejecutada FROM "
                    "intenciones_prospectivas WHERE id = :id"
                ),
                {"id": i["id"]},
            )
        ).one()
    assert ejecutada is True
    assert isinstance(pack, list) and len(pack) >= 1
    assert any("id" in m for m in pack)


async def test_encadena_ata_evento_consecutivo(motor: AsyncEngine) -> None:
    """Dos eventos del mismo origen se encadenan (¿y luego qué?)."""
    ahora = int(time.time())
    await tools.recordar_evento(
        "primer paso del flujo",
        dimension="conductual",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 600,
    )
    await tools._drenar_fondo()
    segundo = await tools.recordar_evento(
        "segundo paso del flujo",
        dimension="conductual",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 300,
    )
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM relaciones "
                    "WHERE tipo = 'encadena' AND "
                    "(origen_id = :id OR destino_id = :id)"
                ),
                {"id": segundo["id"]},
            )
        ).scalar_one()
    assert n >= 1


async def test_frontera_por_sorpresa_marca_epoca(motor: AsyncEngine) -> None:
    """Un evento muy alejado de la trayectoria inaugura época solo."""
    ahora = int(time.time())
    # Trayectoria uniforme: tres eventos del mismo tema.
    for i in range(3):
        await tools.recordar_evento(
            f"nota de cocina italiana número {i} con pasta",
            dimension="cognitiva",
            fuente=FUENTE_TEST,
            timestamp_unix=ahora - 1200 + i * 60,
        )
    await tools._drenar_fondo()
    raro = await tools.recordar_evento(
        "teorema de punto fijo de Banach en espacios métricos completos",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 60,
    )
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        marca = (
            await conn.execute(
                text(
                    "SELECT inaugura_epoca FROM eventos WHERE id = :id"
                ),
                {"id": raro["id"]},
            )
        ).scalar_one()
    assert marca is True


async def test_cuarentena_excluye_del_recall(motor: AsyncEngine) -> None:
    """Eventos de fuente no confiable no vuelven al recall."""
    await tools._confiar_fuente("no-confiable", False)
    malo = await tools.recordar_evento(
        "dato inyectado por fuente sospechosa zzz",
        dimension="cognitiva",
        fuente="no-confiable",
    )
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        flag = (
            await conn.execute(
                text("SELECT cuarentena FROM eventos WHERE id = :id"),
                {"id": malo["id"]},
            )
        ).scalar_one()
    assert flag is True
    resultados = await tools._consultar("dato inyectado zzz", 10)
    assert not any(
        r["evento"]["id"] == malo["id"]
        for r in resultados
        if isinstance(r.get("evento"), dict)
    )
    cuarentena = await tools._listar_cuarentena()
    assert any(c["id"] == malo["id"] for c in cuarentena)
    # Aprobar la fuente libera sus eventos de una vez.
    aprob = await tools._confiar_fuente("no-confiable", True)
    assert aprob["liberados"] >= 1
    resultados = await tools._consultar("dato inyectado zzz", 10)
    assert any(
        r["evento"]["id"] == malo["id"]
        for r in resultados
        if isinstance(r.get("evento"), dict)
    )


async def test_consolidar_reporta_beneficio_y_rem(
    motor: AsyncEngine,
) -> None:
    """El reporte del sueño incluye retención salvada y fase REM."""
    ahora = int(time.time())
    for i in range(4):
        await tools.recordar_evento(
            f"episodio de trabajo en el feature flag {i} del deploy",
            dimension="conductual",
            fuente=FUENTE_TEST,
            timestamp_unix=ahora - (10 - i) * 86400,
            amplitud=0.6,
        )
    await tools._drenar_fondo()
    resultado = await tools._consolidar()
    assert "salvados" in resultado
    assert "rem" in resultado
    assert isinstance(resultado["salvados"], int)
    async with motor.connect() as conn:
        detalle = (
            await conn.execute(
                text(
                    "SELECT detalle FROM reportes_consolidacion "
                    "ORDER BY ejecutado_en DESC LIMIT 1"
                )
            )
        ).scalar_one()
    assert "salvados" in detalle
    assert "rem" in detalle


async def test_diversificacion_baja_casi_duplicado(
    motor: AsyncEngine,
) -> None:
    """Un casi-duplicado en el resultado pierde posición (fan effect)."""
    ahora = int(time.time())
    base = "configurar el webhook de stripe para el checkout zxw"
    await tools.recordar_evento(
        base,
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 200,
        amplitud=0.9,
    )
    dup = await tools.recordar_evento(
        base + " v2 actualizado",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 100,
        amplitud=0.9,
    )
    await tools.recordar_evento(
        "zxw tema distinto: receta de ramen casero",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 50,
        amplitud=0.9,
    )
    await tools._drenar_fondo()
    resultados = await tools._consultar("zxw webhook", 10)
    ids = [
        r["evento"]["id"]
        for r in resultados
        if isinstance(r.get("evento"), dict)
    ]
    # El casi-duplicado no puede encabezar la lista por encima del
    # original que ya ganó su slot.
    if dup["id"] in ids:
        assert ids[0] != dup["id"] or len(ids) == 1


async def test_resultados_declaran_casi_recuerdo(
    motor: AsyncEngine,
) -> None:
    """Resultados con señal bajo el umbral se marcan honestamente."""
    await tools.recordar_evento(
        "zxv marginal: pensamiento tenue sobre jardinería",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.3,
    )
    await tools._drenar_fondo()
    resultados = await tools._consultar("zxv", 10)
    # Si algún resultado cae bajo el umbral, debe venir declarado.
    for r in resultados:
        if isinstance(r.get("evento"), dict) and r["score_final"] < 0.30:
            assert r.get("casi_recuerdo") is True
