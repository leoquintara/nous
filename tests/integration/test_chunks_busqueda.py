"""Tests E2E del retrieval a nivel fragmento (nous_test + embeddings reales)."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.busqueda import BusquedaHibrida4D
from src.core.embeddings import GeneradorEmbeddings
from src.storage.chunks import indexar_chunks_evento, requiere_troceo
from src.storage.models import EventoModel
from src.storage.repositories import EventoRepository

_CONTENIDO_LARGO = """# Manual Operativo del Equipo

## Introducción

""" + ("Rutinas y procesos generales del equipo de soporte. " * 60) + """

## Identificar la Versión de un Proyecto

Para saber si un landing usa React V1 o React V2 revisar tres señales:
el directorio core/ solo existe en V2, los componentes UI de V2 vienen
de @quintara.ai/ui en vez de src/components/ locales, y los imports de
V2 usan aliases @core/ y @widgets/ en vez de rutas relativas desordenadas.

## Cierre

""" + ("Notas finales y checklist de publicación. " * 60)


async def test_chunk_recupera_evento_por_seccion_profunda(
    session: AsyncSession,
) -> None:
    """Una sección profunda compite con su señal propia, no la media."""
    embeddings = GeneradorEmbeddings()
    repo = EventoRepository(session)
    evento = await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_000,
            dimension="cognitiva",
            fuente="test",
            contenido=_CONTENIDO_LARGO,
            embedding=await embeddings.generar(_CONTENIDO_LARGO),
        )
    )
    await indexar_chunks_evento(
        session, evento.id, evento.contenido, embeddings
    )

    hits = await repo.buscar_chunks_por_texto(
        "directorio core existe version", rama=None
    )

    assert hits
    assert hits[0][0].id == evento.id


async def test_busqueda_hibrida_usa_chunks_para_documento_largo(
    session: AsyncSession,
) -> None:
    """La pata de chunks participa en la fusión RRF del pipeline."""
    embeddings = GeneradorEmbeddings()
    repo = EventoRepository(session)
    evento = await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_100,
            dimension="cognitiva",
            fuente="test",
            contenido=_CONTENIDO_LARGO,
            embedding=await embeddings.generar(_CONTENIDO_LARGO),
        )
    )
    await indexar_chunks_evento(
        session, evento.id, evento.contenido, embeddings
    )

    busqueda = BusquedaHibrida4D(embeddings)
    resultados = await busqueda.buscar(
        session, "imports aliases core widgets", top_n=5
    )

    assert resultados
    assert any(r.evento_id == str(evento.id) for r in resultados)


async def test_wanted_link_no_es_evocable_en_recall(
    session: AsyncSession,
) -> None:
    """Los registros wanted-link quedan fuera del recall general."""
    repo = EventoRepository(session)
    await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_200,
            dimension="cognitiva",
            fuente="test",
            contenido="Enlace sin resolver [[pagina-inexistente]]",
            tags=["wanted-link"],
        )
    )

    hits = await repo.buscar_por_texto("enlace resolver pagina", rama=None)

    assert all("wanted-link" not in (m.tags or []) for m, _ in hits)


async def test_curado_recibe_boost_en_score_final(
    session: AsyncSession,
) -> None:
    """Un evento curado puntúa por encima de su gemelo no curado."""
    embeddings = GeneradorEmbeddings()
    repo = EventoRepository(session)
    texto = "protocolo de privacidad de la memoria del usuario"
    vector = await embeddings.generar(texto)
    curado = await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_300,
            dimension="cognitiva",
            fuente="test",
            contenido=texto + " versión curada",
            embedding=vector,
            curado=True,
        )
    )
    await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_301,
            dimension="cognitiva",
            fuente="test",
            contenido=texto + " versión común",
            embedding=vector,
            curado=False,
        )
    )

    busqueda = BusquedaHibrida4D(embeddings)
    resultados = await busqueda.buscar(
        session, "protocolo privacidad memoria", top_n=5
    )

    assert resultados
    assert resultados[0].evento_id == str(curado.id)


async def test_pata_or_recupera_entidad_en_pregunta_natural(
    session: AsyncSession,
) -> None:
    """«quién es X y cuánto tiempo tiene» muere en AND, vive en OR."""
    repo = EventoRepository(session)
    carlos = await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_400,
            dimension="cognitiva",
            fuente="test",
            contenido=(
                "# Carlos\n\nDev semi-senior del equipo de "
                "Digital Booting. GitLab: charlyp04."
            ),
        )
    )
    pregunta = (
        "quien es carlos y cuanto tiempo tiene en digital booting"
    )

    hits_and = await repo.buscar_por_texto(pregunta, rama=None)
    hits_or = await repo.buscar_por_texto_or(pregunta, rama=None)

    assert all(str(m.id) != str(carlos.id) for m, _ in hits_and)
    assert any(str(m.id) == str(carlos.id) for m, _ in hits_or)


async def test_pata_titulo_prioriza_lexema_raro(
    session: AsyncSession,
) -> None:
    """El documento titulado con el término más raro gana la pata."""
    repo = EventoRepository(session)
    carlos = await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_600,
            dimension="cognitiva",
            fuente="test",
            contenido="# Carlos\n\nDev semi-senior del equipo.",
        )
    )
    await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_601,
            dimension="cognitiva",
            fuente="test",
            contenido="# Equipo de Digital Booting\n\nÍndice general.",
        )
    )

    hits = await repo.buscar_por_titulo(
        "quien es carlos del equipo digital", rama=None
    )

    assert hits
    assert str(hits[0][0].id) == str(carlos.id)


async def test_pata_or_no_desplaza_al_match_completo(
    session: AsyncSession,
) -> None:
    """El documento que cubre todos los lexemas sigue encabezando."""
    repo = EventoRepository(session)
    completo = await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_500,
            dimension="cognitiva",
            fuente="test",
            contenido=(
                "Protocolo de ventana de tiempo para responder "
                "mensajes: cinco minutos máximo."
            ),
        )
    )
    await repo.crear(
        EventoModel(
            timestamp_unix=1_700_000_501,
            dimension="cognitiva",
            fuente="test",
            contenido="Lista de mensajes pendientes del día.",
        )
    )

    hits_or = await repo.buscar_por_texto_or(
        "ventana tiempo responder mensajes", rama=None
    )

    assert hits_or
    assert str(hits_or[0][0].id) == str(completo.id)


async def test_requiere_troceo_solo_para_contenido_largo() -> None:
    """El umbral de troceo solo aplica a contenido que lo excede."""
    assert not requiere_troceo("breve")
    assert requiere_troceo("x" * 1300)


async def test_pata_temporal_ordena_por_timestamp(
    session: AsyncSession,
) -> None:
    """«El primero» ordena por timestamp_unix, no por relevancia."""
    embeddings = GeneradorEmbeddings()
    repo = EventoRepository(session)
    viejo = await repo.crear(
        EventoModel(
            timestamp_unix=1_600_000_000,
            dimension="conductual",
            fuente="test",
            contenido="Primera mencion registrada de la entidad zephyr.",
            embedding=await embeddings.generar("zephyr primera"),
        )
    )
    await repo.crear(
        EventoModel(
            timestamp_unix=1_750_000_000,
            dimension="conductual",
            fuente="test",
            contenido="Actividad reciente de la entidad zephyr.",
            embedding=await embeddings.generar("zephyr reciente"),
        )
    )

    asc = await repo.buscar_linea_temporal("zephyr", "asc")
    desc = await repo.buscar_linea_temporal("zephyr", "desc")

    assert asc[0][0].id == viejo.id
    assert desc[0][0].id != viejo.id

    busqueda = BusquedaHibrida4D(embeddings)
    resultados = await busqueda.buscar(
        session,
        "primer registro de zephyr",
        top_n=5,
        orden_temporal="asc",
    )
    assert resultados
    assert resultados[0].evento_id == str(viejo.id)
