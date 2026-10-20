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
    scope: Literal["personal", "workspace"] = "personal"
    workspace: str | None = Field(default=None, max_length=64)


class CerrarLoopRequest(BaseModel):
    """Cuerpo para cerrar un compromiso abierto."""

    resolucion: str = Field(min_length=1, max_length=10_000)
    fuente: str = Field(default="dashboard", min_length=1, max_length=50)


class ObjetivoRequest(BaseModel):
    """Cuerpo para fijar una meta en la línea de universo."""

    titulo: str = Field(min_length=1, max_length=200)
    fecha_objetivo: int = Field(ge=0)
    dimension: Literal["cognitiva", "somatica", "conductual"] | None = None
    descripcion: str | None = Field(default=None, max_length=10_000)
    rama: str = Field(default="main", min_length=1, max_length=64)
    scope: Literal["personal", "workspace"] = "personal"
    workspace: str | None = Field(default=None, max_length=64)


class ObjetivoPatch(BaseModel):
    """Cambios parciales sobre un objetivo existente."""

    titulo: str | None = Field(default=None, min_length=1, max_length=200)
    descripcion: str | None = Field(default=None, max_length=10_000)
    fecha_objetivo: int | None = Field(default=None, ge=0)
    dimension: Literal["cognitiva", "somatica", "conductual"] | None = None
    estado: Literal["activo", "cumplido", "abandonado"] | None = None


class CerrarObjetivoRequest(BaseModel):
    """Cuerpo para cerrar un objetivo (cumplido/abandonado)."""

    estado: Literal["cumplido", "abandonado"]
    resolucion: str | None = Field(default=None, max_length=10_000)
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
    scope: str = "personal"
    workspace_id: str | None = None
    autor_id: str | None = None


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


class ProveedorIARequest(BaseModel):
    """Cuerpo para configurar un proveedor de IA externo.

    ``api_key`` es opcional en actualizaciones: si no viene, se
    conserva la ya cifrada. ``incluir_contenido`` autoriza enviar
    fragmentos de memorias al endpoint — por defecto solo viajan
    agregados estadísticos.
    """

    nombre: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=300)
    modelo: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(default=None, max_length=300)
    habilitado: bool = True
    incluir_contenido: bool = False
    predeterminado: bool = False
    fallback_id: str | None = Field(default=None, max_length=40)


class CandidatoPredictivo(BaseModel):
    """Candidato heurístico que el LLM puede re-ponderar."""

    id: str = Field(max_length=80)
    nombre: str = Field(max_length=80)
    probabilidad: float = Field(ge=0.0, le=1.0)
    descripcion: str = Field(default="", max_length=400)


class PredecirRequest(BaseModel):
    """Cuerpo de la petición de predicción con IA."""

    origen_unix: int | None = Field(default=None, ge=0)
    horizonte_dias: int = Field(default=30, ge=1, le=365)
    candidatos: list[CandidatoPredictivo] | None = Field(default=None, max_length=12)
    proveedor_id: str | None = Field(default=None, max_length=40)


class LlamadaResponse(BaseModel):
    """Una llamada registrada a Nous (metadata, nunca contenido)."""

    id: str
    ts: int
    metodo: str
    ruta: str
    origen: str
    agente: str
    latencia_ms: int
    status: int
    es_ping: bool


class LlamadaRutaStats(BaseModel):
    """Conteo y latencia media agregados por ruta."""

    ruta: str
    llamadas: int
    latencia_media: int


class LlamadaOrigenStats(BaseModel):
    """Conteo de llamadas agregado por origen."""

    origen: str
    llamadas: int


class PingsStats(BaseModel):
    """Resumen del registro de pings/sondas de vida."""

    total: int
    ultimo: int | None


class LlamadasStatsResponse(BaseModel):
    """Agregados del registro de llamadas para el panel."""

    total: int
    por_ruta: list[LlamadaRutaStats]
    por_origen: list[LlamadaOrigenStats]
    pings: PingsStats


