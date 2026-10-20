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
        description="Nous — memoria persistente 4D para agentes de IA.",
    )
    p.add_argument(
        "--version", action="store_true",
        help="Muestra la versión instalada y sale.",
    )
    sub = p.add_subparsers(dest="comando")

    serve = sub.add_parser("serve", help="Arranca el servidor Nous.")
    serve.add_argument(
        "--transport", choices=["stdio", "http"], default=None,
        help="Transporte MCP (default: el de la configuración).",
    )
    serve.add_argument("--host", default=None, help="Host del transporte HTTP.")
    serve.add_argument("--port", type=int, default=None, help="Puerto HTTP.")
    serve.add_argument(
        "--sin-dashboard", action="store_true",
        help="No levantar el dashboard Next.js junto al transporte HTTP.",
    )

    v = sub.add_parser("version", help="Versión y método de instalación.")
    v.add_argument("--json", action="store_true", help="Salida JSON.")

    chk = sub.add_parser(
        "check-update", help="Comprueba si hay una versión nueva."
    )
    chk.add_argument("--json", action="store_true", help="Salida JSON.")

    upg = sub.add_parser(
        "upgrade", help="Actualiza Nous y migra la base de datos."
    )
    upg.add_argument(
        "--no-migrate", action="store_true",
        help="Solo intercambia el código; omite alembic upgrade head.",
    )
    upg.add_argument(
        "--yes", action="store_true",
        help="No pedir confirmación interactiva.",
    )
    upg.add_argument("--json", action="store_true", help="Salida JSON.")

    doc = sub.add_parser("doctor", help="Diagnóstico del entorno local.")
    doc.add_argument("--json", action="store_true", help="Salida JSON.")

    db = sub.add_parser("db", help="Migraciones de base de datos (Alembic).")
    db.add_argument(
        "accion", choices=["upgrade", "current", "heads", "history"],
        help="Acción de Alembic a ejecutar.",
    )

    seed = sub.add_parser("seed", help="Siembra eventos de ejemplo.")
    seed.add_argument(
        "--eventos", type=int, default=210, help="Eventos a sembrar."
    )

    st = sub.add_parser("stats", help="Estadísticas de la memoria.")
    st.add_argument("--json", action="store_true", help="Salida JSON.")

    ex = sub.add_parser("export", help="Exporta la memoria a JSONL.")
    ex.add_argument("archivo", type=Path, help="Ruta de destino (.jsonl).")

    im = sub.add_parser(
        "import",
        help="Importa memoria desde obsidian/gbrain/memlord/JSON/JSONL.",
    )
    im.add_argument(
        "origen",
        type=Path,
        help="Archivo (.json/.jsonl) o directorio (vault .md).",
    )
    im.add_argument(
        "--formato",
        choices=["auto", "jsonl", "json", "obsidian", "gbrain", "memlord"],
        default="auto",
        help="Formato de origen (default: auto por extensión).",
    )
    im.add_argument(
        "--fuente",
        default=None,
        help="Nombre de eventos.fuente para lo importado (default: formato).",
    )
    im.add_argument(
        "--rama",
        default=None,
        help="Línea de universo donde registrar lo importado.",
    )

    sk = sub.add_parser(
        "skills", help="Instala los skills/agentes de Nous en el proyecto."
    )
    sk.add_argument(
        "accion",
        choices=["install", "list"],
        help="install copia los artefactos; list muestra lo disponible.",
    )
    sk.add_argument(
        "--target",
        default=".agents",
        help=(
            "Destino: .agents (default), .claude, .opencode, .cursor "
            "o una ruta personalizada."
        ),
    )
    sk.add_argument(
        "--dir",
        type=Path,
        default=Path("."),
        help="Raíz del proyecto donde instalar (default: cwd).",
    )

    sub.add_parser(
        "benchmark", help="Ejecuta la suite de benchmarks."
    )

    prune = sub.add_parser(
        "prune", help="Poda sináptica: elimina huellas débiles y viejas."
    )
    prune.add_argument(
        "--dias", type=int, default=90,
        help="Solo eventos más antiguos que N días (default 90).",
    )
    prune.add_argument(
        "--amplitud", type=float, default=0.3,
        help="Poda solo huellas con amplitud <= este valor (default 0.3).",
    )
    prune.add_argument(
        "--aplicar", action="store_true",
        help="Elimina de verdad; sin él solo muestra el conteo (dry-run).",
    )
    prune.add_argument("--json", action="store_true", help="Salida JSON.")

    con = sub.add_parser(
        "consolidar",
        help="Consolidación-sueño: refuerza, promueve a curado y archiva.",
    )
    con.add_argument("--json", action="store_true", help="Salida JSON.")

    sub.add_parser(
        "intenciones",
        help="Lista las intenciones prospectivas pendientes.",
    )

    sub.add_parser(
        "archivados",
        help="Lista las memorias archivadas por consolidación.",
    )

    res = sub.add_parser(
        "restaurar",
        help="Restaura una memoria archivada (deshace el olvido).",
    )
    res.add_argument("evento_id", help="UUID del evento archivado.")

    from src.cli.identidad import registrar_subparsers

    registrar_subparsers(sub)
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
        sys.stdout.write(
            ui.ok("Actualización disponible")
            + f" → {ui.info(resultado.comando_upgrade)}\n"
        )
    else:
        sys.stdout.write(ui.suave(resultado.detalle) + "\n")
    return 0


