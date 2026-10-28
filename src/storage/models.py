"""Modelos SQLAlchemy 2.0 de Nous."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos de Nous."""


class UsuarioModel(Base):
    """Persona con acceso a Nous — identidad de keys y autoría."""

    __tablename__ = "usuarios"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    handle: Mapped[str] = mapped_column(String(64), unique=True)
    nombre: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WorkspaceModel(Base):
    """Límite de aislamiento multi-tenant: cada memoria vive en uno."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="RESTRICT"),
        nullable=False,
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MiembroModel(Base):
    """Pertenencia de un usuario a un workspace con su rol."""

    __tablename__ = "miembros"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rol: Mapped[str] = mapped_column(String(20), server_default="member")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "rol IN ('owner','member','viewer')", name="ck_miembros_rol"
        ),
    )


class ApiKeyModel(Base):
    """Llave ``ns_u_*`` de un usuario — la key ES la credencial.

    Solo se persiste el hash SHA-256: la key en claro se entrega una
    vez al crearla. ``workspace_default_id`` es el destino de las
    llamadas MCP/REST que no declaren workspace explícito.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefijo: Mapped[str] = mapped_column(String(16))
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_default_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
    )
    nombre: Mapped[str] = mapped_column(String(100), server_default="")
    ultimo_uso: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    revocada: Mapped[bool] = mapped_column(Boolean, server_default="false")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InvitacionModel(Base):
    """Llave canjeable de un solo uso para unirse a un workspace.

    El owner la genera (CLI o dashboard); el invitado la canjea con su
    handle — crea su usuario, su membresía y su primera API key. Sin
    SMTP: la llave se transporta por cualquier canal (Signal, papel).
    """

    __tablename__ = "invitaciones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    rol: Mapped[str] = mapped_column(String(20), server_default="member")
    creado_por: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    expira_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    canjeada_por: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    canjeada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "rol IN ('member','viewer')", name="ck_invitaciones_rol"
        ),
    )


class EventoModel(Base):
    """Evento atómico en la línea de universo 4D del usuario."""

    __tablename__ = "eventos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        String(20), server_default="personal", nullable=False
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    timestamp_unix: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dimension: Mapped[str] = mapped_column(String(20), nullable=False)
    fuente: Mapped[str] = mapped_column(String(50), nullable=False)
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    amplitud: Mapped[float] = mapped_column(Float, server_default="0.5")
    frecuencia: Mapped[float | None] = mapped_column(Float)
    refuerzos: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    contexto: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'::varchar(64)[]")
    )
    rama: Mapped[str] = mapped_column(String(64), server_default="main")
    tipo: Mapped[str] = mapped_column(String(20), server_default="evento")
    expira_en: Mapped[int | None] = mapped_column(BigInteger)
    reemplazado_por: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eventos.id", ondelete="SET NULL")
    )
    con_quien: Mapped[str | None] = mapped_column(String(128))
    # ADR-003 — patrones neurocognitivos
    tipo_memoria: Mapped[str | None] = mapped_column(String(20))
    estado_emocional: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float, server_default="1.0")
    ultimo_acceso: Mapped[int | None] = mapped_column(BigInteger)
    archivado: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )
    archivado_en: Mapped[int | None] = mapped_column(BigInteger)
    razon_archivo: Mapped[str | None] = mapped_column(Text)
    curado: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # Neuro-2 — primacía de época y ventana lábil de reconsolidación.
    inaugura_epoca: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )
    labil_hasta: Mapped[int | None] = mapped_column(BigInteger)
    # Neuro-3 — olvido dirigido, SRS y cuarentena por fuente.
    olvido_dirigido: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )
    proxima_revision: Mapped[int | None] = mapped_column(BigInteger)
    cuarentena: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )
    # Neuro-4 — debilitamientos por competencia (RIF + proactiva).
    debilitados: Mapped[int] = mapped_column(
        Integer, server_default="0"
    )
    # Neuro-5 — write-approval: indexado pero fuera del recall hasta
    # que el humano apruebe (la memoria propone, el humano dispone).
    staged: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )
    contenido_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('spanish', contenido)", persisted=True),
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "dimension IN ('cognitiva', 'somatica', 'conductual')",
            name="ck_eventos_dimension",
        ),
        CheckConstraint(
            "tipo IN ('evento','hecho','creencia','compromiso',"
            "'preferencia','censor')",
            name="ck_eventos_tipo",
        ),
        CheckConstraint(
            "scope IN ('personal','workspace')", name="ck_eventos_scope"
        ),
        CheckConstraint(
            "tipo_memoria IS NULL OR tipo_memoria IN "
            "('episodica','semantica','procedimental')",
            name="ck_eventos_tipo_memoria",
        ),
        Index("ix_eventos_timestamp", "timestamp_unix"),
        Index("ix_eventos_workspace", "workspace_id"),
        Index("ix_eventos_scope", "scope"),
        Index("ix_eventos_autor", "autor_id"),
        Index("ix_eventos_tipo", "tipo"),
        Index("ix_eventos_dimension", "dimension"),
        Index("ix_eventos_fuente", "fuente"),
        Index("ix_eventos_tipo_memoria", "tipo_memoria"),
        Index("ix_eventos_archivado", "archivado"),
        Index("ix_eventos_curado", "curado"),
        Index("ix_eventos_staged", "staged"),
        Index(
            "ix_eventos_weight_arch", "workspace_id", "archivado", "weight"
        ),
        Index("ix_eventos_tsv", "contenido_tsv", postgresql_using="gin"),
        Index(
            "ix_eventos_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class RelacionModel(Base):
    """Vínculo entre dos eventos de la línea de universo.

    ``explicita``: el agente/usuario declaró la relación al registrarla
    (p. ej. "esto va de la mano con aquello").
    ``semantica``: inferida al indexarse — el embedding del evento
    quedó por encima del umbral de similitud con otro evento previo.

    El par se guarda en orden canónico (``origen_id < destino_id``):
    la relación es no dirigida.
    """

    __tablename__ = "relaciones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    origen_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("eventos.id", ondelete="CASCADE"),
        nullable=False,
    )
    destino_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("eventos.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    peso: Mapped[float] = mapped_column(Float, server_default="1.0")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("origen_id < destino_id", name="ck_relaciones_orden"),
        CheckConstraint(
            "tipo IN ('explicita', 'semantica', 'reemplaza', 'cierra', "
            "'contradice', 'coactiva', 'reconsolida', 'gist', "
            "'rem', 'encadena')",
            name="ck_relaciones_tipo",
        ),
        UniqueConstraint(
            "origen_id", "destino_id", "tipo", name="uq_relaciones_par_tipo"
        ),
        Index("ix_relaciones_origen", "origen_id"),
        Index("ix_relaciones_destino", "destino_id"),
    )


