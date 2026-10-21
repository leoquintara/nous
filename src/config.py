"""Configuración de Nous vía pydantic-settings.

Todas las variables de entorno usan el prefijo ``NOUS_`` y se cargan
automáticamente desde ``.env`` si existe.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PREFIXO_TOKEN = "ns_"

# El .env se resuelve relativo al root del proyecto (donde vive este
# archivo), no al cwd del proceso: los clientes MCP que spawnearan el
# servidor desde otro directorio (p. ej. un launcher que ignore el
# cwd configurado) seguían encontrando la configuración real en lugar
# de caer silenciosamente a los defaults y apuntar a la DB equivocada.
# En instalaciones por paquete (pipx/wheel) no hay .env junto al código:
# se usa el config de nivel usuario en ~/.config/nous/.env.
_ENV_REPO = Path(__file__).resolve().parents[1] / ".env"
_ENV_USUARIO = Path.home() / ".config" / "nous" / ".env"
_ENV_FILE = _ENV_REPO if _ENV_REPO.is_file() else _ENV_USUARIO


class Settings(BaseSettings):
    """Configuración global de la aplicación.

    Local-first por defecto: embeddings locales y transporte stdio.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_prefix="NOUS_", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://nous:nous@localhost:5432/nous"
    commit_sincrono: bool = False
    embedding_provider: Literal["local", "external"] = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2"
    embeddings_backend: Literal["auto", "onnx", "torch"] = "auto"
    hf_offline: bool = True
    precalentar_modelos: bool = True
    reranker_habilitado: bool = True
    reranker_modelo: str = "cross-encoder/ms-marco-MiniLM-L12-v2"
    cache_embeddings_max: int = 256
    dedup_umbral: float = 0.95
    dedup_ventana_segundos: int = 3600
    relacion_umbral: float = 0.55
    relacion_max: int = 3
    priming_segundos: int = 300
    recencia_vida_media_dias: float = 30.0
    mcp_transport: Literal["stdio", "http"] = "stdio"
    api_token: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8720
    # Orígenes CORS del dashboard (separados por coma). El dashboard es
    # un origen distinto al API; self-hosters lo sirven en otro puerto.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    dashboard_usuario: str | None = None
    dashboard_password: str | None = None
    clave_cifrado: str | None = None
    log_level: str = "INFO"
    # Feature gate (grill-me Q11): sin licencia, self-hosted queda
    # limitado a 1 usuario y 3 workspaces — el código multi-tenant
    # completo está en el repo; la llave lo desbloquea (honor system,
    # AGPL). Ajustables para despliegues con convenio propio.
    license_key: str | None = None
    # PEM para verificar la firma RS256 del JWT de licencia (opcional;
    # sin clave la licencia opera en sistema de honor).
    license_public_key: str | None = None
    # Endpoint del servicio de activación de Quintara — si está
    # definido, `nous activate-license` canjea keys opacas por JWT.
    license_activation_url: str | None = None
    max_usuarios_libres: int = 1
    max_workspaces_libres: int = 3
    # ADR-002 — proveedores de decisión System One. "none" = sin
    # provider → heurísticos puros. "auto" intenta Laya local →
    # Ollama → nada. La API key de Jev (externo) exige además el
    # flag de privacidad — nunca sale dato sin doble consentimiento.
    decision_provider: Literal["none", "auto", "laya", "ollama", "jev"] = "auto"
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "tev1:0.8b"
    jev_api_key: str | None = None
    jev_endpoint: str = "https://api.typesafe.ai"
    privacy_permitir_externos: bool = False

    @field_validator("api_token")
    @classmethod
    def _validar_prefijo_token(cls, valor: str | None) -> str | None:
        """Exige el prefijo ``ns_`` en el token configurado."""
        if valor is not None and not valor.startswith(PREFIXO_TOKEN):
            raise ValueError(
                f"NOUS_API_TOKEN debe iniciar con el prefijo '{PREFIXO_TOKEN}'"
            )
        return valor


@lru_cache
def get_settings() -> Settings:
    """Retorna la configuración cacheada de la aplicación.

    Returns:
        Instancia única de Settings.
    """
    return Settings()
