"""Endpoints REST de Nous."""

import asyncio
import hmac
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.auth import requerir_escritura, verificar_token
from src.api.rate_limit import LimitadorIntentos
from src.api.schemas import (
    BenchmarkResponse,
    CanjearRequest,
    CanjearResponse,
    CerrarLoopRequest,
    CerrarObjetivoRequest,
    DecisionRequest,
    EventoRequest,
    EventoResponse,
    FuentePatch,
    FuenteRequest,
    FuenteResponse,
    HealthResponse,
    HistorialImportacion,
    IdentidadResponse,
    ImportarRequest,
    ImportarResponse,
    IntencionRequest,
    InvitacionRequest,
    InvitacionResponse,
    LicenciaResumen,
    LlamadaOrigenStats,
    LlamadaResponse,
    LlamadaRutaStats,
    LlamadasStatsResponse,
    LlaveRequest,
    LlaveResponse,
    LoginRequest,
    LoginResponse,
    MiembroPatch,
    MiembroResumen,
    ObjetivoPatch,
    ObjetivoRequest,
    PingsStats,
    PredecirRequest,
    ProveedorIARequest,
    RamificarRequest,
    RelacionResponse,
    TagResponse,
    UsuarioResumen,
    WorkspaceRequest,
    WorkspaceResumen,
)
from src.cli.transferencia import importar as importar_memoria
from src.config import get_settings
from src.core import ia
from src.core import identidad as idn
from src.core.identidad import contexto_actual
from src.mcp import tools as tools_impl
from src.storage.database import get_session, obtener_engine
from src.storage.repositories import (
    BenchmarkRepository,
    LlamadaRepository,
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
    workspace: str | None = None,
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
            workspace,
        )
    ]


@router.post("/eventos", dependencies=[Depends(requerir_escritura)])
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
        peticion.scope,
        peticion.workspace,
    )


@router.get("/loops", dependencies=[Depends(verificar_token)])
async def listar_loops(
    rama: str = "main", workspace: str | None = None
) -> list[dict[str, Any]]:
    """Lista los compromisos abiertos — quién espera respuesta.

    Args:
        rama: Línea de universo a consultar ("main" por defecto).
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Compromisos abiertos con contraparte y días de espera.
    """
    return await tools_impl._open_loops(rama, workspace)


@router.post("/loops/{compromiso_id}/cerrar", dependencies=[Depends(requerir_escritura)])
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
    estado: str | None = "activo",
    rama: str = "main",
    workspace: str | None = None,
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
        None if estado == "*" else estado, rama, workspace
    )


@router.post("/objetivos", dependencies=[Depends(requerir_escritura)])
async def crear_objetivo(peticion: ObjetivoRequest) -> dict[str, Any]:
    """Fija una meta: las proyecciones fantasma convergen hacia ella."""
    return await tools_impl._crear_objetivo(
        peticion.titulo,
        peticion.fecha_objetivo,
        peticion.dimension,
        peticion.descripcion,
        peticion.rama,
        peticion.scope,
        peticion.workspace,
    )


@router.patch("/objetivos/{objetivo_id}", dependencies=[Depends(requerir_escritura)])
async def actualizar_objetivo(
    objetivo_id: str, peticion: ObjetivoPatch
) -> dict[str, Any]:
    """Aplica cambios parciales a un objetivo existente."""
    cambios = peticion.model_dump(exclude_none=True)
    return await tools_impl._actualizar_objetivo(objetivo_id, cambios)


@router.post("/objetivos/{objetivo_id}/cerrar", dependencies=[Depends(requerir_escritura)])
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
async def contexto_inicial(
    top_n: int = 8, workspace: str | None = None
) -> dict[str, Any]:
    """Pack de arranque de sesión: núcleo + loops + hechos calientes.

    Una sola llamada devuelve la memoria relevante para abrir una
    conversación — cero LLM, todo consultas locales.
    """
    return await tools_impl._contexto_inicial(top_n, workspace)


@router.get("/tags", dependencies=[Depends(verificar_token)])
async def listar_tags(
    limite: int = 200, workspace: str | None = None
) -> list[TagResponse]:
    """Lista los tags de la memoria con su frecuencia.

    Args:
        limite: Máximo de tags (1-500).
        workspace: Slug o UUID del workspace a consultar.

    Returns:
        Tags ordenados por número de eventos descendente.
    """
    return [TagResponse(**t) for t in await tools_impl._tags(limite, workspace)]


