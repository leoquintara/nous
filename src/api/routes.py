"""Nous REST endpoints — English wire contract.

The internal domain stays Spanish (helpers emit Spanish dicts); every
handler normalizes its output through ``traducir_payload`` and translates
English request values back to the internal vocabulary at the boundary.
"""

import asyncio
import hmac
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.auth import (
    requerir_escritura,
    requerir_owner,
    verificar_token,
)
from src.api.rate_limit import LimitadorIntentos
from src.api.schemas import (
    ActivateLicenseRequest,
    AIProviderRequest,
    BenchmarkResponse,
    BranchRequest,
    CallOriginStats,
    CallResponse,
    CallRouteStats,
    CallsStatsResponse,
    CensorRequest,
    CloseCommitmentRequest,
    CloseGoalRequest,
    CurateRequest,
    DecisionRequest,
    DirectedForgettingRequest,
    DraftRequest,
    EventRequest,
    EventResponse,
    GoalPatch,
    GoalRequest,
    HealthResponse,
    IdentityResponse,
    ImportHistoryEntry,
    ImportRequest,
    ImportResponse,
    IntentionRequest,
    InvitationRequest,
    InvitationResponse,
    KeyRequest,
    KeyResponse,
    LicenseSummary,
    LoginRequest,
    LoginResponse,
    MemberPatch,
    MemberSummary,
    PingsStats,
    PredictionRequest,
    PredictRequest,
    RedeemRequest,
    RedeemResponse,
    RelationResponse,
    ResolvePredictionRequest,
    SessionEndRequest,
    SessionStartRequest,
    SnoozeRequest,
    SourcePatch,
    SourceRequest,
    SourceResponse,
    TagResponse,
    TrustSourceRequest,
    UserSummary,
    VerifyRequest,
    WorkspaceRequest,
    WorkspaceSummary,
)
from src.cli.transferencia import importar as importar_memoria
from src.config import get_settings
from src.core import ia
from src.core import identidad as idn
from src.core.identidad import contexto_actual
from src.mcp import tools as tools_impl
from src.mcp.contract import (
    OBJETIVO_ESTADO_ES,
    dimension_es,
    estado_objetivo_es,
    severidad_es,
    tipo_es,
    tipo_memoria_es,
    traducir_payload,
)
from src.storage.database import get_session, obtener_engine
from src.storage.repositories import (
    BenchmarkRepository,
    LlamadaRepository,
    ProyeccionRepository,
)

router = APIRouter()

_limitador_login = LimitadorIntentos(max_intentos=5, ventana_segundos=60)

_PATRON_CLAVE_PREF = re.compile(r"^[a-z0-9_.\-]{1,64}$")

# Source-kind vocabulary differs from event-kind ("agent", not "agente").
_TIPO_FUENTE_ES = {"agent": "agente"}
_ESTADO_FUENTE_ES = {"active": "activa", "archived": "archivada"}


@router.get("/health")  # público vía raíz en main.py
async def health() -> HealthResponse:
    """Liveness check of the API.

    Returns:
        HealthResponse with status 'ok'.
    """
    return HealthResponse(status="ok")


@router.post("/login")
async def login(credentials: LoginRequest, request: Request) -> LoginResponse:
    """Authenticates the dashboard by username/password and returns the token.

    Alternative to pasting the ``ns_*`` token by hand: if the operator
    configured ``NOUS_DASHBOARD_USUARIO`` and ``NOUS_DASHBOARD_PASSWORD``,
    a correct login returns the service token to use as Bearer.
    Rate-limited to 5 attempts per minute per IP against brute force.

    Args:
        credentials: Dashboard username and password.
        request: Incoming request (IP for the rate limit).

    Returns:
        LoginResponse with the service token.

    Raises:
        HTTPException 401: invalid credentials.
        HTTPException 429: attempt quota exceeded.
        HTTPException 503: username/password login not configured.
    """
    ip = request.client.host if request.client else "unknown"
    if not _limitador_login.permite(ip):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts; wait a minute",
        )
    settings = get_settings()
    if not settings.dashboard_usuario or not settings.dashboard_password:
        raise HTTPException(
            status_code=503,
            detail="Username/password login is not configured",
        )
    if not settings.api_token:
        raise HTTPException(status_code=503, detail="Server has no token configured")
    # Both comparisons always run: an `and` short-circuit would leak
    # via timing whether the username exists even when the password fails.
    usuario_ok = hmac.compare_digest(credentials.username, settings.dashboard_usuario)
    password_ok = hmac.compare_digest(credentials.password, settings.dashboard_password)
    if not (usuario_ok and password_ok):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return LoginResponse(token=settings.api_token)


@router.get("/events", dependencies=[Depends(verificar_token)])
async def list_events(
    from_unix: int = 0,
    to_unix: int = 4_102_444_800,
    limit: int = 500,
    branch: str = "main",
    split: bool = False,
    workspace: str | None = None,
) -> list[EventResponse]:
    """Lists universe-line events in a temporal range.

    Args:
        from_unix: Range start, Unix epoch seconds.
        to_unix: Range end, Unix epoch seconds.
        limit: Maximum events (1-4000).
        branch: Universe line to list ("main" by default,
            "*" for every branch).
        split: When true, the limit is split across sources —
            every origin appears even if one dominates volume.

    Returns:
        Events in chronological order.
    """
    return [
        EventResponse(**traducir_payload(e))
        for e in await tools_impl._linea(
            from_unix,
            to_unix,
            limit,
            None if branch == "*" else branch,
            split,
            workspace,
        )
    ]


@router.post("/events", dependencies=[Depends(requerir_escritura)])
async def create_event(request_body: EventRequest) -> dict[str, Any]:
    """Registers a new event (REST equivalent of ``remember``).

    Args:
        request_body: Content, optional dimension and event metadata.

    Returns:
        Dict with id, timestamp and the dimension used.
    """
    return traducir_payload(
        await tools_impl._recordar(
            request_body.content,
            dimension_es(request_body.dimension),
            request_body.source,
            request_body.amplitude,
            request_body.frequency,
            request_body.timestamp_unix,
            request_body.related_ids,
            request_body.tags,
            request_body.branch,
            tipo_es(request_body.kind) or "evento",
            request_body.ttl,
            request_body.supersedes_id,
            request_body.with_whom,
            request_body.scope,
            request_body.workspace,
            tipo_memoria=tipo_memoria_es(request_body.memory_kind),
            estado_emocional=request_body.emotional_state,
            contexto_extra=request_body.context,
            parametrica=request_body.parametric,
        )
    )


