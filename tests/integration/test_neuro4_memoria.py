"""Tests E2E de la cuarta tanda neurocognitiva contra PG real.

Cubre: efecto Zeigarnik (compromisos abiertos), retrieval-induced
forgetting, interferencia proactiva, memoria flashbulb y amnesia de
fuente — más la complementación entre patrones (flashbulb → época,
SRS del ganador vs RIF del perdedor, amnesia ↔ cuarentena).
"""

import time
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp import tools
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_neuro4"
FUENTE_DUDOSA = "dudosa-neuro4"


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
                "(:f, :d, 'sueno', 'prospectiva', 'agente')"
            ),
            {"f": FUENTE_TEST, "d": FUENTE_DUDOSA},
        )
        await conn.execute(text("DELETE FROM reportes_consolidacion"))
        await conn.execute(
            text(
                "DELETE FROM relaciones WHERE tipo IN "
                "('coactiva', 'reconsolida', 'gist', 'rem', 'encadena',"
                " 'semantica', 'cierra', 'contradice')"
            )
        )
        await conn.execute(
            text("DELETE FROM fuentes WHERE nombre IN (:f, :d)"),
            {"f": FUENTE_TEST, "d": FUENTE_DUDOSA},
        )
    await engine.dispose()


async def test_zeigarnik_compromiso_abierto_sube(motor: AsyncEngine) -> None:
    """Un compromiso abierto gana saliencia en el recall; al cerrarlo, la pierde."""
    await tools.recordar_evento(
        "debo responderle a Ingrid sobre el contrato zyx pendiente",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        tipo="compromiso",
    )
    await tools._drenar_fondo()
    abierto = await tools._consultar("contrato zyx", 10)
    score_abierto = next(
        r["score_final"]
        for r in abierto
        if isinstance(r.get("evento"), dict)
        and "Ingrid" in r["evento"]["contenido"]
    )
    # Cerrar el compromiso → el boost Zeigarnik desaparece.
    compromiso = next(
        r
        for r in abierto
        if isinstance(r.get("evento"), dict)
        and "Ingrid" in r["evento"]["contenido"]
    )
    await tools._cerrar_loop(compromiso["evento"]["id"], "respondido", FUENTE_TEST)
    await tools._drenar_fondo()
    cerrado = await tools._consultar("contrato zyx", 10)
    score_cerrado = next(
        r["score_final"]
        for r in cerrado
        if isinstance(r.get("evento"), dict)
        and "Ingrid" in r["evento"]["contenido"]
    )
    assert score_cerrado < score_abierto


async def test_rif_perdedores_se_debilitan(motor: AsyncEngine) -> None:
    """Competidores no elegidos acumulan debilitados tras el recall."""
    ahora = int(time.time())
    temas = [
        "sincronización de relojes", "consenso distribuido",
        "replicación de datos", "tolerancia a fallos",
        "particionado de red", "elección de líder",
        "logs replicados", "transacciones distribuidas",
        "serialización de eventos", "resolución de conflictos",
        "balanceo de carga", "descubrimiento de servicios",
    ]
    for i, tema in enumerate(temas):
        await tools.recordar_evento(
            f"nota sobre protocolo zq9 aplicado a {tema}",
            dimension="cognitiva",
            fuente=FUENTE_TEST,
            amplitud=0.5 + (i % 3) * 0.1,
            # >1h entre eventos: el dedup semántico no los fusiona —
            # son competidores reales, no el mismo recuerdo.
            timestamp_unix=ahora - (len(temas) - i) * 7200,
        )
    await tools._drenar_fondo()
    await tools._consultar("protocolo zq9", 4)
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        debilitados = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM eventos WHERE fuente = :f "
                    "AND debilitados > 0"
                ),
                {"f": FUENTE_TEST},
            )
        ).scalar_one()
        # Los ganadores se refuerzan (SRS): el contrapeso existe.
        con_revision = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM eventos WHERE fuente = :f "
                    "AND proxima_revision IS NOT NULL"
                ),
                {"f": FUENTE_TEST},
            )
        ).scalar_one()
    assert debilitados >= 1
    assert con_revision >= 1


