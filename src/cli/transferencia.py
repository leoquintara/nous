"""Exportación e importación de la memoria 4D en JSONL (portabilidad).

El formato JSONL contiene los campos semánticos del evento — nunca el
embedding: al importar se regenera con el modelo local configurado, así
el archivo sigue siendo válido aunque cambie el proveedor o el modelo.

Local-first: el archivo es del usuario, legible y versionable; ninguna
operación sale de la máquina.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.embeddings import GeneradorEmbeddings
from src.storage.chunks import indexar_chunks_lote
from src.storage.database import get_session
from src.storage.models import EventoModel

logger = logging.getLogger("nous.cli.transferencia")

LOTE_EMBEDDING = 64

_DIMENSIONES = frozenset({"cognitiva", "somatica", "conductual"})


class FilaEvento(TypedDict, total=False):
    """Evento serializado en el formato de transferencia."""

    id: str
    timestamp_unix: int
    dimension: str
    fuente: str | None
    contenido: str
    amplitud: float | None
    frecuencia: float | None
    tags: list[str]
    rama: str


async def exportar(engine: AsyncEngine, archivo: Path) -> int:
    """Vuelca todos los eventos a un archivo JSONL.

    Args:
        engine: Motor async de SQLAlchemy.
        archivo: Ruta de destino (se sobrescribe si existe).

    Returns:
        Número de eventos exportados.
    """
    async with get_session(engine) as session:
        resultado = await session.execute(
            select(EventoModel).order_by(EventoModel.timestamp_unix)
        )
        eventos = resultado.scalars().all()
    with archivo.open("w", encoding="utf-8") as salida:
        for e in eventos:
            fila = FilaEvento(
                id=str(e.id),
                timestamp_unix=e.timestamp_unix,
                dimension=e.dimension,
                fuente=e.fuente,
                contenido=e.contenido,
                amplitud=e.amplitud,
                frecuencia=e.frecuencia,
                tags=list(e.tags or []),
                rama=e.rama,
            )
            salida.write(json.dumps(fila, ensure_ascii=False) + "\n")
    return len(eventos)


async def importar(
    engine: AsyncEngine,
    ruta: Path,
    formato: str = "auto",
    fuente: str | None = None,
    rama: str | None = None,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Importa eventos desde una fuente externa regenerando embeddings.

    Los eventos cuyo ``id`` ya existe se omiten (idempotente: reimportar
    el mismo origen no duplica). Las filas inválidas se cuentan como
    errores y se descartan sin abortar el proceso.

    Args:
        engine: Motor async de SQLAlchemy.
        ruta: Archivo (JSON/JSONL) o directorio (vault obsidian/gbrain).
        formato: ``auto`` o uno de los formatos de ``importadores``.
        fuente: Nombre de ``eventos.fuente`` para las filas (None = el
            formato usado).

    Returns:
        Contadores ``{"importados", "omitidos", "errores", "vinculos"}``.
    """
    from src.cli.importadores import leer_fuente

    filas, vinculos_idx, errores = leer_fuente(ruta, formato, fuente)
    return await importar_filas(
        engine, filas, vinculos_idx, errores, rama, workspace_id
    )