def _cmd_upgrade(no_migrate: bool, yes: bool, como_json: bool) -> int:
    """Implementa ``nous upgrade`` con confirmación y pasos."""
    info = up.check_update()
    if info.actualizacion_disponible is False:
        sys.stdout.write(ui.ok("Ya estás en la versión más reciente.") + "\n")
        return 0
    if not yes and not _confirmar():
        sys.stdout.write("Upgrade cancelado.\n")
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
        return input("¿Continuar con el upgrade? [s/N] ").lower() == "s"
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
            elif valor in (False, "error", "fallo", "sin conexión"):
                render = ui.error(str(valor))
            else:
                render = str(valor)
            sys.stdout.write(f"{ui.suave(clave)}: {render}\n")
    return 0 if datos["base_de_datos"] == "ok" else 1


def _cmd_db(accion: str) -> int:
    """Delega en Alembic las acciones de migración."""
    from alembic.config import main as alembic_main

    argv = accion.split()
    if accion == "upgrade":
        argv = ["upgrade", "head"]
    alembic_main(argv=argv)
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
                select(
                    EventoModel.dimension, func.count(EventoModel.id)
                ).group_by(EventoModel.dimension)
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
    origen: Path, formato: str, fuente: str | None, rama: str | None
) -> int:
    """Implementa ``nous import`` para archivos y directorios."""
    from src.cli.transferencia import importar
    from src.storage.database import crear_engine

    if not await asyncio.to_thread(origen.exists):
        sys.stderr.write(ui.error(f"error: {origen} no existe") + "\n")
        return 1
    engine = crear_engine()
    try:
        resumen = await importar(engine, origen, formato, fuente, rama)
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


async def _poda(
    dias: int, amplitud: float, aplicar: bool
) -> dict[str, object]:
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
            return {"candidatos": await repo.contar_podables(antes_de, amplitud, 0),
                    "aplicado": 0}
    finally:
        await engine.dispose()


async def _consolidar_cli() -> dict[str, object]:
    """Ejecuta la corrida de sueño y persiste su reporte (ADR-003)."""
    from src.core.identidad import WORKSPACE_PERSONAL_ID
    from src.core.memoria.consolidacion import (
        EventoConsolidable,
        decidir_consolidacion,
        resumen_legible,
    )
    from src.core.memoria.tipos import TipoMemoria
    from src.core.modelos import Dimension
    from src.storage.database import crear_engine, get_session
    from src.storage.models import ReporteConsolidacionModel
    from src.storage.repositories import (
        EventoRepository,
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
                        tipo_memoria=(
                            TipoMemoria(m.tipo_memoria)
                            if m.tipo_memoria
                            else None
                        ),
                        ultimo_acceso=m.ultimo_acceso,
                        archivado=m.archivado,
                        curado=m.curado,
                    )
                    for m in modelos
                ]
            )
            await repo.aplicar_consolidacion(
                plan.fortalecer,
                plan.promover,
                plan.archivar,
                plan.ejecutado_en,
            )
            await ReporteConsolidacionRepository(session).crear(
                ReporteConsolidacionModel(
                    workspace_id=WORKSPACE_PERSONAL_ID,
                    ejecutado_en=plan.ejecutado_en,
                    fortalecidos=len(plan.fortalecer),
                    archivados=len(plan.archivar),
                    promovidos=len(plan.promover),
                    detalle={
                        "fortalecidos": plan.fortalecer,
                        "promovidos": plan.promover,
                        "archivados": plan.archivar,
                    },
                )
            )
            await session.commit()
        return {
            "evaluados": len(modelos),
            "fortalecidos": len(plan.fortalecer),
            "promovidos": len(plan.promover),
            "archivados": len(plan.archivar),
            "resumen": resumen_legible(plan),
        }
    finally:
        await engine.dispose()


