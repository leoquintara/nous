"""Fixtures compartidos de la suite de tests."""

import pytest

from src.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings de test con valores por defecto.

    Returns:
        Instancia de Settings con valores por defecto.
    """
    return Settings()
