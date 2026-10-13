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

_engine: AsyncEngine | None = None


def crear_engine() -> AsyncEngine:
    """Crea el engine async a partir de la configuración.

    Returns:
        Engine async de SQLAlchemy con pool_pre_ping habilitado.
    """
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


def obtener_engine() -> AsyncEngine:
    """Retorna el engine compartido del proceso, creándolo si no existe.

    Returns:
        Engine async singleton con pool compartido.
    """
    global _engine
    if _engine is None:
        _engine = crear_engine()
    return _engine


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
