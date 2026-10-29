"""Tests E2E de Neuro-5 contra PostgreSQL real (nous_test).

Cubre el ciclo de sesión completo: bootstrap presupuestado + snapshot
frozen, staged (aprobación pre-escritura), censores + verificar_accion,
predicciones + Brier, sesiones first-class + handoff, y la presión de
presupuesto sobre el tier curado.
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

FUENTE_TEST = "test_neuro5"


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
            text("DELETE FROM eventos WHERE fuente = :f"),
            {"f": FUENTE_TEST},
        )
        await conn.execute(
            text("DELETE FROM sesiones WHERE agente = :f"),
            {"f": FUENTE_TEST},
        )
        await conn.execute(
            text("DELETE FROM predicciones WHERE afirmacion LIKE :f"),
            {"f": FUENTE_TEST + "%"},
        )
        await conn.execute(
            text("DELETE FROM fuentes WHERE nombre = :f"),
            {"f": FUENTE_TEST},
        )
        # Censores (fuente='usuario'), gists del sueño ('sueno') y
        # compromisos de sesión ('agente') de esta suite — todos llevan
        # la marca hex del uuid de test.
        await conn.execute(
            text(
                "DELETE FROM eventos WHERE contenido ~"
                " '(usuarios|legacy)-[a-f0-9]{6,8}'"
                " OR contenido ~ '/home/[a-f0-9]{6,8}'"
                " OR contenido ~ 'deje-abierto-[a-f0-9]{6,8}'"
            )
        )
    await engine.dispose()


async def _id_evento(contenido: str) -> str:
    """Busca el id del evento recién escrito por su contenido único."""
    filas = await tools._consultar(contenido[:40], 10)
    for r in filas:
        if isinstance(r.get("evento"), dict) and contenido[:30] in r["evento"]["contenido"]:
            return str(r["evento"]["id"])
    raise AssertionError(f"no se encontró el evento: {contenido[:40]}")


# ---------- staged: aprobación pre-escritura ----------


async def test_staged_no_aparece_en_recall(motor: AsyncEngine) -> None:
    """Una propuesta staged queda fuera de consultar_memoria hasta aprobarla."""
    marca = f"propuesta-staged-{uuid.uuid4().hex[:8]}"
    async with motor.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO eventos (id, timestamp_unix, dimension, tipo,"
                " contenido, amplitud, weight, fuente, staged, cuarentena,"
                " archivado, curado, refuerzos, debilitados, scope,"
                " workspace_id)"
                " VALUES (gen_random_uuid(), :ts, 'cognitiva', 'hecho',"
                " :contenido, 0.9, 1.0, :f, true, false, false, false,"
                " 0, 0, 'personal',"
                " (SELECT id FROM workspaces LIMIT 1))"
            ),
            {"ts": int(time.time()), "contenido": marca, "f": FUENTE_TEST},
        )
    resultados = await tools._consultar(marca, 10)
    assert all(
        marca not in r["evento"]["contenido"]
        for r in resultados
        if isinstance(r.get("evento"), dict)
    )


async def test_staged_lista_y_aprobar_lo_publica(motor: AsyncEngine) -> None:
    """pendientes_aprobacion lo ve; aprobar_evento lo hace evocable."""
    marca = f"aprueba-staged-{uuid.uuid4().hex[:8]}"
    async with motor.begin() as conn:
        fila = (
            await conn.execute(
                text(
                    "INSERT INTO eventos (id, timestamp_unix, dimension,"
                    " tipo, contenido, amplitud, weight, fuente, staged,"
                    " cuarentena, archivado, curado, refuerzos, debilitados,"
                    " scope, workspace_id)"
                    " VALUES (gen_random_uuid(), :ts, 'cognitiva', 'hecho',"
                    " :contenido, 0.9, 1.0, :f, true, false, false, false,"
                    " 0, 0, 'personal',"
                    " (SELECT id FROM workspaces LIMIT 1)) RETURNING id"
                ),
                {"ts": int(time.time()), "contenido": marca, "f": FUENTE_TEST},
            )
        ).first()
    evento_id = str(fila[0])
    pendientes = await tools._pendientes_aprobacion()
    assert any(p["id"] == evento_id for p in pendientes)

    await tools._aprobar_evento(evento_id)
    async with motor.begin() as conn:
        staged = (
            await conn.execute(
                text("SELECT staged FROM eventos WHERE id = :id"),
                {"id": evento_id},
            )
        ).scalar_one()
    assert staged is False


async def test_rechazar_staged_lo_descarta(motor: AsyncEngine) -> None:
    """rechazar_evento elimina la propuesta — no entra nunca a recall."""
    marca = f"rechaza-staged-{uuid.uuid4().hex[:8]}"
    async with motor.begin() as conn:
        fila = (
            await conn.execute(
                text(
                    "INSERT INTO eventos (id, timestamp_unix, dimension,"
                    " tipo, contenido, amplitud, weight, fuente, staged,"
                    " cuarentena, archivado, curado, refuerzos, debilitados,"
                    " scope, workspace_id)"
                    " VALUES (gen_random_uuid(), :ts, 'cognitiva', 'hecho',"
                    " :contenido, 0.9, 1.0, :f, true, false, false, false,"
                    " 0, 0, 'personal',"
                    " (SELECT id FROM workspaces LIMIT 1)) RETURNING id"
                ),
                {"ts": int(time.time()), "contenido": marca, "f": FUENTE_TEST},
            )
        ).first()
    evento_id = str(fila[0])
    await tools._rechazar_evento(evento_id)
    async with motor.begin() as conn:
        existe = (
            await conn.execute(
                text("SELECT count(*) FROM eventos WHERE id = :id"),
                {"id": evento_id},
            )
        ).scalar_one()
    assert existe == 0


# ---------- censores + verificar_accion ----------


async def test_crear_censor_lo_lista_como_guardrail(motor: AsyncEngine) -> None:
    """El censor se persiste como tipo='censor' con severidad/dominio."""
    regla = f"nunca ejecutes rm -rf sobre /home/{uuid.uuid4().hex[:6]}"
    creado = await tools._crear_censor(regla, "bloquear", "shell")
    censores = await tools._listar_censores()
    propio = next(c for c in censores if c["contenido"] == regla)
    assert propio["tipo"] == "censor"
    assert propio["severidad"] == "bloquear"
    assert propio["dominio"] == "shell"
    assert creado["id"]


async def test_verificar_accion_bloqueada_por_censor(motor: AsyncEngine) -> None:
    """Un censor bloqueante veta la acción propuesta relacionada."""
    marca = uuid.uuid4().hex[:6]
    await tools._crear_censor(
        f"prohibido borrar la tabla usuarios-{marca} en produccion",
        "bloquear",
        "db",
    )
    await tools._drenar_fondo()
    v = await tools._verificar_accion(
        f"quiero borrar la tabla usuarios-{marca} en produccion"
    )
    assert not v["permitido"]
    assert any(
        f"usuarios-{marca}" in c["contenido"] for c in v["censores"]
    )


async def test_verificar_accion_sin_relacion_queda_permitida(
    motor: AsyncEngine,
) -> None:
    """Una acción sin censores aplicables pasa — sin veto por ruido."""
    v = await tools._verificar_accion(
        f"leer el README del proyecto {uuid.uuid4().hex[:8]}"
    )
    assert v["permitido"]


async def test_censor_advertir_informa_sin_bloquear(motor: AsyncEngine) -> None:
    """Severidad 'advertir' deja pasar la acción con cautela."""
    marca = uuid.uuid4().hex[:6]
    await tools._crear_censor(
        f"cuidado al tocar el archivo legacy-{marca}.conf", "advertir"
    )
    await tools._drenar_fondo()
    v = await tools._verificar_accion(
        f"voy a editar legacy-{marca}.conf ahora"
    )
    assert v["permitido"]
    assert v["censores"]


# ---------- predicciones + Brier ----------


async def test_prediccion_ciclo_completo_alimenta_brier(
    motor: AsyncEngine,
) -> None:
    """Registrar → resolver → el reporte de calibración la contabiliza."""
    p = await tools._registrar_prediccion(
        f"test_neuro5 deploy sin rollback-{uuid.uuid4().hex[:6]}",
        confianza=0.9,
    )
    assert p["confianza"] == 0.9

    r = await tools._resolver_prediccion(p["id"], True)
    assert r["acierto"] is True

    reporte = await tools._reporte_calibracion()
    assert reporte["resueltas"] >= 1
    assert reporte["brier"] is not None


async def test_listar_predicciones_filtra_pendientes(motor: AsyncEngine) -> None:
    """pendientes=True solo trae abiertas; resueltas quedan fuera."""
    abierta = await tools._registrar_prediccion(
        f"test_neuro5 abierta-{uuid.uuid4().hex[:8]}",
        confianza=0.6,
    )
    pendientes = await tools._listar_predicciones(pendientes=True)
    assert any(x["id"] == abierta["id"] for x in pendientes)
    resueltas = await tools._listar_predicciones(pendientes=False)
    assert all(x["id"] != abierta["id"] for x in resueltas)


async def test_resolver_prediccion_dos_veces_falla(motor: AsyncEngine) -> None:
    """El resultado es inmutable — no se recalibra la historia."""
    p = await tools._registrar_prediccion(
        f"test_neuro5 unica-{uuid.uuid4().hex[:8]}",
        confianza=0.5,
    )
    await tools._resolver_prediccion(p["id"], False)
    with pytest.raises(ValueError):
        await tools._resolver_prediccion(p["id"], True)


# ---------- sesiones + handoff ----------


async def test_sesion_ciclo_completo_genera_handoff(motor: AsyncEngine) -> None:
    """iniciar → escribir → cerrar produce el documento de continuidad."""
    sesion = await tools._iniciar_sesion(agente=FUENTE_TEST)
    assert sesion["sesion_id"]

    marca = f"deje-abierto-{uuid.uuid4().hex[:8]}"
    await tools.recordar_evento(
        f"compromiso {marca}: revisar el PR pendiente",
        tipo="compromiso",
    )
    cierre = await tools._cerrar_sesion(
        sesion["sesion_id"], resumen="sesion de prueba neuro5"
    )
    assert "handoff" in cierre
    assert "prueba neuro5" in cierre["handoff"]
    assert "Handoff" in cierre["handoff"]

    sesiones = await tools._listar_sesiones()
    mia = next(s for s in sesiones if s["id"] == sesion["sesion_id"])
    assert mia["cerrada"]
    assert mia["eventos"] >= 1


async def test_obtener_handoff_de_sesion_cerrada(motor: AsyncEngine) -> None:
    """El handoff persiste — se puede releer tras el cierre."""
    sesion = await tools._iniciar_sesion(agente=FUENTE_TEST)
    await tools._cerrar_sesion(sesion["sesion_id"], resumen="x")
    datos = await tools._obtener_handoff(sesion["sesion_id"])
    assert datos["handoff"]
    assert datos["fin"] is not None


async def test_iniciar_sesion_devuelve_snapshot_presupuestado(
    motor: AsyncEngine,
) -> None:
    """El snapshot respeta el medidor de presupuesto declarado."""
    sesion = await tools._iniciar_sesion(agente=FUENTE_TEST, presupuesto=800)
    assert "snapshot" in sesion
    assert "NOUS MEMORY" in sesion["snapshot"]
    assert sesion["presupuesto_usado"] <= 800


async def test_contexto_inicial_acepta_presupuesto(motor: AsyncEngine) -> None:
    """contexto_inicial con presupuesto devuelve snapshot + omitidos."""
    pack = await tools._contexto_inicial(presupuesto=500)
    assert "snapshot" in pack
    assert "presupuesto_usado" in pack
    assert "omitidos" in pack


async def test_sesion_cerrada_no_se_recierra(motor: AsyncEngine) -> None:
    """Cerrar dos veces la misma sesión es error, no no-op silencioso."""
    sesion = await tools._iniciar_sesion(agente=FUENTE_TEST)
    await tools._cerrar_sesion(sesion["sesion_id"])
    with pytest.raises(ValueError):
        await tools._cerrar_sesion(sesion["sesion_id"])