async def importar_filas(
    engine: AsyncEngine,
    filas: list[FilaEvento],
    vinculos_idx: list[tuple[int, int]],
    errores: int,
    rama: str | None = None,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Persiste filas ya normalizadas (de cualquier origen) en eventos.

    Punto común para importaciones locales y remotas (API MCP): el
    lector produce filas validadas; aquí se filtran duplicados, se
    regeneran embeddings y se crean los vínculos por índice.
    """
    if rama:
        for f in filas:
            f["rama"] = rama
    nuevos, mapa_ids = await _filtrar_nuevos(engine, filas, workspace_id)
    # Los índices de vínculo se refieren a `filas`; tras filtrar hay que
    # reetiquetarlos al id realmente almacenado — puede diferir del
    # uuid5 actual si la fila entró con contenido pre-normalización.
    idx_a_id = {
        i: mapa_ids.get(
            (f["fuente"] or "import", f["contenido"]), f["id"]
        )
        for i, f in enumerate(filas)
    }
    importados = await _persistir(engine, nuevos, workspace_id)
    vinculos = await _persistir_vinculos(
        engine,
        [
            (uuid.UUID(idx_a_id[a]), uuid.UUID(idx_a_id[b]))
            for a, b in vinculos_idx
            if a in idx_a_id and b in idx_a_id
        ],
    )
    return {
        "importados": importados,
        "omitidos": len(filas) - len(nuevos),
        "errores": errores,
        "vinculos": vinculos,
    }


async def inspeccionar(
    engine: AsyncEngine,
    ruta: Path,
    formato: str = "auto",
    fuente: str | None = None,
) -> dict[str, int]:
    """Vista previa de una importación: lee y cuenta sin escribir nada.

    Args:
        engine: Motor async de SQLAlchemy (para contar duplicados).
        ruta: Archivo o directorio del origen.
        formato: ``auto`` o uno de los formatos de ``importadores``.
        fuente: Nombre de fuente a usar en el conteo de duplicados.

    Returns:
        ``{"nuevas", "omitidos", "errores", "vinculos"}`` — lo que la
        ejecución real importaría, omitiría, fallaría y vincularía.
    """
    from src.cli.importadores import leer_fuente

    filas, vinculos_idx, errores = leer_fuente(ruta, formato, fuente)
    return await contar_nuevas(engine, filas, vinculos_idx, errores)


async def contar_nuevas(
    engine: AsyncEngine,
    filas: list[FilaEvento],
    vinculos_idx: list[tuple[int, int]],
    errores: int,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Cuenta lo que una importación haría sin escribir nada."""
    nuevos, _ = await _filtrar_nuevos(engine, filas, workspace_id)
    return {
        "nuevas": len(nuevos),
        "omitidos": len(filas) - len(nuevos),
        "errores": errores,
        "vinculos": len(vinculos_idx),
    }


async def _persistir_vinculos(
    engine: AsyncEngine, pares: list[tuple[uuid.UUID, uuid.UUID]]
) -> int:
    """Crea relaciones explícitas entre eventos ya persistidos."""
    if not pares:
        return 0
    from src.storage.repositories import EventoRepository, RelacionRepository

    creados = 0
    async with get_session(engine) as session:
        repo = RelacionRepository(session)
        eventos = EventoRepository(session)
        for a, b in pares:
            if a == b:
                continue
            if await eventos.obtener(a) is None or await eventos.obtener(b) is None:
                continue
            if await repo.vincular(a, b, "explicita"):
                creados += 1
        await session.commit()
    return creados


def _leer_jsonl(archivo: Path) -> tuple[list[FilaEvento], int]:
    """Lee y valida el JSONL; retorna (filas válidas, errores)."""
    filas: list[FilaEvento] = []
    errores = 0
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            cruda = json.loads(linea)
            filas.append(_validar_fila(cruda))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Línea JSONL inválida descartada: %s", exc)
            errores += 1
    return filas, errores


def _validar_fila(cruda: dict[str, Any]) -> FilaEvento:
    """Valida una línea JSONL contra el contrato de la tabla eventos.

    Sin esta validación una dimensión arbitraria o una amplitud fuera
    de rango reventaría los CHECK constraints de PostgreSQL a mitad de
    la importación en vez de contarse como línea errónea.
    """
    dimension = str(cruda["dimension"])
    if dimension not in _DIMENSIONES:
        raise ValueError(f"dimensión desconocida: {dimension}")
    timestamp = int(cruda["timestamp_unix"])
    if timestamp < 0:
        raise ValueError("timestamp_unix negativo")
    fuente = cruda.get("fuente")
    if fuente is not None and len(str(fuente)) > 50:
        raise ValueError("fuente excede 50 caracteres")
    contenido = str(cruda["contenido"])
    if not contenido or len(contenido) > 100_000:
        raise ValueError("contenido vacío o excede 100KB")
    amplitud = cruda.get("amplitud")
    if amplitud is not None:
        amplitud = float(amplitud)
        if not 0.0 <= amplitud <= 1.0:
            raise ValueError("amplitud fuera de [0, 1]")
    frecuencia = cruda.get("frecuencia")
    if frecuencia is not None and float(frecuencia) <= 0:
        raise ValueError("frecuencia debe ser positiva")
    tags = cruda.get("tags")
    if tags is not None and not isinstance(tags, list):
        raise ValueError("tags debe ser una lista")
    rama = str(cruda.get("rama") or "main")
    if not rama or len(rama) > 64:
        raise ValueError("rama vacía o excede 64 caracteres")
    return FilaEvento(
        id=str(uuid.UUID(str(cruda["id"]))),
        timestamp_unix=timestamp,
        dimension=dimension,
        fuente=None if fuente is None else str(fuente),
        contenido=contenido,
        amplitud=amplitud,
        frecuencia=None if frecuencia is None else float(frecuencia),
        tags=[str(t)[:64] for t in tags or []],
        rama=rama,
    )


async def _filtrar_nuevos(
    engine: AsyncEngine,
    filas: list[FilaEvento],
    workspace_id: uuid.UUID | None = None,
) -> tuple[list[FilaEvento], dict[tuple[str, str], str]]:
    """Descarta las filas ya presentes y devuelve el mapa de ids reales.

    Una fila ya existe si coincide su id, o su (fuente, contenido) en
    el workspace destino — este segundo chequeo cubre filas insertadas
    con contenido pre-normalización cuyo id difiere del uuid5 actual.
    El dict devuelto mapea (fuente, contenido) → id almacenado para
    reetiquetar vínculos contra la fila real."""
    if not filas:
        return [], {}
    from src.core.identidad import WORKSPACE_PERSONAL_ID

    ws = workspace_id or WORKSPACE_PERSONAL_ID
    ids = [uuid.UUID(f["id"]) for f in filas]
    async with get_session(engine) as session:
        resultado = await session.execute(
            select(EventoModel.id).where(EventoModel.id.in_(ids))
        )
        existentes = {str(fila[0]) for fila in resultado.all()}
        contenidos = await session.execute(
            select(
                EventoModel.id, EventoModel.fuente, EventoModel.contenido
            ).where(
                EventoModel.workspace_id == ws,
                EventoModel.fuente.in_(
                    {f["fuente"] or "import" for f in filas}
                ),
            )
        )
        mapa_ids = {
            (str(fuente), contenido): str(eid)
            for eid, fuente, contenido in contenidos
        }
        pares = set(mapa_ids)
    nuevos = [
        f
        for f in filas
        if f["id"] not in existentes
        and ((f["fuente"] or "import"), f["contenido"]) not in pares
    ]
    return nuevos, mapa_ids


async def _persistir(
    engine: AsyncEngine,
    filas: list[FilaEvento],
    workspace_id: uuid.UUID | None = None,
) -> int:
    """Genera embeddings por lotes e inserta las filas nuevas."""
    if not filas:
        return 0
    from src.core.identidad import WORKSPACE_PERSONAL_ID

    ws_destino = workspace_id or WORKSPACE_PERSONAL_ID
    generador = GeneradorEmbeddings()
    for inicio in range(0, len(filas), LOTE_EMBEDDING):
        lote = filas[inicio : inicio + LOTE_EMBEDDING]
        vectores = await generador.generar_lote([f["contenido"] for f in lote])
        async with get_session(engine) as session:
            for fila, vector in zip(lote, vectores, strict=True):
                session.add(
                    EventoModel(
                        id=uuid.UUID(fila["id"]),
                        timestamp_unix=fila["timestamp_unix"],
                        dimension=fila["dimension"],
                        fuente=fila["fuente"] or "import",
                        contenido=fila["contenido"],
                        embedding=vector,
                        amplitud=(
                            fila["amplitud"]
                            if fila["amplitud"] is not None
                            else 0.5
                        ),
                        frecuencia=fila["frecuencia"],
                        tags=fila.get("tags") or [],
                        rama=fila.get("rama") or "main",
                        workspace_id=ws_destino,
                    )
                )
            await indexar_chunks_lote(
                session,
                [(uuid.UUID(f["id"]), f["contenido"]) for f in lote],
                generador,
            )
            await session.commit()
    return len(filas)