class FuenteModel(Base):
    """Origen de datos administrable de la memoria unificada.

    Todas las fuentes comparten la misma base — ``eventos.fuente`` las
    distingue. ``estado`` permite archivar sin borrar; ``namespace``
    agrupa sub-fuentes (p. ej. ``obsidian/proyecto-x``); ``rama_origen``
    indica la línea de universo donde nació la fuente.
    """

    __tablename__ = "fuentes"

    nombre: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
        primary_key=True,
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    tipo: Mapped[str] = mapped_column(String(20), server_default="manual")
    estado: Mapped[str] = mapped_column(String(20), server_default="activa")
    namespace: Mapped[str] = mapped_column(String(128), server_default="")
    rama_origen: Mapped[str] = mapped_column(String(64), server_default="main")
    confiable: Mapped[bool] = mapped_column(
        Boolean, server_default="true"
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TrayectoriaModel(Base):
    """Patrón detectado en la línea de universo (flow, valle, bifurcación)."""

    __tablename__ = "trayectorias"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        String(20), server_default="personal", nullable=False
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    fecha_inicio: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha_fin: Mapped[int | None] = mapped_column(BigInteger)
    descripcion: Mapped[str | None] = mapped_column(Text)
    eventos_asociados: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    amplitud_promedio: Mapped[float | None] = mapped_column(Float)
    confianza: Mapped[float | None] = mapped_column(Float)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('personal','workspace')",
            name="ck_trayectorias_scope",
        ),
        Index("ix_trayectorias_workspace", "workspace_id"),
    )


class ProyeccionModel(Base):
    """Futuro probable proyectado a partir de la trayectoria del usuario."""

    __tablename__ = "proyecciones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        String(20), server_default="personal", nullable=False
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    horizonte_dias: Mapped[int] = mapped_column(Integer, nullable=False)
    variable_objetivo: Mapped[str | None] = mapped_column(String(50))
    probabilidad: Mapped[float | None] = mapped_column(Float)
    vector_proyectado: Mapped[list[float] | None] = mapped_column(JSONB)
    rama_decision: Mapped[str | None] = mapped_column(Text)
    basada_en_eventos: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('personal','workspace')",
            name="ck_proyecciones_scope",
        ),
        Index("ix_proyecciones_workspace", "workspace_id"),
    )


