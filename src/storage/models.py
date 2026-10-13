"""Modelos SQLAlchemy 2.0 de Nous."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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
        Index("ix_eventos_timestamp", "timestamp_unix"),
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
            "tipo IN ('explicita', 'semantica')", name="ck_relaciones_tipo"
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
