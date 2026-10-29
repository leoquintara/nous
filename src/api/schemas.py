"""Pydantic schemas for the REST API."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class HealthResponse(BaseModel):
    """Response of the /health endpoint."""

    status: str


class BenchmarkResponse(BaseModel):
    """A persisted benchmark run."""

    id: str
    name: str
    scenario: str | None
    params: dict[str, Any]
    metrics: dict[str, Any]
    git_commit: str | None
    created_at: datetime


class LoginRequest(BaseModel):
    """Alternative access credentials for the dashboard."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Login response: the service token to use as Bearer."""

    token: str


class EventRequest(BaseModel):
    """Body to register an event via the REST API."""

    content: str = Field(min_length=1, max_length=100_000)
    dimension: Literal["cognitive", "somatic", "behavioral"] | None = None
    source: str = Field(default="dashboard", min_length=1, max_length=50)
    amplitude: float = Field(default=0.5, ge=0.0, le=1.0)
    frequency: float | None = Field(default=None, gt=0.0)
    timestamp_unix: int | None = Field(default=None, ge=0)
    related_ids: list[str] | None = Field(default=None, max_length=20)
    tags: list[str] | None = Field(default=None, max_length=12)
    branch: str = Field(default="main", min_length=1, max_length=64)
    kind: Literal[
        "event",
        "fact",
        "belief",
        "commitment",
        "preference",
        "censor",
    ] = "event"
    ttl: str | int | None = Field(default=None)
    supersedes_id: str | None = Field(default=None)
    with_whom: str | None = Field(default=None, max_length=128)
    scope: Literal["personal", "workspace"] = "personal"
    workspace: str | None = Field(default=None, max_length=64)
    # ADR-003: user-declared fields (the automatic estimator
    # does not overwrite them — see fijar_tipo_memoria).
    memory_kind: Literal["episodic", "semantic", "procedural"] | None = None
    emotional_state: float | None = Field(default=None, ge=-1.0, le=1.0)
    context: dict[str, Any] | None = Field(default=None)
    # Neuro-6: True = content asserted from the agent's weights,
    # not observed — epistemic ceiling in recall.
    parametric: bool = False

    @field_validator("context")
    @classmethod
    def _context_bounded(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """Rejects contexts >4KB — the column is JSON, not a blob."""
        if value is None:
            return None
        if len(json.dumps(value, default=str)) > 4_096:
            raise ValueError("context exceeds 4KB serialized")
        return value


class CloseCommitmentRequest(BaseModel):
    """Body to close an open commitment."""

    resolution: str = Field(min_length=1, max_length=10_000)
    source: str = Field(default="dashboard", min_length=1, max_length=50)


class GoalRequest(BaseModel):
    """Body to set a goal on the universe line."""

    title: str = Field(min_length=1, max_length=200)
    target_date: int = Field(ge=0)
    dimension: Literal["cognitive", "somatic", "behavioral"] | None = None
    description: str | None = Field(default=None, max_length=10_000)
    branch: str = Field(default="main", min_length=1, max_length=64)
    scope: Literal["personal", "workspace"] = "personal"
    workspace: str | None = Field(default=None, max_length=64)


class GoalPatch(BaseModel):
    """Partial changes to an existing goal."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    target_date: int | None = Field(default=None, ge=0)
    dimension: Literal["cognitive", "somatic", "behavioral"] | None = None
    status: Literal["active", "fulfilled", "abandoned"] | None = None


class CloseGoalRequest(BaseModel):
    """Body to close a goal (fulfilled/abandoned)."""

    status: Literal["fulfilled", "abandoned"]
    resolution: str | None = Field(default=None, max_length=10_000)
    source: str = Field(default="dashboard", min_length=1, max_length=50)


class RelationResponse(BaseModel):
    """Link between two events on the universe line."""

    origin_id: str
    target_id: str
    kind: str
    weight: float


class EventResponse(BaseModel):
    """Persisted event on the universe line."""

    id: str
    timestamp_unix: int
    dimension: str
    source: str | None = None
    content: str
    amplitude: float
    frequency: float | None = None
    reinforcements: int = 0
    tags: list[str] = []
    branch: str = "main"
    kind: str = "event"
    expires_at: int | None = None
    superseded_by: str | None = None
    with_whom: str | None = None
    scope: str = "personal"
    workspace_id: str | None = None
    author_id: str | None = None
    # ADR-003 — memory patterns (optional for compatibility).
    memory_kind: str | None = None
    emotional_state: float | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
    archived: bool = False
    curated: bool = False


class TagResponse(BaseModel):
    """Aggregated tag with its usage count."""

    tag: str
    events: int


class SourceRequest(BaseModel):
    """Body to register a data source."""

    name: str = Field(min_length=1, max_length=64)
    kind: Literal["manual", "obsidian", "gbrain", "memlord", "agent", "import"] = "manual"
    namespace: str = Field(default="", max_length=128)
    origin_branch: str = Field(default="main", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourcePatch(BaseModel):
    """State change for a source (activate/archive)."""

    status: Literal["active", "archived"]


class BranchRequest(BaseModel):
    """Creates a universe-line branch from a source."""

    branch: str = Field(min_length=1, max_length=64)
    namespace: str = Field(default="", max_length=128)


class SourceResponse(BaseModel):
    """Registered source with its real event count."""

    name: str
    kind: str
    status: str
    namespace: str
    origin_branch: str
    metadata: dict[str, Any]
    events: int


class AIProviderRequest(BaseModel):
    """Body to configure an external AI provider.

    ``api_key`` is optional on updates: when absent, the already
    encrypted one is kept. ``include_content`` authorizes sending
    memory fragments to the endpoint — by default only statistical
    aggregates travel.
    """

    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=300)
    model: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(default=None, max_length=300)
    enabled: bool = True
    include_content: bool = False
    default: bool = False
    fallback_id: str | None = Field(default=None, max_length=40)


class PredictiveCandidate(BaseModel):
    """Heuristic candidate the LLM can re-weight."""

    id: str = Field(max_length=80)
    name: str = Field(max_length=80)
    probability: float = Field(ge=0.0, le=1.0)
    description: str = Field(default="", max_length=400)


class PredictRequest(BaseModel):
    """Body of an AI prediction request."""

    origin_unix: int | None = Field(default=None, ge=0)
    horizon_days: int = Field(default=30, ge=1, le=365)
    candidates: list[PredictiveCandidate] | None = Field(default=None, max_length=12)
    provider_id: str | None = Field(default=None, max_length=40)


class CallResponse(BaseModel):
    """A registered call to Nous (metadata, never content)."""

    id: str
    ts: int
    method: str
    route: str
    origin: str
    agent: str
    latency_ms: int
    status: int
    is_ping: bool


class CallRouteStats(BaseModel):
    """Call count and mean latency aggregated by route."""

    route: str
    calls: int
    mean_latency: int


class CallOriginStats(BaseModel):
    """Call count aggregated by origin."""

    origin: str
    calls: int


class PingsStats(BaseModel):
    """Summary of the pings/liveness probes registry."""

    total: int
    last: int | None


class CallsStatsResponse(BaseModel):
    """Aggregates of the call registry for the panel."""

    total: int
    by_route: list[CallRouteStats]
    by_origin: list[CallOriginStats]
    pings: PingsStats


class ImportRequest(BaseModel):
    """Parameters of a memory migration into Nous.

    ``path`` is a file or directory on the machine running the
    backend — local-first: the migration never leaves the device.
    For ``gbrain``/``memlord`` you may pass the MCP server ``url``
    (streamable-HTTP) and a Bearer ``token``: Nous downloads the
    memories directly from the instance instead of reading an
    export.
    """

    format: Literal["auto", "jsonl", "json", "obsidian", "gbrain", "memlord"]
    path: str | None = Field(default=None, min_length=1, max_length=500)
    url: str | None = Field(default=None, min_length=8, max_length=500)
    token: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=50)
    branch: str | None = Field(default=None, max_length=64)
    preview: bool = False


class ImportResponse(BaseModel):
    """Result of a memory migration.

    In ``preview`` mode nothing is written: ``new``/``skipped``
    anticipate what a real execution would do.
    """

    imported: int
    skipped: int
    errors: int
    links: int
    format: str
    source: str
    preview: bool = False
    new: int | None = None


class ImportHistoryEntry(BaseModel):
    """Entry of the executed migrations history."""

    ts: int
    format: str
    source: str
    path: str
    result: dict[str, Any]


# ── Multi-tenant identity ───────────────────────────────────────


class UserSummary(BaseModel):
    """Public data of a user."""

    id: str
    handle: str
    name: str | None = None
    email: str | None = None


class WorkspaceSummary(BaseModel):
    """Workspace with the caller's role in it."""

    id: str
    slug: str
    name: str
    role: str


class LicenseSummary(BaseModel):
    """Current usage against the free-edition limits."""

    licensed: bool
    users: int
    max_users: int | None
    workspaces: int
    max_workspaces: int | None
    tier: str = "oss"
    features: list[str] = []


class IdentityResponse(BaseModel):
    """Who the caller is and which workspaces they belong to."""

    user: UserSummary | None
    active_workspace: WorkspaceSummary
    workspaces: list[WorkspaceSummary]
    license: LicenseSummary | None = None


class WorkspaceRequest(BaseModel):
    """Body to create a new workspace."""

    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class MemberSummary(BaseModel):
    """Workspace member with their role."""

    user_id: str
    handle: str
    name: str | None
    role: str


class MemberPatch(BaseModel):
    """Member role change (owner is not assignable)."""

    role: Literal["member", "viewer"]


class InvitationRequest(BaseModel):
    """Body to issue a redeemable invitation."""

    role: Literal["member", "viewer"] = "member"
    days: int = Field(default=7, ge=1, le=30)


class InvitationResponse(BaseModel):
    """Invitation token in the clear — shown only once."""

    token: str
    expires_days: int


class RedeemRequest(BaseModel):
    """Invitation redemption: creates the guest's user."""

    token: str = Field(min_length=8, max_length=128)
    handle: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)


