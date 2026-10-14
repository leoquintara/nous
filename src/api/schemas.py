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
    relaciona_ids: list[str] | None = Field(default=None, max_length=20)
    tags: list[str] | None = Field(default=None, max_length=12)
    rama: str = Field(default="main", min_length=1, max_length=64)
    tipo: Literal[
        "evento", "hecho", "creencia", "compromiso", "preferencia"
    ] = "evento"
    ttl: str | int | None = Field(default=None)
    reemplaza_id: str | None = Field(default=None)
    con_quien: str | None = Field(default=None, max_length=128)


class CerrarLoopRequest(BaseModel):
    """Cuerpo para cerrar un compromiso abierto."""

    resolucion: str = Field(min_length=1, max_length=10_000)
    fuente: str = Field(default="dashboard", min_length=1, max_length=50)


class RelacionResponse(BaseModel):
    """Vínculo entre dos eventos de la línea de universo."""

    origen_id: str
    destino_id: str
    tipo: str
    peso: float


class EventoResponse(BaseModel):
    """Evento persistido de la línea de universo."""

    id: str
    timestamp_unix: int
    dimension: str
    fuente: str | None = None
    contenido: str
    amplitud: float
    frecuencia: float | None = None
    refuerzos: int = 0
    tags: list[str] = []
    rama: str = "main"
    tipo: str = "evento"
    expira_en: int | None = None
    reemplazado_por: str | None = None
    con_quien: str | None = None


class TagResponse(BaseModel):
    """Tag agregado con su frecuencia de uso."""

    tag: str
    eventos: int


class FuenteRequest(BaseModel):
    """Cuerpo para registrar una fuente de datos."""

    nombre: str = Field(min_length=1, max_length=64)
    tipo: Literal[
        "manual", "obsidian", "gbrain", "memlord", "agente", "import"
    ] = "manual"
    namespace: str = Field(default="", max_length=128)
    rama_origen: str = Field(default="main", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FuentePatch(BaseModel):
    """Cambio de estado de una fuente (activar/archivar)."""

    estado: Literal["activa", "archivada"]


class RamificarRequest(BaseModel):
    """Crea una rama de línea de universo a partir de una fuente."""

    rama: str = Field(min_length=1, max_length=64)
    namespace: str = Field(default="", max_length=128)


class FuenteResponse(BaseModel):
    """Fuente registrada con su conteo real de eventos."""

    nombre: str
    tipo: str
    estado: str
    namespace: str
    rama_origen: str
    metadata: dict[str, Any]
    eventos: int