@router.get("/commitments", dependencies=[Depends(verificar_token)])
async def list_commitments(
    branch: str = "main", workspace: str | None = None
) -> list[dict[str, Any]]:
    """Lists open commitments — who is waiting for an answer.

    Args:
        branch: Universe line to query ("main" by default).
        workspace: Workspace slug or UUID to query.

    Returns:
        Open commitments with counterpart and days waiting.
    """
    return traducir_payload(await tools_impl._open_loops(branch, workspace))


@router.post(
    "/commitments/{commitment_id}/close",
    dependencies=[Depends(requerir_escritura)],
)
async def close_commitment(
    commitment_id: str, request_body: CloseCommitmentRequest
) -> dict[str, Any]:
    """Closes a commitment by registering its resolution.

    The commitment stays on the line linked to the closing event
    (relation "cierra") — audited transition, never a deletion.
    """
    return traducir_payload(
        await tools_impl._cerrar_loop(commitment_id, request_body.resolution, request_body.source)
    )


@router.get("/goals", dependencies=[Depends(verificar_token)])
async def list_goals(
    status: str | None = "active",
    branch: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists the user's goals — anchors the future converges to.

    Args:
        status: Lifecycle filter ("active" default, "fulfilled",
            "abandoned", "*" for all).
        branch: Universe line to query.

    Returns:
        Goals sorted by deadline, nearest first.
    """
    estado = estado_objetivo_es(status) if status != "*" else None
    return traducir_payload(await tools_impl._objetivos(estado, branch, workspace))


@router.post("/goals", dependencies=[Depends(requerir_escritura)])
async def create_goal(request_body: GoalRequest) -> dict[str, Any]:
    """Sets a goal: ghost projections converge towards it."""
    return traducir_payload(
        await tools_impl._crear_objetivo(
            request_body.title,
            request_body.target_date,
            dimension_es(request_body.dimension),
            request_body.description,
            request_body.branch,
            request_body.scope,
            request_body.workspace,
        )
    )


@router.patch("/goals/{goal_id}", dependencies=[Depends(requerir_escritura)])
async def update_goal(goal_id: str, request_body: GoalPatch) -> dict[str, Any]:
    """Applies partial changes to an existing goal."""
    cambios: dict[str, Any] = {}
    if request_body.title is not None:
        cambios["titulo"] = request_body.title
    if request_body.description is not None:
        cambios["descripcion"] = request_body.description
    if request_body.target_date is not None:
        cambios["fecha_objetivo"] = request_body.target_date
    if request_body.dimension is not None:
        cambios["dimension"] = dimension_es(request_body.dimension)
    if request_body.status is not None:
        cambios["estado"] = OBJETIVO_ESTADO_ES[request_body.status]
    return traducir_payload(await tools_impl._actualizar_objetivo(goal_id, cambios))


@router.post("/goals/{goal_id}/close", dependencies=[Depends(requerir_escritura)])
async def close_goal(goal_id: str, request_body: CloseGoalRequest) -> dict[str, Any]:
    """Closes a goal (fulfilled/abandoned) with audit trail.

    When a resolution is given it is registered as a linked
    behavioral event — closure is a persisted transition, never
    a deletion.
    """
    return traducir_payload(
        await tools_impl._cumplir_objetivo(
            goal_id,
            OBJETIVO_ESTADO_ES[request_body.status],
            request_body.resolution,
            request_body.source,
        )
    )


@router.get("/bootstrap", dependencies=[Depends(verificar_token)])
async def bootstrap_context(
    top_n: int = 8,
    budget: int | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Session bootstrap pack: guardrails + core + loops + hot facts;
    with ``budget`` it also includes the injectable snapshot.
    """
    return traducir_payload(await tools_impl._contexto_inicial(top_n, budget, workspace))


@router.get("/tags", dependencies=[Depends(verificar_token)])
async def list_tags(limit: int = 200, workspace: str | None = None) -> list[TagResponse]:
    """Lists memory tags with their usage frequency.

    Args:
        limit: Maximum tags (1-500).
        workspace: Workspace slug or UUID to query.

    Returns:
        Tags sorted by event count descending.
    """
    return [TagResponse(**traducir_payload(t)) for t in await tools_impl._tags(limit, workspace)]


@router.get("/sources", dependencies=[Depends(verificar_token)])
async def list_sources(workspace: str | None = None) -> list[SourceResponse]:
    """Lists registered data sources with their real event count."""
    return [SourceResponse(**traducir_payload(f)) for f in await tools_impl._fuentes(workspace)]


@router.post("/sources", dependencies=[Depends(requerir_escritura)])
async def create_source(request_body: SourceRequest) -> dict[str, Any]:
    """Registers a new source (idempotent by name)."""
    return traducir_payload(
        await tools_impl._crear_fuente(
            request_body.name,
            _TIPO_FUENTE_ES.get(request_body.kind, request_body.kind),
            request_body.namespace,
            request_body.origin_branch,
            request_body.metadata,
        )
    )


@router.patch("/sources/{name}", dependencies=[Depends(requerir_escritura)])
async def update_source(name: str, request_body: SourcePatch) -> dict[str, Any]:
    """Activates or archives a source (its events are untouched)."""
    estado = _ESTADO_FUENTE_ES[request_body.status]
    ok = await tools_impl._estado_fuente(name, estado)
    if not ok:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"name": name, "status": request_body.status}


@router.delete("/sources/{name}", dependencies=[Depends(requerir_escritura)])
async def delete_source(name: str) -> dict[str, Any]:
    """Deletes the source registry entry; its events remain."""
    ok = await tools_impl._eliminar_fuente(name)
    if not ok:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"name": name, "deleted": True}


@router.post("/sources/{name}/branch", dependencies=[Depends(requerir_escritura)])
async def branch_source(name: str, request_body: BranchRequest) -> dict[str, Any]:
    """Creates a parallel universe line derived from the source.

    The branch materializes as a sub-source ``{name}:{branch}``:
    events written there belong to the ``branch`` line, which shares
    the entire past with the main one.
    """
    resultado = await tools_impl._ramificar_fuente(
        name, request_body.branch, request_body.namespace
    )
    if resultado is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return traducir_payload(resultado)


@router.get("/relations", dependencies=[Depends(verificar_token)])
async def list_relations(limit: int = 2000, workspace: str | None = None) -> list[RelationResponse]:
    """Lists links between events (explicit and semantic).

    Args:
        limit: Maximum links (1-5000).

    Returns:
        Relations newest first: origin_id, target_id, kind
        (explicita|semantica) and weight.
    """
    return [
        RelationResponse(**traducir_payload(r))
        for r in await tools_impl._relaciones(limit, workspace)
    ]


