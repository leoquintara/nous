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


async def test_requiere_troceo_solo_para_contenido_largo() -> None:
    """El umbral de troceo solo aplica a contenido que lo excede."""
    assert not requiere_troceo("breve")
    assert requiere_troceo("x" * 1300)
