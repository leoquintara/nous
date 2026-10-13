"""Schemas Pydantic de la API REST."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


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
