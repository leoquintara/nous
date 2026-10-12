"""Configuración de Nous vía pydantic-settings.

Todas las variables de entorno usan el prefijo ``NOUS_`` y se cargan
automáticamente desde ``.env`` si existe.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global de la aplicación.

    Local-first por defecto: embeddings locales y transporte stdio.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="NOUS_", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://nous:nous@localhost:5432/nous"
    embedding_provider: Literal["local", "external"] = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2"
    mcp_transport: Literal["stdio", "http"] = "stdio"
    api_host: str = "127.0.0.1"
    api_port: int = 8720
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Retorna la configuración cacheada de la aplicación.

    Returns:
        Instancia única de Settings.
    """
    return Settings()