@router.get("/fuentes", dependencies=[Depends(verificar_token)])
async def listar_fuentes(workspace: str | None = None) -> list[FuenteResponse]:
    """Lista las fuentes de datos registradas con su conteo real."""
    return [
        FuenteResponse(**f) for f in await tools_impl._fuentes(workspace)
    ]


@router.post("/fuentes", dependencies=[Depends(requerir_escritura)])
async def crear_fuente(peticion: FuenteRequest) -> dict[str, Any]:
    """Registra una fuente nueva (idempotente por nombre)."""
    return await tools_impl._crear_fuente(
        peticion.nombre,
        peticion.tipo,
        peticion.namespace,
        peticion.rama_origen,
        peticion.metadata,
    )


@router.patch("/fuentes/{nombre}", dependencies=[Depends(requerir_escritura)])
async def estado_fuente(nombre: str, peticion: FuentePatch) -> dict[str, Any]:
    """Activa o archiva una fuente (los eventos no se tocan)."""
    ok = await tools_impl._estado_fuente(nombre, peticion.estado)
    if not ok:
        raise HTTPException(status_code=404, detail="Fuente no encontrada")
    return {"nombre": nombre, "estado": peticion.estado}


@router.delete("/fuentes/{nombre}", dependencies=[Depends(requerir_escritura)])
async def eliminar_fuente(nombre: str) -> dict[str, Any]:
    """Elimina el registro de la fuente; sus eventos permanecen."""
    ok = await tools_impl._eliminar_fuente(nombre)
    if not ok:
        raise HTTPException(status_code=404, detail="Fuente no encontrada")
    return {"nombre": nombre, "eliminada": True}


@router.post("/fuentes/{nombre}/ramificar", dependencies=[Depends(requerir_escritura)])
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
async def listar_relaciones(
    limite: int = 2000, workspace: str | None = None
) -> list[RelacionResponse]:
    """Lista los vínculos entre eventos (explícitos y semánticos).

    Args:
        limite: Máximo de vínculos (1-5000).

    Returns:
        Relaciones más recientes primero: origen_id, destino_id,
        tipo (explicita|semantica) y peso.
    """
    return [
        RelacionResponse(**r)
        for r in await tools_impl._relaciones(limite, workspace)
    ]


@router.delete("/eventos/{evento_id}", dependencies=[Depends(requerir_escritura)])
async def olvidar_evento(evento_id: str) -> dict[str, Any]:
    """Borra un evento de la memoria (olvidar_evento).

    Borrado duro e irreversible — el evento y sus vínculos desaparecen
    de la línea de universo. Para correcciones auditables usar
    ``reemplaza_id`` al registrar.

    Args:
        evento_id: UUID del evento a eliminar.

    Returns:
        Dict con eliminado y vinculos_borrados.
    """
    return await tools_impl._olvidar(evento_id)


