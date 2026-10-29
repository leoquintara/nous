"""Canonical English MCP tools for Nous — persistent 4D memory.

Every tool declares explicitly when to use it and when NOT to, so the
invoking model does not confuse responsibilities. These are the public
names new integrations should call; the Spanish names in ``tools.py``
remain registered as deprecated aliases (same contract, legacy names
and arguments) and forward to the same helpers.
"""

from typing import Any, Literal

from src.core.episodios import GAP_MAX_DEFECTO_SEGUNDOS
from src.mcp import tools as _es
from src.mcp.contract import (
    dimension_es,
    estado_objetivo_es,
    severidad_es,
    tipo_es,
    tipo_memoria_es,
    traducir_payload,
)
from src.mcp.server import mcp_server


@mcp_server.tool()
async def remember(
    content: str,
    dimension: Literal["cognitive", "somatic", "behavioral"] | None = None,
    source: str = "agent",
    amplitude: float = 0.5,
    frequency: float | None = None,
    timestamp_unix: int | None = None,
    related_ids: list[str] | None = None,
    tags: list[str] | None = None,
    branch: str = "main",
    kind: str = "event",
    ttl: str | int | None = None,
    supersedes_id: str | None = None,
    with_whom: str | None = None,
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
    memory_kind: Literal["episodic", "semantic", "procedural"] | None = None,
    emotional_state: float | None = None,
    parametric: bool = False,
) -> dict[str, Any]:
    """Stores a new event in the user's persistent 4D memory.

    USE WHEN: recording a thought, decision, emotion, body state or
    action to consult later. DO NOT use to search existing memories —
    that is ``recall``. DO NOT use to list a date range — ``timeline``.

    Content is vectorized locally in the background (nothing leaves the
    machine): the response is immediate ("indexed": false) and semantic
    search finds it milliseconds later; text search sees it instantly.
    Omit ``dimension`` for automatic classification.
    Near-duplicate events within the last hour merge on indexing
    (reinforcement) instead of duplicating — same events at distinct
    times do create separate traces.

    Args:
        content: Event text (e.g. "decided to use PostgreSQL").
        dimension: "cognitive" (ideas/decisions/reflections), "somatic"
            (emotions/energy/body) or "behavioral" (actions/habits/
            commits). Omit for automatic classification.
        source: Event origin (e.g. "devin", "claude", "user").
        amplitude: Intensity/importance 0.0–1.0. Default 0.5.
        frequency: Estimated brain frequency in Hz when coming from a
            wearable/EEG (Delta <4, Theta 4-8, Alpha 8-13, Beta 13-30,
            Gamma >30). Omit without biometric data; Nous does not
            estimate it on its own.
        timestamp_unix: Unix epoch in SECONDS. Default: now.
        related_ids: UUIDs of existing events to link explicitly
            (e.g. when the user declares two facts belong together).
            Invalid ids are ignored.
        tags: Tags relating the content to other memories
            (e.g. ["work", "franz"]). #hashtags inside the content are
            extracted and merged automatically.
        branch: Universe line where to record the event ("main"
            default). Branches are parallel lines diverging from main,
            useful for scenarios.
        kind: Epistemic class — "event" (default), "fact" (objective
            datum), "belief" (revisable opinion), "commitment" (pending
            promise → opens a loop listed by ``open_commitments`` until
            closed) or "preference" (user taste).
        ttl: Expiry — "30d", "12h", "45m", "2w" or Unix epoch. Omitted
            = never expires. Expired memories stop being evocable but
            stay in the timeline.
        supersedes_id: UUID of the event this one replaces: "my weight
            is 79" supersedes "my weight is 80". The old one stays for
            archaeology; the new one heads the chain.
        with_whom: Only with kind="commitment" — the counterpart
            awaiting response (person, team, system).
        scope: "personal" (only you see it — default, nothing leaks to
            the team by accident) or "workspace" (visible to the whole
            workspace team).
        workspace: Slug or UUID of the target workspace (overrides the
            key's default). Requires membership.
        memory_kind: ADR-003 — "episodic", "semantic" or "procedural"
            when the caller already knows (e.g. a check-in declares
            "episodic"). Omit to let the classifier estimate it.
        emotional_state: Declared affective valence -1.0–1.0
            (negative → positive). When present, the lexical estimator
            does not overwrite it. Useful for somatic check-ins.
        parametric: True when the content is asserted from the agent's
            parametric knowledge (its weights), not an observation or
            source — recall confidence is capped at 0.70 until reality
            corroborates it (3 useful reinforcements or human
            approval). USE for epistemic honesty: "I think…" ≠ "I saw…".

    Returns:
        Dict with "id", "timestamp_unix", used "dimension", "indexed"
        (false while the embedding generates in background),
        "classification_confidence" when auto-classified,
        "links_created" when links were requested, and
        "context": {"complete": bool, ...} — when the event mentions
        entities with no history in memory, complete=false and the
        suggestion says what to ask the user. RESPOND by asking for
        that piece when it appears.
    """
    return traducir_payload(
        await _es._recordar(
            contenido=content,
            dimension=dimension_es(dimension),
            fuente=source,
            amplitud=amplitude,
            frecuencia=frequency,
            timestamp_unix=timestamp_unix,
            relaciona_ids=related_ids,
            tags=tags,
            rama=branch,
            tipo=tipo_es(kind) or kind,
            ttl=ttl,
            reemplaza_id=supersedes_id,
            con_quien=with_whom,
            scope=scope,
            workspace=workspace,
            tipo_memoria=tipo_memoria_es(memory_kind),
            estado_emocional=emotional_state,
            parametrica=parametric,
        )
    )


