"""Conexión async a PostgreSQL vía SQLAlchemy 2.0 + asyncpg."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import get_settings


def crear_engine() -> AsyncEngine:
    """Crea el engine async a partir de la configuración.

    Returns:
        Engine async de SQLAlchemy con pool_pre_ping habilitado.
    """
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Provee una sesión async con cleanup garantizado.

    Args:
        engine: Engine async de SQLAlchemy.

    Yields:
        Sesión async lista para usar.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
