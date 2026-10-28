"""Handoff — el documento de continuidad al cerrar sesión.

Exportar la memoria completa (JSONL) es backup bruto; el handoff es
otra cosa: un documento curado que la SIGUIENTE sesión (humana o de
agente) lee en dos minutos para retomar donde esta quedó. Contiene
solo lo accionable: loops abiertos, compromisos pendientes, núcleo
curado y lo que esta sesión escribió.

Lógica pura — la capa MCP le entrega las consultas ya resueltas y él
devuelve markdown. Sin I/O, sin reloj.
"""

from __future__ import annotations

from typing import Any


def _fecha_legible(ts: int) -> str:
    """Timestamp → 'YYYY-MM-DD HH:MM' UTC para encabezados."""
    import time as _t

    return _t.strftime("%Y-%m-%d %H:%M", _t.gmtime(ts))


def generar_handoff(
    agente: str,
    inicio: int,
    fin: int,
    resumen: str | None,
    escritos: list[dict[str, Any]],
    loops: list[dict[str, Any]],
    nucleo: list[dict[str, Any]],
    censores: list[dict[str, Any]],
) -> str:
    """Compone el markdown de continuidad de la sesión cerrada.

    Args:
        agente: Nombre del agente que corrió la sesión.
        inicio: Timestamp Unix de apertura.
        fin: Timestamp Unix de cierre.
        resumen: Resumen libre que el agente dejó (opcional).
        escritos: Eventos registrados en la sesión — ``{contenido,
            tipo}`` (lo nuevo que la próxima sesión debe saber).
        loops: Compromisos abiertos al cierre — ``{contenido,
            dias_abierto}``.
        nucleo: Memorias curadas relevantes — ``{contenido}``.
        censores: Reglas activas — ``{contenido, severidad}``; la
            próxima sesión hereda las prohibiciones, no las negocia.

    Returns:
        Documento markdown listo para persistir y/o copiar al system
        prompt de la siguiente sesión.
    """
    lineas = [
        "# Handoff — continuidad de sesión",
        "",
        f"- agente: {agente or 'desconocido'}",
        f"- ventana: {_fecha_legible(inicio)} → {_fecha_legible(fin)} UTC",
    ]
    if resumen:
        lineas += ["", "## Resumen", "", resumen]
    if censores:
        lineas += ["", "## Guardrails activos", ""]
        lineas += [
            f"- **{c['severidad'].upper()}** — {c['contenido']}"
            for c in censores
        ]
    if loops:
        lineas += ["", "## Loops abiertos", ""]
        lineas += [
            f"- {loop['contenido']} "
            f"({loop.get('dias_abierto', '?')}d esperando)"
            for loop in loops
        ]
    if escritos:
        lineas += ["", "## Lo que esta sesión dejó", ""]
        lineas += [
            f"- [{e['tipo']}] {e['contenido']}" for e in escritos
        ]
    if nucleo:
        lineas += ["", "## Núcleo curado", ""]
        lineas += [f"- {n['contenido']}" for n in nucleo]
    lineas += [
        "",
        "---",
        "Generado por Nous — la próxima sesión arranca informada.",
    ]
    return "\n".join(lineas)
