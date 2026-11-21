"""Backfill del enriquecimiento ADR-003 para eventos importados.

Los eventos que entraron por importación (memlord/gbrain/JSON) se
indexaron con embedding pero saltaron el enriquecimiento del indexer:
``tipo_memoria``, ``estado_emocional`` y ``contexto`` quedaron vacíos.
Este script los recorre y aplica la misma clasificación local:

- ``tipo_memoria``: heurístico léxico (``clasificar_tipo_memoria_heuristico``).
- ``estado_emocional``: valencia léxica (``estimar_estado_emocional``),
  NULL cuando el texto es neutro — igual que el indexer.
- ``contexto``: sella ``hora_dia`` (del ``timestamp_unix`` real) y
  ``fuente`` (la fuente declarada del evento), preservando claves extra.

Todo es local e idempotente: solo toca filas con ``tipo_memoria IS NULL``
o ``contexto = {}``, y nunca sobrescribe valores existentes.

Uso:
    python scripts/backfill_enriquecimiento.py [--batch 500] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.core.memoria.tipos import (
    clasificar_tipo_memoria_heuristico,
    estimar_estado_emocional,
)
from src.storage.database import obtener_engine
from src.storage.models import EventoModel

logger = logging.getLogger("backfill_enriquecimiento")


async def backfill(batch: int, dry_run: bool) -> dict[str, int]:
    """Enriquece eventos importados con la clasificación del indexer.

    Args:
        batch: Filas por commit intermedio.
        dry_run: Cuenta lo que haría sin escribir.

    Returns:
        Contadores de filas tocadas por categoría.
    """
    stats = {"candidatos": 0, "tipo_memoria": 0, "emocion": 0, "contexto": 0}
    engine = obtener_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        stmt = select(
            EventoModel.id,
            EventoModel.contenido,
            EventoModel.timestamp_unix,
            EventoModel.fuente,
            EventoModel.contexto,
            EventoModel.tipo_memoria,
        ).where(
            or_(
                EventoModel.tipo_memoria.is_(None),
                EventoModel.contexto == {},
            )
        )
        pendientes = (await session.execute(stmt)).all()
        stats["candidatos"] = len(pendientes)
        logger.info("Candidatos a enriquecer: %d", stats["candidatos"])

        for i, ev in enumerate(pendientes, 1):
            valores: dict[str, object] = {}
            if ev.tipo_memoria is None:
                valores["tipo_memoria"] = clasificar_tipo_memoria_heuristico(
                    ev.contenido
                ).value
                stats["tipo_memoria"] += 1
                valencia = estimar_estado_emocional(ev.contenido)
                if valencia is not None:
                    valores["estado_emocional"] = valencia
                    stats["emocion"] += 1
            if not ev.contexto:
                valores["contexto"] = {
                    "hora_dia": time.localtime(ev.timestamp_unix).tm_hour,
                    "fuente": ev.fuente,
                }
                stats["contexto"] += 1
            if valores and not dry_run:
                await session.execute(
                    update(EventoModel)
                    .where(EventoModel.id == ev.id)
                    .values(**valores)
                )
            if i % batch == 0:
                if not dry_run:
                    await session.commit()
                logger.info("Progreso: %d/%d", i, stats["candidatos"])
        if not dry_run:
            await session.commit()
    return stats


def main() -> None:
    """CLI del backfill."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stats = asyncio.run(backfill(args.batch, args.dry_run))
    prefix = "[dry-run] " if args.dry_run else ""
    logger.info("%sResultado: %s", prefix, stats)


if __name__ == "__main__":
    main()
