"""Endpoints REST de Nous."""

import hmac
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.auth import verificar_token
from src.api.rate_limit import LimitadorIntentos
from src.api.schemas import (
    BenchmarkResponse,
    CerrarLoopRequest,
    CerrarObjetivoRequest,
    EventoRequest,
    EventoResponse,
    FuentePatch,
    FuenteRequest,
    FuenteResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    ObjetivoPatch,
    ObjetivoRequest,
    PredecirRequest,
    ProveedorIARequest,
    RamificarRequest,
    RelacionResponse,
    TagResponse,
)
from src.config import get_settings
from src.core import ia
from src.mcp import tools as tools_impl
from src.storage.database import get_session, obtener_engine
from src.storage.repositories import (
    BenchmarkRepository,
    ProyeccionRepository,
)

router = APIRouter()

_limitador_login = LimitadorIntentos(max_intentos=5, ventana_segundos=60)

_PATRON_CLAVE_PREF = re.compile(r"^[a-z0-9_.\-]{1,64}$")


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
        raise HTTPException(status_code=503, detail="Servidor sin token configurado")
    # Ambas comparaciones se evalúan siempre: un cortocircuito `and`
    # revelaría por timing si el usuario existe aunque el password falle.
    usuario_ok = hmac.compare_digest(credenciales.usuario, settings.dashboard_usuario)
    password_ok = hmac.compare_digest(credenciales.password, settings.dashboard_password)
    if not (usuario_ok and password_ok):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    return LoginResponse(token=settings.api_token)


@router.get("/eventos", dependencies=[Depends(verificar_token)])
async def listar_eventos(
    desde_unix: int = 0,
    hasta_unix: int = 4_102_444_800,
    limite: int = 500,
    rama: str = "main",
    repartir: bool = False,
) -> list[EventoResponse]:
    """Lista eventos de la línea de universo en un rango temporal.

    Args:
        desde_unix: Inicio del rango, Unix epoch en segundos.
        hasta_unix: Fin del rango, Unix epoch en segundos.
        limite: Máximo de eventos (1-4000).
        rama: Línea de universo a listar ("main" por defecto,
            "*" para todas las ramas).
        repartir: Si es true, el límite se reparte por fuente —
            garantiza que todas las procedencias aparezcan aunque
            una domine el volumen de eventos.

    Returns:
        Eventos en orden cronológico.
    """
    return [
        EventoResponse(**e)
        for e in await tools_impl._linea(
            desde_unix,
            hasta_unix,
            limite,
            None if rama == "*" else rama,
            repartir,
        )
    ]


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
        peticion.relaciona_ids,
        peticion.tags,
        peticion.rama,
        peticion.tipo,
        peticion.ttl,
        peticion.reemplaza_id,
        peticion.con_quien,
    )


@router.get("/loops", dependencies=[Depends(verificar_token)])
async def listar_loops(rama: str = "main") -> list[dict[str, Any]]:
    """Lista los compromisos abiertos — quién espera respuesta.

    Args:
        rama: Línea de universo a consultar ("main" por defecto).

    Returns:
        Compromisos abiertos con contraparte y días de espera.
    """
    return await tools_impl._open_loops(rama)


@router.post("/loops/{compromiso_id}/cerrar", dependencies=[Depends(verificar_token)])
async def cerrar_loop(
    compromiso_id: str, peticion: CerrarLoopRequest
) -> dict[str, Any]:
    """Cierra un compromiso registrando su resolución.

    El compromiso permanece en la línea enlazado al evento de cierre
    (relación "cierra") — transición auditada, nunca un borrado.
    """
    return await tools_impl._cerrar_loop(
        compromiso_id, peticion.resolucion, peticion.fuente
    )


@router.get("/objetivos", dependencies=[Depends(verificar_token)])
async def listar_objetivos(
    estado: str | None = "activo", rama: str = "main"
) -> list[dict[str, Any]]:
    """Lista los objetivos del usuario — metas que anclan el futuro.

    Args:
        estado: Filtro de ciclo de vida ("activo" default, "cumplido",
            "abandonado", "*" para todos).
        rama: Línea de universo a consultar.

    Returns:
        Objetivos ordenados por fecha límite, el más próximo primero.
    """
    return await tools_impl._objetivos(
        None if estado == "*" else estado, rama
    )


@router.post("/objetivos", dependencies=[Depends(verificar_token)])
async def crear_objetivo(peticion: ObjetivoRequest) -> dict[str, Any]:
    """Fija una meta: las proyecciones fantasma convergen hacia ella."""
    return await tools_impl._crear_objetivo(
        peticion.titulo,
        peticion.fecha_objetivo,
        peticion.dimension,
        peticion.descripcion,
        peticion.rama,
    )


@router.patch("/objetivos/{objetivo_id}", dependencies=[Depends(verificar_token)])
async def actualizar_objetivo(
    objetivo_id: str, peticion: ObjetivoPatch
) -> dict[str, Any]:
    """Aplica cambios parciales a un objetivo existente."""
    cambios = peticion.model_dump(exclude_none=True)
    return await tools_impl._actualizar_objetivo(objetivo_id, cambios)


