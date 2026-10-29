"""Tests E2E de Neuro-6 contra PostgreSQL real (nous_test).

Cubre la capa epistémica: techo paramétrico en metamemoria,
desglose de confianza, aprobación humana como corroboración,
expansión hebbiana STP/LTP y el mapa de lagunas.
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

FUENTE_TEST = "test_neuro6"


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Redirige las tools a nous_test y limpia las filas de la suite."""
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
            text("DELETE FROM fuentes WHERE nombre = :f"),
            {"f": FUENTE_TEST},
        )
    await engine.dispose()


async def _insertar_evento(
    motor: AsyncEngine, contenido: str, parametrica: bool = False
) -> str:
    """Inserta un evento de prueba y devuelve su id."""
    async with motor.begin() as conn:
        fila = (
            await conn.execute(
                text(
                    "INSERT INTO eventos (id, timestamp_unix, dimension,"
                    " tipo, contenido, amplitud, weight, fuente, staged,"
                    " cuarentena, archivado, curado, refuerzos, debilitados,"
                    " scope, parametrica, workspace_id)"
                    " VALUES (gen_random_uuid(), :ts, 'cognitiva', 'hecho',"
                    " :contenido, 0.9, 1.0, :f, false, false, false, false,"
                    " 0, 0, 'personal', :parametrica,"
                    " (SELECT id FROM workspaces LIMIT 1)) RETURNING id"
                ),
                {
                    "ts": int(time.time()),
                    "contenido": contenido,
                    "f": FUENTE_TEST,
                    "parametrica": parametrica,
                },
            )
        ).first()
    return str(fila[0])


# ---------- techo paramétrico + desglose ----------


async def test_parametrica_topa_confianza_al_recall(
    motor: AsyncEngine,
) -> None:
    """Lo afirmado desde los pesos no supera el techo 0.70."""
    marca = f"parametrica-{uuid.uuid4().hex[:8]}"
    await _insertar_evento(
        motor, f"{marca} afirmado sin observar", parametrica=True
    )
    resultados = await tools._consultar(marca, 10)
    propio = next(
        r for r in resultados if marca in r["evento"]["contenido"]
    )
    assert propio["evento"]["parametrica"] is True
    assert propio["confianza"] <= 0.70
    assert propio["desglose_confianza"]["techo"] == 0.7
    assert propio["desglose_confianza"]["parametrica"] is True


async def test_no_parametrica_no_tiene_techo(motor: AsyncEngine) -> None:
    """Contenido observado llega a la confianza completa."""
    marca = f"observado-{uuid.uuid4().hex[:8]}"
    await _insertar_evento(
        motor, f"{marca} visto en el log", parametrica=False
    )
    resultados = await tools._consultar(marca, 10)
    propio = next(
        r for r in resultados if marca in r["evento"]["contenido"]
    )
    assert propio["evento"]["parametrica"] is False
    assert propio["desglose_confianza"]["techo"] == 1.0


async def test_aprobar_staged_levanta_parametrica(
    motor: AsyncEngine,
) -> None:
    """La aprobación humana corrobora — limpia la marca paramétrica."""
    marca = f"staged-parametrica-{uuid.uuid4().hex[:8]}"
    async with motor.begin() as conn:
        fila = (
            await conn.execute(
                text(
                    "INSERT INTO eventos (id, timestamp_unix, dimension,"
                    " tipo, contenido, amplitud, weight, fuente, staged,"
                    " cuarentena, archivado, curado, refuerzos, debilitados,"
                    " scope, parametrica, workspace_id)"
                    " VALUES (gen_random_uuid(), :ts, 'cognitiva', 'hecho',"
                    " :contenido, 0.9, 1.0, :f, true, false, false, false,"
                    " 0, 0, 'personal', true,"
                    " (SELECT id FROM workspaces LIMIT 1)) RETURNING id"
                ),
                {"ts": int(time.time()), "contenido": marca, "f": FUENTE_TEST},
            )
        ).first()
    evento_id = str(fila[0])

    await tools.aprobar_evento(evento_id)

    async with motor.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT staged, parametrica FROM eventos"
                    " WHERE id = :id"
                ),
                {"id": evento_id},
            )
        ).first()
    assert row.staged is False
    # Aprobada por humano = corroborada — el techo ya no aplica.
    assert row.parametrica is False


# ---------- expansión hebbiana STP/LTP ----------


async def test_expansion_coactiva_trae_vecinos(motor: AsyncEngine) -> None:
    """Un vecino coactivo asoma en el recall aunque no matchee."""
    marca = uuid.uuid4().hex[:8]
    marca_vecino = uuid.uuid4().hex[:8]
    ancla_id = await _insertar_evento(
        motor, f"ancla-{marca} término único de búsqueda"
    )
    # El vecino no comparte ningún token con la consulta: solo puede
    # asomar por la arista coactiva, nunca como hit léxico directo.
    vecino_id = await _insertar_evento(
        motor, f"vecino-{marca_vecino} concepto sin relación textual"
    )
    # Arista hebbiana sembrada: se encendieron juntas hace 1h.
    async with motor.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO relaciones (origen_id, destino_id, tipo,"
                " peso, ultima_coactiva, workspace_id)"
                " VALUES (least(CAST(:a AS uuid), CAST(:b AS uuid)),"
                " greatest(CAST(:a AS uuid), CAST(:b AS uuid)),"
                " 'coactiva', 1.5, :uc,"
                " (SELECT workspace_id FROM eventos"
                " WHERE id = CAST(:a AS uuid)))"
            ),
            {
                "a": ancla_id,
                "b": vecino_id,
                "uc": int(time.time()) - 3600,
            },
        )
    resultados = await tools._consultar(f"ancla-{marca}", 10)
    asociados = [r for r in resultados if r.get("via") == "coactiva"]
    assert asociados, "el vecino hebbiano debió asomar vía coactiva"
    hallado = next(
        (
            a
            for a in asociados
            if f"vecino-{marca_vecino}" in a["evento"]["contenido"]
        ),
        None,
    )
    assert hallado is not None
    # STP: 1h tras el co-encendido el efectivo supera el LTP 1.5.
    assert hallado["peso_efectivo"] > 1.5


# ---------- mapa de lagunas ----------


async def test_mapa_lagunas_sobre_tema_vacio_es_total(
    motor: AsyncEngine,
) -> None:
    """Sin evidencia, la herramienta lo declara como laguna total."""
    mapa = await tools._mapa_lagunas(f"laguna-{uuid.uuid4().hex[:12]}", 10)
    assert mapa["es_laguna_total"] is True
    assert mapa["cobertura"] == 0.0
    assert mapa["lagunas"]


async def test_mapa_lagunas_reporta_estructura(motor: AsyncEngine) -> None:
    """Con evidencia, el mapa declara cobertura y huecos concretos."""
    marca = f"cubierto-{uuid.uuid4().hex[:8]}"
    await _insertar_evento(motor, f"{marca} evidencia cognitiva")
    mapa = await tools._mapa_lagunas(marca, 10)
    assert mapa["es_laguna_total"] is False
    assert mapa["resultados"] >= 1
    assert "somatica" in " ".join(mapa["lagunas"])