async def _listar_intenciones_cli() -> list[dict[str, object]]:
    """Lista las intenciones prospectivas pendientes."""
    from src.storage.database import crear_engine, get_session
    from src.storage.repositories import IntencionProspectivaRepository

    engine = crear_engine()
    try:
        async with get_session(engine) as session:
            filas = await IntencionProspectivaRepository(
                session
            ).listar(ejecutadas=False)
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
            restaurado = await EventoRepository(session).restaurar(
                _uuid.UUID(evento_id)
            )
            await session.commit()
        return restaurado
    finally:
        await engine.dispose()


def _cmd_benchmark() -> int:
    """Ejecuta la suite de benchmarks como subproceso."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "scripts.benchmark_search"], check=False
    )
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
    estatico = (
        Path(__file__).resolve().parents[1] / "dashboard_static" / "index.html"
    )
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
            capture_output=True, timeout=15, check=False,
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
        proc = subprocess.Popen(
            [yarn, "dev"], cwd=directorio, start_new_session=True
        )
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
                    "Para uso manual: nous serve --transport http"
                )
                + "\n"
            )
    elif not args.sin_dashboard:
        directorio = _dir_dashboard()
        if directorio is not None:
            if _lanzar_dashboard(directorio) is None:
                sys.stderr.write(
                    ui.aviso("yarn no está en PATH — dashboard no lanzado.\n")
                )
            else:
                sys.stderr.write(
                    ui.info(f"Dashboard Next.js dev en {directorio}\n")
                )
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
            + ui.suave(
                "Revisa NOUS_DATABASE_URL y que el servidor esté arriba.\n"
            )
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


def _despachar(args: argparse.Namespace) -> int:
    """Enruta el subcomando parseado a su implementación."""
    comando = args.comando
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
        return asyncio.run(_cmd_seed(args.eventos))
    if comando == "export":
        return asyncio.run(_cmd_export(args.archivo))
    if comando == "import":
        return asyncio.run(
            _cmd_import(args.origen, args.formato, args.fuente, args.rama)
        )
    if comando == "skills":
        from src.cli.skills import cmd_skills

        return cmd_skills(args.accion, args.target, args.dir)
    if comando == "benchmark":
        return _cmd_benchmark()
    if comando in ("users", "workspaces", "keys"):
        from src.cli.identidad import cmd_identidad

        return cmd_identidad(comando, args)
    if comando == "prune":
        datos = asyncio.run(_poda(args.dias, args.amplitud, args.aplicar))
        if args.json:
            _json(datos)
        elif datos["aplicado"]:
            sys.stdout.write(
                ui.ok(f"Podados {datos['eliminados']} eventos.") + "\n"
            )
        else:
            sys.stdout.write(
                ui.aviso(
                    f"{datos['candidatos']} eventos podables "
                    "(usa --aplicar para eliminarlos)."
                )
                + "\n"
            )
        return 0
    if comando == "consolidar":
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
                    f"archivados={datos['archivados']}"
                )
                + "\n"
            )
        return 0
    if comando == "intenciones":
        filas = asyncio.run(_listar_intenciones_cli())
        if not filas:
            sys.stdout.write(ui.suave("Sin intenciones pendientes.\n"))
        for f in filas:
            sys.stdout.write(
                f"{ui.acento(str(f['trigger_tipo']))} "
                f"{f['intencion']}\n"
            )
        return 0
    if comando == "archivados":
        filas = asyncio.run(_listar_archivados_cli())
        if not filas:
            sys.stdout.write(ui.suave("Sin memorias archivadas.\n"))
        for f in filas:
            razon = ui.suave(f"({f['razon_archivo']})")
            sys.stdout.write(
                f"{ui.suave(str(f['id']))} {f['contenido']} {razon}\n"
            )
        return 0
    if comando == "restaurar":
        try:
            restaurado = asyncio.run(_restaurar_cli(args.evento_id))
        except ValueError:
            sys.stderr.write(
                ui.error(f"UUID inválido: {args.evento_id}") + "\n"
            )
            return 2
        if restaurado:
            sys.stdout.write(ui.ok("Memoria restaurada.") + "\n")
            return 0
        sys.stderr.write(
            ui.error("El evento no existe o no está archivado.") + "\n"
        )
        return 1
    return 2


def _imprimir_stats(datos: dict[str, object]) -> None:
    """Imprime las estadísticas en formato legible."""
    sys.stdout.write(
        f"Eventos totales: {ui.negrita(str(datos['total']))}\n"
    )
    por_dimension = cast("dict[str, int]", datos["por_dimension"])
    for dim, n in por_dimension.items():
        sys.stdout.write(f"  {ui.info(dim)}: {n}\n")


if __name__ == "__main__":
    raise SystemExit(main())