@router.get("/buscar", dependencies=[Depends(verificar_token)])
async def buscar(
    q: str,
    top_n: int = 10,
    rama: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Búsqueda híbrida 4D con contexto temporal (consultar_memoria).

    Args:
        q: Consulta en lenguaje natural.
        top_n: Máximo de resultados (1-50).
        rama: Línea de universo a consultar ("main" default,
            "*" para todas).

    Returns:
        Resultados con evento, scores y contexto temporal.
    """
    return await tools_impl._consultar(
        q, top_n, None if rama == "*" else rama, workspace
    )


@router.post("/proyeccion", dependencies=[Depends(requerir_escritura)])
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
    ctx = await contexto_actual(obtener_engine())
    async with get_session(obtener_engine()) as session:
        ultima = await ProyeccionRepository(session, ctx).ultima()
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


@router.put("/preferencias/{clave}", dependencies=[Depends(requerir_escritura)])
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


@router.put("/ia/proveedores/{pid}", dependencies=[Depends(requerir_escritura)])
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


@router.delete("/ia/proveedores/{pid}", dependencies=[Depends(requerir_escritura)])
async def eliminar_proveedor_ia(pid: str) -> dict[str, Any]:
    """Elimina un proveedor de IA y su key cifrada."""
    async with get_session(obtener_engine()) as session:
        ok = await ia.eliminar_proveedor(session, pid)
        if not ok:
            raise HTTPException(status_code=404, detail="Proveedor no encontrado")
        await session.commit()
    return {"id": pid, "eliminado": True}


@router.post("/ia/proveedores/{pid}/probar", dependencies=[Depends(requerir_escritura)])
async def probar_proveedor_ia(pid: str) -> dict[str, Any]:
    """Prueba conectividad con el endpoint (GET /models)."""
    async with get_session(obtener_engine()) as session:
        return await ia.probar_proveedor(session, pid)


@router.post("/ia/predecir", dependencies=[Depends(requerir_escritura)])
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


@router.get("/llamadas", dependencies=[Depends(verificar_token)])
async def listar_llamadas(
    limite: int = 200, ruta: str | None = None
) -> list[LlamadaResponse]:
    """Lista llamadas registradas a Nous, más recientes primero.

    Args:
        limite: Máximo de filas (tope 1000).
        ruta: Filtro exacto de ruta; None = todas.

    Returns:
        Registro de trazabilidad: método, ruta, origen, latencia,
        status. Solo metadata — nunca contenido.
    """
    async with get_session(obtener_engine()) as session:
        filas = await LlamadaRepository(session).listar(limite, ruta)
    return [
        LlamadaResponse(
            id=str(f.id),
            ts=f.ts,
            metodo=f.metodo,
            ruta=f.ruta,
            origen=f.origen,
            agente=f.agente,
            latencia_ms=f.latencia_ms,
            status=f.status,
            es_ping=f.es_ping,
        )
        for f in filas
    ]


@router.get("/llamadas/stats", dependencies=[Depends(verificar_token)])
async def stats_llamadas() -> LlamadasStatsResponse:
    """Agregados del registro: por ruta, por origen y pings.

    Returns:
        Total, top rutas con latencia media, orígenes y resumen de
        pings (total + último timestamp).
    """
    async with get_session(obtener_engine()) as session:
        stats = await LlamadaRepository(session).stats()
    return LlamadasStatsResponse(
        total=stats["total"],
        por_ruta=[LlamadaRutaStats(**r) for r in stats["por_ruta"]],
        por_origen=[LlamadaOrigenStats(**r) for r in stats["por_origen"]],
        pings=PingsStats(**stats["pings"]),
    )


@router.post("/ping", dependencies=[Depends(requerir_escritura)])
async def ping() -> dict[str, Any]:
    """Registra un ping explícito — queda marcado en el log de llamadas.

    Returns:
        ``{"status": "ok", "ts": ...}`` para medir conectividad.
    """
    return {"status": "ok", "ts": int(time.time())}


@router.post("/importar", dependencies=[Depends(requerir_escritura)])
async def importar(peticion: ImportarRequest) -> ImportarResponse:
    """Migra memoria externa a Nous desde un archivo/directorio local.

    Formatos: ``obsidian`` (vault .md), ``gbrain`` (dir .md o export),
    ``memlord`` (export JSON/JSONL), ``jsonl``/``json`` nativos. La
    ruta es de la máquina donde corre este backend — nada sale del
    equipo. Los ids ya existentes se omiten (idempotente). Con
    ``vista_previa`` solo lee y anticipa contadores, sin escribir.

    Args:
        peticion: Formato, ruta local, fuente opcional y vista_previa.

    Returns:
        Contadores: importados, omitidos (duplicados), errores,
        vínculos creados. En vista previa: ``nuevas`` = filas que
        entrarían, ``importados`` = 0.
    """
    from pathlib import Path

    if peticion.url:
        return await _importar_remoto(peticion)
    if not peticion.ruta:
        raise HTTPException(
            status_code=422,
            detail="indica una ruta local o una url de instancia",
        )
    ruta_str = peticion.ruta  # narrowed por el 422 de arriba
    ruta = await asyncio.to_thread(lambda: Path(ruta_str).expanduser())
    if not await asyncio.to_thread(ruta.exists):
        raise HTTPException(
            status_code=422, detail=f"ruta inaccesible: {peticion.ruta}"
        )
    if peticion.vista_previa:
        from src.cli.transferencia import inspeccionar

        previa = await inspeccionar(
            obtener_engine(),
            ruta,
            formato=peticion.formato,
            fuente=peticion.fuente,
        )
        return ImportarResponse(
            importados=0,
            omitidos=previa["omitidos"],
            errores=previa["errores"],
            vinculos=previa["vinculos"],
            formato=peticion.formato,
            fuente=peticion.fuente or peticion.formato,
            vista_previa=True,
            nuevas=previa["nuevas"],
        )
    resultado = await importar_memoria(
        obtener_engine(),
        ruta,
        formato=peticion.formato,
        fuente=peticion.fuente,
        rama=peticion.rama,
    )
    await _registrar_importacion(peticion, resultado)
    return ImportarResponse(
        importados=resultado["importados"],
        omitidos=resultado["omitidos"],
        errores=resultado["errores"],
        vinculos=resultado["vinculos"],
        formato=peticion.formato,
        fuente=peticion.fuente or peticion.formato,
    )


_MAX_HISTORIAL_IMPORTACIONES = 15


async def _importar_remoto(peticion: ImportarRequest) -> ImportarResponse:
    """Migra memoria desde una instancia MCP remota (gbrain/memlord).

    El endpoint ``url`` debe ser streamable-HTTP (``/mcp``); la conexión
    y la descarga ocurren solo por este gesto explícito del usuario.
    """
    from src.cli.importador_remoto import ErrorRemoto, leer_fuente_remota
    from src.cli.transferencia import contar_nuevas, importar_filas

    try:
        filas, vinculos, errores = await leer_fuente_remota(
            peticion.url or "",
            peticion.token,
            peticion.formato,
            peticion.fuente or peticion.formato,
        )
    except ErrorRemoto as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if peticion.vista_previa:
        previa = await contar_nuevas(obtener_engine(), filas, vinculos, errores)
        return ImportarResponse(
            importados=0,
            omitidos=previa["omitidos"],
            errores=previa["errores"],
            vinculos=previa["vinculos"],
            formato=peticion.formato,
            fuente=peticion.fuente or peticion.formato,
            vista_previa=True,
            nuevas=previa["nuevas"],
        )
    resultado = await importar_filas(
        obtener_engine(), filas, vinculos, errores, peticion.rama
    )
    await _registrar_importacion(peticion, resultado)
    return ImportarResponse(
        importados=resultado["importados"],
        omitidos=resultado["omitidos"],
        errores=resultado["errores"],
        vinculos=resultado["vinculos"],
        formato=peticion.formato,
        fuente=peticion.fuente or peticion.formato,
    )


async def _registrar_importacion(
    peticion: ImportarRequest, resultado: dict[str, int]
) -> None:
    """Añade la migración al historial persistido (máx. 15 entradas)."""
    from src.storage.repositories import ConfiguracionRepository

    entrada = {
        "ts": int(time.time()),
        "formato": peticion.formato,
        "fuente": peticion.fuente or peticion.formato,
        "ruta": (peticion.url or peticion.ruta or "")[:300],
        "resultado": resultado,
    }
    ctx = await contexto_actual(obtener_engine())
    async with get_session(obtener_engine()) as session:
        repo = ConfiguracionRepository(session, ctx)
        valor = await repo.obtener("importaciones_historial")
        historial: list[dict[str, Any]] = (
            valor["items"] if isinstance(valor, dict) else []
        )
        historial.insert(0, entrada)
        await repo.guardar(
            "importaciones_historial",
            {"items": historial[:_MAX_HISTORIAL_IMPORTACIONES]},
        )
        await session.commit()


@router.get("/importar/historial", dependencies=[Depends(verificar_token)])
async def historial_importaciones() -> list[HistorialImportacion]:
    """Devuelve las últimas migraciones ejecutadas (máx. 15)."""
    from src.storage.repositories import ConfiguracionRepository

    ctx = await contexto_actual(obtener_engine())
    async with get_session(obtener_engine()) as session:
        valor = await ConfiguracionRepository(session, ctx).obtener(
            "importaciones_historial"
        )
    items = valor["items"] if isinstance(valor, dict) else []
    return [HistorialImportacion(**i) for i in items]


# ── Identidad multi-tenant ──────────────────────────────────────


def _ws_resumen(w: idn.WorkspaceConRol) -> WorkspaceResumen:
    """Traduce el dataclass de dominio al schema REST."""
    return WorkspaceResumen(
        id=str(w.id), slug=w.slug, nombre=w.nombre, rol=w.rol
    )


async def _ctx_owner_en(ref: str) -> tuple[idn.Contexto, idn.Contexto]:
    """Resuelve el contexto actual y exige rol owner en ``ref``.

    Returns:
        (contexto actual, contexto en el workspace destino).

    Raises:
        PermisoDenegadoError: Sin membresía o rol inferior a owner.
        ErrorValidacion: Workspace inexistente.
    """
    from src.core.exceptions import PermisoDenegadoError

    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    ctx_ws = await idn.resolver_workspace(engine, ctx, ref)
    if ctx_ws.rol != "owner":
        raise PermisoDenegadoError(
            "se requiere rol owner en el workspace"
        )
    return ctx, ctx_ws


@router.get("/identidad", dependencies=[Depends(verificar_token)])
async def identidad_actual() -> IdentidadResponse:
    """Quién es el llamante y a qué workspaces pertenece."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    workspaces = await idn.listar_workspaces_con_rol(
        engine, ctx.usuario_id
    )
    activo = next(
        (w for w in workspaces if w.id == ctx.workspace_id), None
    )
    if activo is None:
        activo = idn.WorkspaceConRol(
            id=ctx.workspace_id, slug="", nombre="", rol=ctx.rol
        )
    usuario: UsuarioResumen | None = None
    if ctx.usuario_id is not None:
        from sqlalchemy import select as _sel

        from src.storage.models import UsuarioModel

        async with get_session(engine) as session:
            u = (
                await session.execute(
                    _sel(UsuarioModel).where(
                        UsuarioModel.id == ctx.usuario_id
                    )
                )
            ).scalar_one_or_none()
        if u is not None:
            usuario = UsuarioResumen(
                id=str(u.id),
                handle=u.handle,
                nombre=u.nombre,
                email=u.email,
            )
    lic = await idn.estado_licencia(engine)
    return IdentidadResponse(
        usuario=usuario,
        workspace_activo=_ws_resumen(activo),
        workspaces=[_ws_resumen(w) for w in workspaces],
        licencia=LicenciaResumen(
            licenciado=lic.licenciado,
            usuarios=lic.usuarios,
            max_usuarios=lic.max_usuarios,
            workspaces=lic.workspaces,
            max_workspaces=lic.max_workspaces,
            tier=lic.tier,
            features=list(lic.features),
        ),
    )


