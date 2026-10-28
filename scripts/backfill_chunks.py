"""Backfill de ``evento_chunks`` para eventos ya persistidos.

Idempotente: ``reemplazar_chunks`` borra y reinserta, así que es
re-ejecutable sin duplicar. Uso::

    python scripts/backfill_chunks.py [--workspace SLUG] [--solo-sin-chunks]

Sin ``--workspace`` indexa todos los workspaces.
"""

import argparse
import asyncio
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.embeddings import GeneradorEmbeddings
from src.storage.chunks import indexar_chunks_lote, requiere_troceo
from src.storage.database import crear_engine, get_session
from src.storage.models import EventoChunkModel, EventoModel, WorkspaceModel

logger = logging.getLogger("backfill_chunks")
LOTE = 40


async def _workspaces(
    session: AsyncSession,
) -> dict[str, uuid.UUID]:
    filas = (await session.execute(select(WorkspaceModel.slug, WorkspaceModel.id))).all()
    return {slug: id_ for slug, id_ in filas}


async def main() -> None:
    """Indexa los chunks de los eventos que lo ameritan."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--solo-sin-chunks",
        action="store_true",
        help="salta eventos que ya tienen fragmentos indexados",
    )
    args = parser.parse_args()

    engine = crear_engine()
    generador = GeneradorEmbeddings()
    try:
        async with get_session(engine) as session:
            ws = await _workspaces(session)
            if args.workspace and args.workspace not in ws:
                raise SystemExit(
                    f"workspace '{args.workspace}' no existe: {sorted(ws)}"
                )
            filtros = []
            if args.workspace:
                filtros.append(EventoModel.workspace_id == ws[args.workspace])
            if args.solo_sin_chunks:
                tiene = (
                    select(func.count())
                    .where(EventoChunkModel.evento_id == EventoModel.id)
                    .scalar_subquery()
                )
                filtros.append(tiene == 0)
            total = (
                await session.execute(
                    select(func.count()).select_from(EventoModel).where(*filtros)
                )
            ).scalar_one()
            logger.info("eventos candidatos: %d", total)
            procesados = indexados = 0
            offset = 0
            while True:
                filas = (
                    await session.execute(
                        select(EventoModel.id, EventoModel.contenido)
                        .where(*filtros)
                        .order_by(EventoModel.id)
                        .offset(offset)
                        .limit(LOTE)
                    )
                ).all()
                if not filas:
                    break
                items = [
                    (f[0], f[1]) for f in filas if requiere_troceo(f[1])
                ]
                indexados += await indexar_chunks_lote(
                    session, items, generador
                )
                await session.commit()
                procesados += len(filas)
                offset += LOTE
                logger.info(
                    "progreso: %d/%d eventos, %d chunks",
                    procesados, total, indexados,
                )
            logger.info("listo: %d chunks indexados", indexados)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
