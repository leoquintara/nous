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


class EventoModel(Base):
    """Evento atómico en la línea de universo 4D del usuario."""

    __tablename__ = "eventos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
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
            "tipo IN ('evento','hecho','creencia','compromiso','preferencia')",
            name="ck_eventos_tipo",
        ),
        Index("ix_eventos_timestamp", "timestamp_unix"),
        Index("ix_eventos_tipo", "tipo"),
        Index("ix_eventos_dimension", "dimension"),
        Index("ix_eventos_fuente", "fuente"),
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
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    peso: Mapped[float] = mapped_column(Float, server_default="1.0")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("origen_id < destino_id", name="ck_relaciones_orden"),
        CheckConstraint(
            "tipo IN ('explicita', 'semantica', 'reemplaza', 'cierra')",
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
    tipo: Mapped[str] = mapped_column(String(20), server_default="manual")
    estado: Mapped[str] = mapped_column(String(20), server_default="activa")
    namespace: Mapped[str] = mapped_column(String(128), server_default="")
    rama_origen: Mapped[str] = mapped_column(String(64), server_default="main")
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


class ProyeccionModel(Base):
    """Futuro probable proyectado a partir de la trayectoria del usuario."""

    __tablename__ = "proyecciones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
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
        Index("ix_objetivos_estado", "estado"),
        Index("ix_objetivos_fecha", "fecha_objetivo"),
    )


class ConfiguracionModel(Base):
    """Configuración persistente del usuario (clave-valor)."""

    __tablename__ = "configuracion"

    clave: Mapped[str] = mapped_column(String(100), primary_key=True)
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