@router.post("/objetivos/{objetivo_id}/cerrar", dependencies=[Depends(verificar_token)])
async def cerrar_objetivo(
    objetivo_id: str, peticion: CerrarObjetivoRequest
) -> dict[str, Any]:
    """Cierra un objetivo (cumplido/abandonado) con auditoría.

    Si hay resolución, se registra como evento conductual enlazado —
    el cierre es transición persistida, nunca un borrado.
    """
    return await tools_impl._cumplir_objetivo(
        objetivo_id,
        peticion.estado,
        peticion.resolucion,
        peticion.fuente,
    )


@router.get("/contexto-inicial", dependencies=[Depends(verificar_token)])
async def contexto_inicial(top_n: int = 8) -> dict[str, Any]:
    """Pack de arranque de sesión: núcleo + loops + hechos calientes.

    Una sola llamada devuelve la memoria relevante para abrir una
    conversación — cero LLM, todo consultas locales.
    """
    return await tools_impl._contexto_inicial(top_n)


@router.get("/tags", dependencies=[Depends(verificar_token)])
async def listar_tags(limite: int = 200) -> list[TagResponse]:
    """Lista los tags de la memoria con su frecuencia.

    Args:
        limite: Máximo de tags (1-500).

    Returns:
        Tags ordenados por número de eventos descendente.
    """
    return [TagResponse(**t) for t in await tools_impl._tags(limite)]


@router.get("/fuentes", dependencies=[Depends(verificar_token)])
async def listar_fuentes() -> list[FuenteResponse]:
    """Lista las fuentes de datos registradas con su conteo real."""
    return [FuenteResponse(**f) for f in await tools_impl._fuentes()]


@router.post("/fuentes", dependencies=[Depends(verificar_token)])
async def crear_fuente(peticion: FuenteRequest) -> dict[str, Any]:
    """Registra una fuente nueva (idempotente por nombre)."""
    return await tools_impl._crear_fuente(
        peticion.nombre,
        peticion.tipo,
        peticion.namespace,
        peticion.rama_origen,
        peticion.metadata,
    )


@router.patch("/fuentes/{nombre}", dependencies=[Depends(verificar_token)])
async def estado_fuente(nombre: str, peticion: FuentePatch) -> dict[str, Any]:
    """Activa o archiva una fuente (los eventos no se tocan)."""
    ok = await tools_impl._estado_fuente(nombre, peticion.estado)
    if not ok:
        raise HTTPException(status_code=404, detail="Fuente no encontrada")
    return {"nombre": nombre, "estado": peticion.estado}


@router.delete("/fuentes/{nombre}", dependencies=[Depends(verificar_token)])
async def eliminar_fuente(nombre: str) -> dict[str, Any]:
    """Elimina el registro de la fuente; sus eventos permanecen."""
    ok = await tools_impl._eliminar_fuente(nombre)
    if not ok:
        raise HTTPException(status_code=404, detail="Fuente no encontrada")
    return {"nombre": nombre, "eliminada": True}


@router.post("/fuentes/{nombre}/ramificar", dependencies=[Depends(verificar_token)])
async def ramificar_fuente(
    nombre: str, peticion: RamificarRequest
) -> dict[str, Any]:
    """Crea una línea de universo paralela derivada de la fuente.

    La rama se materializa como sub-fuente ``{nombre}:{rama}``: los
    eventos escritos ahí pertenecen a la línea ``rama``, que comparte
    todo el pasado con la principal.
    """
    resultado = await tools_impl._ramificar_fuente(
        nombre, peticion.rama, peticion.namespace
    )
    if resultado is None:
        raise HTTPException(status_code=404, detail="Fuente no encontrada")
    return resultado


@router.get("/relaciones", dependencies=[Depends(verificar_token)])
async def listar_relaciones(limite: int = 2000) -> list[RelacionResponse]:
    """Lista los vínculos entre eventos (explícitos y semánticos).

    Args:
        limite: Máximo de vínculos (1-5000).

    Returns:
        Relaciones más recientes primero: origen_id, destino_id,
        tipo (explicita|semantica) y peso.
    """
    return [
        RelacionResponse(**r)
        for r in await tools_impl._relaciones(limite)
    ]


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
async def generar_proyeccion(horizonte_dias: int = 7, dias_historial: int = 30) -> dict[str, Any]:
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


def _validar_clave(clave: str) -> str:
    """Valida el formato de la clave de preferencia (path segment seguro).

    Args:
        clave: Clave recibida en el path.

    Returns:
        La clave validada.

    Raises:
        HTTPException 422: formato inválido.
    """
    if not _PATRON_CLAVE_PREF.fullmatch(clave):
        raise HTTPException(
            status_code=422,
            detail="clave inválida: solo [a-z0-9_.-], máx 64 caracteres",
        )
    return clave


@router.get("/preferencias/{clave}", dependencies=[Depends(verificar_token)])
async def obtener_preferencia(clave: str) -> dict[str, Any]:
    """Lee un documento de preferencias persistido en PostgreSQL.

    Args:
        clave: Identificador de la preferencia (p. ej. ``cerebro``).

    Returns:
        Documento JSON almacenado, o ``{}`` si la clave no existe.
    """
    return await tools_impl._preferencia_leer(_validar_clave(clave))