@router.get("/entity", dependencies=[Depends(verificar_token)])
async def entity_card(
    name: str,
    max_mentions: int = 8,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Named-entity card.

    Resolves the name to its page (title IDF lookup) and composes the
    card: page, graph edges, backlinks that mention it, open
    commitments and its temporal span on the worldline.

    Args:
        name: Entity name ("oscar", "kinam").
        max_mentions: Maximum backlinks (1-24).

    Returns:
        {"found", "card", "variants", "links", "mentions",
        "open_commitments", "span"}.
    """
    return traducir_payload(await tools_impl._entidad(name, max_mentions, workspace))


@router.delete("/events/{event_id}", dependencies=[Depends(requerir_escritura)])
async def forget_event(event_id: str) -> dict[str, Any]:
    """Deletes an event from memory (``forget_event``).

    Hard, irreversible deletion — the event and its links disappear
    from the universe line. For auditable corrections use
    ``supersedes_id`` when registering instead.

    Args:
        event_id: UUID of the event to delete.

    Returns:
        Dict with deleted and links_removed.
    """
    return traducir_payload(await tools_impl._olvidar(event_id))


@router.get("/search", dependencies=[Depends(verificar_token)])
async def search(
    q: str,
    top_n: int = 10,
    branch: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid 4D search with temporal context (``recall``).

    Args:
        q: Natural-language query.
        top_n: Maximum results (1-50).
        branch: Universe line to query ("main" default,
            "*" for all).

    Returns:
        Results with event, scores and temporal context.
    """
    return traducir_payload(
        await tools_impl._consultar(q, top_n, None if branch == "*" else branch, workspace)
    )


@router.get("/gaps", dependencies=[Depends(verificar_token)])
async def gap_map(
    q: str,
    top_n: int = 10,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Gap map — what memory does NOT know about the query.

    Same search as ``/search`` but the response is the X-ray of the
    missing: coverage, mean confidence and each concrete gap
    (dimensions without evidence, weak evidence, purely parametric
    content).

    Args:
        q: Natural-language query.
        top_n: Maximum results to evaluate (1-50).

    Returns:
        {query, results, coverage, mean_confidence, gaps,
        is_total_gap, parametric_count}.
    """
    return traducir_payload(await tools_impl._mapa_lagunas(q, top_n, workspace))


@router.post("/projection", dependencies=[Depends(requerir_escritura)])
async def generate_projection(horizon_days: int = 7, history_days: int = 30) -> dict[str, Any]:
    """Generates and persists a projection (``project_trajectory``).

    Args:
        horizon_days: Days ahead (1-90).
        history_days: Days of history to analyze (1-365).

    Returns:
        Projection with per-dimension distribution and trajectories.
    """
    return traducir_payload(await tools_impl._proyectar(horizon_days, history_days))


@router.get("/projection/current", dependencies=[Depends(verificar_token)])
async def current_projection() -> dict[str, Any]:
    """Returns the last persisted projection without recomputing.

    Returns:
        Dict with the current projection or {"status": "no_projections"}.
    """
    ctx = await contexto_actual(obtener_engine())
    async with get_session(obtener_engine()) as session:
        ultima = await ProyeccionRepository(session, ctx).ultima()
    if ultima is None:
        return {"status": "no_projections"}
    return {
        "id": str(ultima.id),
        "horizon_days": ultima.horizonte_dias,
        "probable_dimension": ultima.rama_decision,
        "probability": ultima.probabilidad,
        "distribution": ultima.vector_proyectado,
        "events_analyzed": len(ultima.basada_en_eventos),
        "created_at": ultima.creado_en.isoformat(),
    }


def _validar_clave(key: str) -> str:
    """Validates the preference-key format (safe path segment).

    Args:
        key: Key received in the path.

    Returns:
        The validated key.

    Raises:
        HTTPException 422: invalid format.
    """
    if not _PATRON_CLAVE_PREF.fullmatch(key):
        raise HTTPException(
            status_code=422,
            detail="invalid key: only [a-z0-9_.-], max 64 characters",
        )
    return key


@router.get("/preferences/{key}", dependencies=[Depends(verificar_token)])
async def read_preference(key: str) -> dict[str, Any]:
    """Reads a preference document persisted in PostgreSQL.

    Args:
        key: Preference identifier (e.g. ``cerebro``).

    Returns:
        Stored JSON document, or ``{}`` if the key does not exist.
    """
    return await tools_impl._preferencia_leer(_validar_clave(key))


@router.put("/preferences/{key}", dependencies=[Depends(requerir_escritura)])
async def save_preference(key: str, value: dict[str, Any]) -> dict[str, Any]:
    """Persists (upsert) a preference document in PostgreSQL.

    Args:
        key: Preference identifier.
        value: JSON document to store (max ~1MB per JSONB column).

    Returns:
        Confirmation with the saved key.
    """
    await tools_impl._preferencia_guardar(_validar_clave(key), value)
    return {"key": key, "saved": True}


# ── AI providers and prediction engine ──────────────────────────
# Local-first: with no provider enabled Nous is fully functional.
# API keys are stored encrypted (pgcrypto) and the API only exposes
# the mask; ``include_content`` authorizes sending memory fragments —
# by default only statistical aggregates travel.


@router.get("/ai/providers", dependencies=[Depends(verificar_token)])
async def list_ai_providers() -> list[dict[str, Any]]:
    """Lists configured AI providers (keys masked)."""
    async with get_session(obtener_engine()) as session:
        return traducir_payload(await ia.listar_proveedores(session))


@router.put("/ai/providers/{pid}", dependencies=[Depends(requerir_escritura)])
async def save_ai_provider(pid: str, request_body: AIProviderRequest) -> dict[str, Any]:
    """Creates or updates an AI provider (key encrypted in PostgreSQL).

    When ``api_key`` is absent from the body, the existing one is kept.
    """
    datos = {
        "nombre": request_body.name,
        "base_url": request_body.base_url,
        "modelo": request_body.model,
        "habilitado": request_body.enabled,
        "incluir_contenido": request_body.include_content,
        "predeterminado": request_body.default,
    }
    if request_body.api_key is not None:
        datos["api_key"] = request_body.api_key
    if request_body.fallback_id is not None:
        datos["fallback_id"] = request_body.fallback_id
    async with get_session(obtener_engine()) as session:
        try:
            publico = await ia.guardar_proveedor(session, pid, datos)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        await session.commit()
    return traducir_payload(publico)


@router.delete("/ai/providers/{pid}", dependencies=[Depends(requerir_escritura)])
async def delete_ai_provider(pid: str) -> dict[str, Any]:
    """Deletes an AI provider and its encrypted key."""
    async with get_session(obtener_engine()) as session:
        ok = await ia.eliminar_proveedor(session, pid)
        if not ok:
            raise HTTPException(status_code=404, detail="Provider not found")
        await session.commit()
    return {"id": pid, "deleted": True}


@router.post("/ai/providers/{pid}/test", dependencies=[Depends(requerir_escritura)])
async def test_ai_provider(pid: str) -> dict[str, Any]:
    """Tests connectivity with the endpoint (GET /models)."""
    async with get_session(obtener_engine()) as session:
        return traducir_payload(await ia.probar_proveedor(session, pid))


@router.post("/ai/predict", dependencies=[Depends(requerir_escritura)])
async def predict_ai(request_body: PredictRequest) -> dict[str, Any]:
    """Generates future scenarios with the enabled AI provider.

    The LLM receives the statistical summary of the line up to
    ``origin`` (plus memory fragments only if the provider has
    ``include_content``) and returns scenarios with probability and
    narrative. With ``candidates`` the heuristic futures of the
    stream are re-weighted. Without a provider, ``ok=false`` and the
    consumer falls back to the local heuristic.
    """
    origen = request_body.origin_unix or int(time.time())
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
    candidatos = (
        [
            {
                "id": c.id,
                "nombre": c.name,
                "probabilidad": c.probability,
                "descripcion": c.description,
            }
            for c in request_body.candidates
        ]
        if request_body.candidates
        else None
    )
    async with get_session(obtener_engine()) as session:
        return traducir_payload(
            await ia.predecir_escenarios(
                session,
                origen=origen,
                horizonte_dias=request_body.horizon_days,
                eventos=eventos,
                metas=metas,
                loops_abiertos=loops,
                candidatos=candidatos,
                proveedor_id=request_body.provider_id,
            )
        )


@router.get("/activity", dependencies=[Depends(verificar_token)])
async def activity(limit: int = 50) -> list[dict[str, Any]]:
    """Recent brain activity for the live visualization.

    ``store`` events (new imprint) and ``recall`` events (evocation
    with hits), newest first.

    Args:
        limit: Maximum events (1-100).

    Returns:
        List of events {kind, q, ids, ts}.
    """
    return traducir_payload(tools_impl.actividad_reciente(limit))


@router.get("/benchmarks", dependencies=[Depends(verificar_token)])
async def list_benchmarks(name: str | None = None, limit: int = 50) -> list[BenchmarkResponse]:
    """Lists persisted benchmark runs, newest first.

    Args:
        name: Filter by benchmark name (None = all).
        limit: Maximum runs to return.

    Returns:
        List of runs for the performance dashboard.
    """
    async with get_session(obtener_engine()) as session:
        corridas = await BenchmarkRepository(session).listar(name, limit)
    return [
        BenchmarkResponse(
            id=str(c.id),
            name=c.nombre,
            scenario=c.escenario,
            params=c.parametros,
            metrics=c.metricas,
            git_commit=c.git_commit,
            created_at=c.creado_en,
        )
        for c in corridas
    ]


@router.get("/calls", dependencies=[Depends(verificar_token)])
async def list_calls(limit: int = 200, route: str | None = None) -> list[CallResponse]:
    """Lists registered calls to Nous, newest first.

    Args:
        limit: Maximum rows (cap 1000).
        route: Exact route filter; None = all.

    Returns:
        Traceability log: method, route, origin, latency, status.
        Metadata only — never content.
    """
    async with get_session(obtener_engine()) as session:
        filas = await LlamadaRepository(session).listar(limit, route)
    return [
        CallResponse(
            id=str(f.id),
            ts=f.ts,
            method=f.metodo,
            route=f.ruta,
            origin=f.origen,
            agent=f.agente,
            latency_ms=f.latencia_ms,
            status=f.status,
            is_ping=f.es_ping,
        )
        for f in filas
    ]


@router.get("/calls/stats", dependencies=[Depends(verificar_token)])
async def calls_stats() -> CallsStatsResponse:
    """Registry aggregates: by route, by origin and pings.

    Returns:
        Total, top routes with mean latency, origins and ping
        summary (total + last timestamp).
    """
    async with get_session(obtener_engine()) as session:
        stats = await LlamadaRepository(session).stats()
    return CallsStatsResponse(
        total=stats["total"],
        by_route=[
            CallRouteStats(
                route=r["ruta"],
                calls=r["llamadas"],
                mean_latency=r["latencia_media"],
            )
            for r in stats["por_ruta"]
        ],
        by_origin=[
            CallOriginStats(origin=r["origen"], calls=r["llamadas"]) for r in stats["por_origen"]
        ],
        pings=PingsStats(total=stats["pings"]["total"], last=stats["pings"]["ultimo"]),
    )


@router.post("/ping", dependencies=[Depends(requerir_escritura)])
async def ping() -> dict[str, Any]:
    """Registers an explicit ping — marked in the call log.

    Returns:
        ``{"status": "ok", "ts": ...}`` to measure connectivity.
    """
    return {"status": "ok", "ts": int(time.time())}


@router.post("/import", dependencies=[Depends(requerir_escritura)])
async def import_memory(request_body: ImportRequest) -> ImportResponse:
    """Migrates external memory into Nous from a local file/directory.

    Formats: ``obsidian`` (.md vault), ``gbrain`` (.md dir or export),
    ``memlord`` (JSON/JSONL export), native ``jsonl``/``json``. The
    path lives on the machine running this backend — nothing leaves
    the device. Existing ids are skipped (idempotent). With
    ``preview`` it only reads and anticipates counters, no writes.

    Args:
        request_body: Format, local path, optional source and preview.

    Returns:
        Counters: imported, skipped (duplicates), errors, links
        created. In preview: ``new`` = rows that would enter,
        ``imported`` = 0.
    """
    from pathlib import Path

    if request_body.url:
        return await _import_remote(request_body)
    if not request_body.path:
        raise HTTPException(
            status_code=422,
            detail="provide a local path or an instance url",
        )
    ruta_str = request_body.path  # narrowed by the 422 above
    ruta = await asyncio.to_thread(lambda: Path(ruta_str).expanduser())
    if not await asyncio.to_thread(ruta.exists):
        raise HTTPException(status_code=422, detail=f"inaccessible path: {request_body.path}")
    if request_body.preview:
        from src.cli.transferencia import inspeccionar

        previa = await inspeccionar(
            obtener_engine(),
            ruta,
            formato=request_body.format,
            fuente=request_body.source,
        )
        return ImportResponse(
            imported=0,
            skipped=previa["omitidos"],
            errors=previa["errores"],
            links=previa["vinculos"],
            format=request_body.format,
            source=request_body.source or request_body.format,
            preview=True,
            new=previa["nuevas"],
        )
    resultado = await importar_memoria(
        obtener_engine(),
        ruta,
        formato=request_body.format,
        fuente=request_body.source,
        rama=request_body.branch,
    )
    await _register_import(request_body, resultado)
    return ImportResponse(
        imported=resultado["importados"],
        skipped=resultado["omitidos"],
        errors=resultado["errores"],
        links=resultado["vinculos"],
        format=request_body.format,
        source=request_body.source or request_body.format,
    )


_MAX_IMPORT_HISTORY = 15


async def _import_remote(request_body: ImportRequest) -> ImportResponse:
    """Migrates memory from a remote MCP instance (gbrain/memlord).

    The ``url`` endpoint must be streamable-HTTP (``/mcp``); the
    connection and download happen only through this explicit
    user gesture.
    """
    from src.cli.importador_remoto import ErrorRemoto, leer_fuente_remota
    from src.cli.transferencia import contar_nuevas, importar_filas

    try:
        filas, vinculos, errores = await leer_fuente_remota(
            request_body.url or "",
            request_body.token,
            request_body.format,
            request_body.source or request_body.format,
        )
    except ErrorRemoto as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if request_body.preview:
        previa = await contar_nuevas(obtener_engine(), filas, vinculos, errores)
        return ImportResponse(
            imported=0,
            skipped=previa["omitidos"],
            errors=previa["errores"],
            links=previa["vinculos"],
            format=request_body.format,
            source=request_body.source or request_body.format,
            preview=True,
            new=previa["nuevas"],
        )
    resultado = await importar_filas(
        obtener_engine(), filas, vinculos, errores, request_body.branch
    )
    await _register_import(request_body, resultado)
    return ImportResponse(
        imported=resultado["importados"],
        skipped=resultado["omitidos"],
        errors=resultado["errores"],
        links=resultado["vinculos"],
        format=request_body.format,
        source=request_body.source or request_body.format,
    )


async def _register_import(request_body: ImportRequest, resultado: dict[str, int]) -> None:
    """Appends the migration to the persisted history (max 15 entries)."""
    from src.storage.repositories import ConfiguracionRepository

    entrada = {
        "ts": int(time.time()),
        "format": request_body.format,
        "source": request_body.source or request_body.format,
        "path": (request_body.url or request_body.path or "")[:300],
        "result": resultado,
    }
    ctx = await contexto_actual(obtener_engine())
    async with get_session(obtener_engine()) as session:
        repo = ConfiguracionRepository(session, ctx)
        valor = await repo.obtener("importaciones_historial")
        historial: list[dict[str, Any]] = valor["items"] if isinstance(valor, dict) else []
        historial.insert(0, entrada)
        await repo.guardar(
            "importaciones_historial",
            {"items": historial[:_MAX_IMPORT_HISTORY]},
        )
        await session.commit()


@router.get("/import/history", dependencies=[Depends(verificar_token)])
async def import_history() -> list[ImportHistoryEntry]:
    """Returns the last executed migrations (max 15)."""
    from src.storage.repositories import ConfiguracionRepository

    ctx = await contexto_actual(obtener_engine())
    async with get_session(obtener_engine()) as session:
        valor = await ConfiguracionRepository(session, ctx).obtener("importaciones_historial")
    items = valor["items"] if isinstance(valor, dict) else []
    return [
        ImportHistoryEntry(
            ts=i["ts"],
            format=i.get("format", i.get("formato", "")),
            source=i.get("source", i.get("fuente", "")),
            path=i.get("path", i.get("ruta", "")),
            result=i.get("result", i.get("resultado", {})),
        )
        for i in items
    ]


# ── Multi-tenant identity ───────────────────────────────────────


def _ws_summary(w: idn.WorkspaceConRol) -> WorkspaceSummary:
    """Translates the domain dataclass to the REST schema."""
    return WorkspaceSummary(id=str(w.id), slug=w.slug, name=w.nombre, role=w.rol)


async def _ctx_owner_en(ref: str) -> tuple[idn.Contexto, idn.Contexto]:
    """Resolves the current context and requires owner role in ``ref``.

    Returns:
        (current context, context in the target workspace).

    Raises:
        PermisoDenegadoError: No membership or role below owner.
        ErrorValidacion: Workspace does not exist.
    """
    from src.core.exceptions import PermisoDenegadoError

    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    ctx_ws = await idn.resolver_workspace(engine, ctx, ref)
    if ctx_ws.rol != "owner":
        raise PermisoDenegadoError("owner role required in the workspace")
    return ctx, ctx_ws


@router.get("/identity", dependencies=[Depends(verificar_token)])
async def current_identity() -> IdentityResponse:
    """Who the caller is and which workspaces they belong to."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    workspaces = await idn.listar_workspaces_con_rol(engine, ctx.usuario_id)
    activo = next((w for w in workspaces if w.id == ctx.workspace_id), None)
    if activo is None:
        activo = idn.WorkspaceConRol(id=ctx.workspace_id, slug="", nombre="", rol=ctx.rol)
    usuario: UserSummary | None = None
    if ctx.usuario_id is not None:
        from sqlalchemy import select as _sel

        from src.storage.models import UsuarioModel

        async with get_session(engine) as session:
            u = (
                await session.execute(_sel(UsuarioModel).where(UsuarioModel.id == ctx.usuario_id))
            ).scalar_one_or_none()
        if u is not None:
            usuario = UserSummary(
                id=str(u.id),
                handle=u.handle,
                name=u.nombre,
                email=u.email,
            )
    lic = await idn.estado_licencia(engine)
    return IdentityResponse(
        user=usuario,
        active_workspace=_ws_summary(activo),
        workspaces=[_ws_summary(w) for w in workspaces],
        license=LicenseSummary(
            licensed=lic.licenciado,
            users=lic.usuarios,
            max_users=lic.max_usuarios,
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
async def create_workspace(request_body: WorkspaceRequest) -> WorkspaceSummary:
    """Creates a workspace; the caller becomes its owner."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    owner_id = ctx.usuario_id or idn.USUARIO_OWNER_ID
    ws = await idn.crear_workspace(engine, request_body.slug, request_body.name, owner_id)
    return WorkspaceSummary(id=str(ws.id), slug=ws.slug, name=ws.nombre, role="owner")


@router.get(
    "/workspaces/{ref}/members",
    dependencies=[Depends(verificar_token)],
)
async def list_members(ref: str) -> list[MemberSummary]:
    """Workspace roster — any member can read it."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    ctx_ws = await idn.resolver_workspace(engine, ctx, ref)
    return [
        MemberSummary(
            user_id=str(m.usuario_id),
            handle=m.handle,
            name=m.nombre,
            role=m.rol,
        )
        for m in await idn.listar_miembros(engine, ctx_ws.workspace_id)
    ]


@router.patch(
    "/workspaces/{ref}/members/{uid}",
    dependencies=[Depends(requerir_escritura)],
    status_code=204,
)
async def change_member_role(ref: str, uid: str, patch: MemberPatch) -> None:
    """Changes a member's role — owners only."""
    _ctx, ctx_ws = await _ctx_owner_en(ref)
    await idn.cambiar_rol(obtener_engine(), ctx_ws.workspace_id, _uuid(uid), patch.role)


@router.delete(
    "/workspaces/{ref}/members/{uid}",
    dependencies=[Depends(requerir_escritura)],
    status_code=204,
)
async def remove_member(ref: str, uid: str) -> None:
    """Removes a member — owners only; their memories remain."""
    _ctx, ctx_ws = await _ctx_owner_en(ref)
    await idn.quitar_miembro(obtener_engine(), ctx_ws.workspace_id, _uuid(uid))


def _uuid(ref: str) -> uuid.UUID:
    """Parses a path UUID or raises 422."""
    try:
        return uuid.UUID(ref)
    except ValueError:
        raise HTTPException(422, f"invalid UUID: {ref!r}") from None


@router.post(
    "/workspaces/{ref}/invitations",
    dependencies=[Depends(requerir_escritura)],
    status_code=201,
)
async def create_invitation(ref: str, request_body: InvitationRequest) -> InvitationResponse:
    """Issues an invitation token — owners only; shown once."""
    ctx, ctx_ws = await _ctx_owner_en(ref)
    token = await idn.crear_invitacion(
        obtener_engine(),
        ctx_ws.workspace_id,
        request_body.role,
        dias=request_body.days,
        creada_por=ctx.usuario_id,
    )
    return InvitationResponse(token=token, expires_days=request_body.days)


_limitador_canje = LimitadorIntentos(max_intentos=5, ventana_segundos=60)


@router.post("/invitations/redeem", status_code=201)
async def redeem_invitation(request_body: RedeemRequest, request: Request) -> RedeemResponse:
    """Public invitation redemption — no Bearer: the token is the proof.

    Rate-limited per IP like /login. Returns the new user's key
    only once.
    """
    ip = request.client.host if request.client else "unknown"
    if not _limitador_canje.permite(ip):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts; wait a minute",
        )
    usuario, key = await idn.canjear_invitacion(
        obtener_engine(),
        request_body.token,
        request_body.handle,
        request_body.name,
        email=request_body.email,
    )
    return RedeemResponse(handle=usuario.handle, key=key)


def _key_summary(k: object) -> KeyResponse:
    """Translates ApiKeyModel to the schema (without exposing the hash)."""
    from src.storage.models import ApiKeyModel

    assert isinstance(k, ApiKeyModel)
    return KeyResponse(
        id=str(k.id),
        prefix=k.prefijo,
        name=k.nombre or None,
        workspace_default_id=(
            str(k.workspace_default_id) if k.workspace_default_id is not None else None
        ),
        revoked=k.revocada,
        last_used=k.ultimo_uso,
        created_at=k.creado_en,
    )


@router.get("/keys", dependencies=[Depends(verificar_token)])
async def list_keys() -> list[KeyResponse]:
    """API keys of the caller (never the hash nor the secret)."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    return [_key_summary(k) for k in await idn.listar_keys(engine, ctx.usuario_id)]


@router.post(
    "/keys",
    dependencies=[Depends(requerir_escritura)],
    status_code=201,
)
async def create_key(request_body: KeyRequest) -> dict[str, str]:
    """Issues a new key for the caller — shown in the clear once."""
    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    if ctx.usuario_id is None:
        raise HTTPException(422, "no user identity (development mode)")
    ws_default = (
        _uuid(request_body.workspace_default_id)
        if request_body.workspace_default_id
        else ctx.workspace_id
    )
    if request_body.workspace_default_id is not None:
        await idn.resolver_workspace(engine, ctx, request_body.workspace_default_id)
    key = await idn.emitir_key_usuario(engine, ctx.usuario_id, ws_default, request_body.name)
    return {"key": key}


@router.delete(
    "/keys/{ref}",
    dependencies=[Depends(verificar_token)],
    status_code=204,
)
async def revoke_key(ref: str) -> None:
    """Revokes a key — only if it belongs to the caller."""
    from sqlalchemy import select as _sel

    from src.storage.models import ApiKeyModel

    engine = obtener_engine()
    ctx = await contexto_actual(engine)
    if ctx.usuario_id is None:
        raise HTTPException(422, "no user identity (development mode)")
    async with get_session(engine) as session:
        try:
            key_id = _uuid(ref)
            cond = ApiKeyModel.id == key_id
        except HTTPException:
            cond = ApiKeyModel.prefijo == ref
        fila = (
            await session.execute(
                _sel(ApiKeyModel.id).where(cond, ApiKeyModel.usuario_id == ctx.usuario_id)
            )
        ).scalar_one_or_none()
    if fila is None:
        raise HTTPException(404, "key not found")
    await idn.revocar_key_usuario(engine, ref)


# ---------------------------------------------------------------------
# ADR-003 — neurocognitive patterns: prospective intentions,
# sleep-consolidation, reversible forgetting and script mining.
# Parity with the MCP tools of the same name.
# ---------------------------------------------------------------------


@router.post("/intentions", dependencies=[Depends(requerir_escritura)])
async def create_intention(request_body: IntentionRequest) -> dict[str, Any]:
    """Registers a prospective intention (prospective memory).

    ``at`` = temporal trigger ("3d", epoch); ``when`` = event
    trigger (text to watch for). Exactly one of the two.
    """
    return traducir_payload(
        await tools_impl._crear_intencion(
            request_body.intention,
            request_body.at,
            request_body.when,
            request_body.threshold,
            request_body.workspace,
        )
    )


@router.get("/intentions", dependencies=[Depends(verificar_token)])
async def list_intentions(
    executed: bool | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists the prospective intentions of the workspace."""
    return traducir_payload(await tools_impl._listar_intenciones(executed, workspace))


@router.post(
    "/intentions/{intention_id}/cancel",
    dependencies=[Depends(requerir_escritura)],
)
async def cancel_intention(intention_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Cancels a pending prospective intention."""
    return traducir_payload(await tools_impl._cancelar_intencion(intention_id, workspace))


@router.post("/consolidate", dependencies=[Depends(requerir_escritura)])
async def consolidate(workspace: str | None = None) -> dict[str, Any]:
    """Runs the workspace sleep-consolidation pass.

    Replay + promotion to curated + reversible archiving, with a
    persisted report — the process is auditable, never silent.
    """
    return traducir_payload(await tools_impl._consolidar(workspace))


@router.get("/consolidation/reports", dependencies=[Depends(verificar_token)])
async def consolidation_reports(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Consolidation run reports (the dream journal)."""
    ctx = await contexto_actual(obtener_engine())
    if workspace is not None:
        ctx = await idn.resolver_workspace(obtener_engine(), ctx, workspace)
    from src.storage.repositories import ReporteConsolidacionRepository

    async with get_session(obtener_engine()) as session:
        filas = await ReporteConsolidacionRepository(session, ctx).listar()
    return [
        {
            "id": str(r.id),
            "executed_at": r.ejecutado_en,
            "strengthened": r.fortalecidos,
            "archived": r.archivados,
            "promoted": r.promovidos,
            "detail": r.detalle,
        }
        for r in filas
    ]


@router.post("/scripts/mine", dependencies=[Depends(requerir_escritura)])
async def mine_scripts(workspace: str | None = None) -> dict[str, Any]:
    """Mines recurrent sequences from the universe line."""
    return traducir_payload(await tools_impl._minar_scripts(workspace))


@router.get("/scripts", dependencies=[Depends(verificar_token)])
async def list_scripts(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Scripts/patterns detected by mining."""
    return traducir_payload(await tools_impl._listar_scripts(workspace))


@router.get("/archived", dependencies=[Depends(verificar_token)])
async def list_archived(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Memories archived by consolidation — recoverable."""
    return traducir_payload(await tools_impl._listar_archivados(workspace))


@router.post(
    "/events/{event_id}/restore",
    dependencies=[Depends(requerir_escritura)],
)
async def restore_event(event_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Restores an archived memory — undoes the forgetting."""
    return traducir_payload(await tools_impl._restaurar_evento(event_id, workspace))


@router.post(
    "/events/{event_id}/curate",
    dependencies=[Depends(requerir_escritura)],
)
async def curate_event(
    event_id: str,
    request_body: CurateRequest | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Marks the memory as curated (or uncurates with ``curated=false``).

    Curated memories never decay nor get archived — they are the
    living MEMORY.md.
    """
    curado = True if request_body is None else request_body.curated
    return traducir_payload(await tools_impl._marcar_curado(event_id, curado, workspace))


# ---------------------------------------------------------------------
# ADR-002 Level 2 — prescriptive advisor (Pro feature, licensed).
# ---------------------------------------------------------------------


@router.post("/decision", dependencies=[Depends(requerir_escritura)])
async def ask_decision(request_body: DecisionRequest) -> dict[str, Any]:
    """Evaluates a dilemma against the 4D universe line (Pro feature).

    Without license → 402 via ``CupoExcedidoError``. Without a
    System One provider → ``available=false`` with the assembled
    context.
    """
    return traducir_payload(
        await tools_impl._consultar_decision(
            request_body.dilemma, request_body.options, request_body.workspace
        )
    )


# ---------------------------------------------------------------------
# ADR-001 — license management from the dashboard (owner only).
# The license belongs to the SERVER, not to a workspace — hence it
# requires ``requerir_owner`` and not ``requerir_escritura``.
# ---------------------------------------------------------------------


@router.post("/license/activate", dependencies=[Depends(requerir_owner)])
async def activate_license(
    request_body: ActivateLicenseRequest,
) -> dict[str, Any]:
    """Activates a Pro license key by validating and persisting it locally.

    Equivalent to ``nous activate-license`` — the file lands in
    ``~/.nous/license.jwt`` and applies on the next ``licencia_actual()``.
    Only the server owner can activate it.

    Returns:
        {activated, tier, expires, signature_verified}.

    Raises:
        HTTPException 403: caller is not the owner.
        HTTPException 422: invalid or expired key (via NousError).
    """
    from src.core import license as lic_mod

    lic = await asyncio.to_thread(lic_mod.activar_licencia, request_body.key)
    return {
        "activated": True,
        "tier": lic.tier,
        "expires": lic.expira,
        "signature_verified": lic.firma_verificada,
    }


@router.delete("/license", dependencies=[Depends(requerir_owner)])
async def deactivate_license() -> dict[str, Any]:
    """Deletes the local license file (back to the free edition).

    Does not affect ``NOUS_LICENSE_KEY``: if that env var is still
    defined, the license stays active even after the file is removed.
    """
    from src.core import license as lic_mod

    quitada = await asyncio.to_thread(lic_mod.desactivar_licencia)
    return {"activated": False, "file_removed": quitada}


@router.post("/drafts", dependencies=[Depends(requerir_escritura)])
async def save_draft(request_body: DraftRequest) -> dict[str, Any]:
    """Saves a volatile thought into working memory.

    The buffer is deliberately short: it expires in ~30 minutes
    without rehearsal, and promotes to the universe line if
    repeated enough.
    """
    return traducir_payload(
        await tools_impl._recordar_borrador(
            request_body.content,
            dimension_es(request_body.dimension) or "cognitiva",
            request_body.workspace,
        )
    )


@router.get("/drafts", dependencies=[Depends(verificar_token)])
async def list_drafts(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Live drafts of the workspace working buffer."""
    return traducir_payload(await tools_impl._listar_borrador(workspace))


@router.post(
    "/drafts/{draft_id}/rehearse",
    dependencies=[Depends(requerir_escritura)],
)
async def rehearse_draft(draft_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Rehearses a draft — at the threshold it promotes to an event."""
    return traducir_payload(await tools_impl._ensayar_borrador(draft_id, workspace))


@router.delete(
    "/drafts/{draft_id}",
    dependencies=[Depends(requerir_escritura)],
)
async def discard_draft(draft_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Removes a draft from the buffer without promoting it."""
    return traducir_payload(await tools_impl._descartar_borrador(draft_id, workspace))


# ── Neuro-3 ────────────────────────────────────────────────────


@router.post(
    "/events/{event_id}/forget",
    dependencies=[Depends(requerir_escritura)],
)
async def forget_gradually(
    event_id: str, request_body: DirectedForgettingRequest
) -> dict[str, Any]:
    """Directed forgetting: accelerated decay, no deletion nor archive."""
    return traducir_payload(
        await tools_impl._olvidar_gradual(event_id, request_body.forget, request_body.workspace)
    )


@router.get("/reviews", dependencies=[Depends(verificar_token)])
async def pending_reviews(limit: int = 20, workspace: str | None = None) -> list[dict[str, Any]]:
    """Memories whose optimal (SRS) review time has already passed."""
    return traducir_payload(await tools_impl._repasos_pendientes(limit, workspace))


@router.post(
    "/intentions/{intention_id}/snooze",
    dependencies=[Depends(requerir_escritura)],
)
async def snooze_intention(intention_id: str, request_body: SnoozeRequest) -> dict[str, Any]:
    """Postpones the firing of an intention (snooze)."""
    return traducir_payload(
        await tools_impl._aplazar_intencion(
            intention_id, request_body.seconds, request_body.workspace
        )
    )


@router.post(
    "/sources/{name}/trust",
    dependencies=[Depends(requerir_escritura)],
)
async def trust_source(name: str, request_body: TrustSourceRequest) -> dict[str, Any]:
    """Approves a source (releases its quarantine) or isolates it."""
    return traducir_payload(
        await tools_impl._confiar_fuente(name, request_body.trusted, request_body.workspace)
    )


@router.get("/quarantine", dependencies=[Depends(verificar_token)])
async def list_quarantine(limit: int = 50, workspace: str | None = None) -> list[dict[str, Any]]:
    """Events held in quarantine by an untrusted source."""
    return traducir_payload(await tools_impl._listar_cuarentena(limit, workspace))


# ── Neuro-5: full session cycle ──────────────────────────────────


@router.post("/sessions", dependencies=[Depends(requerir_escritura)])
async def start_session(
    request_body: SessionStartRequest,
) -> dict[str, Any]:
    """Opens a session: boundary + budgeted bootstrap + snapshot."""
    return traducir_payload(
        await tools_impl._iniciar_sesion(
            request_body.agent, request_body.budget, request_body.workspace
        )
    )


@router.post("/sessions/close", dependencies=[Depends(requerir_escritura)])
async def end_session(
    request_body: SessionEndRequest,
) -> dict[str, Any]:
    """Closes the open session and generates its continuity handoff."""
    return traducir_payload(
        await tools_impl._cerrar_sesion(
            request_body.session_id, request_body.summary, request_body.workspace
        )
    )


@router.get("/sessions", dependencies=[Depends(verificar_token)])
async def list_sessions(limit: int = 20, workspace: str | None = None) -> list[dict[str, Any]]:
    """Session history of the workspace."""
    return traducir_payload(await tools_impl._listar_sesiones(limit, workspace))


@router.get("/sessions/handoff", dependencies=[Depends(verificar_token)])
async def get_handoff(
    session_id: str | None = None, workspace: str | None = None
) -> dict[str, Any]:
    """Handoff of the last closed session (or the given one)."""
    return traducir_payload(await tools_impl._obtener_handoff(session_id, workspace))


@router.post("/censors", dependencies=[Depends(requerir_escritura)])
async def create_censor(request_body: CensorRequest) -> dict[str, Any]:
    """Registers a guardrail — a correction matured into a conduct rule."""
    return traducir_payload(
        await tools_impl._crear_censor(
            request_body.rule,
            severidad_es(request_body.severity) or "advertir",
            request_body.domain,
            request_body.scope,
            request_body.workspace,
        )
    )


@router.get("/censors", dependencies=[Depends(verificar_token)])
async def list_censors(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Active guardrails of the workspace."""
    return traducir_payload(await tools_impl._listar_censores(workspace))


@router.post("/verify", dependencies=[Depends(verificar_token)])
async def verify_action(
    request_body: VerifyRequest,
) -> dict[str, Any]:
    """Verifies an action against the censors — pre-action."""
    return traducir_payload(
        await tools_impl._verificar_accion(request_body.action, request_body.workspace)
    )


@router.post("/predictions", dependencies=[Depends(requerir_escritura)])
async def register_prediction(
    request_body: PredictionRequest,
) -> dict[str, Any]:
    """Declares a prediction with confidence — raw material for Brier."""
    return traducir_payload(
        await tools_impl._registrar_prediccion(
            request_body.claim,
            request_body.confidence,
            request_body.event_id,
            request_body.workspace,
        )
    )


@router.post(
    "/predictions/{prediction_id}/resolve",
    dependencies=[Depends(requerir_escritura)],
)
async def resolve_prediction(
    prediction_id: str, request_body: ResolvePredictionRequest
) -> dict[str, Any]:
    """Resolves a prediction — hit or miss, the Brier counts it."""
    return traducir_payload(
        await tools_impl._resolver_prediccion(
            prediction_id, request_body.hit, request_body.workspace
        )
    )


@router.get("/predictions", dependencies=[Depends(verificar_token)])
async def list_predictions(
    pending: bool | None = None,
    limit: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Open and resolved predictions of the workspace."""
    return traducir_payload(await tools_impl._listar_predicciones(pending, limit, workspace))


@router.get("/calibration", dependencies=[Depends(verificar_token)])
async def calibration_report(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Brier + reliability curve — measured honesty."""
    return traducir_payload(await tools_impl._reporte_calibracion(workspace))


@router.get("/approvals/pending", dependencies=[Depends(verificar_token)])
async def pending_approvals(limit: int = 50, workspace: str | None = None) -> list[dict[str, Any]]:
    """Staged writes awaiting approval (write-approval)."""
    return traducir_payload(await tools_impl._pendientes_aprobacion(limit, workspace))


@router.post(
    "/approvals/{event_id}/approve",
    dependencies=[Depends(requerir_escritura)],
)
async def approve_event(event_id: str) -> dict[str, Any]:
    """Approves a staged proposal — it enters recall."""
    return traducir_payload(await tools_impl._aprobar_evento(event_id))


@router.delete("/approvals/{event_id}", dependencies=[Depends(requerir_escritura)])
async def reject_event(event_id: str) -> dict[str, Any]:
    """Rejects a staged proposal — the write never existed."""
    return traducir_payload(await tools_impl._rechazar_evento(event_id))


@router.post("/approvals/approve-all", dependencies=[Depends(requerir_escritura)])
async def approve_all_pending(
    workspace: str | None = None,
) -> dict[str, Any]:
    """Approves every visible staged proposal."""
    return traducir_payload(await tools_impl._aprobar_pendientes(workspace))
