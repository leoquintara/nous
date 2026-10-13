"""Endpoints REST de Nous."""

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.auth import verificar_token
from src.api.rate_limit import LimitadorIntentos
from src.api.schemas import (
    BenchmarkResponse,
    EventoRequest,
    EventoResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
)
from src.config import get_settings
from src.mcp import tools as tools_impl
from src.storage.database import get_session, obtener_engine
from src.storage.repositories import BenchmarkRepository, ProyeccionRepository

router = APIRouter()

_limitador_login = LimitadorIntentos(max_intentos=5, ventana_segundos=60)


@router.get("/health")  # público vía raíz en main.py
async def health() -> HealthResponse:
    """Verificación de vida de la API.

    Returns:
        HealthResponse con status 'ok'.
    """
    return HealthResponse(status="ok")


@router.post("/login")
async def login(credenciales: LoginRequest, request: Request) -> LoginResponse:
    """Autentica al dashboard por usuario/contraseña y entrega el token.

    Alternativa a pegar el token ``ns_*`` a mano: si el operador configuró
    ``NOUS_DASHBOARD_USUARIO`` y ``NOUS_DASHBOARD_PASSWORD``, un login
    correcto devuelve el token de servicio para usar como Bearer.
    Limitado a 5 intentos por minuto por IP contra fuerza bruta.

    Args:
        credenciales: Usuario y contraseña del dashboard.
        request: Petición entrante (IP para el rate limit).

    Returns:
        LoginResponse con el token de servicio.

    Raises:
        HTTPException 401: credenciales inválidas.
        HTTPException 429: cupo de intentos excedido.
        HTTPException 503: login por usuario/contraseña no configurado.
    """
    ip = request.client.host if request.client else "desconocida"
    if not _limitador_login.permite(ip):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos; espera un minuto",
        )
    settings = get_settings()
    if not settings.dashboard_usuario or not settings.dashboard_password:
        raise HTTPException(
            status_code=503,
            detail="Login por usuario/contraseña no configurado",
        )
    if not settings.api_token:
        raise HTTPException(
            status_code=503, detail="Servidor sin token configurado"
        )
    # Ambas comparaciones se evalúan siempre: un cortocircuito `and`
    # revelaría por timing si el usuario existe aunque el password falle.
    usuario_ok = hmac.compare_digest(
        credenciales.usuario, settings.dashboard_usuario
    )
    password_ok = hmac.compare_digest(
        credenciales.password, settings.dashboard_password
    )
    if not (usuario_ok and password_ok):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    return LoginResponse(token=settings.api_token)


@router.get("/eventos", dependencies=[Depends(verificar_token)])
async def listar_eventos(
    desde_unix: int = 0,
    hasta_unix: int = 4_102_444_800,
    limite: int = 500,
) -> list[EventoResponse]:
    """Lista eventos de la línea de universo en un rango temporal.

    Args:
        desde_unix: Inicio del rango, Unix epoch en segundos.
        hasta_unix: Fin del rango, Unix epoch en segundos.
        limite: Máximo de eventos (1-500).

    Returns:
        Eventos en orden cronológico.
    """
    return [EventoResponse(**e) for e in await tools_impl._linea(
        desde_unix, hasta_unix, limite
    )]


@router.post("/eventos", dependencies=[Depends(verificar_token)])
async def crear_evento(peticion: EventoRequest) -> dict[str, Any]:
    """Registra un evento nuevo (equivalente REST de recordar_evento).

    Args:
        peticion: Contenido, dimensión opcional y metadatos del evento.

    Returns:
        Dict con id, timestamp y dimensión usada.
    """
    return await tools_impl._recordar(
        peticion.contenido,
        peticion.dimension,
        peticion.fuente,
        peticion.amplitud,
        peticion.frecuencia,
        peticion.timestamp_unix,
    )


@router.get("/buscar", dependencies=[Depends(verificar_token)])
async def buscar(q: str, top_n: int = 10) -> list[dict[str, Any]]:
    """Búsqueda híbrida 4D con contexto temporal (consultar_memoria).

    Args:
        q: Consulta en lenguaje natural.
        top_n: Máximo de resultados (1-50).

    Returns:
        Resultados con evento, scores y contexto temporal.
    """
    return await tools_impl._consultar(q, top_n)


@router.post("/proyeccion", dependencies=[Depends(verificar_token)])
async def generar_proyeccion(
    horizonte_dias: int = 7, dias_historial: int = 30
) -> dict[str, Any]:
    """Genera y persiste una proyección (proyectar_trayectoria).

    Args:
        horizonte_dias: Días hacia adelante (1-90).
        dias_historial: Días de historial a analizar (1-365).

    Returns:
        Proyección con distribución por dimensión y trayectorias.
    """
    return await tools_impl._proyectar(horizonte_dias, dias_historial)


@router.get("/proyeccion/actual", dependencies=[Depends(verificar_token)])
async def proyeccion_actual() -> dict[str, Any]:
    """Devuelve la última proyección persistida sin recalcular.

    Returns:
        Dict con la proyección vigente o {"estado": "sin_proyecciones"}.
    """
    async with get_session(obtener_engine()) as session:
        ultima = await ProyeccionRepository(session).ultima()
    if ultima is None:
        return {"estado": "sin_proyecciones"}
    return {
        "id": str(ultima.id),
        "horizonte_dias": ultima.horizonte_dias,
        "dimension_probable": ultima.rama_decision,
        "probabilidad": ultima.probabilidad,
        "distribucion": ultima.vector_proyectado,
        "eventos_analizados": len(ultima.basada_en_eventos),
        "creado_en": ultima.creado_en.isoformat(),
    }


@router.get("/benchmarks", dependencies=[Depends(verificar_token)])
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