@router.put("/preferencias/{clave}", dependencies=[Depends(verificar_token)])
async def guardar_preferencia(clave: str, valor: dict[str, Any]) -> dict[str, Any]:
    """Persiste (upsert) un documento de preferencias en PostgreSQL.

    Args:
        clave: Identificador de la preferencia.
        valor: Documento JSON a almacenar (máx ~1MB por la columna JSONB).

    Returns:
        Confirmación con la clave guardada.
    """
    await tools_impl._preferencia_guardar(_validar_clave(clave), valor)
    return {"clave": clave, "guardado": True}


# ── Proveedores de IA y motor predictivo ─────────────────────────
# Local-first: sin proveedor habilitado Nous funciona completo. Las
# API keys se guardan cifradas (pgcrypto) y la API solo expone la
# máscara; ``incluir_contenido`` autoriza enviar fragmentos de
# memorias — por defecto solo viajan agregados estadísticos.


@router.get("/ia/proveedores", dependencies=[Depends(verificar_token)])
async def listar_proveedores_ia() -> list[dict[str, Any]]:
    """Lista los proveedores de IA configurados (keys enmascaradas)."""
    async with get_session(obtener_engine()) as session:
        return await ia.listar_proveedores(session)


@router.put("/ia/proveedores/{pid}", dependencies=[Depends(verificar_token)])
async def guardar_proveedor_ia(
    pid: str, peticion: ProveedorIARequest
) -> dict[str, Any]:
    """Crea o actualiza un proveedor de IA (key cifrada en PostgreSQL).

    Si ``api_key`` no viene en el cuerpo, se conserva la existente.
    """
    async with get_session(obtener_engine()) as session:
        try:
            publico = await ia.guardar_proveedor(
                session, pid, peticion.model_dump()
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        await session.commit()
    return publico


@router.delete("/ia/proveedores/{pid}", dependencies=[Depends(verificar_token)])
async def eliminar_proveedor_ia(pid: str) -> dict[str, Any]:
    """Elimina un proveedor de IA y su key cifrada."""
    async with get_session(obtener_engine()) as session:
        ok = await ia.eliminar_proveedor(session, pid)
        if not ok:
            raise HTTPException(status_code=404, detail="Proveedor no encontrado")
        await session.commit()
    return {"id": pid, "eliminado": True}


@router.post("/ia/proveedores/{pid}/probar", dependencies=[Depends(verificar_token)])
async def probar_proveedor_ia(pid: str) -> dict[str, Any]:
    """Prueba conectividad con el endpoint (GET /models)."""
    async with get_session(obtener_engine()) as session:
        return await ia.probar_proveedor(session, pid)


@router.post("/ia/predecir", dependencies=[Depends(verificar_token)])
async def predecir_ia(peticion: PredecirRequest) -> dict[str, Any]:
    """Genera escenarios de futuro con el proveedor de IA habilitado.

    El LLM recibe el resumen estadístico de la línea hasta ``origen``
    (más fragmentos de memorias solo si el proveedor tiene
    ``incluir_contenido``) y devuelve escenarios con probabilidad y
    narrativa. Con ``candidatos`` se re-ponderan los futuros
    heurísticos del río. Sin proveedor, ``ok=false`` y el consumidor
    cae a la heurística local.
    """
    origen = peticion.origen_unix or int(time.time())
    eventos = [
        {
            "ts": e["timestamp_unix"],
            "dimension": e["dimension"],
            "amplitud": e["amplitud"],
            "contenido": e["contenido"],
        }
        for e in await tools_impl._linea(0, origen, 800, None, True)
    ]
    metas = [
        {"titulo": o["titulo"], "fecha_objetivo": o["fecha_objetivo"]}
        for o in await tools_impl._objetivos("activo", "main")
    ]
    loops = len(await tools_impl._open_loops("main"))
    async with get_session(obtener_engine()) as session:
        return await ia.predecir_escenarios(
            session,
            origen=origen,
            horizonte_dias=peticion.horizonte_dias,
            eventos=eventos,
            metas=metas,
            loops_abiertos=loops,
            candidatos=(
                [c.model_dump() for c in peticion.candidatos]
                if peticion.candidatos
                else None
            ),
            proveedor_id=peticion.proveedor_id,
        )


@router.get("/actividad", dependencies=[Depends(verificar_token)])
async def actividad(limite: int = 50) -> list[dict[str, Any]]:
    """Actividad reciente del cerebro para la visualización en vivo.

    Eventos ``store`` (huella nueva) y ``recall`` (evocación con hits),
    ordenados del más reciente al más antiguo.

    Args:
        limite: Máximo de eventos (1-100).

    Returns:
        Lista de eventos {kind, q, ids, ts}.
    """
    return tools_impl.actividad_reciente(limite)


@router.get("/benchmarks", dependencies=[Depends(verificar_token)])
async def listar_benchmarks(nombre: str | None = None, limite: int = 50) -> list[BenchmarkResponse]:
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
