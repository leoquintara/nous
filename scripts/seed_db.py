"""Pobla la base de datos con eventos de ejemplo."""

import asyncio
import logging

logger = logging.getLogger("nous.scripts.seed_db")


async def main() -> None:
    """Punto de entrada del seed."""
    logger.info("Seed pendiente de implementar")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