class RedeemResponse(BaseModel):
    """Redemption result: the new user's key in the clear."""

    handle: str
    key: str


class KeyRequest(BaseModel):
    """Body to issue a new own key."""

    name: str | None = Field(default=None, max_length=80)
    workspace_default_id: str | None = Field(default=None)


class KeyResponse(BaseModel):
    """Key metadata (never the hash nor the secret)."""

    id: str
    prefix: str
    name: str | None
    workspace_default_id: str | None
    revoked: bool
    last_used: datetime | None
    created_at: datetime


class IntentionRequest(BaseModel):
    """Body to register a prospective intention (ADR-003)."""

    intention: str = Field(min_length=1, max_length=2_000)
    at: str | int | None = Field(default=None)
    when: str | None = Field(default=None, max_length=500)
    threshold: float = Field(default=0.6, gt=0.0, le=1.0)
    workspace: str | None = Field(default=None, max_length=64)


class DecisionRequest(BaseModel):
    """Body for the prescriptive advisor (ADR-002 Level 2, Pro)."""

    dilemma: str = Field(min_length=1, max_length=2_000)
    options: list[str] | None = Field(default=None, max_length=6)
    workspace: str | None = Field(default=None, max_length=64)


class CurateRequest(BaseModel):
    """Body to curate/uncurate an event (forgetting immunity)."""

    curated: bool = True


