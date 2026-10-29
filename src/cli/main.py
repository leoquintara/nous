"""Punto de entrada de la CLI de Nous — ``nous <comando>``.

La CLI es una capa de presentación: parsea argumentos y delega en
``src.core``/``src.storage``/``src.cli.upgrade``. Nunca contiene lógica
de negocio propia.

Códigos de salida: 0 éxito, 1 error de ejecución, 2 uso incorrecto.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import io
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from src.cli import ui
from src.cli import upgrade as up
from src.config import get_settings

logger = logging.getLogger("nous.cli")


def _parser() -> argparse.ArgumentParser:
    """Construye el parser raíz con todos los subcomandos."""
    p = argparse.ArgumentParser(
        prog="nous",
        description="Nous — persistent 4D memory for AI agents.",
    )
    p.add_argument(
        "--version",
        action="store_true",
        help="Prints the installed version and exits.",
    )
    sub = p.add_subparsers(dest="comando")

    serve = sub.add_parser("serve", help="Starts the Nous server.")
    serve.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=None,
        help="MCP transport (default: configuration value).",
    )
    serve.add_argument("--host", default=None, help="HTTP transport host.")
    serve.add_argument("--port", type=int, default=None, help="HTTP port.")
    serve.add_argument(
        "--no-dashboard",
        "--no-dashboard",
        action="store_true",
        help="Do not start the Next.js dashboard with the HTTP transport.",
    )

    v = sub.add_parser("version", help="Version and installation method.")
    v.add_argument("--json", action="store_true", help="JSON output.")

    chk = sub.add_parser("check-update", help="Checks whether a newer version exists.")
    chk.add_argument("--json", action="store_true", help="JSON output.")

    upg = sub.add_parser("upgrade", help="Upgrades Nous and migrates the database.")
    upg.add_argument(
        "--no-migrate",
        action="store_true",
        help="Swap code only; skip alembic upgrade head.",
    )
    upg.add_argument(
        "--yes",
        action="store_true",
        help="Do not ask for interactive confirmation.",
    )
    upg.add_argument("--json", action="store_true", help="JSON output.")

    doc = sub.add_parser("doctor", help="Diagnoses the local environment.")
    doc.add_argument("--json", action="store_true", help="JSON output.")

    db = sub.add_parser("db", help="Database migrations (Alembic).")
    db.add_argument(
        "accion",
        metavar="action",
        choices=["upgrade", "current", "heads", "history"],
        help="Alembic action to run.",
    )

    seed = sub.add_parser("seed", help="Seeds sample events.")
    seed.add_argument("--events", "--eventos", type=int, default=210, help="Events to seed.")

    st = sub.add_parser("stats", help="Memory statistics.")
    st.add_argument("--json", action="store_true", help="JSON output.")

    ex = sub.add_parser("export", help="Exports memory to JSONL.")
    ex.add_argument("archivo", metavar="path", type=Path, help="Destination path (.jsonl).")

    im = sub.add_parser(
        "import",
        help="Imports memory from obsidian/gbrain/memlord/JSON/JSONL.",
    )
    im.add_argument(
        "origen",
        metavar="source_path",
        type=Path,
        help="File (.json/.jsonl) or directory (.md vault).",
    )
    im.add_argument(
        "--format",
        "--formato",
        choices=["auto", "jsonl", "json", "obsidian", "gbrain", "memlord"],
        default="auto",
        help="Source format (default: auto by extension).",
    )
    im.add_argument(
        "--source",
        "--fuente",
        default=None,
        help="eventos.fuente name for the import (default: format).",
    )
    im.add_argument(
        "--branch",
        "--rama",
        default=None,
        help="Universe line to record the import into.",
    )
    im.add_argument(
        "--workspace",
        default=None,
        help="Slug or UUID of the target workspace (default: personal).",
    )

    sk = sub.add_parser("skills", help="Installs the Nous skills/agents into the project.")
    sk.add_argument(
        "accion",
        metavar="action",
        choices=["install", "list"],
        help="install copies the artifacts; list shows what is available.",
    )
    sk.add_argument(
        "--target",
        default=".agents",
        help=(
            "Destino: .agents (default), .claude, .opencode, .cursor " "o una ruta personalizada."
        ),
    )
    sk.add_argument(
        "--dir",
        type=Path,
        default=Path("."),
        help="Project root to install into (default: cwd).",
    )

    sub.add_parser("benchmark", help="Runs the benchmark suite.")

    prune = sub.add_parser("prune", help="Synaptic pruning: removes weak, old imprints.")
    prune.add_argument(
        "--days",
        "--dias",
        type=int,
        default=90,
        help="Only events older than N days (default 90).",
    )
    prune.add_argument(
        "--amplitude",
        "--amplitud",
        type=float,
        default=0.3,
        help="Prunes only imprints with amplitude <= this value (default 0.3).",
    )
    prune.add_argument(
        "--apply",
        "--aplicar",
        action="store_true",
        help="Actually deletes; without it only shows the count (dry-run).",
    )
    prune.add_argument("--json", action="store_true", help="JSON output.")

    con = sub.add_parser(
        "consolidate",
        aliases=["consolidar"],
        help="Sleep-consolidation: strengthens, promotes to curated and archives.",
    )
    con.add_argument("--json", action="store_true", help="JSON output.")

    sub.add_parser(
        "intentions",
        aliases=["intenciones"],
        help="Lists pending prospective intentions.",
    )

    sub.add_parser(
        "reviews",
        aliases=["repasos"],
        help="Lists memories whose optimal (SRS) review has elapsed.",
    )

    sub.add_parser(
        "archived",
        aliases=["archivados"],
        help="Lists memories archived by consolidation.",
    )

    res = sub.add_parser(
        "restore",
        aliases=["restaurar"],
        help="Restores an archived memory (undoes forgetting).",
    )
    res.add_argument("evento_id", metavar="event_id", help="UUID of the archived event.")

    olv = sub.add_parser(
        "forget",
        aliases=["olvidar"],
        help="Directed forgetting: accelerates the natural decay of a memory.",
    )
    olv.add_argument("evento_id", metavar="event_id", help="UUID of the event to forget.")
    olv.add_argument(
        "--undo",
        "--deshacer",
        action="store_true",
        help="Removes the mark: the memory decays normally again.",
    )

    sub.add_parser(
        "quarantine",
        aliases=["cuarentena"],
        help="Lists memories quarantined by an untrusted source.",
    )

    cf = sub.add_parser(
        "trust",
        aliases=["confiar"],
        help="Approves a source (or distrusts it with --distrust).",
    )
    cf.add_argument("fuente", help="Source name.")
    cf.add_argument(
        "--distrust",
        "--desconfiar",
        action="store_true",
        help="Marca la fuente como no confiable (cuarentena).",
    )

    ap = sub.add_parser(
        "snooze",
        aliases=["aplazar"],
        help="Snooze: pospone el disparo de una intención pendiente.",
    )
    ap.add_argument("intencion_id", metavar="intention_id", help="UUID of the intention.")
    ap.add_argument("horas", type=int, help="Horas a posponer.")

    al = sub.add_parser(
        "activate-license",
        help="Activates a Pro license key (~/.nous/license.jwt).",
    )
    al.add_argument("key", help="License key (JWT or landing serial).")
    al.add_argument("--json", action="store_true", help="JSON output.")

    # Neuro-5 — ciclo de sesión completo.
    sub.add_parser("sessions", aliases=["sesiones"], help="Session history (memory boundaries).")
    ho = sub.add_parser("handoff", help="Handoff of the last closed session.")
    ho.add_argument(
        "sesion_id",
        metavar="session_id",
        nargs="?",
        default=None,
        help="Session UUID (default: last closed).",
    )
    sub.add_parser("censors", aliases=["censores"], help="Lists active guardrails.")
    cn = sub.add_parser("censor", help="Registers a guardrail (learned rule).")
    cn.add_argument("regla", metavar="rule", help="La regla en texto.")
    cn.add_argument(
        "--block",
        "--bloquear",
        action="store_true",
        help="Block severity — vetoes the action (default: warn).",
    )
    cn.add_argument("--domain", "--dominio", default=None, help="Scope of the rule (e.g. git).")
    ver = sub.add_parser(
        "verify", aliases=["verificar"], help="Verifies an action against the censors."
    )
    ver.add_argument("accion", metavar="action", help="The proposed action as text.")
    sub.add_parser(
        "calibration",
        aliases=["calibracion"],
        help="Brier score and reliability curve of predictions.",
    )
    pre = sub.add_parser(
        "predict", aliases=["predecir"], help="Registers a prediction with confidence."
    )
    pre.add_argument("afirmacion", metavar="claim", help="What is being predicted.")
    pre.add_argument(
        "--confidence",
        "--confianza",
        type=float,
        default=0.7,
        help="Probabilidad declarada 0.0-1.0 (default 0.7).",
    )
    res2 = sub.add_parser("resolve", aliases=["resolver"], help="Resolves a prediction (hit/miss).")
    res2.add_argument("prediccion_id", metavar="prediction_id", help="UUID of the prediction.")
    res2.add_argument(
        "--miss",
        "--fallo",
        action="store_true",
        help="The claim missed (default: hit).",
    )
    sub.add_parser(
        "predictions", aliases=["predicciones"], help="Lista predicciones abiertas y resueltas."
    )
    sub.add_parser("pending", aliases=["pendientes"], help="Staged writes pending approval.")
    ap2 = sub.add_parser("approve", aliases=["aprobar"], help="Approves a staged write (or --all).")
    ap2.add_argument(
        "evento_id", metavar="event_id", nargs="?", default=None, help="UUID of the proposal."
    )
    ap2.add_argument("--all", "--todo", action="store_true", help="Approves every staged write.")
    rec = sub.add_parser(
        "reject", aliases=["rechazar"], help="Rejects a staged write (discards it)."
    )
    rec.add_argument("evento_id", metavar="event_id", help="UUID of the proposal.")
    lag = sub.add_parser("gaps", aliases=["lagunas"], help="Gap map — what memory does not know.")
    lag.add_argument("consulta", metavar="query", help="El tema a sondear.")
    lag.add_argument(
        "--top",
        type=int,
        default=10,
        help="Resultados a evaluar (default 10).",
    )

    from src.cli.identidad import registrar_subparsers
    from src.cli.persona import registrar_subparsers as reg_persona

    registrar_subparsers(sub)
    reg_persona(sub)
    return p


def _json(objeto: Any) -> None:
    """Imprime un objeto como JSON en stdout."""
    sys.stdout.write(json.dumps(objeto, ensure_ascii=False) + "\n")


def _cmd_version(como_json: bool) -> int:
    """Implementa ``nous version``."""
    datos = {
        "version": up.version_actual(),
        "metodo_instalacion": up.detectar_metodo(),
        "python": sys.version.split()[0],
    }
    if como_json:
        _json(datos)
    else:
        metodo = datos["metodo_instalacion"]
        pyver = datos["python"]
        sys.stdout.write(
            f"{ui.acento('nous')} {datos['version']} "
            f"{ui.suave(f'({metodo}, python {pyver})')}\n"
        )
    return 0


def _cmd_check_update(como_json: bool) -> int:
    """Implementa ``nous check-update``."""
    resultado = up.check_update()
    if como_json:
        _json(resultado.a_dict())
        return 0
    sys.stdout.write(f"Versión actual: {resultado.version_actual}\n")
    sys.stdout.write(f"Método: {resultado.metodo}\n")
    if resultado.version_reciente:
        sys.stdout.write(f"Más reciente: {resultado.version_reciente}\n")
    if resultado.actualizacion_disponible:
        sys.stdout.write(ui.ok("Update available") + f" → {ui.info(resultado.comando_upgrade)}\n")
    else:
        sys.stdout.write(ui.suave(resultado.detalle) + "\n")
    return 0


def _cmd_upgrade(no_migrate: bool, yes: bool, como_json: bool) -> int:
    """Implementa ``nous upgrade`` con confirmación y pasos."""
    info = up.check_update()
    if info.actualizacion_disponible is False:
        sys.stdout.write(ui.ok("Already on the latest version.") + "\n")
        return 0
    if not yes and not _confirmar():
        sys.stdout.write("Upgrade cancelled.\n")
        return 1
    resultado = up.upgrade(no_migrate=no_migrate)
    if como_json:
        _json(resultado.a_dict())
    else:
        for paso in resultado.pasos:
            marca = ui.ok("✓") if paso.ok else ui.error("✗")
            sys.stdout.write(f"  {marca} {paso.nombre}\n")
        estilo_detalle = ui.ok if resultado.ok else ui.error
        sys.stdout.write(estilo_detalle(resultado.detalle) + "\n")
    return 0 if resultado.ok else 1


def _confirmar() -> bool:
    """Pide confirmación interactiva para el upgrade."""
    try:
        return input("Continue with the upgrade? [y/N] ").lower() == "s"
    except (EOFError, KeyboardInterrupt):
        return False


async def _diagnostico() -> dict[str, object]:
    """Reúne los chequeos de ``nous doctor`` sin imprimir nada."""
    from sqlalchemy import text

    from src.storage.database import crear_engine, get_session

    settings = get_settings()
    checks: dict[str, object] = {
        "version": up.version_actual(),
        "metodo_instalacion": up.detectar_metodo(),
        "token_configurado": settings.api_token is not None,
        "hf_offline": settings.hf_offline,
    }
    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            await session.execute(text("SELECT 1"))
            total = await session.scalar(text("SELECT count(*) FROM eventos"))
        checks["base_de_datos"] = "ok"
        checks["eventos"] = total
    except Exception as exc:  # diagnóstico: reportar el fallo, no abortar
        checks["base_de_datos"] = f"error: {type(exc).__name__}"
    finally:
        await engine.dispose()
    checks["modelo_cacheado"] = _modelo_en_cache(settings.embedding_model)
    return checks


def _modelo_en_cache(modelo: str) -> bool:
    """Indica si el modelo de embeddings está en la caché local de HF."""
    try:
        from huggingface_hub import try_to_load_from_cache

        # try_to_load_from_cache devuelve la ruta, None si no hay caché,
        # o el sentinel _CACHED_NO_EXIST (que no es str ni Path).
        ruta = try_to_load_from_cache(modelo, "config.json")
        return isinstance(ruta, str | Path)
    except Exception:  # ausencia de huggingface_hub no rompe el diagnóstico
        return False


def _cmd_doctor(como_json: bool) -> int:
    """Implementa ``nous doctor``."""
    datos = asyncio.run(_diagnostico())
    if como_json:
        _json(datos)
    else:
        for clave, valor in datos.items():
            if valor in ("ok", True):
                render = ui.ok(str(valor))
            elif valor in (False, "error", "fallo", "offline"):
                render = ui.error(str(valor))
            else:
                render = str(valor)
            sys.stdout.write(f"{ui.suave(clave)}: {render}\n")
    return 0 if datos["base_de_datos"] == "ok" else 1


def _cmd_activate_license(key: str, como_json: bool) -> int:
    """Implementa ``nous activate-license`` (ADR-001).

    Valida la key en local y la persiste en ``~/.nous/license.jwt``;
    el servidor la descubre como fallback de ``NOUS_LICENSE_KEY``.
    Delega en ``src.core.license.activar_licencia`` — el mismo punto
    de activación que ``POST /api/licencia/activar``.

    Args:
        key: License key (JWT RS256 u opaca de la landing).
        como_json: True imprime el resultado como JSON.

    Returns:
        0 si la licencia quedó activada, 1 si es inválida/expirada.
    """
    from src.core import license as lic_mod
    from src.core.exceptions import ErrorValidacion

    try:
        lic = lic_mod.activar_licencia(key)
    except ErrorValidacion:
        if como_json:
            _json({"activada": False, "motivo": "invalid or expired key"})
        else:
            sys.stderr.write(ui.error("Invalid or expired license key — nothing was saved.") + "\n")
        return 1
    if como_json:
        _json(
            {
                "activada": True,
                "tier": lic.tier,
                "expira": lic.expira,
                "firma_verificada": lic.firma_verificada,
                "archivo": str(lic_mod.ruta_licencia()),
            }
        )
    else:
        firma = " (firma verificada)" if lic.firma_verificada else ""
        sys.stdout.write(
            ui.ok(f"Licencia {lic.tier} activada{firma}")
            + ui.suave(f" → {lic_mod.ruta_licencia()}")
            + "\n"
        )
    return 0


def _cmd_db(accion: str) -> int:
    """Delega en Alembic las acciones de migración.

    Sin ``alembic.ini`` — el wheel no lo lleva. ``config_alembic()``
    resuelve ``script_location`` desde el paquete instalado, así que
    ``nous db …`` funciona tras ``pip install`` desde cualquier cwd.
    """
    from alembic import command

    from src.storage.migraciones import config_alembic

    cfg = config_alembic()
    acciones = {
        "upgrade": lambda: command.upgrade(cfg, "head"),
        "current": lambda: command.current(cfg),
        "heads": lambda: command.heads(cfg),
        "history": lambda: command.history(cfg),
    }
    acciones[accion]()
    return 0


async def _stats() -> dict[str, object]:
    """Calcula conteos por dimensión y rango temporal de la memoria."""
    from sqlalchemy import func, select

    from src.storage.database import crear_engine, get_session
    from src.storage.models import EventoModel

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            filas = await session.execute(
                select(EventoModel.dimension, func.count(EventoModel.id)).group_by(
                    EventoModel.dimension
                )
            )
            total = await session.scalar(select(func.count(EventoModel.id)))
            rango = await session.execute(
                select(
                    func.min(EventoModel.timestamp_unix),
                    func.max(EventoModel.timestamp_unix),
                )
            )
            minimo, maximo = rango.one()
        return {
            "total": total or 0,
            "por_dimension": dict(filas.all()),
            "primer_evento": minimo,
            "ultimo_evento": maximo,
        }
    finally:
        await engine.dispose()


async def _cmd_seed(eventos: int) -> int:
    """Implementa ``nous seed`` reutilizando scripts.seed_db."""
    from scripts.seed_db import sembrar

    sembrados = await sembrar(eventos)
    sys.stdout.write(ui.ok(f"Sembrados {sembrados} eventos.") + "\n")
    return 0


async def _cmd_export(archivo: Path) -> int:
    """Implementa ``nous export``."""
    from src.cli.transferencia import exportar
    from src.storage.database import crear_engine

    engine = crear_engine()
    try:
        n = await exportar(engine, archivo)
    finally:
        await engine.dispose()
    sys.stdout.write(ui.ok(f"Exportados {n} eventos a {archivo}") + "\n")
    return 0


async def _cmd_import(
    origen: Path,
    formato: str,
    fuente: str | None,
    rama: str | None,
    workspace: str | None,
) -> int:
    """Implementa ``nous import`` para archivos y directorios."""
    from src.cli.identidad import _workspace_por_slug
    from src.cli.transferencia import importar
    from src.core.exceptions import ErrorValidacion
    from src.storage.database import crear_engine

    if not await asyncio.to_thread(origen.exists):
        sys.stderr.write(ui.error(f"error: {origen} no existe") + "\n")
        return 1
    engine = crear_engine()
    try:
        ws_id = None
        if workspace:
            try:
                ws = await _workspace_por_slug(engine, workspace)
            except ErrorValidacion as e:
                sys.stderr.write(ui.error(f"error: {e}") + "\n")
                return 1
            ws_id = ws.id
        resumen = await importar(engine, origen, formato, fuente, rama, ws_id)
    finally:
        await engine.dispose()
    exito = resumen["errores"] == 0 or resumen["importados"] > 0
    estilo_resumen = ui.ok if exito else ui.error
    sys.stdout.write(
        estilo_resumen(
            "Importados {importados}, omitidos {omitidos}, errores {errores},"
            " vínculos {vinculos}".format(**resumen)
        )
        + "\n"
    )
    return 0 if exito else 1


async def _poda(dias: int, amplitud: float, aplicar: bool) -> dict[str, object]:
    """Cuenta o elimina los eventos débiles anteriores a N días."""
    import time as _time

    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import EventoRepository

    antes_de = int(_time.time()) - dias * 86_400
    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            repo = EventoRepository(session)
            if aplicar:
                eliminados = await repo.podar(antes_de, amplitud, 0)
                await session.commit()
                return {"eliminados": eliminados, "aplicado": 1}
            return {"candidatos": await repo.contar_podables(antes_de, amplitud, 0), "aplicado": 0}
    finally:
        await engine.dispose()


async def _consolidar_cli() -> dict[str, object]:
    """Ejecuta la corrida de sueño y persiste su reporte (ADR-003)."""
    import time
    import uuid

    from src.core.identidad import WORKSPACE_PERSONAL_ID
    from src.core.memoria.consolidacion import (
        EventoConsolidable,
        decidir_consolidacion,
        resumen_legible,
    )
    from src.core.memoria.rem import pares_rem
    from src.core.memoria.ritmos import ventana_optima_sueno
    from src.core.memoria.semanticizacion import EpisodioGist, plan_gists
    from src.core.memoria.tipos import TipoMemoria
    from src.core.modelos import Dimension
    from src.storage.database import crear_engine, get_session
    from src.storage.models import EventoModel, ReporteConsolidacionModel
    from src.storage.repositories import (
        EventoRepository,
        RelacionRepository,
        ReporteConsolidacionRepository,
    )

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            repo = EventoRepository(session)
            modelos = await repo.listar_para_consolidacion()
            plan = decidir_consolidacion(
                [
                    EventoConsolidable(
                        id=str(m.id),
                        timestamp_unix=m.timestamp_unix,
                        dimension=Dimension(m.dimension),
                        tipo=m.tipo,
                        amplitud=m.amplitud,
                        refuerzos=m.refuerzos,
                        weight=m.weight,
                        tipo_memoria=(TipoMemoria(m.tipo_memoria) if m.tipo_memoria else None),
                        ultimo_acceso=m.ultimo_acceso,
                        archivado=m.archivado,
                        curado=m.curado,
                        inaugura_epoca=m.inaugura_epoca,
                        olvido_dirigido=m.olvido_dirigido,
                    )
                    for m in modelos
                ]
            )
            # Overflow-pressure (Neuro-5): el tier curado tiene
            # presupuesto — el sueño promueve solo lo que cabe.
            promovidos = list(plan.promover)
            if promovidos:
                presupuesto = get_settings().curado_max_chars
                usado = await repo.chars_curados()
                por_id = {str(m.id): m for m in modelos}
                caben = []
                for eid in promovidos:
                    modelo = por_id.get(eid)
                    largo = len(modelo.contenido) if modelo else 0
                    if usado + largo <= presupuesto:
                        caben.append(eid)
                        usado += largo
                promovidos_fuera = len(promovidos) - len(caben)
                promovidos = caben
            else:
                promovidos_fuera = 0
            await repo.aplicar_consolidacion(
                plan.fortalecer,
                promovidos,
                plan.archivar,
                plan.ejecutado_en,
            )
            # Semanticización: episodios afines → gist semántico
            # vinculado por aristas "gist" — el sueño abstrae.
            episodicos = await repo.episodicas_con_embedding()
            gists = plan_gists(
                [
                    EpisodioGist(
                        id=str(e.id),
                        contenido=e.contenido,
                        weight=e.weight,
                        embedding=e.embedding,
                    )
                    for e in episodicos
                ]
            )
            ahora = int(time.time())
            relaciones = RelacionRepository(session)
            gist_ids: list[str] = []
            for gist in gists:
                evento_gist = EventoModel(
                    timestamp_unix=ahora,
                    dimension="cognitiva",
                    fuente="sueno",
                    contenido=gist.contenido,
                    amplitud=0.8,
                    tipo="hecho",
                    tipo_memoria="semantica",
                    tags=["gist", "semanticizacion"],
                    workspace_id=WORKSPACE_PERSONAL_ID,
                )
                await repo.crear(evento_gist)
                for miembro in gist.miembros:
                    await relaciones.vincular(evento_gist.id, uuid.UUID(miembro), "gist")
                gist_ids.append(str(evento_gist.id))
            # Fase REM: pares de similitud media reciben aristas
            # "rem" — la recombinación creativa del sueño.
            pares_soñados = pares_rem(
                [
                    EpisodioGist(
                        id=str(e.id),
                        contenido=e.contenido,
                        weight=e.weight,
                        embedding=e.embedding,
                    )
                    for e in episodicos
                ]
            )
            rem_ids: list[tuple[str, str]] = []
            for id_a, id_b in pares_soñados:
                if await relaciones.vincular(uuid.UUID(id_a), uuid.UUID(id_b), "rem"):
                    rem_ids.append((id_a, id_b))
            await ReporteConsolidacionRepository(session).crear(
                ReporteConsolidacionModel(
                    workspace_id=WORKSPACE_PERSONAL_ID,
                    ejecutado_en=plan.ejecutado_en,
                    fortalecidos=len(plan.fortalecer),
                    archivados=len(plan.archivar),
                    promovidos=len(promovidos),
                    detalle={
                        "fortalecidos": plan.fortalecer,
                        "promovidos": promovidos,
                        "archivados": plan.archivar,
                        "gists": gist_ids,
                        "rem": rem_ids,
                        "salvados": plan.salvados,
                        "curado_fuera_presupuesto": promovidos_fuera,
                    },
                )
            )
            await session.commit()
        ventana = ventana_optima_sueno([m.timestamp_unix for m in modelos])
        return {
            "evaluados": len(modelos),
            "fortalecidos": len(plan.fortalecer),
            "promovidos": len(promovidos),
            "curado_fuera_presupuesto": promovidos_fuera,
            "archivados": len(plan.archivar),
            "semanticos": len(gist_ids),
            "rem": len(rem_ids),
            "salvados": len(plan.salvados),
            "resumen": resumen_legible(plan),
            "ventana_sugerida": {
                "hora_inicio": ventana.hora_inicio,
                "duracion_horas": ventana.duracion_horas,
                "confianza": ventana.confianza,
            },
        }
    finally:
        await engine.dispose()


async def _listar_intenciones_cli() -> list[dict[str, object]]:
    """Lists pending prospective intentions."""
    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import IntencionProspectivaRepository

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            filas = await IntencionProspectivaRepository(session).listar(ejecutadas=False)
        return [
            {
                "id": str(f.id),
                "intencion": f.intencion,
                "trigger_tipo": f.trigger_tipo,
                "trigger_valor": f.trigger_valor,
                "creada_en": f.creada_en,
            }
            for f in filas
        ]
    finally:
        await engine.dispose()


async def _listar_repasos_cli() -> list[dict[str, object]]:
    """Lists memories whose optimal (SRS) review has elapsed."""
    import time

    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import EventoRepository

    engine = crear_engine()
    try:
        ahora = int(time.time())
        async with get_session(engine) as session:
            filas = await EventoRepository(session).pendientes_revision(ahora)
        return [
            {
                "id": str(f.id),
                "contenido": f.contenido[:120],
                "proxima_revision": f.proxima_revision,
                "dias_atraso": round((ahora - (f.proxima_revision or 0)) / 86400.0, 1),
            }
            for f in filas
        ]
    finally:
        await engine.dispose()


async def _listar_archivados_cli() -> list[dict[str, object]]:
    """Lista las memorias archivadas recuperables."""
    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import EventoRepository

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            filas = await EventoRepository(session).listar_archivados()
        return [
            {
                "id": str(f.id),
                "contenido": f.contenido[:120],
                "archivado_en": f.archivado_en,
                "razon_archivo": f.razon_archivo,
            }
            for f in filas
        ]
    finally:
        await engine.dispose()


async def _restaurar_cli(evento_id: str) -> bool:
    """Restaura un evento archivado por UUID."""
    import uuid as _uuid

    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import EventoRepository

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            restaurado = await EventoRepository(session).restaurar(_uuid.UUID(evento_id))
            await session.commit()
        return restaurado
    finally:
        await engine.dispose()


async def _olvidar_cli(evento_id: str, deshacer: bool) -> bool:
    """Marca/desmarca el olvido dirigido de un evento por UUID."""
    import uuid as _uuid

    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import EventoRepository

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            ok = await EventoRepository(session).marcar_olvido_dirigido(
                _uuid.UUID(evento_id), not deshacer
            )
            await session.commit()
        return ok
    finally:
        await engine.dispose()


async def _listar_cuarentena_cli() -> list[dict[str, object]]:
    """Lista los eventos en cuarentena por fuente no confiable."""
    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import EventoRepository

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            filas = await EventoRepository(session).listar_cuarentena()
        return [
            {
                "id": str(f.id),
                "contenido": f.contenido[:120],
                "fuente": f.fuente,
            }
            for f in filas
        ]
    finally:
        await engine.dispose()


async def _confiar_cli(fuente: str, confiable: bool) -> None:
    """Registra la fuente si falta y ajusta su confianza."""
    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import EventoRepository, FuenteRepository

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            repo_f = FuenteRepository(session)
            await repo_f.crear(fuente)
            await repo_f.marcar_confiable(fuente, confiable)
            if confiable:
                await EventoRepository(session).aprobar_cuarentena_fuente(fuente)
            await session.commit()
    finally:
        await engine.dispose()


async def _aplazar_cli(intencion_id: str, horas: int) -> bool:
    """Postpones an intention firing by N hours (snooze)."""
    import time
    import uuid as _uuid

    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import IntencionProspectivaRepository

    engine = crear_engine()
    try:
        hasta = int(time.time()) + horas * 3600
        async with get_session(engine) as session:
            ok = await IntencionProspectivaRepository(session).aplazar(
                _uuid.UUID(intencion_id), hasta
            )
            await session.commit()
        return ok
    finally:
        await engine.dispose()


def _cmd_benchmark() -> int:
    """Ejecuta la suite de benchmarks como subproceso."""
    import subprocess

    proc = subprocess.run([sys.executable, "-m", "scripts.benchmark_search"], check=False)
    return proc.returncode


def _dir_dashboard() -> Path | None:
    """Localiza el proyecto dashboard (repo hermano o checkout viejo).

    ADR-001: el dashboard vive en ``../nous-dashboard``; se acepta el
    legado ``dashboard/`` dentro del repo por transición.
    """
    for base in (Path.cwd(), up.RAIZ_PROYECTO):
        for nombre in ("nous-dashboard", "dashboard"):
            candidato = base / nombre / "package.json"
            if candidato.is_file():
                return candidato.parent
        hermano = base.parent / "nous-dashboard" / "package.json"
        if hermano.is_file():
            return hermano.parent
    return None


def _hay_dashboard_empaquetado() -> bool:
    """Indica si el paquete incluye el build estático del dashboard."""
    estatico = Path(__file__).resolve().parents[1] / "dashboard_static" / "index.html"
    return estatico.is_file()


def _detener_dashboard(proc: subprocess.Popen[bytes]) -> None:
    """Termina el árbol del dashboard al salir del servidor.

    En Windows el proceso raíz es un cmd/npm envoltorio: se usa
    ``taskkill /T`` para matar también a node. En POSIX el hijo vive
    en su propia sesión y se cierra con SIGTERM al grupo.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return
    killpg = getattr(os, "killpg", None)  # solo POSIX
    if killpg is None:
        proc.terminate()
        return
    try:
        killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()