class BenchmarkRunModel(Base):
    """Ejecución de un benchmark, persistida para el dashboard.

    Cada corrida guarda qué se midió (``nombre``/``escenario``), con
    qué parámetros, las métricas resultantes y el commit de git, para
    comparar el rendimiento del sistema a lo largo del tiempo.
    """

    __tablename__ = "benchmarks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    nombre: Mapped[str] = mapped_column(String(50), nullable=False)
    escenario: Mapped[str | None] = mapped_column(String(200))
    parametros: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    metricas: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    git_commit: Mapped[str | None] = mapped_column(String(40))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_benchmarks_nombre", "nombre"),
        Index("ix_benchmarks_creado_en", "creado_en"),
    )


class ObjetivoModel(Base):
    """Meta del usuario que ancla una proyección futura.

    Los cauces-fantasma del río temporal convergen hacia objetivos
    activos. ``dimension`` None = objetivo transversal; ``estado`` es
    un ciclo de vida auditable (activo → cumplido/abandonado) —
    cerrar registra ``evento_cierre_id``, nunca se borra en duro.
    """

    __tablename__ = "objetivos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        String(20), server_default="personal", nullable=False
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    dimension: Mapped[str | None] = mapped_column(String(20))
    fecha_objetivo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), server_default="activo")
    rama: Mapped[str] = mapped_column(String(64), server_default="main")
    evento_cierre_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eventos.id", ondelete="SET NULL")
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "dimension IS NULL OR dimension IN "
            "('cognitiva','somatica','conductual')",
            name="ck_objetivos_dimension",
        ),
        CheckConstraint(
            "estado IN ('activo','cumplido','abandonado')",
            name="ck_objetivos_estado",
        ),
        CheckConstraint(
            "scope IN ('personal','workspace')",
            name="ck_objetivos_scope",
        ),
        Index("ix_objetivos_workspace", "workspace_id"),
        Index("ix_objetivos_estado", "estado"),
        Index("ix_objetivos_fecha", "fecha_objetivo"),
    )


class ConfiguracionModel(Base):
    """Configuración persistente del usuario (clave-valor)."""

    __tablename__ = "configuracion"

    clave: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
        primary_key=True,
    )
    # Sentinel zero-UUID = config de workspace; un usuario real =
    # preferencia personal suya dentro de ese workspace.
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    valor: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LlamadaModel(Base):
    """Una llamada HTTP a Nous: metadata de trazabilidad, nunca contenido."""

    __tablename__ = "llamadas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metodo: Mapped[str] = mapped_column(String(10), nullable=False)
    ruta: Mapped[str] = mapped_column(String(160), nullable=False)
    origen: Mapped[str] = mapped_column(String(80), server_default="")
    agente: Mapped[str] = mapped_column(String(200), server_default="")
    latencia_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    es_ping: Mapped[bool] = mapped_column(Boolean, server_default="false")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_llamadas_ts", "ts"),
        Index("ix_llamadas_ruta", "ruta"),
        Index("ix_llamadas_ping", "es_ping"),
    )


class IntencionProspectivaModel(Base):
    """Memoria prospectiva: intención pendiente con trigger time/event.

    ``trigger_tipo='time'``: ``trigger_valor = {"ts": <unix>}`` dispara
    cuando llega la fecha. ``trigger_tipo='event'``: ``trigger_valor =
    {"query": "<texto>"}`` se evalúa contra eventos entrantes.
    """

    __tablename__ = "intenciones_prospectivas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    creada_en: Mapped[int] = mapped_column(BigInteger, nullable=False)
    intencion: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    trigger_valor: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    ejecutada: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )
    cancelada: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )
    ejecutada_en: Mapped[int | None] = mapped_column(BigInteger)
    cancelada_en: Mapped[int | None] = mapped_column(BigInteger)
    # Neuro-3 — snooze: la intención no dispara hasta ese momento
    # (ni por tiempo ni por evento); None = sin aplazar.
    aplazada_hasta: Mapped[int | None] = mapped_column(BigInteger)
    # Neuro-3 — prep pack: memorias relacionadas adjuntadas al
    # disparar (lista de {"id", "contenido"} — el "por qué" listo).
    preparacion: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(
            "trigger_tipo IN ('time','event')",
            name="ck_intenciones_trigger_tipo",
        ),
        Index("ix_intenciones_workspace", "workspace_id", "ejecutada", "cancelada"),
        Index("ix_intenciones_trigger", "trigger_tipo"),
    )


