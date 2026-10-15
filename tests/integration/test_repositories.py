"""Tests de integración de los repositorios.

Requieren un PostgreSQL real accesible (NOUS_TEST_DATABASE_URL).
Se saltan automáticamente si la DB no está disponible.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import BenchmarkRunModel, EventoModel
from src.storage.repositories import BenchmarkRepository, EventoRepository

EMBEDDING_DIM = 384


def _evento(timestamp_unix: int, contenido: str, **kwargs: object) -> EventoModel:
    """Factory de eventos de prueba con defaults mínimos."""
    return EventoModel(
        timestamp_unix=timestamp_unix,
        dimension="cognitiva",
        fuente="test",
        contenido=contenido,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_crear_y_obtener_evento(session: AsyncSession) -> None:
    """Verifica que un evento se persiste y se recupera por id."""
    repo = EventoRepository(session)
    evento = await repo.crear(
        _evento(1_700_000_000, "decidí usar PostgreSQL para nous")
    )

    recuperado = await repo.obtener(evento.id)

    assert recuperado is not None
    assert recuperado.contenido == "decidí usar PostgreSQL para nous"
    assert recuperado.dimension == "cognitiva"


async def test_buscar_por_texto_encuentra_evento(session: AsyncSession) -> None:
    """Verifica que la búsqueda tsvector encuentra eventos por contenido."""
    repo = EventoRepository(session)
    await repo.crear(_evento(1_700_000_000, "medité veinte minutos hoy"))
    await repo.crear(_evento(1_700_000_100, "hice tres commits en el repo"))

    resultados = await repo.buscar_por_texto("meditar")

    assert len(resultados) == 1
    assert "medité" in resultados[0][0].contenido
    assert resultados[0][1] > 0.0


async def test_buscar_por_vector_ordena_por_similitud(session: AsyncSession) -> None:
    """Verifica que la búsqueda coseno ordena por cercanía del vector."""
    repo = EventoRepository(session)
    vector_cercano = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    vector_lejano = [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2)
    await repo.crear(
        _evento(1_700_000_000, "evento cercano", embedding=vector_cercano)
    )
    await repo.crear(
        _evento(1_700_000_100, "evento lejano", embedding=vector_lejano)
    )

    resultados = await repo.buscar_por_vector(vector_cercano)

    assert len(resultados) == 2
    assert resultados[0][0].contenido == "evento cercano"
    assert resultados[0][1] > resultados[1][1]


async def test_adyacentes_retorna_previos_y_siguientes(
    session: AsyncSession,
) -> None:
    """Verifica que adyacentes devuelve contexto temporal en ambas direcciones."""
    repo = EventoRepository(session)
    await repo.crear(_evento(1_700_000_000, "pasado lejano"))
    await repo.crear(_evento(1_700_000_050, "pasado cercano"))
    await repo.crear(_evento(1_700_000_200, "futuro cercano"))

    previos, siguientes = await repo.adyacentes(1_700_000_100, cantidad=5)

    assert [e.contenido for e in previos] == ["pasado cercano", "pasado lejano"]
    assert [e.contenido for e in siguientes] == ["futuro cercano"]


async def test_adyacentes_lote_equivale_a_adyacentes_individual(
    session: AsyncSession,
) -> None:
    """La consulta en lote devuelve lo mismo que adyacentes por marca."""
    repo = EventoRepository(session)
    await repo.crear(_evento(1_600_000_000, "lote previo uno"))
    await repo.crear(_evento(1_600_000_050, "lote previo dos"))
    await repo.crear(_evento(1_600_000_100, "lote centro"))
    await repo.crear(_evento(1_600_000_200, "lote posterior"))

    marcas = [1_600_000_100, 1_600_000_200]
    mapa = await repo.adyacentes_lote(marcas, cantidad=3)

    for i, ts in enumerate(marcas):
        previos_esperados, siguientes_esperados = await repo.adyacentes(
            ts, cantidad=3
        )
        previos, siguientes = mapa[i]
        assert [e.contenido for e in previos] == [
            e.contenido for e in previos_esperados
        ]
        assert [e.contenido for e in siguientes] == [
            e.contenido for e in siguientes_esperados
        ]


async def test_adyacentes_lote_vacio_retorna_mapa_vacio(
    session: AsyncSession,
) -> None:
    """Sin marcas no se ejecuta consulta y el mapa sale vacío."""
    repo = EventoRepository(session)
    assert await repo.adyacentes_lote([]) == {}


async def test_registrar_y_listar_benchmark(session: AsyncSession) -> None:
    """Verifica el roundtrip de una corrida de benchmark persistida."""
    repo = BenchmarkRepository(session)
    corrida = await repo.registrar(
        BenchmarkRunModel(
            nombre="busqueda_hibrida",
            escenario="test",
            parametros={"consultas": 30},
            metricas={"latencia_ms_p95": 42.0},
            git_commit="abc1234",
        )
    )

    corridas = await repo.listar(nombre="busqueda_hibrida")

    assert len(corridas) == 1
    assert corridas[0].id == corrida.id
    assert corridas[0].metricas["latencia_ms_p95"] == 42.0


async def test_eliminar_borra_el_evento(session: AsyncSession) -> None:
    """eliminar borra el evento de forma incondicional."""
    repo = EventoRepository(session)
    evento = await repo.crear(_evento(1_700_000_000, "olvidable"))

    assert await repo.eliminar(evento.id) is True
    assert await repo.obtener(evento.id) is None
    assert await repo.eliminar(evento.id) is False


async def test_eliminar_de_limpia_vinculos(session: AsyncSession) -> None:
    """eliminar_de borra los vínculos que apuntan al evento."""
    from src.storage.repositories import RelacionRepository

    eventos = EventoRepository(session)
    relaciones = RelacionRepository(session)
    a = await eventos.crear(_evento(1_700_000_000, "origen"))
    b = await eventos.crear(_evento(1_700_000_100, "destino"))
    await relaciones.vincular(a.id, b.id, "explicita")

    assert await relaciones.eliminar_de(a.id) == 1
    assert await relaciones.listar() == []


async def test_buscar_por_texto_filtra_rama(session: AsyncSession) -> None:
    """La búsqueda por texto limita a la rama indicada."""
    repo = EventoRepository(session)
    await repo.crear(_evento(1_700_000_000, "decisión real", rama="main"))
    await repo.crear(
        _evento(1_700_000_100, "decisión alternativa", rama="whatif")
    )

    main = await repo.buscar_por_texto("decisión", rama="main")
    whatif = await repo.buscar_por_texto("decisión", rama="whatif")
    todas = await repo.buscar_por_texto("decisión")

    assert [e.contenido for e, _ in main] == ["decisión real"]
    assert [e.contenido for e, _ in whatif] == ["decisión alternativa"]
    assert len(todas) == 2


async def test_buscar_por_vector_filtra_rama(session: AsyncSession) -> None:
    """La búsqueda vectorial limita a la rama indicada."""
    repo = EventoRepository(session)
    vec = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    await repo.crear(
        _evento(1_700_000_000, "main", embedding=vec, rama="main")
    )
    await repo.crear(
        _evento(1_700_000_100, "rama", embedding=vec, rama="whatif")
    )

    main = await repo.buscar_por_vector(vec, rama="main")

    assert len(main) == 1
    assert main[0][0].rama == "main"