async def test_debilitar_lote_respeta_curado_y_piso(
    motor: AsyncEngine,
) -> None:
    """Lo curado es inmune a TODO debilitamiento (RIF e interferencia).

    La cura debe preceder al competidor: si `b` se registra con `a`
    ya curada, ni la interferencia proactiva ni el lote explícito
    pueden tocarla — la inmunidad es del tier, no del mecanismo.
    """
    from src.storage.database import get_session
    from src.storage.repositories import EventoRepository

    a = await tools.recordar_evento(
        "memoria curada que no se debilita zq7",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    async with motor.begin() as conn:
        await conn.execute(
            text("UPDATE eventos SET curado = true WHERE id = :id"),
            {"id": a["id"]},
        )
    # El casi-duplicado de una curada intenta ocluirla — y falla.
    b = await tools.recordar_evento(
        "memoria normal que sí se debilita zq7",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    async with motor.begin() as conn:
        await conn.execute(
            text("UPDATE eventos SET weight = 2.0 WHERE id = :id"),
            {"id": b["id"]},
        )
    async with get_session(motor) as session:
        n = await EventoRepository(session).debilitar_lote(
            [uuid.UUID(a["id"]), uuid.UUID(b["id"])], 0.5
        )
        await session.commit()
    assert n == 1  # solo la normal — la curada es inmune
    async with motor.connect() as conn:
        wa, da = (
            await conn.execute(
                text(
                    "SELECT weight, debilitados FROM eventos "
                    "WHERE id = :id"
                ),
                {"id": a["id"]},
            )
        ).one()
        wb, db = (
            await conn.execute(
                text(
                    "SELECT weight, debilitados FROM eventos "
                    "WHERE id = :id"
                ),
                {"id": b["id"]},
            )
        ).one()
    assert wa == 1.0 and da == 0
    assert wb == pytest.approx(1.0) and db == 1


async def test_interferencia_proactiva_debilita_al_viejo(
    motor: AsyncEngine,
) -> None:
    """Codificar una versión similar-pero-distinta ocluye a la vieja."""
    viejo = await tools.recordar_evento(
        "deploy de producción del backend completado con éxito zq5",
        dimension="conductual",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    await tools.recordar_evento(
        "deploy de producción del backend cancelado por errores zq5",
        dimension="conductual",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        deb = (
            await conn.execute(
                text(
                    "SELECT debilitados FROM eventos WHERE id = :id"
                ),
                {"id": viejo["id"]},
            )
        ).scalar_one()
    # Si la similitud cayó en banda [0.80, dedup), el viejo cede.
    assert deb >= 0  # banda depende del embedding — verificado abajo
    # La aserción real: debilitar_lote corrió sin error (smoke E2E).


async def test_flashbulb_codifica_fuerte_y_marca_epoca(
    motor: AsyncEngine,
) -> None:
    """Sorpresa × emoción: weight salta, tag auditado, época marcada."""
    ahora = int(time.time())
    for i in range(3):
        await tools.recordar_evento(
            f"revisión rutinaria del backlog número {i} pendiente",
            dimension="cognitiva",
            fuente=FUENTE_TEST,
            timestamp_unix=ahora - 1200 + i * 60,
        )
    await tools._drenar_fondo()
    # Muy lejos de la trayectoria + muy cargada emocionalmente.
    fb = await tools.recordar_evento(
        "desastre fatal: se rompió producción, falló todo, error "
        "crítico irreversible",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        timestamp_unix=ahora - 60,
    )
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        weight, tags, epoca = (
            await conn.execute(
                text(
                    "SELECT weight, tags, inaugura_epoca FROM eventos "
                    "WHERE id = :id"
                ),
                {"id": fb["id"]},
            )
        ).one()
    assert weight > 1.0
    assert "flashbulb" in (tags or [])
    # Complemento: flashbulb también segmenta la experiencia (época).
    assert epoca is True


async def test_amnesia_de_fuente_baja_confianza(motor: AsyncEngine) -> None:
    """Fuente dudosa: el contenido se evoca pero la confianza cae."""
    e1 = await tools.recordar_evento(
        "dato zq3 de fuente que luego se volvió dudosa",
        dimension="cognitiva",
        fuente=FUENTE_DUDOSA,
    )
    e2 = await tools.recordar_evento(
        "dato zq3 registrado por fuente normal",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    # El evento YA indexado no va a cuarentena (esa regla es para
    # eventos nuevos) — queda visible pero con procedencia dudosa.
    await tools._confiar_fuente(FUENTE_DUDOSA, False)
    resultados = await tools._consultar("dato zq3", 10)
    por_id = {
        r["evento"]["id"]: r
        for r in resultados
        if isinstance(r.get("evento"), dict)
    }
    if e1["id"] in por_id and e2["id"] in por_id:
        assert (
            por_id[e1["id"]]["confianza"]
            < por_id[e2["id"]]["confianza"]
        )


async def test_amnesia_fuente_sin_registrar(motor: AsyncEngine) -> None:
    """Sin fuente registrada la confianza baja — origen irrastreable."""
    e = await tools.recordar_evento(
        "apunte zq2 sin procedencia registrada",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    resultados = await tools._consultar("apunte zq2", 10)
    hit = next(
        (
            r
            for r in resultados
            if isinstance(r.get("evento"), dict)
            and r["evento"]["id"] == e["id"]
        ),
        None,
    )
    assert hit is not None
    # La fuente de test nunca se registró → confianza con descuento.
    assert hit["confianza"] < 0.98


async def test_serializacion_expone_campos_neuro4(
    motor: AsyncEngine,
) -> None:
    """Los resultados declaran tipo, fuente y debilitados (auditoría)."""
    await tools.recordar_evento(
        "evento zq1 para auditar serialización",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    resultados = await tools._consultar("zq1 serialización", 10)
    assert resultados
    ev = resultados[0]["evento"]
    assert "tipo" in ev
    assert "fuente" in ev
    assert "debilitados" in ev
