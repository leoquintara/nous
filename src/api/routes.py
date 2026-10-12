"""Endpoints REST de Nous."""

from fastapi import APIRouter

from src.api.schemas import HealthResponse

router = APIRouter()


@router.get("/health")
async def health() -> HealthResponse:
    """Verificación de vida de la API.

    Returns:
        HealthResponse con status 'ok'.
    """
    return HealthResponse(status="ok")
