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
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.embeddings import GeneradorEmbeddings
from src.storage.database import get_session
from src.storage.models import EventoModel

logger = logging.getLogger("nous.cli.transferencia")

LOTE_EMBEDDING = 64


class FilaEvento(TypedDict):
    """Evento serializado en el formato JSONL de transferencia."""

    id: str
    timestamp_unix: int
    dimension: str
    fuente: str | None
    contenido: str
    amplitud: float | None
    frecuencia: float | None


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
            )
            salida.write(json.dumps(fila, ensure_ascii=False) + "\n")
    return len(eventos)


async def importar(engine: AsyncEngine, archivo: Path) -> dict[str, int]:
    """Importa eventos desde JSONL regenerando embeddings.

    Los eventos cuyo ``id`` ya existe se omiten (idempotente: reimportar
    el mismo archivo no duplica). Las líneas inválidas se cuentan como
    errores y se descartan sin abortar el proceso.

    Args:
        engine: Motor async de SQLAlchemy.
        archivo: Ruta del JSONL de origen.

    Returns:
        Contadores ``{"importados", "omitidos", "errores"}``.
    """
    filas, errores = _leer_jsonl(archivo)
    nuevos = await _filtrar_nuevos(engine, filas)
    omitidos = len(filas) - len(nuevos)
    importados = await _persistir(engine, nuevos)
    return {
        "importados": importados,
        "omitidos": omitidos,
        "errores": errores,
    }


def _leer_jsonl(archivo: Path) -> tuple[list[FilaEvento], int]:
    """Lee y valida el JSONL; retorna (filas válidas, errores)."""
    filas: list[FilaEvento] = []
    errores = 0
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            cruda = json.loads(linea)
            filas.append(
                FilaEvento(
                    id=str(uuid.UUID(str(cruda["id"]))),
                    timestamp_unix=int(cruda["timestamp_unix"]),
                    dimension=str(cruda["dimension"]),
                    fuente=cruda.get("fuente"),
                    contenido=str(cruda["contenido"]),
                    amplitud=cruda.get("amplitud"),
                    frecuencia=cruda.get("frecuencia"),
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Línea JSONL inválida descartada: %s", exc)
            errores += 1
    return filas, errores


async def _filtrar_nuevos(
    engine: AsyncEngine, filas: list[FilaEvento]
) -> list[FilaEvento]:
    """Descarta las filas cuyo id ya existe en la base de datos."""
    if not filas:
        return []
    ids = [uuid.UUID(f["id"]) for f in filas]
    async with get_session(engine) as session:
        resultado = await session.execute(
            select(EventoModel.id).where(EventoModel.id.in_(ids))
        )
        existentes = {str(fila[0]) for fila in resultado.all()}
    return [f for f in filas if f["id"] not in existentes]


async def _persistir(engine: AsyncEngine, filas: list[FilaEvento]) -> int:
    """Genera embeddings por lotes e inserta las filas nuevas."""
    if not filas:
        return 0
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
                    )
                )
            await session.commit()
    return len(filas)
