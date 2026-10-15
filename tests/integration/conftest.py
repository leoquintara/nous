"""Fixtures compartidos de los tests de integración."""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = os.environ.get(
    "NOUS_TEST_DATABASE_URL",
    "postgresql+asyncpg://nous:nous@localhost:5433/nous_test",
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Sesión con rollback al final para no contaminar la DB de test."""
    engine = create_async_engine(TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OSError:
        await engine.dispose()
        pytest.skip("PostgreSQL de test no disponible")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _licencia_test() -> Iterator[None]:
    """Licencia activa por defecto en toda la suite de integración.

    El feature gate (1 usuario/3 workspaces sin licencia) haría
    fallar cualquier test que cree identidades — los tests del
    propio gate desactivan la licencia explícitamente.
    """

    from src.config import get_settings

    s = get_settings()
    prev = s.license_key
    s.license_key = "test-suite"
    try:
        yield
    finally:
        s.license_key = prev
