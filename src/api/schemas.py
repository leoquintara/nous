"""Schemas Pydantic de la API REST."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Respuesta del endpoint /health."""

    status: str