class ScriptDetectadoModel(Base):
    """Patrón de comportamiento recurrente minado de la línea de universo.

    ``pasos`` es un array JSONB de contenidos (o ids de evento) en orden;
    ``frecuencia`` = soporte relativo, ``confianza`` = qué tan fiel es la
    secuencia observada al patrón.
    """

    __tablename__ = "scripts_detectados"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    nombre: Mapped[str] = mapped_column(String(160), nullable=False)
    pasos: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    frecuencia: Mapped[float] = mapped_column(Float, nullable=False)
    confianza: Mapped[float] = mapped_column(Float, nullable=False)
    creado_en: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actualizado_en: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_scripts_workspace", "workspace_id"),
        Index("ix_scripts_confianza", "confianza"),
    )


class BorradorModel(Base):
    """Memoria de trabajo — buffer volátil de Baddeley.

    Borradores/hipótesis/contexto transitorio del agente: expira si
    nadie lo ensaya (el olvido es la función, no un bug — como las
    intenciones, se evalúa al consultar, sin scheduler) y promueve a
    la línea de universo si acumula repeticiones. Nunca contamina la
    búsqueda principal: lo que sobrevive entra como evento real.
    """

    __tablename__ = "borradores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[str] = mapped_column(
        String(20), server_default="cognitiva"
    )
    ensayos: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    creado_en: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expira_en: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "dimension IN ('cognitiva', 'somatica', 'conductual')",
            name="ck_borradores_dimension",
        ),
        Index("ix_borradores_workspace", "workspace_id", "expira_en"),
    )


class ReporteConsolidacionModel(Base):
    """Resumen auditable de una corrida de consolidación (sueño).

    El equivalente del dream diary de OpenClaw: cada corrida deja qué se
    fortaleció, qué se archivó y qué se promovió a curado — revisable
    por el usuario, nunca silencioso.
    """

    __tablename__ = "reportes_consolidacion"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
    )
    ejecutado_en: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fortalecidos: Mapped[int] = mapped_column(Integer, server_default="0")
    archivados: Mapped[int] = mapped_column(Integer, server_default="0")
    promovidos: Mapped[int] = mapped_column(Integer, server_default="0")
    detalle: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("ix_reportes_workspace", "workspace_id", "ejecutado_en"),
    )


class PrediccionModel(Base):
    """Afirmación con confianza declarada — materia prima del Brier.

    La calibración solo se mide si el sistema confiesa qué tan seguro
    estaba ANTES de saber el resultado. ``acierto`` queda NULL hasta
    que la vida (o el usuario) resuelve la predicción.
    """

    __tablename__ = "predicciones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    afirmacion: Mapped[str] = mapped_column(Text, nullable=False)
    confianza: Mapped[float] = mapped_column(Float, nullable=False)
    evento_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("eventos.id", ondelete="SET NULL"),
    )
    acierto: Mapped[bool | None] = mapped_column(Boolean)
    creado_en: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resuelto_en: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint(
            "confianza >= 0.0 AND confianza <= 1.0",
            name="ck_predicciones_confianza",
        ),
        Index("ix_predicciones_workspace", "workspace_id", "acierto"),
    )


class SesionModel(Base):
    """Límite de sesión first-class — dónde la memoria rinde.

    El bootstrap abre la sesión (snapshot congelado); el handoff la
    cierra (documento curado de continuidad). ``eventos`` cuenta las
    huellas escritas dentro del rango — la sesión se mide por lo que
    dejó, no por su duración.
    """

    __tablename__ = "sesiones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        server_default=text(
            "'00000000-0000-0000-0000-000000000002'::uuid"
        ),
        nullable=False,
    )
    autor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
    )
    agente: Mapped[str] = mapped_column(String(80), server_default="")
    inicio: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fin: Mapped[int | None] = mapped_column(BigInteger)
    resumen: Mapped[str | None] = mapped_column(Text)
    handoff: Mapped[str | None] = mapped_column(Text)
    eventos: Mapped[int] = mapped_column(Integer, server_default="0")

    __table_args__ = (
        Index("ix_sesiones_workspace", "workspace_id", "inicio"),
    )
