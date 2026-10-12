"""Benchmark de la búsqueda híbrida (objetivo: < 200ms p95)."""

import asyncio
import logging

logger = logging.getLogger("nous.scripts.benchmark_search")


async def main() -> None:
    """Punto de entrada del benchmark."""
    logger.info("Benchmark pendiente de implementar")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
