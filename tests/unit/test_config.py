"""Tests de carga de configuración desde .env."""

import os
from pathlib import Path

from src.config import get_settings


def test_env_se_carga_desde_el_root_sin_importar_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """El .env del repo aplica aunque el proceso arranque en otro cwd.

    Regresión: un launcher MCP que spawnee el servidor desde un cwd
    distinto al root del proyecto no debe caer a los defaults de
    ``database_url`` (apuntaría a una DB equivocada en silencio).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOUS_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        esperado = os.environ.get("NOUS_DATABASE_URL")
        if esperado:
            assert settings.database_url == esperado
        else:
            assert settings.database_url != (
                "postgresql+asyncpg://nous:nous@localhost:5432/nous"
            )
    finally:
        get_settings.cache_clear()
