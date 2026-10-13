"""Tests de integración de la CLI contra PostgreSQL real (nous_test).

Cubre el roundtrip export/import de la memoria en JSONL y las
estadísticas. El generador de embeddings se sustituye por uno falso:
lo que se valida es la persistencia, no el modelo.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.cli import main as cli
from src.cli import transferencia
from src.storage.models import EMBEDDING_DIM
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_cli"


class _EmbeddingsFalsos:
    """GeneradorEmbeddings falso: vectores deterministas sin modelo."""

    async def generar_lote(self, textos: list[str]) -> list[list[float]]:
        """Retorna un vector de 0.1 por cada texto."""
        return [[0.1] * EMBEDDING_DIM for _ in textos]


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Redirige la CLI a nous_test y limpia las filas creadas."""
    engine = create_async_engine(TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OSError:
        await engine.dispose()
        pytest.skip("PostgreSQL de test no disponible")

    from src.config import get_settings

    # cli.main corre en otro thread con su propio event loop: no se
    # puede inyectar el engine (asyncpg se liga al loop que lo creó).
    # Apuntamos la config a nous_test para que crear_engine construya
    # un engine propio en el loop del thread.
    monkeypatch.setenv("NOUS_DATABASE_URL", TEST_DB_URL)
    get_settings.cache_clear()
    monkeypatch.setattr(
        transferencia, "GeneradorEmbeddings", _EmbeddingsFalsos
    )
    yield engine
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente IN ('test_cli', 'import')")
        )
    await engine.dispose()
    # El lru_cache retiene la Settings apuntando a nous_test: limpiar
    # tras restaurar el env para no contaminar tests posteriores.
    get_settings.cache_clear()


async def _insertar_evento(motor: AsyncEngine, evento_id: uuid.UUID) -> None:
    """Inserta un evento de prueba con fuente test_cli."""
    async with motor.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO eventos (id, timestamp_unix, dimension, "
                "fuente, contenido, amplitud) "
                "VALUES (:id, :ts, 'cognitiva', :fuente, 'nota', 0.7)"
            ),
            {
                "id": str(evento_id),
                "ts": int(time.time()),
                "fuente": FUENTE_TEST,
            },
        )


async def test_exportar_escribe_jsonl_sin_embedding(
    motor: AsyncEngine, tmp_path: Path
) -> None:
    """El JSONL exportado contiene los campos semánticos, no el vector."""
    evento_id = uuid.uuid4()
    await _insertar_evento(motor, evento_id)
    archivo = tmp_path / "memoria.jsonl"
    total = await transferencia.exportar(motor, archivo)
    assert total >= 1
    filas = [
        json.loads(linea)
        for linea in archivo.read_text("utf-8").splitlines()
    ]
    mia = [f for f in filas if f["id"] == str(evento_id)]
    assert len(mia) == 1
    assert mia[0]["dimension"] == "cognitiva"
    assert "embedding" not in mia[0]


async def test_importar_roundtrip_dedup_y_errores(
    motor: AsyncEngine, tmp_path: Path
) -> None:
    """Importa nuevos, omite duplicados por id y cuenta líneas malas."""
    duplicado = uuid.uuid4()
    await _insertar_evento(motor, duplicado)
    nuevo = uuid.uuid4()
    archivo = tmp_path / "memoria.jsonl"
    archivo.write_text(
        json.dumps({
            "id": str(nuevo), "timestamp_unix": int(time.time()),
            "dimension": "somatica", "fuente": FUENTE_TEST,
            "contenido": "descansé bien", "amplitud": 0.6,
            "frecuencia": None,
        }) + "\n"
        + json.dumps({
            "id": str(duplicado), "timestamp_unix": int(time.time()),
            "dimension": "cognitiva", "fuente": FUENTE_TEST,
            "contenido": "duplicado", "amplitud": 0.5,
            "frecuencia": None,
        }) + "\n"
        + "linea corrupta\n",
        encoding="utf-8",
    )
    resumen = await transferencia.importar(motor, archivo)
    assert resumen == {
        "importados": 1, "omitidos": 1, "errores": 1, "vinculos": 0,
    }
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text(
                    "SELECT dimension, embedding IS NOT NULL "
                    "FROM eventos WHERE id = :id"
                ),
                {"id": str(nuevo)},
            )
        ).one()
    assert fila == ("somatica", True)


async def test_stats_reporta_totales(
    motor: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    """nous stats --json reporta el total real de la DB de test."""
    await _insertar_evento(motor, uuid.uuid4())
    # cli.main usa asyncio.run: requiere un thread sin loop corriendo.
    assert await asyncio.to_thread(cli.main, ["stats", "--json"]) == 0
    datos = json.loads(capsys.readouterr().out)
    assert datos["total"] >= 1
    assert datos["por_dimension"]["cognitiva"] >= 1


async def test_cli_export_e2e(
    motor: AsyncEngine, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """El comando `nous export` completo funciona end-to-end."""
    await _insertar_evento(motor, uuid.uuid4())
    archivo = tmp_path / "salida.jsonl"
    assert await asyncio.to_thread(
        cli.main, ["export", str(archivo)]
    ) == 0
    assert "Exportados" in capsys.readouterr().out
    assert archivo.is_file()