@router.post(
    "/workspaces",
    dependencies=[Depends(requerir_escritura)],
    status_code=201,
)
async def crear_ws(peticion: WorkspaceRequest) -> WorkspaceResumen:
    """Crea un workspace; el llamante queda como owner."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    owner_id = ctx.usuario_id or idn.USUARIO_OWNER_ID
    ws = await idn.crear_workspace(
        engine, peticion.slug, peticion.nombre, owner_id
    )
    return WorkspaceResumen(
        id=str(ws.id), slug=ws.slug, nombre=ws.nombre, rol="owner"
    )


@router.get(
    "/workspaces/{ref}/miembros",
    dependencies=[Depends(verificar_token)],
)
async def listar_miembros(ref: str) -> list[MiembroResumen]:
    """Roster del workspace — cualquier miembro lo puede leer."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    ctx_ws = await idn.resolver_workspace(engine, ctx, ref)
    return [
        MiembroResumen(
            usuario_id=str(m.usuario_id),
            handle=m.handle,
            nombre=m.nombre,
            rol=m.rol,
        )
        for m in await idn.listar_miembros(engine, ctx_ws.workspace_id)
    ]


@router.patch(
    "/workspaces/{ref}/miembros/{uid}",
    dependencies=[Depends(requerir_escritura)],
    status_code=204,
)
async def cambiar_rol_miembro(ref: str, uid: str, patch: MiembroPatch) -> None:
    """Cambia el rol de un miembro — solo owners."""
    _ctx, ctx_ws = await _ctx_owner_en(ref)
    await idn.cambiar_rol(
        obtener_engine(), ctx_ws.workspace_id, _uuid(uid), patch.rol
    )