class ImportarRequest(BaseModel):
    """Parámetros de una migración de memoria hacia Nous.

    ``ruta`` es un archivo o directorio en la máquina donde corre el
    backend — local-first: la migración no sale del equipo. Para
    ``gbrain``/``memlord`` se puede indicar ``url`` del servidor MCP
    (streamable-HTTP) y ``token`` Bearer: Nous descarga las memorias
    directamente de la instancia en vez de leer un export.
    """

    formato: Literal["auto", "jsonl", "json", "obsidian", "gbrain", "memlord"]
    ruta: str | None = Field(default=None, min_length=1, max_length=500)
    url: str | None = Field(default=None, min_length=8, max_length=500)
    token: str | None = Field(default=None, max_length=500)
    fuente: str | None = Field(default=None, max_length=50)
    rama: str | None = Field(default=None, max_length=64)
    vista_previa: bool = False


class ImportarResponse(BaseModel):
    """Resultado de una migración de memoria.

    En ``vista_previa`` no se escribe nada: ``nuevas``/``omitidos``
    anticipan lo que haría la ejecución real.
    """

    importados: int
    omitidos: int
    errores: int
    vinculos: int
    formato: str
    fuente: str
    vista_previa: bool = False
    nuevas: int | None = None


class HistorialImportacion(BaseModel):
    """Entrada del historial de migraciones ejecutadas."""

    ts: int
    formato: str
    fuente: str
    ruta: str
    resultado: dict[str, Any]


# ── Identidad multi-tenant ──────────────────────────────────────


class UsuarioResumen(BaseModel):
    """Datos públicos de un usuario."""

    id: str
    handle: str
    nombre: str | None = None
    email: str | None = None


class WorkspaceResumen(BaseModel):
    """Workspace con el rol del llamante en él."""

    id: str
    slug: str
    nombre: str
    rol: str


class LicenciaResumen(BaseModel):
    """Uso actual contra los límites de la edición libre."""

    licenciado: bool
    usuarios: int
    max_usuarios: int | None
    workspaces: int
    max_workspaces: int | None


class IdentidadResponse(BaseModel):
    """Quién es el llamante y a qué workspaces pertenece."""

    usuario: UsuarioResumen | None
    workspace_activo: WorkspaceResumen
    workspaces: list[WorkspaceResumen]
    licencia: LicenciaResumen | None = None


class WorkspaceRequest(BaseModel):
    """Cuerpo para crear un workspace nuevo."""

    slug: str = Field(min_length=1, max_length=64)
    nombre: str = Field(min_length=1, max_length=200)


class MiembroResumen(BaseModel):
    """Miembro de un workspace con su rol."""

    usuario_id: str
    handle: str
    nombre: str | None
    rol: str


class MiembroPatch(BaseModel):
    """Cambio de rol de un miembro (owner no asignable)."""

    rol: Literal["member", "viewer"]


class InvitacionRequest(BaseModel):
    """Cuerpo para emitir una invitación canjeable."""

    rol: Literal["member", "viewer"] = "member"
    dias: int = Field(default=7, ge=1, le=30)


class InvitacionResponse(BaseModel):
    """Token de invitación en claro — se muestra una sola vez."""

    token: str
    expira_dias: int


class CanjearRequest(BaseModel):
    """Canje de invitación: crea el usuario del invitado."""

    token: str = Field(min_length=8, max_length=128)
    handle: str = Field(min_length=1, max_length=64)
    nombre: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)


class CanjearResponse(BaseModel):
    """Resultado del canje: key del nuevo usuario en claro."""

    handle: str
    key: str


class LlaveRequest(BaseModel):
    """Cuerpo para emitir una key propia nueva."""

    nombre: str | None = Field(default=None, max_length=80)
    workspace_default_id: str | None = Field(default=None)


class LlaveResponse(BaseModel):
    """Metadata de una key (nunca el hash ni el secreto)."""

    id: str
    prefijo: str
    nombre: str | None
    workspace_default_id: str | None
    revocada: bool
    ultimo_uso: datetime | None
    creado_en: datetime


class IntencionRequest(BaseModel):
    """Cuerpo para registrar una intención prospectiva (ADR-003)."""

    intencion: str = Field(min_length=1, max_length=2_000)
    en: str | int | None = Field(default=None)
    cuando: str | None = Field(default=None, max_length=500)
    umbral: float = Field(default=0.6, gt=0.0, le=1.0)
    workspace: str | None = Field(default=None, max_length=64)


class DecisionRequest(BaseModel):
    """Cuerpo para el consejero prescriptivo (ADR-002 Nivel 2, Pro)."""

    dilema: str = Field(min_length=1, max_length=2_000)
    opciones: list[str] | None = Field(default=None, max_length=6)
    workspace: str | None = Field(default=None, max_length=64)
