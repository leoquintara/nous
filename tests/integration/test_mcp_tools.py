"""Tests E2E de las tools MCP contra PostgreSQL real (nous_test).

Las tools usan el engine singleton del proceso; el fixture lo
redirige a la base de test aislada y limpia las filas creadas.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.mcp import tools
from tests.integration.conftest import TEST_DB_URL

FUENTE_TEST = "test_mcp"


@pytest_asyncio.fixture
async def motor(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Redirige las tools a nous_test y limpia al finalizar."""
    engine = create_async_engine(TEST_DB_URL)
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
        tools, "crear_engine",
        lambda: create_async_engine(TEST_DB_URL),
    )
    yield engine
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM eventos WHERE fuente = :fuente"),
            {"fuente": FUENTE_TEST},
        )
        await conn.execute(text("DELETE FROM proyecciones"))
    await engine.dispose()


async def test_recordar_evento_persiste_con_dimension_explicita(
    motor: AsyncEngine,
) -> None:
    """Verifica que recordar_evento guarda el evento en la DB."""
    respuesta = await tools.recordar_evento(
        "decidí probar la persistencia de nous",
        dimension="cognitiva",
        fuente=FUENTE_TEST,
        amplitud=0.8,
    )
    assert respuesta["dimension"] == "cognitiva"
    assert respuesta["indexado"] is False
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text(
                    "SELECT dimension, amplitud, embedding IS NOT NULL "
                    "FROM eventos WHERE id = :id"
                ),
                {"id": respuesta["id"]},
            )
        ).one()
    assert fila[0] == "cognitiva"
    # La sorpresa (prediction-error) puede premiar la amplitud en
    # indexación: nunca la baja de lo declarado y nunca pasa de 1.0.
    assert 0.8 <= fila[1] <= 1.0
    assert fila[2] is True


async def test_recordar_evento_clasifica_sin_dimension(
    motor: AsyncEngine,
) -> None:
    """Verifica la clasificación automática cuando se omite dimensión."""
    respuesta = await tools.recordar_evento(
        "corrí cinco kilómetros y me siento agotado",
        fuente=FUENTE_TEST,
    )
    assert respuesta["dimension"] in {"cognitiva", "somatica", "conductual"}
    assert "confianza_clasificacion" in respuesta


async def test_consultar_memoria_retorna_contexto_4d(
    motor: AsyncEngine,
) -> None:
    """Verifica resultados con vecinos temporales adjuntos."""
    for texto, dim in [
        ("desayuné avena temprano", "conductual"),
        ("reflexioné sobre el futuro del proyecto", "cognitiva"),
        ("dormí profundamente toda la noche", "somatica"),
    ]:
        await tools.recordar_evento(texto, dimension=dim, fuente=FUENTE_TEST)
    await tools._drenar_fondo()

    resultados = await tools.consultar_memoria("futuro del proyecto", top_n=3)

    assert resultados
    top = resultados[0]
    assert "futuro" in top["evento"]["contenido"]
    assert "contexto_temporal" in top
    assert top["score_final"] > 0


async def test_proyectar_trayectoria_persiste_proyeccion(
    motor: AsyncEngine,
) -> None:
    """Verifica que la proyección se genera y persiste en la DB."""
    await tools.recordar_evento(
        "evento semilla para proyección", fuente=FUENTE_TEST
    )
    respuesta = await tools.proyectar_trayectoria(
        horizonte_dias=7, dias_historial=30
    )
    assert respuesta["dimension_probable"] in respuesta["distribucion"]
    total = sum(respuesta["distribucion"].values())
    assert total == pytest.approx(1.0, abs=0.01)
    async with motor.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM proyecciones WHERE id = :id"),
                {"id": respuesta["id"]},
            )
        ).scalar_one()
    assert n == 1


async def test_recordar_evento_rechaza_amplitud_invalida(
    motor: AsyncEngine,
) -> None:
    """Verifica que amplitud fuera de [0, 1] lanza ValueError."""
    with pytest.raises(ValueError, match="amplitud"):
        await tools.recordar_evento("x", amplitud=1.5)


async def test_consultar_memoria_sin_coincidencias_lista_vacia(
    motor: AsyncEngine,
) -> None:
    """Verifica que una query sin hits retorna lista vacía."""
    resultados = await tools.consultar_memoria("xyzzy termo inexistente")
    assert resultados == []