@router.delete(
    "/workspaces/{ref}/miembros/{uid}",
    dependencies=[Depends(requerir_escritura)],
    status_code=204,
)
async def quitar_miembro_endpoint(ref: str, uid: str) -> None:
    """Expulsa un miembro — solo owners; sus memorias permanecen."""
    _ctx, ctx_ws = await _ctx_owner_en(ref)
    await idn.quitar_miembro(
        obtener_engine(), ctx_ws.workspace_id, _uuid(uid)
    )


def _uuid(ref: str) -> uuid.UUID:
    """Parsea un UUID de path o lanza 422."""
    try:
        return uuid.UUID(ref)
    except ValueError:
        raise HTTPException(422, f"UUID inválido: {ref!r}") from None


@router.post(
    "/workspaces/{ref}/invitaciones",
    dependencies=[Depends(requerir_escritura)],
    status_code=201,
)
async def crear_invitacion_ws(
    ref: str, peticion: InvitacionRequest
) -> InvitacionResponse:
    """Emite un token de invitación — solo owners; se ve una vez."""
    ctx, ctx_ws = await _ctx_owner_en(ref)
    token = await idn.crear_invitacion(
        obtener_engine(),
        ctx_ws.workspace_id,
        peticion.rol,
        dias=peticion.dias,
        creada_por=ctx.usuario_id,
    )
    return InvitacionResponse(token=token, expira_dias=peticion.dias)


