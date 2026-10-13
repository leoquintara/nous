"""Importadores de fuentes externas hacia la memoria unificada.

Formatos soportados:

- ``obsidian``: un vault = directorio de ``.md``. El frontmatter
  (``tags:``) y los #hashtags alimentan ``tags``; los [[wikilinks]]
  entre notas importadas se convierten en relaciones explícitas.
- ``gbrain``: directorio de páginas ``.md`` o export JSON/JSONL con
  filas ``{fact|content|contenido|text}``.
- ``memlord``: export JSONL/JSON de ``store_memory``
  (``{name, content, memory_type, tags}``).
- ``jsonl``/``json``: formato nativo de ``nous export`` (tolerante con
  los campos anteriores).

Todo queda en la misma base distinguido por ``fuente`` — la unificación
es el punto: una sola memoria, muchos orígenes.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from src.cli.transferencia import FilaEvento, _validar_fila
from src.core.contexto import fusionar_tags

logger = logging.getLogger("nous.cli.importadores")

_FORMATOS = ("auto", "jsonl", "json", "obsidian", "gbrain", "memlord")

_PATRON_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_PATRON_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_PATRON_FM_TAGS = re.compile(r"^tags:\s*(.+)$", re.MULTILINE)


def formato_soportado() -> tuple[str, ...]:
    """Devuelve los formatos aceptados por ``nous import``."""
    return _FORMATOS


def leer_fuente(
    ruta: Path, formato: str, fuente: str | None
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Lee una fuente externa y la traduce a filas de eventos.

    Args:
        ruta: Archivo o directorio de la fuente.
        formato: ``auto`` (infiere) o uno de los formatos soportados.
        fuente: Nombre a grabar en ``eventos.fuente`` (None = el formato).

    Returns:
        (filas válidas, pares de índices a vincular, nº de errores).
    """
    if formato == "auto":
        formato = _inferir(ruta)
    nombre = fuente or formato
    if formato == "obsidian":
        return _leer_vault(ruta, nombre)
    if formato == "gbrain":
        return _leer_gbrain(ruta, nombre)
    if formato == "memlord":
        return _leer_memlord(ruta, nombre)
    return _leer_json_generico(ruta, nombre)


def _inferir(ruta: Path) -> str:
    """Deduce el formato por la forma del origen."""
    if ruta.is_dir():
        return "obsidian"
    if ruta.suffix == ".jsonl":
        return "jsonl"
    return "json"


def _fila_base(
    contenido: str,
    fuente: str,
    tags: list[str] | None = None,
    ts: int | None = None,
    amplitud: float = 0.5,
    dimension: str = "cognitiva",
    evento_id: str | None = None,
) -> FilaEvento:
    """Construye una fila válida de importación con defaults sanos."""
    return _validar_fila(
        {
            "id": evento_id or str(uuid.uuid4()),
            "timestamp_unix": ts or int(time.time()),
            "dimension": dimension,
            "fuente": fuente[:50],
            "contenido": contenido,
            "amplitud": amplitud,
            "tags": fusionar_tags(tags, contenido),
        }
    )