@mcp_server.tool()
async def open_commitments(
    branch: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists open promises: commitments made and not yet resolved.

    USE WHEN: the user asks "what do I owe?", "what's pending?",
    "who is waiting on me?" or at session start to recover open loops.
    DO NOT use for goals (use ``list_goals``) or scheduled reminders
    (``list_intentions``).

    Zeigarnik loops: open commitments carry extra activation in recall
    — pending things weigh on the mind. Each result includes
    "days_open" so the agent can prioritize stale promises.

    Args:
        branch: Universe line to inspect ("main" default).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of open commitments with "id", "content", "with_whom",
        "days_open" and "timestamp_unix".
    """
    return traducir_payload(await _es._open_loops(branch, workspace))


@mcp_server.tool()
async def close_commitment(
    commitment_id: str,
    resolution: str,
    source: str = "agent",
) -> dict[str, Any]:
    """Closes an open commitment: the promise was kept or cancelled.

    USE WHEN: a pending commitment gets resolved — the counterpart
    answered, the task was done, or it was dropped. Closing records a
    linked event and releases the Zeigarnik loop.
    DO NOT use to close goals — that is ``close_goal``.

    Args:
        commitment_id: UUID of the commitment event (from
            ``open_commitments`` or ``remember`` with kind="commitment").
        resolution: How it closed — free text ("samay confirmed",
            "cancelled, no longer applies"). It is stored as the
            closing event's content.
        source: Who closes it (agent or user name).

    Returns:
        Dict with "closed": true and the closing event id.
    """
    return traducir_payload(await _es._cerrar_loop(commitment_id, resolution, source))


@mcp_server.tool()
async def bootstrap_context(
    top_n: int = 8,
    budget: int | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Session bootstrap: open loops + hot facts + due reminders.

    CALL AT THE START of every session — a cheap call with double
    value: it returns the warm context the agent needs AND fires
    temporal intentions that came due. Nous remembers when consulted:
    without a bootstrap call, reminders stay silent.

    The returned pack fits a token budget: working context, pending
    commitments, recent high-weight events and fired reminders —
    enough to resume work without re-reading everything.

    Args:
        top_n: Max hot facts to include.
        budget: Token budget for the pack (None = configured default).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the bootstrap sections (hot facts, open loops,
        fired "reminders" when any came due) and packing metadata.
    """
    return traducir_payload(await _es._contexto_inicial(top_n, budget, workspace))


@mcp_server.tool()
async def list_goals(
    status: str = "active",
    branch: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists the user's declared goals, filtered by status.

    USE WHEN: the user asks about their objectives, roadmap or "what
    am I trying to achieve". DO NOT use for pending promises —
    ``open_commitments`` — or one-off reminders — ``list_intentions``.

    Args:
        status: "active" (default), "fulfilled", "abandoned" or "*"
            for all.
        branch: Universe line ("main" default).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of goals with "id", "title", "status", "target_date"
        and "dimension".
    """
    return traducir_payload(
        await _es._objetivos(
            None if status == "*" else estado_objetivo_es(status), branch, workspace
        )
    )


@mcp_server.tool()
async def create_goal(
    title: str,
    target_date: int,
    dimension: str | None = None,
    description: str | None = None,
    branch: str = "main",
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registers a declared goal in the universe line.

    USE WHEN: the user states a durable objective ("I want to launch
    Nous by March"). Goals are first-class entities: they anchor
    trajectory projection and can be fulfilled or abandoned with
    audit trail.
    DO NOT use for reminders — ``create_intention`` — or for pending
    promises to third parties — ``remember`` kind="commitment".

    Args:
        title: Short goal title ("launch the MCP beta").
        target_date: Unix epoch for the target date.
        dimension: "cognitive"|"somatic"|"behavioral"; omit for
            automatic classification.
        description: Optional longer context.
        branch: Universe line ("main" default).
        scope: "personal" (default) or "workspace".
        workspace: Slug or UUID of the target workspace.

    Returns:
        Dict with the created goal ("id", "title", "status").
    """
    return traducir_payload(
        await _es._crear_objetivo(
            title,
            target_date,
            dimension_es(dimension),
            description,
            branch,
            scope,
            workspace,
        )
    )


@mcp_server.tool()
async def close_goal(
    goal_id: str,
    status: str,
    resolution: str | None = None,
    source: str = "agent",
) -> dict[str, Any]:
    """Closes a goal as fulfilled or abandoned, with audit trail.

    USE WHEN: the user marks a goal done or drops it. Closing records
    the outcome on the universe line.
    DO NOT use for commitments — ``close_commitment``.

    Args:
        goal_id: UUID of the goal (from ``list_goals``).
        status: "fulfilled" or "abandoned".
        resolution: Optional note on how it ended.
        source: Who closes it (agent or user name).

    Returns:
        Dict with the updated goal and its final "status".
    """
    return traducir_payload(
        await _es._cumplir_objetivo(
            goal_id, estado_objetivo_es(status) or status, resolution, source
        )
    )


@mcp_server.tool()
async def recall(
    query: str,
    top_n: int = 10,
    branch: str = "main",
    workspace: str | None = None,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Hybrid retrieval over persistent 4D memory.

    THE main read tool. USE WHENEVER the agent needs what the user
    said, decided, felt or did before — preferences, facts, decisions,
    events, states. The internal router resolves entity/temporal/
    thematic intent automatically: ask in natural language.

    Per query it returns scored results with temporal context
    (surrounding events), confidence breakdown (metamemory — not all
    memories are equally reliable), open-commitment boosts, and —
    for temporal questions — a typed verdict: earliest/latest/
    duration/asof/change/provenance with falsification and conflict
    metadata when evidence is ambiguous. When evidence is missing or
    rivals exist, "clarification" suggests the exact question to ask.

    Related associations arrive flagged "via": "tags" (one-hop tag
    expansion, hubs suppressed) — related, not direct evidence.

    Args:
        query: Natural-language question in any language
            ("when did we decide X", "what does Samay like", "first
            message from Y", "how long has Z been going on").
        top_n: Max direct results.
        branch: Universe line; "*" searches every branch.
        workspace: Slug or UUID; None uses the key's default.
        max_tokens: Approximate cap on the serialized results —
            overflow is reported as "truncated" with "omitted" count.

    Returns:
        List with the scored results (each: "event", scores,
        "confidence", "confidence_breakdown", "temporal_context",
        optional "reminder"/"prepared_context") followed by a final
        "meta" dict (system + verdict + clarification +
        reminder/episode enrichments).
    """
    return traducir_payload(
        await _es._consultar(query, top_n, None if branch == "*" else branch, workspace, max_tokens)
    )


@mcp_server.tool()
async def gap_map(
    query: str,
    top_n: int = 10,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Epistemic gaps: what the query did NOT cover in memory.

    USE WHEN: the honest answer may be "we don't know" — before
    asserting from parametric knowledge, check whether memory simply
    lacks coverage on the topic. Gaps surface as missing dimensions,
    weak evidence or purely parametric content.
    DO NOT use as a substitute for ``recall`` — this answers
    "what is missing", not "what do we have".

    Args:
        query: The topic to probe for coverage.
        top_n: Depth of the underlying scan.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with detected "gaps" (typed reasons + suggestions) and
        whether the gap is total ("is_total_gap").
    """
    return traducir_payload(await _es._mapa_lagunas(query, top_n, workspace))


@mcp_server.tool()
async def timeline(
    from_unix: int,
    to_unix: int,
    limit: int = 100,
    branch: str = "main",
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """The universe line: events between two timestamps, in order.

    USE WHEN: the user asks "what happened between X and Y", needs a
    chronology, or wants to audit a period. Immutable history —
    superseded events stay in the line as archaeology.
    DO NOT use for semantic questions — that is ``recall``.

    Args:
        from_unix: Range start, Unix epoch seconds (inclusive).
        to_unix: Range end, Unix epoch seconds (inclusive).
        limit: Max events to return.
        branch: Universe line ("main" default).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Chronological list of events (serialized dicts).
    """
    return traducir_payload(
        await _es._linea(
            from_unix,
            to_unix,
            limit,
            None if branch == "*" else branch,
            workspace=workspace,
        )
    )


@mcp_server.tool()
async def list_episodes(
    from_unix: int,
    to_unix: int,
    gap_max_seconds: int | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Temporal episodes: contiguous activity segments in a range.

    USE WHEN: reconstructing "sessions" of related activity —
    events clustered by temporal proximity, with their span and
    dominant dimension. Useful for summaries and trajectory context.
    DO NOT use for the raw event list — ``timeline``.

    Args:
        from_unix: Range start, Unix epoch seconds.
        to_unix: Range end, Unix epoch seconds.
        gap_max_seconds: Max gap inside an episode; a larger silence
            splits it. None uses the configured default.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of episodes with "start", "end", "event_ids",
        "dominant_dimension" and duration.
    """
    return traducir_payload(
        await _es._episodios(
            from_unix,
            to_unix,
            gap_max_seconds if gap_max_seconds is not None else GAP_MAX_DEFECTO_SEGUNDOS,
            workspace,
        )
    )


@mcp_server.tool()
async def project_trajectory(
    horizon_days: int = 7,
    history_days: int = 30,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Probable futures: trajectory projection from recent history.

    USE WHEN: the user asks "where is this going", "what comes next",
    or planning needs the likely evolution of current patterns.
    Projections are honest: derived from trajectory dynamics, each
    scenario carries probability — not prophecy.
    DO NOT use for what already happened — ``timeline`` or ``recall``.

    Args:
        horizon_days: How far ahead to project.
        history_days: How much history feeds the projection.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the detected "trajectory" and projected scenarios
        with probabilities.
    """
    return traducir_payload(await _es._proyectar(horizon_days, history_days, workspace))


@mcp_server.tool()
async def forget_event(event_id: str) -> dict[str, Any]:
    """Hard-forgets an event: removes it from evocable memory.

    USE WHEN: the user explicitly asks to delete a memory, or data
    should not be retained. Directed forgetting — the event stops
    being retrievable.
    Prefer ``forget_gradually`` for "make this less prominent"
    rather than deletion.

    Args:
        event_id: UUID of the event to forget.

    Returns:
        Dict with confirmation of the directed forgetting.
    """
    return traducir_payload(await _es._olvidar(event_id))


@mcp_server.tool()
async def list_tags(
    limit: int = 100,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists the tags in use, with usage counts.

    USE WHEN: exploring how memory is organized, or checking whether a
    topic tag already exists before writing.
    DO NOT use to read tagged content — use ``recall`` with the tag.

    Args:
        limit: Max tags to return, ordered by usage.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of {"tag", usage count} dicts.
    """
    return traducir_payload(await _es._tags(min(max(limit, 1), 500), workspace))


@mcp_server.tool()
async def list_sources(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lists the provenance sources recorded in memory.

    USE WHEN: auditing where memories came from (agents, imports,
    channels) — useful for trust decisions via ``trust_source``.
    DO NOT confuse with workspaces: a source is WHO wrote; a
    workspace is WHERE it lives.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of sources with counts.
    """
    return traducir_payload(await _es._fuentes(workspace))


@mcp_server.tool()
async def branch_source(
    name: str,
    branch: str,
    namespace: str = "",
) -> dict[str, Any] | None:
    """Forks a source's timeline into a parallel branch.

    USE WHEN: a source's history should diverge into an alternate
    universe line (scenarios, migrations, experiments) without
    touching the main line.

    Args:
        name: Source identifier to branch.
        branch: Target branch name.
        namespace: Optional namespace prefix.

    Returns:
        Dict with the created branch metadata.
    """
    return traducir_payload(await _es._ramificar_fuente(name, branch, namespace))


@mcp_server.tool()
async def list_relations(
    limit: int = 500,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists explicit relations between events (the memory graph).

    USE WHEN: inspecting how memories connect — links created by
    ``related_ids``, supersede chains, contradictions and
    co-activation edges.
    DO NOT use for entity-centric view — ``entity``.

    Args:
        limit: Max relations to return.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of relations with "origin_id", "target_id", "kind".
    """
    return traducir_payload(await _es._relaciones(limit, workspace))


@mcp_server.tool()
async def entity(
    name: str,
    max_mentions: int = 8,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Entity card: everything memory knows about a name.

    USE WHEN: the query is about a concrete person, project or thing
    ("who is Samay", "what is the ruba project"). Returns the entity
    card: synthesized attributes, relation graph, temporal span and
    recent mentions.
    DO NOT use for thematic questions — ``recall``.

    Args:
        name: Entity name (case-insensitive).
        max_mentions: Recent mentions to include.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "found", "name", "card", "graph", "mentions",
        "backlinks" and temporal "span".
    """
    return traducir_payload(await _es._entidad(name, max_mentions, workspace))


@mcp_server.tool()
async def read_preference(key: str) -> dict[str, Any]:
    """Reads a stored preference/configuration document by key.

    USE WHEN: retrieving durable user settings or structured docs
    persisted via ``save_preference`` (e.g. persona banks, playbooks).

    Args:
        key: Preference key.

    Returns:
        Dict with "key" and the stored "value" (or found=false).
    """
    # El documento es libre del usuario: no se traduce (ver contract.py).
    resultado: dict[str, Any] = await _es._preferencia_leer(key)
    return resultado


@mcp_server.tool()
async def save_preference(key: str, value: dict[str, Any]) -> dict[str, Any]:
    """Persists a preference/configuration document under a key.

    USE WHEN: saving durable structured settings — persona answers,
    playbooks, calibration tweaks. For narrative memories use
    ``remember`` instead.

    Args:
        key: Preference key.
        valor: JSON-serializable document.

    Returns:
        Dict with "key" and "saved": true.
    """
    resultado: dict[str, Any] = await _es.guardar_preferencia(key, value)

    return resultado


@mcp_server.tool()
async def list_workspaces() -> list[dict[str, Any]]:
    """Lists the workspaces the caller's identity can see.

    USE WHEN: choosing where a memory belongs, or checking which
    workspaces exist for the current key.

    Returns:
        List of {"slug", "name", "role"} dicts.
    """
    resultado: list[dict[str, Any]] = await _es.listar_workspaces()

    return resultado


@mcp_server.tool()
async def create_workspace(slug: str, name: str) -> dict[str, Any]:
    """Creates a new memory workspace (tenant).

    USE WHEN: a new project/team/domain needs isolated memory with its
    own members and scopes. The creator becomes owner.
    DO NOT confuse with branches: a workspace isolates tenants; a
    branch forks timelines inside one.

    Args:
        slug: URL-safe identifier ("client-ruba").
        name: Human-readable name.

    Returns:
        Dict with the created workspace.
    """
    resultado: dict[str, Any] = await _es.crear_workspace(slug, name)

    return resultado


@mcp_server.tool()
async def list_members(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lists workspace members and their roles.

    USE WHEN: auditing who can read/write a workspace's memory.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of {"handle", "role"} member dicts.
    """
    resultado: list[dict[str, Any]] = await _es.listar_miembros(workspace)

    return resultado


@mcp_server.tool()
async def invite_member(
    role: str = "member",
    days: int = 7,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Generates a single-use invitation key for the workspace.

    USE WHEN: onboarding a person or agent into shared memory —
    the invite key (``ns_inv_*``) is redeemed by the new member to
    create their user + first key. Requires owner/admin role.

    Args:
        role: Granted role — "member" (default), "admin" or "viewer".
        days: Invitation expiry in days.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the "token" invitation key and expiry.
    """
    resultado: dict[str, Any] = await _es.invitar_miembro(role, days, workspace)

    return resultado


@mcp_server.tool()
async def change_member_role(
    handle: str,
    role: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Changes a workspace member's role. Requires owner/admin.

    USE WHEN: promoting or demoting a member ("viewer" → "member").

    Args:
        handle: Member handle (from ``list_members``).
        role: New role — "admin", "member" or "viewer".
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "handle" and new "role".
    """
    resultado: dict[str, Any] = await _es.cambiar_rol_miembro(handle, role, workspace)

    return resultado


@mcp_server.tool()
async def remove_member(handle: str, workspace: str | None = None) -> dict[str, Any]:
    """Removes a member from the workspace. Requires owner/admin.

    USE WHEN: revoking a member's access to shared memory — their
    events remain attributed but they lose visibility.

    Args:
        handle: Member handle (from ``list_members``).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "handle" and "removed": true.
    """
    resultado: dict[str, Any] = await _es.quitar_miembro(handle, workspace)

    return resultado


@mcp_server.tool()
async def create_intention(
    intention: str,
    at: str | int | None = None,
    when: str | None = None,
    threshold: float = 0.6,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Prospective memory: an intention that fires later.

    USE WHEN: "remind me to X in 3d" (time trigger via ``at``) or
    "tell me when Y happens" (event trigger via ``when``). The
    intention fires when consulting Nous (e.g. ``bootstrap_context``)
    or when the trigger event arrives — there is no scheduler or
    push notification; warn the user of this.
    DO NOT use for pending promises — ``remember`` kind="commitment".

    Args:
        intention: What to remember, in text ("call the dentist").
        at: Time trigger — "3d", "12h", Unix epoch or ISO date.
        when: Event trigger — text of the event that should fire it.
        threshold: Similarity required for an event trigger to match.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the created intention ("id", "trigger_kind",
        "trigger_value").
    """
    return traducir_payload(await _es._crear_intencion(intention, at, when, threshold, workspace))


@mcp_server.tool()
async def list_intentions(
    fired: bool | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists prospective intentions, optionally filtered by state.

    USE WHEN: checking pending reminders ("what's scheduled?") or
    auditing which already fired.

    Args:
        fired: None = only pending; True = only fired; False same as
            None for backward compatibility.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of intentions with "id", "intention", "trigger_kind",
        "trigger_value" and "status".
    """
    return traducir_payload(await _es._listar_intenciones(fired, workspace))


@mcp_server.tool()
async def cancel_intention(intention_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Cancels a pending intention before it fires.

    USE WHEN: the user drops a reminder ("never mind the dentist").

    Args:
        intention_id: UUID of the intention (from ``list_intentions``).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id" and "cancelled": true.
    """
    return traducir_payload(await _es._cancelar_intencion(intention_id, workspace))


@mcp_server.tool()
async def consolidate_memory(workspace: str | None = None) -> dict[str, Any]:
    """Sleep cycle: consolidation over recent memory.

    USE WHEN: a consolidation pass is due (typically nightly or after
    heavy writing) — promotes hot candidates, reinforces useful
    traces, archives the stale, extracts semantic gists from related
    episodes and produces REM pairs. Returns the readable report.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Consolidation report: "promoted", "strengthened", "archived",
        "gists", "rem" pairs and the legible "summary".
    """
    return traducir_payload(await _es._consolidar(workspace))


@mcp_server.tool()
async def list_consolidation_reports(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists past consolidation (sleep) reports.

    USE WHEN: auditing what previous sleep cycles did — what was
    promoted, archived or semanticized, and when.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of stored consolidation reports.
    """
    resultado: list[dict[str, Any]] = await _es.listar_reportes_consolidacion(workspace)

    return resultado


@mcp_server.tool()
async def mine_scripts(workspace: str | None = None) -> dict[str, Any]:
    """Mines recurring event sequences (behavioral scripts).

    USE WHEN: detecting routines in the timeline — repeated patterns
    that could become procedural memories or predictions.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "detected" script candidates and mining stats.
    """
    return traducir_payload(await _es._minar_scripts(workspace))


@mcp_server.tool()
async def list_scripts(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lists mined behavioral scripts.

    USE WHEN: reviewing the recurring sequences detected by
    ``mine_scripts`` — steps, support, confidence.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of scripts with "steps" and "support".
    """
    return traducir_payload(await _es._listar_scripts(workspace))


@mcp_server.tool()
async def list_archived(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lists archived memories (consolidated out of active recall).

    USE WHEN: checking what sleep cycles archived — archived memories
    still exist but cost more to reach; ``restore_event`` brings one
    back to active recall.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of archived event dicts.
    """
    return traducir_payload(await _es._listar_archivados(workspace))


@mcp_server.tool()
async def restore_event(event_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Restores an archived memory to active recall.

    USE WHEN: an archived event becomes relevant again — restoring
    lifts it out of the archive.

    Args:
        event_id: UUID of the archived event (from ``list_archived``).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id" and "restored": true.
    """
    return traducir_payload(await _es._restaurar_evento(event_id, workspace))


@mcp_server.tool()
async def ask_decision(
    dilemma: str,
    options: list[str] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Structured decision support over the memory and censor state.

    USE WHEN: the user faces a choice and wants the memory-informed
    view — relevant precedents, matching censors, risks and a scored
    read of each option.
    DO NOT use as an oracle — it organizes evidence; the human decides.

    Args:
        dilemma: The decision at hand, in text.
        options: Candidate options; None lets the engine derive them.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the scored "options", matching "censors",
        "hidden_risk" flags and a "recommendation".
    """
    return traducir_payload(await _es._consultar_decision(dilemma, options, workspace))


@mcp_server.tool()
async def save_draft(
    content: str,
    dimension: Literal["cognitive", "somatic", "behavioral"] = "cognitive",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Working-memory buffer: saves a draft with TTL (Baddeley buffer).

    USE WHEN: holding intermediate state — a partial answer, a plan in
    progress — that should expire unless rehearsed into real memory.
    Drafts decay unless ``rehearse_draft`` promotes them.
    DO NOT use for durable memories — ``remember``.

    Args:
        content: Draft text.
        dimension: Buffer channel — "cognitive" (default), "somatic"
            or "behavioral".
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id", expiry and remaining buffer "capacity".
    """
    dim_es = dimension_es(dimension) or "cognitiva"
    return traducir_payload(await _es._recordar_borrador(content, dim_es, workspace))


@mcp_server.tool()
async def list_drafts(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lists live working-memory drafts (with TTL countdown).

    USE WHEN: checking what the buffer currently holds before it
    expires.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of draft dicts with remaining TTL and "rehearsals".
    """
    return traducir_payload(await _es._listar_borrador(workspace))


@mcp_server.tool()
async def rehearse_draft(draft_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Rehearses a draft: refreshes TTL and may promote it to memory.

    USE WHEN: a draft keeps being useful — each rehearsal renews it;
    enough rehearsals promote it to durable memory automatically.

    Args:
        draft_id: UUID of the draft (from ``list_drafts``).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id", refreshed TTL and "promoted" when applicable.
    """
    return traducir_payload(await _es._ensayar_borrador(draft_id, workspace))


@mcp_server.tool()
async def discard_draft(draft_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Drops a working-memory draft immediately.

    USE WHEN: buffered state is no longer needed.

    Args:
        draft_id: UUID of the draft.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id" and "discarded": bool.
    """
    return traducir_payload(await _es._descartar_borrador(draft_id, workspace))


@mcp_server.tool()
async def forget_gradually(
    event_id: str,
    forget: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Directed forgetting flag: marks an event to decay faster.

    USE WHEN: a memory should fade — "stop bringing this up" without
    hard-deleting it (``forget_event``). The flag steepens decay;
    reversible by passing forget=false.

    Args:
        event_id: UUID of the event.
        forget: True to flag for faster decay, False to unflag.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id" and the "directed_forgetting" flag state.
    """
    return traducir_payload(await _es._olvidar_gradual(event_id, forget, workspace))


@mcp_server.tool()
async def pending_reviews(
    limit: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Spaced-repetition reviews due now (SRS over memories).

    USE WHEN: surfacing memories scheduled for re-exposure — the
    spacing effect keeps important traces strong via periodic review.

    Args:
        limit: Max pending reviews.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of due reviews with "next_review" timestamps.
    """
    return traducir_payload(await _es._repasos_pendientes(limit, workspace))


@mcp_server.tool()
async def snooze_intention(
    intention_id: str,
    seconds: int = 3600,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Snoozes a fired or pending intention by N seconds.

    USE WHEN: a reminder fired too early — "not now, ask me later".

    Args:
        intention_id: UUID of the intention.
        seconds: Snooze duration (default 1 hour).
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id", "snoozed_until" and "seconds".
    """
    return traducir_payload(await _es._aplazar_intencion(intention_id, seconds, workspace))


@mcp_server.tool()
async def trust_source(
    name: str,
    trusted: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Marks a provenance source as trustworthy (or not).

    USE WHEN: a source earns or loses trust — trusted sources lift
    quarantined events and boost recall confidence; untrusted ones
    keep their memories flagged.

    Args:
        name: Source identifier (from ``list_sources``).
        trusted: True to trust, False to revoke.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "source", "trusted" and "released" count of events
        lifted from quarantine.
    """
    return traducir_payload(await _es._confiar_fuente(name, trusted, workspace))


@mcp_server.tool()
async def list_quarantine(
    limit: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists quarantined memories (untrusted-source hold).

    USE WHEN: reviewing what memory holds pending trust — quarantined
    events are retrievable but flagged until their source is trusted
    (``trust_source``) or they earn reinforcement.

    Args:
        limit: Max quarantined events.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of quarantined event dicts.
    """
    return traducir_payload(await _es._listar_cuarentena(limit, workspace))


@mcp_server.tool()
async def start_session(
    agent: str | None = None,
    budget: int = 4000,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Opens a session boundary and returns the bootstrap pack.

    USE WHEN: a work session starts — the session boundary is where
    memory pays off: it records the opening event, fires due
    intentions and returns the budgeted context pack (same data as
    ``bootstrap_context`` plus session bookkeeping for the handoff).

    Args:
        agent: Calling agent name (audit trail).
        budget: Token budget for the bootstrap pack.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Bootstrap pack plus session "id" — pass it to
        ``end_session``/``get_handoff`` when closing.
    """
    return traducir_payload(await _es._iniciar_sesion(agent, budget, workspace))


@mcp_server.tool()
async def end_session(
    session_id: str | None = None,
    summary: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Closes a session with an optional summary (generates handoff).

    USE WHEN: the work session ends — closing records the boundary,
    stores the summary for the next session's handoff and fires
    anything pending at the boundary.

    Args:
        session_id: UUID from ``start_session``; None closes the most
            recent open session.
        summary: Free-text session summary for the handoff.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the closed session and handoff metadata.
    """
    return traducir_payload(await _es._cerrar_sesion(session_id, summary, workspace))


@mcp_server.tool()
async def get_handoff(
    session_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Session continuity document: what the last session left.

    USE WHEN: resuming work — returns the handoff the previous session
    generated: open threads, decisions made, where things stand.

    Args:
        session_id: UUID of a specific session; None uses the latest.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "handoff" content and session metadata.
    """
    return traducir_payload(await _es._obtener_handoff(session_id, workspace))


@mcp_server.tool()
async def list_sessions(
    limit: int = 20,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists recent work sessions.

    USE WHEN: auditing session history — what agent worked when,
    which sessions produced handoffs.

    Args:
        limit: Max sessions.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of session dicts with "agent", "status", times and
        "has_handoff".
    """
    return traducir_payload(await _es._listar_sesiones(limit, workspace))


@mcp_server.tool()
async def create_censor(
    rule: str,
    severity: str = "warn",
    domain: str | None = None,
    scope: Literal["personal", "workspace"] = "personal",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registers a pre-action censor rule (Neuro-5 verification).

    USE WHEN: the user sets a guardrail — "before deleting data ask
    me", "warn before touching production". Matching censors are
    evaluated by ``verify_action`` before an agent acts.

    Args:
        rule: The guardrail text ("never force-push to main").
        severity: "warn" (advisory) or "block" (halt the action).
        domain: Optional scope word limiting where it applies.
        scope: "personal" (default) or "workspace".
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the created censor.
    """
    return traducir_payload(
        await _es._crear_censor(rule, severidad_es(severity) or severity, domain, scope, workspace)
    )


@mcp_server.tool()
async def list_censores(workspace: str | None = None) -> list[dict[str, Any]]:
    """Lists registered censor rules.

    USE WHEN: reviewing active guardrails — what the agent must check
    before acting.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of censors with "rule", "severity", "domain", "status".
    """
    return traducir_payload(await _es._listar_censores(workspace))


@mcp_server.tool()
async def verify_action(action: str, workspace: str | None = None) -> dict[str, Any]:
    """Pre-action verdict: do censors allow this action?

    USE WHEN: before executing anything potentially sensitive —
    matches the action text against registered censors and returns a
    verdict ("proceed" | "caution" | "halt") with the matched rules.

    Args:
        action: Description of the intended action.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "verdict", matched "censors" and reasoning.
    """
    return traducir_payload(await _es._verificar_accion(action, workspace))


@mcp_server.tool()
async def register_prediction(
    claim: str,
    confidence: float,
    event_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registers a probabilistic prediction for calibration tracking.

    USE WHEN: the agent asserts something with a probability ("the
    deploy will succeed", "she'll prefer option A") — the claim gets
    resolved later by ``resolve_prediction`` and feeds the Brier /
    reliability curve in ``calibration_report``.

    Args:
        claim: What is predicted, in text.
        confidence: Declared probability (0.0–1.0).
        event_id: UUID of a related event, if any.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the registered prediction.
    """
    return traducir_payload(await _es._registrar_prediccion(claim, confidence, event_id, workspace))


@mcp_server.tool()
async def resolve_prediction(
    prediction_id: str,
    hit: bool,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Closes the loop: reality said whether the prediction hit.

    USE WHEN: the outcome of a registered prediction is known —
    resolving feeds calibration scoring.

    Args:
        prediction_id: UUID of the prediction.
        hit: True if it happened, False if it did not.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with the resolved prediction and its "status".
    """
    return traducir_payload(await _es._resolver_prediccion(prediction_id, hit, workspace))


@mcp_server.tool()
async def list_predictions(
    pending: bool | None = None,
    limit: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists registered predictions, optionally only pending ones.

    USE WHEN: reviewing open predictions awaiting resolution, or
    auditing resolved ones.

    Args:
        pending: True = only unresolved; None/False = all.
        limit: Max predictions.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of predictions with "claim", "confidence", "status".
    """
    return traducir_payload(await _es._listar_predicciones(pending, limit, workspace))


@mcp_server.tool()
async def calibration_report(workspace: str | None = None) -> dict[str, Any]:
    """Calibration quality: Brier score + reliability curve.

    USE WHEN: auditing how calibrated the agent's declared
    probabilities are — are 80% claims right ~80% of the time?
    Requires resolved predictions (``resolve_prediction``).

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "brier", reliability "curve", "resolved" count and
        overconfidence bias.
    """
    return traducir_payload(await _es._reporte_calibracion(workspace))


@mcp_server.tool()
async def pending_approvals(
    limit: int = 50,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    """Lists staged events awaiting human approval.

    USE WHEN: reviewing the approval queue — events flagged staged
    (e.g. parametric claims, sensitive content) that need a human
    approve/reject before joining normal recall.

    Args:
        limit: Max pending events.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        List of staged events pending approval.
    """
    return traducir_payload(await _es._pendientes_aprobacion(limit, workspace))


@mcp_server.tool()
async def approve_event(event_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Approves a staged event: joins normal memory.

    USE WHEN: a human confirms a staged memory is valid — approval
    lifts the parametric confidence cap and clears the staged flag.

    Args:
        event_id: UUID of the staged event.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id" and "approved": true.
    """
    return traducir_payload(await _es._aprobar_evento(event_id, workspace))


@mcp_server.tool()
async def reject_event(event_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Rejects a staged event: it does not join normal memory.

    USE WHEN: a human vets a staged memory as wrong — rejection marks
    it so it stops surfacing.

    Args:
        event_id: UUID of the staged event.
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with "id" and "rejected": true.
    """
    return traducir_payload(await _es._rechazar_evento(event_id, workspace))


@mcp_server.tool()
async def approve_all_pending(workspace: str | None = None) -> dict[str, Any]:
    """Batch-approves every staged event in the queue.

    USE WHEN: the human reviews the queue and clears it wholesale —
    use ``approve_event``/``reject_event`` for per-item review.

    Args:
        workspace: Slug or UUID; None uses the key's default.

    Returns:
        Dict with count of "approved" events.
    """
    return traducir_payload(await _es._aprobar_pendientes(workspace))