_limitador_canje = LimitadorIntentos(max_intentos=5, ventana_segundos=60)


@router.post("/invitaciones/canjear", status_code=201)
async def canjear(peticion: CanjearRequest, request: Request) -> CanjearResponse:
    """Canje público de invitación — sin Bearer: el token es la prueba.

    Rate-limited por IP como /login. Devuelve la key del usuario
    nuevo una sola vez.
    """
    ip = request.client.host if request.client else "desconocida"
    if not _limitador_canje.permite(ip):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos; espera un minuto",
        )
    usuario, key = await idn.canjear_invitacion(
        obtener_engine(),
        peticion.token,
        peticion.handle,
        peticion.nombre,
        email=peticion.email,
    )
    return CanjearResponse(handle=usuario.handle, key=key)


def _llave_resumen(k: object) -> LlaveResponse:
    """Traduce ApiKeyModel al schema (sin exponer el hash)."""
    from src.storage.models import ApiKeyModel

    assert isinstance(k, ApiKeyModel)
    return LlaveResponse(
        id=str(k.id),
        prefijo=k.prefijo,
        nombre=k.nombre or None,
        workspace_default_id=(
            str(k.workspace_default_id)
            if k.workspace_default_id is not None
            else None
        ),
        revocada=k.revocada,
        ultimo_uso=k.ultimo_uso,
        creado_en=k.creado_en,
    )


@router.get("/llaves", dependencies=[Depends(verificar_token)])
async def listar_llaves() -> list[LlaveResponse]:
    """Keys de API del llamante (nunca el hash ni el secreto)."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    return [
        _llave_resumen(k)
        for k in await idn.listar_keys(engine, ctx.usuario_id)
    ]


@router.post(
    "/llaves",
    dependencies=[Depends(requerir_escritura)],
    status_code=201,
)
async def crear_llave(peticion: LlaveRequest) -> dict[str, str]:
    """Emite una key nueva para el llamante — en claro una vez."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    if ctx.usuario_id is None:
        raise HTTPException(
            422, "sin identidad de usuario (modo desarrollo)"
        )
    ws_default = (
        _uuid(peticion.workspace_default_id)
        if peticion.workspace_default_id
        else ctx.workspace_id
    )
    if peticion.workspace_default_id is not None:
        await idn.resolver_workspace(
            engine, ctx, peticion.workspace_default_id
        )
    key = await idn.emitir_key_usuario(
        engine, ctx.usuario_id, ws_default, peticion.nombre
    )
    return {"key": key}


@router.delete(
    "/llaves/{ref}",
    dependencies=[Depends(verificar_token)],
    status_code=204,
)
async def revocar_llave(ref: str) -> None:
    """Revoca una key — solo si pertenece al llamante."""
    from sqlalchemy import select as _sel

    from src.storage.models import ApiKeyModel

    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    if ctx.usuario_id is None:
        raise HTTPException(
            422, "sin identidad de usuario (modo desarrollo)"
        )
    async with get_session(engine) as session:
        try:
            key_id = _uuid(ref)
            cond = ApiKeyModel.id == key_id
        except HTTPException:
            cond = ApiKeyModel.prefijo == ref
        fila = (
            await session.execute(
                _sel(ApiKeyModel.id).where(
                    cond, ApiKeyModel.usuario_id == ctx.usuario_id
                )
            )
        ).scalar_one_or_none()
    if fila is None:
        raise HTTPException(404, "key no encontrada")
    await idn.revocar_key_usuario(engine, ref)


