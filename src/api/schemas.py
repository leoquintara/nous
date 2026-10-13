"""Schemas Pydantic de la API REST."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Respuesta del endpoint /health."""

    status: str


class BenchmarkResponse(BaseModel):
    """Una corrida de benchmark persistida."""

    id: str
    nombre: str
    escenario: str | None
    parametros: dict[str, Any]
    metricas: dict[str, Any]
    git_commit: str | None
    creado_en: datetime


class LoginRequest(BaseModel):
    """Credenciales de acceso alternativo al dashboard."""

    usuario: str
    password: str


class LoginResponse(BaseModel):
    """Respuesta de login: el token de servicio para usar como Bearer."""

    token: str


class EventoRequest(BaseModel):
    """Cuerpo para registrar un evento desde la API REST."""

    contenido: str = Field(min_length=1, max_length=100_000)
    dimension: Literal["cognitiva", "somatica", "conductual"] | None = None
    fuente: str = Field(default="dashboard", min_length=1, max_length=50)
    amplitud: float = Field(default=0.5, ge=0.0, le=1.0)
    frecuencia: float | None = Field(default=None, gt=0.0)
    timestamp_unix: int | None = Field(default=None, ge=0)


class EventoResponse(BaseModel):
    """Evento persistido de la línea de universo."""

    id: str
    timestamp_unix: int
    dimension: str
    fuente: str | None = None
    contenido: str
    amplitud: float
    frecuencia: float | None = None
