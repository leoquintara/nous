"""migrar_gbrain.py — Migra el contenido de gbrain a Nous.

Fuente: base `gbrain` en el postgres18 compartido (tablas pages,
page_versions, facts, links, tags, wanted_links, dsh_memories).
Destino: workspace `gbrain` de Nous, con retag a su flujo nativo:

- ``pages`` → eventos (tipo por ``page.type``; ``curado`` para
  synapse/reglas; dimensión via ``ClasificadorDimensiones``).
- ``page_versions`` → eventos encadenados con ``reemplazado_por``;
  la página vigente cierra la cadena (historia inmutable 4D).
- ``facts`` → eventos con tipo nativo (fact/event/preference/belief)
  y ``superseded_by`` → ``reemplazado_por``.
- ``links`` → relaciones ``explicita`` en orden canónico.
- ``wanted_links`` → eventos ``hecho`` clasificados por Nous
  (lagunas del grafo declaradas) + relación al origen.
- ``dsh_memories`` → eventos ``hecho``.

Ids determinísticos ``uuid5(gbrain:<entidad>:<pk>)`` — reintentos
idempotentes. Embeddings se regeneran con el modelo local de Nous.

Uso:
    python -m scripts.migrar_gbrain [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core.clasificador import ClasificadorDimensiones
from src.core.embeddings import GeneradorEmbeddings
from src.core.modelos import Dimension
from src.storage.database import crear_engine, get_session
from src.storage.models import EventoModel, RelacionModel

GBRAIN_URL = (
    "postgresql+asyncpg://postgres:spicer1994@localhost:5433/gbrain"
)
NS = uuid.uuid5(uuid.NAMESPACE_URL, "nous:gbrain")
LOTE_EMBEDDING = 32
FUENTE = "gbrain"
WS_SLUG = "gbrain"
OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# page.type → tipo nativo Nous (default: hecho — es conocimiento)
PAGE_TIPO = {
    "preference": "preferencia",
    "minutas": "evento",
}
FACT_TIPO = {
    "fact": "hecho",
    "event": "evento",
    "preference": "preferencia",
    "belief": "creencia",
}
# page.type → tipo_memoria (episódica=vivencias, procedimental=cómo
# hacer, semántica=conocimiento)
PAGE_TIPO_MEMORIA = {
    "minutas": "episodica",
    "reglas": "procedimental",
    "procesos": "procedimental",
}
# Tipos de página marcados como contenido curado (inmune a decay)
PAGE_CURADO = {"synapse", "reglas"}


def gid(entidad: str, pk: object) -> uuid.UUID:
    """Id determinístico por PK de gbrain — reintentos idempotentes."""
    return uuid.uuid5(NS, f"{entidad}:{pk}")


def unix_ts(dt: datetime | None) -> int:
    """Convierte datetime tz-aware a epoch; fallback = ahora de gbrain."""
    if dt is None:
        return 0
    return int(dt.timestamp())


@dataclass
class MigEvento:
    """Evento pendiente de inserción, con trazabilidad a gbrain."""

    id: uuid.UUID
    timestamp_unix: int
    dimension: Dimension
    contenido: str
    tipo: str
    amplitud: float = 0.5
    tags: list[str] = field(default_factory=list)
    contexto: dict[str, Any] = field(default_factory=dict)
    tipo_memoria: str | None = None
    curado: bool = False
    ultimo_acceso: int | None = None
    expira_en: int | None = None
    # Se resuelven tras insertar: el evento fue reemplazado por este id
    reemplazado_por: uuid.UUID | None = None


def dedupe_tags(tags: list[str]) -> list[str]:
    """Dedup de tags ignorando case ('synapse'/'Synapse' -> una)."""
    vistos: set[str] = set()
    resultado: list[str] = []
    for t in tags:
        clave = t.strip().lower()
        if clave and clave not in vistos:
            vistos.add(clave)
            resultado.append(t.strip()[:64])
    return resultado


def contenido_pagina(title: str | None, compiled_truth: str) -> str:
    """Contenido del evento: título como encabezado si falta."""
    cuerpo = (compiled_truth or "").strip()
    if title and not cuerpo.lstrip().startswith("#"):
        return f"# {title}\n\n{cuerpo}"
    return cuerpo or (title or "")


async def cargar_gbrain(gb: AsyncEngine) -> dict[str, Any]:
    """Lee todas las tablas núcleo de gbrain en un solo viaje."""
    async with gb.connect() as c:
        datos: dict[str, Any] = {}
        datos["pages"] = (
            await c.execute(
                text(
                    "select id, slug, type, title, compiled_truth,"
                    " frontmatter, emotional_weight, created_at,"
                    " effective_date, last_retrieved_at, source_id,"
                    " generation from pages where deleted_at is null"
                )
            )
        ).mappings().all()
        datos["versions"] = (
            await c.execute(
                text(
                    "select id, page_id, compiled_truth, frontmatter,"
                    " snapshot_at, title, type, is_deleted, source_path"
                    " from page_versions order by page_id, snapshot_at"
                )
            )
        ).mappings().all()
        datos["facts"] = (
            await c.execute(
                text(
                    "select id, entity_slug, fact, kind, confidence,"
                    " valid_from, valid_until, superseded_by, source,"
                    " created_at, claim_metric, claim_value, claim_unit,"
                    " event_type from facts"
                )
            )
        ).mappings().all()
        datos["tags"] = (
            await c.execute(text("select page_id, tag from tags"))
        ).all()
        datos["links"] = (
            await c.execute(
                text(
                    "select from_page_id, to_page_id, link_type"
                    " from links where link_type != ''"
                )
            )
        ).all()
        datos["wanted"] = (
            await c.execute(
                text(
                    "select id, origin_page_id, target_ref, ref_kind,"
                    " context, first_seen_at from wanted_links"
                )
            )
        ).mappings().all()
        datos["dsh"] = (
            await c.execute(
                text(
                    "select id, content, tags, scope, created_at"
                    " from dsh_memories"
                )
            )
        ).mappings().all()
    return datos


async def asegurar_workspace(engine: AsyncEngine) -> uuid.UUID:
    """Crea el workspace `gbrain` + membresía owner si no existen."""
    async with get_session(engine) as session:
        ws = (
            await session.execute(
                text("select id from workspaces where slug = :s"),
                {"s": WS_SLUG},
            )
        ).scalar()
        if ws is None:
            ws = uuid.uuid4()
            await session.execute(
                text(
                    "insert into workspaces (id, slug, nombre, owner_id)"
                    " values (:id, :slug, :nombre, :owner)"
                ),
                {
                    "id": ws,
                    "slug": WS_SLUG,
                    "nombre": "gbrain (migrado)",
                    "owner": OWNER_ID,
                },
            )
            await session.execute(
                text(
                    "insert into miembros (workspace_id, usuario_id, rol)"
                    " values (:w, :u, 'owner')"
                ),
                {"w": ws, "u": OWNER_ID},
            )
            await session.commit()
    return uuid.UUID(str(ws))


async def construir_eventos(
    datos: dict[str, Any], clasificador: ClasificadorDimensiones
) -> list[MigEvento]:
    """Traduce todas las entidades de gbrain a MigEvento Nous."""
    eventos: list[MigEvento] = []
    tags_por_page: dict[int, list[str]] = {}
    for page_id, tag in datos["tags"]:
        tags_por_page.setdefault(page_id, []).append(tag)

    async def dimension_de(contenido: str) -> Dimension:
        if not contenido.strip():
            return Dimension.COGNITIVA
        resultado = await clasificador.clasificar(contenido)
        return resultado.dimension

    for p in datos["pages"]:
        contenido = contenido_pagina(p["title"], p["compiled_truth"])
        ptype = p["type"] or "documentacion"
        namespace = str(p["slug"]).split("/", 1)[0]
        eventos.append(
            MigEvento(
                id=gid("page", p["id"]),
                timestamp_unix=unix_ts(p["effective_date"] or p["created_at"]),
                dimension=await dimension_de(contenido),
                contenido=contenido,
                tipo=PAGE_TIPO.get(ptype, "hecho"),
                amplitud=p["emotional_weight"] or 0.5,
                tags=dedupe_tags(
                    tags_por_page.get(p["id"], [])
                    + ["gbrain", f"gbrain:{namespace}"]
                ),
                contexto={
                    "gbrain_id": p["id"],
                    "slug": p["slug"],
                    "type": ptype,
                    "frontmatter": dict(p["frontmatter"] or {}),
                    "generation": p["generation"],
                    "migrado_de": "gbrain",
                },
                tipo_memoria=PAGE_TIPO_MEMORIA.get(ptype, "semantica"),
                curado=ptype in PAGE_CURADO,
                ultimo_acceso=(
                    unix_ts(p["last_retrieved_at"]) or None
                ),
            )
        )

    # page_versions → cadena reemplazado_por ordenada por snapshot_at;
    # la última versión apunta al evento de la página vigente.
    ver_por_page: dict[int, list[Any]] = {}
    for v in datos["versions"]:
        ver_por_page.setdefault(v["page_id"], []).append(v)
    for page_id, versiones in ver_por_page.items():
        ids_cadena = [gid("ver", v["id"]) for v in versiones]
        ids_cadena.append(gid("page", page_id))
        for i, v in enumerate(versiones):
            contenido = contenido_pagina(
                v["title"], v["compiled_truth"] or ""
            )
            eventos.append(
                MigEvento(
                    id=gid("ver", v["id"]),
                    timestamp_unix=unix_ts(v["snapshot_at"]),
                    dimension=await dimension_de(contenido),
                    contenido=contenido,
                    tipo="hecho",
                    tags=dedupe_tags(
                        ["gbrain", "gbrain:version"]
                    ),
                    contexto={
                        "gbrain_version_id": v["id"],
                        "page_id": page_id,
                        "is_deleted": v["is_deleted"],
                        "source_path": v["source_path"],
                        "migrado_de": "gbrain",
                    },
                    tipo_memoria="semantica",
                    reemplazado_por=ids_cadena[i + 1],
                )
            )

    for f in datos["facts"]:
        eventos.append(
            MigEvento(
                id=gid("fact", f["id"]),
                timestamp_unix=unix_ts(f["valid_from"] or f["created_at"]),
                dimension=await dimension_de(f["fact"] or ""),
                contenido=f["fact"] or "",
                tipo=FACT_TIPO.get(f["kind"] or "fact", "hecho"),
                amplitud=f["confidence"] or 0.5,
                tags=dedupe_tags(
                    ["gbrain", f"fact:{f['entity_slug'] or 'sin-entidad'}"]
                ),
                contexto={
                    "gbrain_fact_id": f["id"],
                    "entity_slug": f["entity_slug"],
                    "source": f["source"],
                    "confidence": f["confidence"],
                    "claim_metric": f["claim_metric"],
                    "claim_value": f["claim_value"],
                    "claim_unit": f["claim_unit"],
                    "migrado_de": "gbrain",
                },
                tipo_memoria="semantica",
                expira_en=unix_ts(f["valid_until"]) or None,
                reemplazado_por=(
                    gid("fact", f["superseded_by"])
                    if f["superseded_by"]
                    else None
                ),
            )
        )

    slug_por_page = {p["id"]: p["slug"] for p in datos["pages"]}
    for w in datos["wanted"]:
        origen_slug = slug_por_page.get(w["origin_page_id"], "?")
        contexto_txt = " ".join(str(w["context"] or "").split())[:240]
        contenido = (
            f"Enlace sin resolver [[{w['target_ref']}]] referenciado"
            f" desde «{origen_slug}»: {contexto_txt}"
        )
        eventos.append(
            MigEvento(
                id=gid("wanted", w["id"]),
                timestamp_unix=unix_ts(w["first_seen_at"]),
                dimension=await dimension_de(contenido),
                contenido=contenido,
                tipo="hecho",
                tags=dedupe_tags(["gbrain", "wanted-link"]),
                contexto={
                    "gbrain_wanted_id": w["id"],
                    "origin_page_id": w["origin_page_id"],
                    "origin_slug": origen_slug,
                    "target_ref": w["target_ref"],
                    "ref_kind": w["ref_kind"],
                    "migrado_de": "gbrain",
                },
                tipo_memoria="semantica",
            )
        )

    for d in datos["dsh"]:
        eventos.append(
            MigEvento(
                id=gid("dsh", d["id"]),
                timestamp_unix=unix_ts(d["created_at"]),
                dimension=await dimension_de(d["content"] or ""),
                contenido=d["content"] or "",
                tipo="hecho",
                tags=dedupe_tags(list(d["tags"] or []) + ["gbrain", "dsh"]),
                contexto={
                    "gbrain_dsh_id": d["id"],
                    "scope": d["scope"],
                    "migrado_de": "gbrain",
                },
                tipo_memoria="semantica",
            )
        )
    return eventos


async def filtrar_nuevos(
    engine: AsyncEngine, eventos: list[MigEvento]
) -> list[MigEvento]:
    """Dedup por id determinístico contra eventos ya migrados."""
    if not eventos:
        return []
    ids = [e.id for e in eventos]
    async with get_session(engine) as session:
        existentes = {
            fila[0]
            for fila in (
                await session.execute(
                    text("select id from eventos where id = any(:ids)"),
                    {"ids": ids},
                )
            ).all()
        }
    return [e for e in eventos if e.id not in existentes]


async def persistir(
    engine: AsyncEngine,
    eventos: list[MigEvento],
    workspace_id: uuid.UUID,
    generador: GeneradorEmbeddings,
) -> int:
    """Genera embeddings por lotes e inserta los eventos nuevos.

    ``reemplazado_por`` se aplica en un UPDATE posterior: las cadenas
    de versiones cruzan los límites de lote y el FK exige que el
    evento sucesor ya exista."""
    reemplazos = [
        (e.id, e.reemplazado_por) for e in eventos if e.reemplazado_por
    ]
    insertados = 0
    for inicio in range(0, len(eventos), LOTE_EMBEDDING):
        lote = eventos[inicio : inicio + LOTE_EMBEDDING]
        vectores = await generador.generar_lote(
            [e.contenido for e in lote]
        )
        async with get_session(engine) as session:
            for ev, vector in zip(lote, vectores, strict=True):
                session.add(
                    EventoModel(
                        id=ev.id,
                        timestamp_unix=ev.timestamp_unix,
                        dimension=ev.dimension.value,
                        fuente=FUENTE,
                        contenido=ev.contenido,
                        embedding=vector,
                        amplitud=ev.amplitud,
                        tags=ev.tags,
                        contexto=ev.contexto,
                        tipo=ev.tipo,
                        tipo_memoria=ev.tipo_memoria,
                        curado=ev.curado,
                        ultimo_acceso=ev.ultimo_acceso,
                        expira_en=ev.expira_en,
                        rama="main",
                        scope="workspace",
                        workspace_id=workspace_id,
                        autor_id=OWNER_ID,
                    )
                )
            await session.commit()
        insertados += len(lote)
    if reemplazos:
        async with get_session(engine) as session:
            existentes = {
                f[0]
                for f in (
                    await session.execute(
                        text(
                            "select id from eventos where id = any(:ids)"
                        ),
                        {"ids": [r for _, r in reemplazos]},
                    )
                ).all()
            }
            for eid, reemplaza in reemplazos:
                if reemplaza not in existentes:
                    continue
                await session.execute(
                    text(
                        "update eventos set reemplazado_por = :r"
                        " where id = :i"
                    ),
                    {"r": reemplaza, "i": eid},
                )
            await session.commit()
    return insertados


async def migrar_links(
    engine: AsyncEngine,
    datos: dict[str, Any],
    workspace_id: uuid.UUID,
) -> int:
    """Crea relaciones `explicita` desde links y wanted_links."""
    pares: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for from_id, to_id, _tipo in datos["links"]:
        a, b = gid("page", from_id), gid("page", to_id)
        if a == b:
            continue
        pares.add((a, b) if a < b else (b, a))
    for w in datos["wanted"]:
        a, b = gid("wanted", w["id"]), gid("page", w["origin_page_id"])
        if a == b:
            continue
        pares.add((a, b) if a < b else (b, a))

    if not pares:
        return 0
    lista = list(pares)
    async with get_session(engine) as session:
        existentes = {
            (r[0], r[1])
            for r in (
                await session.execute(
                    text(
                        "select origen_id, destino_id from relaciones"
                        " where tipo = 'explicita' and origen_id = any(:a)"
                        " and destino_id = any(:b)"
                    ),
                    {
                        "a": [p[0] for p in lista],
                        "b": [p[1] for p in lista],
                    },
                )
            ).all()
        }
        creadas = 0
        for origen, destino in lista:
            if (origen, destino) in existentes:
                continue
            session.add(
                RelacionModel(
                    id=uuid.uuid4(),
                    origen_id=origen,
                    destino_id=destino,
                    tipo="explicita",
                    peso=1.0,
                    workspace_id=workspace_id,
                    autor_id=OWNER_ID,
                )
            )
            creadas += 1
        await session.commit()
    return creadas


async def registrar_fuente(
    engine: AsyncEngine, workspace_id: uuid.UUID
) -> None:
    """Registra 'gbrain' en la tabla fuentes para observabilidad."""
    async with get_session(engine) as session:
        existe = (
            await session.execute(
                text(
                    "select 1 from fuentes where nombre = :n"
                    " and workspace_id = :w"
                ),
                {"n": FUENTE, "w": workspace_id},
            )
        ).scalar()
        if existe:
            return
        await session.execute(
            text(
                "insert into fuentes (nombre, tipo, estado, namespace,"
                " rama_origen, metadata, workspace_id, autor_id,"
                " confiable) values (:n, 'agente', 'activa', 'gbrain',"
                " 'main', :m, :w, :u, true)"
            ),
            {
                "n": FUENTE,
                "m": '{"migracion": "postgres:gbrain -> nous"}',
                "w": workspace_id,
                "u": OWNER_ID,
            },
        )
        await session.commit()


async def main() -> None:
    """Ejecuta la migración completa (o solo reporta con --dry-run)."""
    dry = "--dry-run" in sys.argv
    gb = create_async_engine(GBRAIN_URL)
    nous = crear_engine()
    try:
        datos = await cargar_gbrain(gb)
        print(
            f"gbrain: {len(datos['pages'])} pages,"
            f" {len(datos['versions'])} versions, {len(datos['facts'])}"
            f" facts, {len(datos['wanted'])} wanted,"
            f" {len(datos['links'])} links, {len(datos['dsh'])} dsh"
        )
        if dry:
            return
        ws = await asegurar_workspace(nous)
        generador = GeneradorEmbeddings()
        clasificador = ClasificadorDimensiones(generador)
        eventos = await construir_eventos(datos, clasificador)
        nuevos = await filtrar_nuevos(nous, eventos)
        print(
            f"eventos: {len(eventos)} construidos,"
            f" {len(nuevos)} nuevos"
        )
        insertados = await persistir(nous, nuevos, ws, generador)
        vinculos = await migrar_links(nous, datos, ws)
        await registrar_fuente(nous, ws)
        print(f"importados {insertados} eventos + {vinculos} relaciones")
    finally:
        await gb.dispose()
        await nous.dispose()


if __name__ == "__main__":
    asyncio.run(main())