# ---------------------------------------------------------------------
# ADR-003 — patrones neurocognitivos: intenciones prospectivas,
# consolidación-sueño, olvido reversible y minería de scripts.
# Paridad con las tools MCP del mismo nombre.
# ---------------------------------------------------------------------


@router.post("/intenciones", dependencies=[Depends(requerir_escritura)])
async def crear_intencion(peticion: IntencionRequest) -> dict[str, Any]:
    """Registra una intención prospectiva (memoria prospectiva).

    ``en`` = trigger temporal ("3d", epoch); ``cuando`` = trigger de
    evento (texto a vigilar). Exactamente uno de los dos.
    """
    return await tools_impl._crear_intencion(
        peticion.intencion,
        peticion.en,
        peticion.cuando,
        peticion.umbral,
        peticion.workspace,
    )


@router.get("/intenciones", dependencies=[Depends(verificar_token)])
async def listar_intenciones(
    ejecutadas: bool | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lista las intenciones prospectivas del workspace."""
    return await tools_impl._listar_intenciones(ejecutadas, workspace)


@router.post(
    "/intenciones/{intencion_id}/cancelar",
    dependencies=[Depends(requerir_escritura)],
)
async def cancelar_intencion(
    intencion_id: str, workspace: str | None = None
) -> dict[str, Any]:
    """Cancela una intención prospectiva pendiente."""
    return await tools_impl._cancelar_intencion(intencion_id, workspace)


@router.post("/consolidar", dependencies=[Depends(requerir_escritura)])
async def consolidar(workspace: str | None = None) -> dict[str, Any]:
    """Ejecuta la corrida de consolidación-sueño del workspace.

    Replay + promoción a curado + archivado reversible, con reporte
    persistido — el proceso es auditable, nunca silencioso.
    """
    return await tools_impl._consolidar(workspace)


@router.get(
    "/consolidacion/reportes", dependencies=[Depends(verificar_token)]
)
async def reportes_consolidacion(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Reportes de corridas de consolidación (el diario del sueño)."""
    ctx = await contexto_actual(obtener_engine())
    if workspace is not None:
        ctx = await idn.resolver_workspace(
            obtener_engine(), ctx, workspace
        )
    from src.storage.repositories import ReporteConsolidacionRepository

    async with get_session(obtener_engine()) as session:
        filas = await ReporteConsolidacionRepository(session, ctx).listar()
    return [
        {
            "id": str(r.id),
            "ejecutado_en": r.ejecutado_en,
            "fortalecidos": r.fortalecidos,
            "archivados": r.archivados,
            "promovidos": r.promovidos,
            "detalle": r.detalle,
        }
        for r in filas
    ]


@router.post("/scripts/minar", dependencies=[Depends(requerir_escritura)])
async def minar(workspace: str | None = None) -> dict[str, Any]:
    """Mina secuencias recurrentes de la línea de universo."""
    return await tools_impl._minar_scripts(workspace)


@router.get("/scripts", dependencies=[Depends(verificar_token)])
async def listar_scripts(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Scripts/patrones detectados por minería."""
    return await tools_impl._listar_scripts(workspace)


@router.get("/archivados", dependencies=[Depends(verificar_token)])
async def listar_archivados(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Memorias archivadas por consolidación — recuperables."""
    return await tools_impl._listar_archivados(workspace)


@router.post(
    "/eventos/{evento_id}/restaurar",
    dependencies=[Depends(requerir_escritura)],
)
async def restaurar_evento(
    evento_id: str, workspace: str | None = None
) -> dict[str, Any]:
    """Restaura una memoria archivada — deshace el olvido."""
    return await tools_impl._restaurar_evento(evento_id, workspace)


# ---------------------------------------------------------------------
# ADR-002 Nivel 2 — consejero prescriptivo (feature Pro, licencia).
# ---------------------------------------------------------------------


@router.post("/decision", dependencies=[Depends(requerir_escritura)])
async def consultar_decision(peticion: DecisionRequest) -> dict[str, Any]:
    """Evalúa un dilema contra la línea de universo 4D (feature Pro).

    Sin licencia → 402 vía ``CupoExcedidoError``. Sin provider
    System One → ``disponible=false`` con el contexto ensamblado.
    """
    return await tools_impl._consultar_decision(
        peticion.dilema, peticion.opciones, peticion.workspace
    )
