"""Subcomandos de identidad renderizable — ``nous persona``.

El banco de respuestas vive en el almacén de preferencias
(``persona.respuestas``) — el mismo documento que un agente puede
leer vía MCP ``leer_preferencia`` — y ``render`` lo materializa a
``SOUL.md``/``USER.md``/``ACCESS_POLICY.md``/``HEARTBEAT.md``.

Capa de presentación: parsea, delega en ``src.core.persona`` (puro)
y ``ConfiguracionRepository``, y formatea la salida.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from src.cli import ui
from src.cli.main import _json
from src.core.exceptions import ErrorValidacion
from src.core.persona import (
    ARCHIVOS,
    CLAVE_BANCO,
    catalogo,
    faltantes,
    hash_banco,
    render,
)
from src.storage.database import crear_engine, get_session
from src.storage.repositories import ConfiguracionRepository


def registrar_subparsers(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Añade ``persona`` al parser raíz."""
    persona = sub.add_parser(
        "persona",
        help="Banco de identidad + render de SOUL.md/USER.md/…",
    )
    p_sub = persona.add_subparsers(dest="accion", required=True)

    p_set = p_sub.add_parser("set", help="Saves a bank answer (upsert by key).")
    p_set.add_argument("clave", metavar="key", help="Bank key (e.g. persona.nombre).")
    p_set.add_argument("valor", metavar="answer", help="Verbatim answer from the user.")

    p_show = p_sub.add_parser("show", help="Shows the bank and its hash.")
    p_show.add_argument("--json", action="store_true", help="JSON output.")

    p_sub.add_parser("claves", help="Catalog of required keys per file.")

    p_render = p_sub.add_parser("render", help="Renders the identity files.")
    p_render.add_argument(
        "--only",
        choices=list(ARCHIVOS),
        default=None,
        help="Renders only this file.",
    )
    p_render.add_argument(
        "--out",
        type=Path,
        default=Path("."),
        help="Directorio destino (default: cwd).",
    )
    p_render.add_argument(
        "--check",
        action="store_true",
        help="Solo reporta claves faltantes; no escribe nada.",
    )
    p_render.add_argument("--json", action="store_true", help="JSON output.")


def cmd_persona(args: argparse.Namespace) -> int:
    """Enruta la acción de ``nous persona`` a su implementación."""
    tabla = {
        "set": _set,
        "show": _show,
        "claves": _claves,
        "render": _render,
    }
    fn = tabla.get(args.accion)
    if fn is None:
        return 2
    try:
        return asyncio.run(fn(args))
    except ErrorValidacion as e:
        sys.stderr.write(ui.error(f"error: {e}") + "\n")
        return 1


async def _leer_banco(engine: Any) -> dict[str, Any]:
    """Lee el banco de respuestas del almacén de preferencias."""
    async with get_session(engine) as session:
        valor = await ConfiguracionRepository(session, None).obtener(CLAVE_BANCO)
    return valor if isinstance(valor, dict) else {}


async def _set(args: argparse.Namespace) -> int:
    """``nous persona set`` — upsert de una respuesta."""
    engine = crear_engine()
    try:
        banco = await _leer_banco(engine)
        banco[args.clave] = args.valor
        async with get_session(engine) as session:
            await ConfiguracionRepository(session, None).guardar(CLAVE_BANCO, banco)
            await session.commit()
    finally:
        await engine.dispose()
    sys.stdout.write(
        ui.ok(f"{args.clave} guardada.") + " " + ui.suave(f"banco: {hash_banco(banco)}") + "\n"
    )
    return 0


async def _show(args: argparse.Namespace) -> int:
    """``nous persona show`` — banco + hash + cobertura por archivo."""
    engine = crear_engine()
    try:
        banco = await _leer_banco(engine)
    finally:
        await engine.dispose()
    salida: dict[str, Any] = {
        "hash": hash_banco(banco),
        "respuestas": banco,
        "cobertura": {
            archivo: {"faltantes": list(faltantes(archivo, banco))} for archivo in ARCHIVOS
        },
    }
    if args.json:
        _json(salida)
        return 0
    sys.stdout.write(ui.negrita(f"banco {salida['hash']}") + "\n")
    if not banco:
        sys.stdout.write(ui.suave("  (empty — `nous persona claves` shows the catalog)") + "\n")
    for clave in sorted(banco):
        sys.stdout.write(f"  {clave} = {banco[clave]}\n")
    for archivo, cob in salida["cobertura"].items():
        falta = cob["faltantes"]
        marca = ui.ok("listo") if not falta else ui.aviso(f"faltan {len(falta)}")
        sys.stdout.write(f"  {archivo}: {marca}\n")
    return 0


async def _claves(args: argparse.Namespace) -> int:
    """``nous persona claves`` — catálogo de claves requeridas."""
    for archivo, claves in catalogo().items():
        sys.stdout.write(ui.negrita(archivo) + "\n")
        for clave in claves:
            sys.stdout.write(f"  {clave}\n")
    return 0


async def _render(args: argparse.Namespace) -> int:
    """``nous persona render`` — materializa los archivos."""
    engine = crear_engine()
    try:
        banco = await _leer_banco(engine)
    finally:
        await engine.dispose()

    objetivos = [args.only] if args.only else list(ARCHIVOS)
    pendientes = {a: faltantes(a, banco) for a in objetivos if faltantes(a, banco)}
    if pendientes:
        for archivo, falta in pendientes.items():
            sys.stderr.write(ui.error(f"{archivo}: faltan {', '.join(falta)}") + "\n")
        sys.stderr.write(ui.suave("complete them with `nous persona set`") + "\n")
        return 1

    if args.check:
        salida = {"hash": hash_banco(banco), "listos": objetivos}
        if args.json:
            _json(salida)
        else:
            sys.stdout.write(ui.ok(f"todo listo (banco {salida['hash']})") + "\n")
        return 0

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    escritos: list[str] = []
    for archivo in objetivos:
        contenido = render(archivo, banco)
        destino = out / archivo
        destino.write_text(contenido, encoding="utf-8")
        escritos.append(str(destino))

    if args.json:
        _json({"hash": hash_banco(banco), "escritos": escritos})
    else:
        for ruta in escritos:
            sys.stdout.write(f"  - {ruta}\n")
        sys.stdout.write(
            ui.ok(f"{len(escritos)} archivos renderizados.")
            + " "
            + ui.suave(f"banco {hash_banco(banco)}")
            + "\n"
        )
    return 0