async def test_obtener_linea_rango_invertido_lanza_error(
    motor: AsyncEngine,
) -> None:
    """Verifica que desde > hasta lanza ValueError."""
    with pytest.raises(ValueError, match="desde_unix"):
        await tools.obtener_linea_de_universo(2000, 1000)


async def test_recordar_evento_crea_vinculos_explicitos(
    motor: AsyncEngine,
) -> None:
    """Verifica que relaciona_ids persiste vínculos entre eventos."""
    a = await tools.recordar_evento(
        "día de carga laboral intensa", dimension="conductual", fuente=FUENTE_TEST
    )
    b = await tools.recordar_evento(
        "el estrés viene con la carga",
        dimension="somatica",
        fuente=FUENTE_TEST,
        relaciona_ids=[a["id"]],
    )
    assert b["vinculos_creados"] == 1
    async with motor.connect() as conn:
        fila = (
            await conn.execute(
                text(
                    "SELECT tipo, peso FROM relaciones "
                    "WHERE (origen_id = :a AND destino_id = :b) "
                    "OR (origen_id = :b AND destino_id = :a)"
                ),
                {"a": a["id"], "b": b["id"]},
            )
        ).one()
    assert fila[0] == "explicita"
    assert fila[1] == pytest.approx(1.0)


async def test_recordar_evento_ignora_ids_invalidos(
    motor: AsyncEngine,
) -> None:
    """Verifica que ids inexistentes o malformados no rompen el insert."""
    respuesta = await tools.recordar_evento(
        "evento con vínculos malos",
        fuente=FUENTE_TEST,
        relaciona_ids=["no-es-uuid", "00000000-0000-0000-0000-000000000000"],
    )
    assert "vinculos_creados" not in respuesta


async def test_recordar_evento_reporta_entidad_sin_antecedente(
    motor: AsyncEngine,
) -> None:
    """Verifica el aviso de contexto incompleto con nombre propio nuevo."""
    respuesta = await tools.recordar_evento(
        "Xiomara me genera mucha presión",
        fuente=FUENTE_TEST,
    )
    contexto = respuesta["contexto"]
    assert contexto["completo"] is False
    assert "Xiomara" in contexto["entidades_nuevas"]
    assert "sugerencia" in contexto


async def test_recordar_evento_contexto_completo_con_antecedente(
    motor: AsyncEngine,
) -> None:
    """Verifica completo=true cuando la entidad ya tiene precedente."""
    await tools.recordar_evento(
        "Bernarda es mi gata", fuente=FUENTE_TEST
    )
    respuesta = await tools.recordar_evento(
        "Bernarda dormía junto al teclado", fuente=FUENTE_TEST
    )
    assert respuesta["contexto"]["completo"] is True


async def test_indexacion_crea_vinculo_semantico(motor: AsyncEngine) -> None:
    """Verifica que el worker ata huellas muy similares al indexar."""
    await tools.recordar_evento(
        "el ajedrez del domingo me concentró muchísimo",
        fuente=FUENTE_TEST,
    )
    b = await tools.recordar_evento(
        "el ajedrez del domingo me concentró bastante",
        fuente=FUENTE_TEST,
    )
    await tools._drenar_fondo()
    async with motor.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM relaciones "
                    "WHERE tipo = 'semantica' AND "
                    "(origen_id = :b OR destino_id = :b)"
                ),
                {"b": b["id"]},
            )
        ).scalar_one()
    # Si la dedup los fusionó, no hay segundo evento ni vínculo;
    # si quedaron separados, la similitud alta debió crear el vínculo.
    if n == 0:
        async with motor.connect() as conn:
            existe_b = (
                await conn.execute(
                    text("SELECT count(*) FROM eventos WHERE id = :b"),
                    {"b": b["id"]},
                )
            ).scalar_one()
        assert existe_b == 0  # fusionado por dedup


async def test_relaciones_lista_vinculos(motor: AsyncEngine) -> None:
    """Verifica que _relaciones devuelve los vínculos persistidos."""
    a = await tools.recordar_evento("pieza uno", fuente=FUENTE_TEST)
    await tools.recordar_evento(
        "pieza dos", fuente=FUENTE_TEST, relaciona_ids=[a["id"]]
    )
    filas = await tools._relaciones(50)
    par = {str(a["id"])}
    assert any(
        {r["origen_id"], r["destino_id"]} == par
        or a["id"] in (r["origen_id"], r["destino_id"])
        for r in filas
    )