class ActivateLicenseRequest(BaseModel):
    """Body to activate a license key from the dashboard (ADR-001)."""

    key: str = Field(min_length=8, max_length=4_096)


class DraftRequest(BaseModel):
    """Body to save a volatile thought (working buffer)."""

    content: str = Field(min_length=1, max_length=4_000)
    dimension: Literal["cognitive", "somatic", "behavioral"] = "cognitive"
    workspace: str | None = Field(default=None, max_length=64)


class DirectedForgettingRequest(BaseModel):
    """Body to mark/unmark directed forgetting (Neuro-3)."""

    forget: bool = True
    workspace: str | None = Field(default=None, max_length=64)


class ArchiveRequest(BaseModel):
    """Body to manually archive an event — reversible forgetting."""

    reason: str | None = Field(default=None, max_length=200)
    workspace: str | None = Field(default=None, max_length=64)


class SnoozeRequest(BaseModel):
    """Body to postpone a prospective intention (snooze)."""

    seconds: int = Field(default=3600, ge=0, le=31_536_000)
    workspace: str | None = Field(default=None, max_length=64)


class TrustSourceRequest(BaseModel):
    """Body to approve/quarantine a source (Neuro-3)."""

    trusted: bool = True
    workspace: str | None = Field(default=None, max_length=64)


class SessionStartRequest(BaseModel):
    """Body to open a session — bootstrap + boundary (Neuro-5)."""

    agent: str | None = Field(default=None, max_length=80)
    budget: int = Field(default=4000, ge=0, le=100_000)
    workspace: str | None = Field(default=None, max_length=64)


class SessionEndRequest(BaseModel):
    """Body to close a session — generates the handoff."""

    session_id: str | None = Field(default=None, max_length=40)
    summary: str | None = Field(default=None, max_length=4_000)
    workspace: str | None = Field(default=None, max_length=64)


class CensorRequest(BaseModel):
    """Body to register a guardrail (learned rule)."""

    rule: str = Field(min_length=1, max_length=2_000)
    severity: Literal["block", "warn"] = "warn"
    domain: str | None = Field(default=None, max_length=80)
    scope: Literal["personal", "workspace"] = "personal"
    workspace: str | None = Field(default=None, max_length=64)


class VerifyRequest(BaseModel):
    """Body to verify an action against the censors."""

    action: str = Field(min_length=1, max_length=2_000)
    workspace: str | None = Field(default=None, max_length=64)


class PredictionRequest(BaseModel):
    """Body to declare a prediction with confidence (Brier)."""

    claim: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0.0, le=1.0)
    event_id: str | None = Field(default=None, max_length=40)
    workspace: str | None = Field(default=None, max_length=64)


class ResolvePredictionRequest(BaseModel):
    """Body to resolve a prediction — closes the loop."""

    hit: bool
    workspace: str | None = Field(default=None, max_length=64)