def _lanzar_dashboard(directorio: Path) -> subprocess.Popen[bytes] | None:
    """Arranca el dashboard Next.js en modo dev como subproceso.

    Corre en su **propio grupo de proceso**: Ctrl+C solo llega a
    Nous, y ``atexit`` elimina el árbol completo al salir — sin
    nodos huérfanos en :3000. (``next start`` no aplica: el build
    de producción es ``output: export`` para servirlo estático.)

    Args:
        directorio: Raíz del proyecto dashboard.

    Returns:
        El proceso lanzado, o None si yarn no está disponible.
    """
    yarn = shutil.which("yarn.cmd") or shutil.which("yarn")
    if yarn is None:
        return None
    if sys.platform == "win32":
        proc = subprocess.Popen(
            f'"{yarn}" dev',
            cwd=directorio,
            shell=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        proc = subprocess.Popen([yarn, "dev"], cwd=directorio, start_new_session=True)
    atexit.register(_detener_dashboard, proc)
    return proc


async def _chequear_db() -> str | None:
    """Verifica que PostgreSQL responde antes de levantar el servidor.

    Returns:
        None si la conexión responde; mensaje de error legible si no.
    """
    from sqlalchemy import text

    from src.storage.database import crear_engine

    engine = crear_engine()
    try:
        async with engine.connect() as conexion:
            await conexion.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — pre-flight convierte
        return f"{type(exc).__name__}: {exc}"  # cualquier fallo de conexión a mensaje
    finally:
        await engine.dispose()
    return None


def _cmd_serve(args: argparse.Namespace) -> int:
    """Configura el entorno y delega en ``src.main.main()``."""
    if args.transport:
        os.environ["NOUS_MCP_TRANSPORT"] = args.transport
    if args.host:
        os.environ["NOUS_API_HOST"] = args.host
    if args.port:
        os.environ["NOUS_API_PORT"] = str(args.port)
    settings = get_settings()
    transporte = args.transport or settings.mcp_transport
    if transporte == "stdio":
        if sys.stdin.isatty():
            # Cada Enter envía una línea vacía que el SDK marca como
            # JSON-RPC inválido — es cosmético, pero explicarlo ahorra
            # la confusión de un "Internal Server Error" en consola.
            sys.stderr.write(
                ui.aviso(
                    "stdio es para clientes MCP — cada Enter imprime un "
                    "error de protocolo inofensivo.\n"
                    "For manual use: nous serve --transport http"
                )
                + "\n"
            )
    elif not args.no_dashboard:
        directorio = _dir_dashboard()
        if directorio is not None:
            if _lanzar_dashboard(directorio) is None:
                sys.stderr.write(ui.aviso("yarn no está en PATH — dashboard no lanzado.\n"))
            else:
                sys.stderr.write(ui.info(f"Dashboard Next.js dev en {directorio}\n"))
        elif _hay_dashboard_empaquetado():
            sys.stderr.write(
                ui.info(
                    f"Dashboard incluido — se sirve en "
                    f"http://{settings.api_host}:{settings.api_port}/\n"
                )
            )
        else:
            sys.stderr.write(
                ui.suave(
                    "Dashboard no disponible (sin checkout ni build "
                    "empaquetado); solo API+MCP.\n"
                )
            )
    problema_db = asyncio.run(_chequear_db())
    if problema_db is not None:
        sys.stderr.write(
            ui.error(f"PostgreSQL inalcanzable: {problema_db}\n")
            + ui.suave("Revisa NOUS_DATABASE_URL y que el servidor esté arriba.\n")
        )
        return 1
    from src.main import main as servidor_main

    try:
        servidor_main()
    except KeyboardInterrupt:
        # stdio/no-uvicorn: el interrupt llega limpio; uvicorn lo
        # gestiona solo con shutdown graceful.
        sys.stderr.write(ui.suave("\nNous detenido.\n"))
        return 130
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la CLI; retorna el código de salida.

    Args:
        argv: Argumentos de línea de comando (None = sys.argv).

    Returns:
        0 éxito, 1 error de ejecución, 2 uso incorrecto (argparse).
    """
    # Consolas Windows cp1252 no codifican →, ✓, ✗: forzar UTF-8.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    if args.version or args.comando == "version":
        return _cmd_version(getattr(args, "json", False))
    if args.comando is None:
        _parser().print_help()
        return 0
    return _despachar(args)


_COMANDO_ALIAS: dict[str, str] = {
    "consolidar": "consolidate",
    "intenciones": "intentions",
    "repasos": "reviews",
    "archivados": "archived",
    "restaurar": "restore",
    "olvidar": "forget",
    "cuarentena": "quarantine",
    "confiar": "trust",
    "aplazar": "snooze",
    "sesiones": "sessions",
    "censores": "censors",
    "verificar": "verify",
    "calibracion": "calibration",
    "predecir": "predict",
    "resolver": "resolve",
    "predicciones": "predictions",
    "pendientes": "pending",
    "aprobar": "approve",
    "rechazar": "reject",
    "lagunas": "gaps",
    "canjear": "redeem",
}


def _despachar(args: argparse.Namespace) -> int:
    """Enruta el subcomando parseado a su implementación."""
    comando = _COMANDO_ALIAS.get(args.comando, args.comando)
    if comando == "serve":
        return _cmd_serve(args)
    if comando == "check-update":
        return _cmd_check_update(args.json)
    if comando == "upgrade":
        return _cmd_upgrade(args.no_migrate, args.yes, args.json)
    if comando == "doctor":
        return _cmd_doctor(args.json)
    if comando == "db":
        return _cmd_db(args.accion)
    if comando == "stats":
        datos = asyncio.run(_stats())
        _json(datos) if args.json else _imprimir_stats(datos)
        return 0
    if comando == "seed":
        return asyncio.run(_cmd_seed(args.events))
    if comando == "export":
        return asyncio.run(_cmd_export(args.archivo))
    if comando == "import":
        return asyncio.run(
            _cmd_import(
                args.origen,
                args.format,
                args.source,
                args.branch,
                args.workspace,
            )
        )
    if comando == "skills":
        from src.cli.skills import cmd_skills

        return cmd_skills(args.accion, args.target, args.dir)
    if comando == "benchmark":
        return _cmd_benchmark()
    if comando == "activate-license":
        return _cmd_activate_license(args.key, args.json)
    if comando in ("users", "workspaces", "keys"):
        from src.cli.identidad import cmd_identidad

        return cmd_identidad(comando, args)
    if comando == "persona":
        from src.cli.persona import cmd_persona

        return cmd_persona(args)
    if comando == "prune":
        datos = asyncio.run(_poda(args.days, args.amplitude, args.apply))
        if args.json:
            _json(datos)
        elif datos["aplicado"]:
            sys.stdout.write(ui.ok(f"Podados {datos['eliminados']} eventos.") + "\n")
        else:
            sys.stdout.write(
                ui.aviso(f"{datos['candidatos']} eventos podables " "(use --apply to delete them).")
                + "\n"
            )
        return 0
    if comando == "consolidate":
        datos = asyncio.run(_consolidar_cli())
        if args.json:
            _json(datos)
        else:
            sys.stdout.write(str(datos["resumen"]) + "\n")
            sys.stdout.write(
                ui.suave(
                    f"evaluados={datos['evaluados']} "
                    f"promovidos={datos['promovidos']} "
                    f"fortalecidos={datos['fortalecidos']} "
                    f"archivados={datos['archivados']} "
                    f"semanticos={datos['semanticos']}"
                )
                + "\n"
            )
            v = cast(dict[str, Any], datos["ventana_sugerida"])
            sys.stdout.write(
                ui.suave(
                    f"ventana óptima de sueño: {v['hora_inicio']}:00 "
                    f"(+{v['duracion_horas']}h, "
                    f"confianza {v['confianza']})"
                )
                + "\n"
            )
        return 0
    if comando == "intentions":
        filas = asyncio.run(_listar_intenciones_cli())
        if not filas:
            sys.stdout.write(ui.suave("No pending intentions.\n"))
        for f in filas:
            sys.stdout.write(f"{ui.acento(str(f['trigger_tipo']))} " f"{f['intencion']}\n")
        return 0
    if comando == "reviews":
        filas = asyncio.run(_listar_repasos_cli())
        if not filas:
            sys.stdout.write(ui.suave("Sin repasos pendientes.\n"))
        for f in filas:
            sys.stdout.write(f"{ui.acento(str(f['dias_atraso']) + 'd')} " f"{f['contenido']}\n")
        return 0
    if comando == "archived":
        filas = asyncio.run(_listar_archivados_cli())
        if not filas:
            sys.stdout.write(ui.suave("Sin memorias archivadas.\n"))
        for f in filas:
            razon = ui.suave(f"({f['razon_archivo']})")
            sys.stdout.write(f"{ui.suave(str(f['id']))} {f['contenido']} {razon}\n")
        return 0
    if comando == "restore":
        try:
            restaurado = asyncio.run(_restaurar_cli(args.evento_id))
        except ValueError:
            sys.stderr.write(ui.error(f"UUID inválido: {args.evento_id}") + "\n")
            return 2
        if restaurado:
            sys.stdout.write(ui.ok("Memoria restaurada.") + "\n")
            return 0
        sys.stderr.write(ui.error("The event does not exist or is not archived.") + "\n")
        return 1
    if comando == "forget":
        try:
            ok = asyncio.run(_olvidar_cli(args.evento_id, args.undo))
        except ValueError:
            sys.stderr.write(ui.error(f"UUID inválido: {args.evento_id}") + "\n")
            return 2
        if ok:
            accion = "Olvido normal restaurado." if args.undo else ("Olvido dirigido marcado.")
            sys.stdout.write(ui.ok(accion) + "\n")
            return 0
        sys.stderr.write(ui.error("El evento no existe.") + "\n")
        return 1
    if comando == "quarantine":
        filas = asyncio.run(_listar_cuarentena_cli())
        if not filas:
            sys.stdout.write(ui.suave("Sin memorias en cuarentena.\n"))
        for f in filas:
            sys.stdout.write(
                f"{ui.acento(str(f['fuente']))} " f"{ui.suave(str(f['id']))} {f['contenido']}\n"
            )
        return 0
    if comando == "trust":
        asyncio.run(_confiar_cli(args.source, not args.distrust))
        if args.distrust:
            sys.stdout.write(ui.ok(f"Fuente '{args.source}' en cuarentena.") + "\n")
        else:
            sys.stdout.write(ui.ok(f"Fuente '{args.source}' aprobada.") + "\n")
        return 0
    if comando == "snooze":
        try:
            ok = asyncio.run(_aplazar_cli(args.intencion_id, args.horas))
        except ValueError:
            sys.stderr.write(ui.error(f"UUID inválido: {args.intencion_id}") + "\n")
            return 2
        if ok:
            sys.stdout.write(ui.ok(f"Intención aplazada {args.horas}h.") + "\n")
            return 0
        sys.stderr.write(ui.error("The intention does not exist or is no longer pending.") + "\n")
        return 1
    if comando == "sessions":
        from src.mcp.tools import _listar_sesiones

        filas = asyncio.run(_listar_sesiones())
        if not filas:
            sys.stdout.write(ui.suave("Sin sesiones registradas.\n"))
        for f in filas:
            estado = "cerrada" if f["cerrada"] else "abierta"
            sys.stdout.write(
                f"{ui.acento(estado)} {ui.suave(str(f['id']))} "
                f"{f.get('agente') or 'agente?'} — "
                f"{f['eventos']} eventos\n"
            )
        return 0
    if comando == "handoff":
        from src.mcp.tools import _obtener_handoff

        try:
            datos = asyncio.run(_obtener_handoff(args.sesion_id))
        except ValueError as e:
            sys.stderr.write(ui.error(str(e)) + "\n")
            return 1
        sys.stdout.write(str(datos["handoff"]) + "\n")
        return 0
    if comando == "censors":
        from src.mcp.tools import _listar_censores

        filas = asyncio.run(_listar_censores())
        if not filas:
            sys.stdout.write(ui.suave("Sin guardrails vigentes.\n"))
        for f in filas:
            sys.stdout.write(f"{ui.acento(str(f['severidad']).upper())} " f"{f['contenido']}\n")
        return 0
    if comando == "censor":
        from src.mcp.tools import _crear_censor

        severidad = "bloquear" if args.block else "advertir"
        creado = asyncio.run(_crear_censor(args.regla, severidad, args.domain))
        sys.stdout.write(ui.ok(f"Censor registrado ({severidad}): {creado['id']}") + "\n")
        return 0
    if comando == "verify":
        from src.mcp.tools import _verificar_accion

        veredicto = asyncio.run(_verificar_accion(args.accion))
        if veredicto["permitido"]:
            sys.stdout.write(ui.ok(f"Permitido — {veredicto['motivo']}") + "\n")
        else:
            sys.stdout.write(ui.error(f"Bloqueado — {veredicto['motivo']}") + "\n")
        for c in cast("list[dict[str, Any]]", veredicto["censores"]):
            sys.stdout.write(f"  {ui.suave(str(c['severidad']))} {c['contenido']}\n")
        return 0 if veredicto["permitido"] else 1
    if comando == "calibration":
        from src.mcp.tools import _reporte_calibracion

        rep = asyncio.run(_reporte_calibracion())
        if rep["brier"] is None:
            sys.stdout.write(ui.suave("Sin predicciones resueltas aún.\n"))
            return 0
        sys.stdout.write(
            f"Brier: {ui.negrita(str(rep['brier']))} "
            f"({rep['resueltas']} resueltas, "
            f"{rep['pendientes']} pendientes)\n"
            f"acierto empírico: {rep['acierto_empirico']} "
            f"· confianza media: {rep['confianza_media']} "
            f"· sesgo: {rep['sesgo_sobreconfianza']}\n"
        )
        for b in cast("list[dict[str, float]]", rep["curva"]):
            sys.stdout.write(
                f"  {b['desde']:.1f}–{b['hasta']:.1f}: n={b['n']} "
                f"declarada {b['confianza_media']:.2f} "
                f"→ real {b['acierto_empirico']:.2f}\n"
            )
        return 0
    if comando == "predict":
        from src.mcp.tools import _registrar_prediccion

        datos = asyncio.run(_registrar_prediccion(args.afirmacion, args.confidence))
        sys.stdout.write(ui.ok(f"Predicción registrada: {datos['id']}") + "\n")
        return 0
    if comando == "resolve":
        from src.mcp.tools import _resolver_prediccion

        try:
            asyncio.run(_resolver_prediccion(args.prediccion_id, not args.miss))
        except ValueError as e:
            sys.stderr.write(ui.error(str(e)) + "\n")
            return 1
        sys.stdout.write(ui.ok("Prediction resolved.") + "\n")
        return 0
    if comando == "predictions":
        from src.mcp.tools import _listar_predicciones

        filas = asyncio.run(_listar_predicciones())
        if not filas:
            sys.stdout.write(ui.suave("Sin predicciones.\n"))
        for f in filas:
            estado = (
                "pendiente" if f["acierto"] is None else ("acertó" if f["acierto"] else "falló")
            )
            sys.stdout.write(f"{ui.acento(estado)} {f['confianza']:.0%} " f"{f['afirmacion']}\n")
        return 0
    if comando == "pending":
        from src.mcp.tools import _pendientes_aprobacion

        filas = asyncio.run(_pendientes_aprobacion())
        if not filas:
            sys.stdout.write(ui.suave("Sin escrituras pendientes de aprobación.\n"))
        for f in cast("list[dict[str, Any]]", filas):
            sys.stdout.write(
                f"{ui.suave(str(f['id']))} [{f['tipo']}] " f"{str(f['contenido'])[:120]}\n"
            )
        return 0
    if comando == "approve":
        from src.mcp.tools import _aprobar_evento, _aprobar_pendientes

        if args.all:
            aprob = asyncio.run(_aprobar_pendientes())
            sys.stdout.write(ui.ok(f"{aprob['aprobados']} propuestas aprobadas.") + "\n")
            return 0
        if not args.evento_id:
            sys.stderr.write(ui.error("provide a UUID or use --all") + "\n")
            return 2
        try:
            asyncio.run(_aprobar_evento(args.evento_id))
        except ValueError as e:
            sys.stderr.write(ui.error(str(e)) + "\n")
            return 1
        sys.stdout.write(ui.ok("Propuesta aprobada.") + "\n")
        return 0
    if comando == "reject":
        from src.mcp.tools import _rechazar_evento

        try:
            asyncio.run(_rechazar_evento(args.evento_id))
        except ValueError as e:
            sys.stderr.write(ui.error(str(e)) + "\n")
            return 1
        sys.stdout.write(ui.ok("Propuesta descartada.") + "\n")
        return 0
    if comando == "gaps":
        from src.mcp.tools import _mapa_lagunas

        datos = asyncio.run(_mapa_lagunas(args.consulta, args.top))
        if datos["es_laguna_total"]:
            sys.stdout.write(ui.aviso("Total gap — no evidence at all.") + "\n")
            return 0
        sys.stdout.write(
            f"cobertura {datos['cobertura']:.0%} · "
            f"confianza media {datos['confianza_media']} · "
            f"paramétricas {datos['n_parametricas']}\n"
        )
        for lag_ in cast("list[str]", datos["lagunas"]):
            sys.stdout.write(f"  {ui.aviso('·')} {lag_}\n")
        return 0
    return 2


def _imprimir_stats(datos: dict[str, object]) -> None:
    """Imprime las estadísticas en formato legible."""
    sys.stdout.write(f"Eventos totales: {ui.negrita(str(datos['total']))}\n")
    por_dimension = cast("dict[str, int]", datos["por_dimension"])
    for dim, n in por_dimension.items():
        sys.stdout.write(f"  {ui.info(dim)}: {n}\n")


if __name__ == "__main__":
    raise SystemExit(main())
