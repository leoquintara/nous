"""Endpoints REST de Nous."""

from fastapi import APIRouter

from src.api.schemas import BenchmarkResponse, HealthResponse
from src.storage.database import get_session, obtener_engine
from src.storage.repositories import BenchmarkRepository

router = APIRouter()


@router.get("/health")
async def health() -> HealthResponse:
    """Verificación de vida de la API.

    Returns:
        HealthResponse con status 'ok'.
    """
    return HealthResponse(status="ok")


@router.get("/api/benchmarks")
async def listar_benchmarks(
    nombre: str | None = None, limite: int = 50
) -> list[BenchmarkResponse]:
    """Lista corridas de benchmark persistidas, más recientes primero.

    Args:
        nombre: Filtra por nombre de benchmark (None = todas).
        limite: Máximo de corridas a retornar.

    Returns:
        Lista de corridas para el dashboard de rendimiento.
    """
    async with get_session(obtener_engine()) as session:
        corridas = await BenchmarkRepository(session).listar(nombre, limite)
    return [
        BenchmarkResponse(
            id=str(c.id),
            nombre=c.nombre,
            escenario=c.escenario,
            parametros=c.parametros,
            metricas=c.metricas,
            git_commit=c.git_commit,
            creado_en=c.creado_en,
        )
        for c in corridas
    ]