def _leer_vault(
    vault: Path, fuente: str
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Importa un vault Obsidian: notas → eventos, wikilinks → vínculos."""
    filas: list[FilaEvento] = []
    vinculos: list[tuple[int, int]] = []
    errores = 0
    por_nombre: dict[str, int] = {}
    enlaces: list[tuple[int, str]] = []
    for md in sorted(vault.rglob("*.md")):
        try:
            texto = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Nota ilegible %s: %s", md, exc)
            errores += 1
            continue
        tags = _tags_frontmatter(texto)
        cuerpo = _PATRON_FRONTMATTER.sub("", texto).strip()
        if not cuerpo:
            continue
        try:
            filas.append(
                _fila_base(
                    f"{md.stem}\n\n{cuerpo}",
                    fuente,
                    tags,
                    ts=int(md.stat().st_mtime),
                )
            )
            por_nombre[md.stem.lower()] = len(filas) - 1
            for m in _PATRON_WIKILINK.finditer(texto):
                enlaces.append((len(filas) - 1, m.group(1).strip().lower()))
        except ValueError as exc:
            logger.warning("Nota descartada %s: %s", md, exc)
            errores += 1
    for origen, destino in enlaces:
        if destino in por_nombre and por_nombre[destino] != origen:
            vinculos.append((origen, por_nombre[destino]))
    return filas, vinculos, errores


def _tags_frontmatter(texto: str) -> list[str]:
    """Extrae tags del frontmatter YAML (línea ``tags: a b c`` o lista)."""
    fm = _PATRON_FRONTMATTER.match(texto)
    if not fm:
        return []
    linea = _PATRON_FM_TAGS.search(fm.group(1))
    if not linea:
        return []
    crudo = linea.group(1).strip()
    if crudo.startswith("["):
        return [t.strip().strip("\"'") for t in crudo.strip("[]").split(",")]
    return crudo.split()


def _leer_gbrain(
    ruta: Path, fuente: str
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Importa páginas .md de un source gbrain o su export JSON/JSONL."""
    if ruta.is_dir():
        return _leer_vault(ruta, fuente)
    return _leer_json_generico(ruta, fuente)


def _leer_memlord(
    ruta: Path, fuente: str
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Importa un export memlord: filas con name/content/memory_type."""
    return _leer_json_generico(ruta, fuente, estilo="memlord")


def _leer_json_generico(
    ruta: Path, fuente: str, estilo: str = "nous"
) -> tuple[list[FilaEvento], list[tuple[int, int]], int]:
    """Lee JSON o JSONL tolerante: nativo Nous, memlord u OpenClaw."""
    filas: list[FilaEvento] = []
    errores = 0
    crudo: Any = None
    if ruta.suffix == ".json":
        try:
            crudo = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("JSON inválido: %s", exc)
            return [], [], 1
        lineas = crudo if isinstance(crudo, list) else [crudo]
    else:
        lineas = [
            linea
            for linea in ruta.read_text(encoding="utf-8").splitlines()
            if linea.strip()
        ]
    for linea in lineas:
        try:
            obj = linea if isinstance(linea, dict) else json.loads(linea)
            filas.append(_fila_desde_obj(obj, fuente, estilo))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Fila inválida descartada: %s", exc)
            errores += 1
    return filas, [], errores


def _fila_desde_obj(
    obj: dict[str, Any], fuente: str, estilo: str
) -> FilaEvento:
    """Normaliza un objeto JSON a FilaEvento según el estilo de origen."""
    if "contenido" in obj and "timestamp_unix" in obj:
        # Formato nativo de nous export.
        fila = _validar_fila(obj)
        fila["fuente"] = fila["fuente"] or fuente
        fila["tags"] = fusionar_tags(obj.get("tags"), fila["contenido"])
        return fila
    if estilo == "memlord" or "memory_type" in obj or "name" in obj:
        # Export memlord: {name, content, memory_type, tags}.
        contenido = str(obj.get("content") or obj.get("name") or "")
        return _fila_base(
            f"{obj.get('name', '')}\n\n{contenido}".strip(),
            fuente,
            obj.get("tags") if isinstance(obj.get("tags"), list) else None,
            amplitud=0.6 if obj.get("memory_type") == "decision" else 0.5,
        )
    # Formato flexible (gbrain/openclaw): {fact|text|content}.
    contenido = str(
        obj.get("contenido")
        or obj.get("fact")
        or obj.get("text")
        or obj.get("content")
        or ""
    )
    if not contenido:
        raise ValueError("fila sin contenido reconocible")
    ts = obj.get("timestamp_unix") or obj.get("created_at")
    return _fila_base(
        contenido,
        fuente,
        obj.get("tags") if isinstance(obj.get("tags"), list) else None,
        ts=int(ts) if isinstance(ts, int | float) else None,
    )
