"""Registro de llamadas a Nous: trazabilidad local-first.

El middleware ASGI encola cada petición en un buffer en memoria; un
vaciador periódico lo persiste en ``llamadas`` por lotes, así el
registro nunca bloquea la request. Solo se guarda metadata (método,
ruta, origen, latencia, status) — NUNCA el contenido ni tokens.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.repositories import LlamadaRepository

logger = logging.getLogger("nous.trazas")

LOTE_FLUSH = 100
INTERVALO_FLUSH_S = 3.0
_MAX_BUFFER = 2000

_buffer: deque[dict[str, Any]] = deque()

_AGENTES_CONOCIDOS = (
    "claude",
    "cursor",
    "windsurf",
    "cline",
    "devin",
    "codex",
    "copilot",
    "openclaw",
    "fastmcp",
    "mcp",
)


def clasificar_origen(
    origen_declarado: str | None, user_agent: str
) -> tuple[str, str]:
    """Deduce quién llama: header explícito, UA de agente o navegador.

    Args:
        origen_declarado: Valor del header ``x-nous-origen`` si existe.
        user_agent: Header User-Agent crudo.

    Returns:
        ``(origen, agente)`` — origen es la etiqueta corta, agente el
        user-agent completo truncado a 200 caracteres.
    """
    agente = user_agent[:200]
    if origen_declarado and origen_declarado.strip():
        return origen_declarado.strip()[:80], agente
    ua = user_agent.lower()
    for nombre in _AGENTES_CONOCIDOS:
        if nombre in ua:
            return f"agente:{nombre}", agente
    if "mozilla" in ua or "safari" in ua or "chrome" in ua:
        return "usuario:navegador", agente
    if "python" in ua or "httpx" in ua or "curl" in ua or "node" in ua:
        return "agente:programa", agente
    return "desconocido", agente


def registrar(
    metodo: str,
    ruta: str,
    origen: str,
    agente: str,
    latencia_ms: int,
    status: int,
    es_ping: bool,
) -> None:
    """Encola una llamada para persistencia por lotes (no bloquea).

    Args:
        metodo: Método HTTP (GET, POST…).
        ruta: Path solicitado (sin query).
        origen: Etiqueta de quién llama (dashboard, agente:cursor…).
        agente: User-Agent crudo truncado.
        latencia_ms: Duración total de la request en milisegundos.
        status: Código HTTP de la respuesta.
        es_ping: True si es sonda de vida o ping explícito.
    """
    if len(_buffer) >= _MAX_BUFFER:
        _buffer.popleft()
    _buffer.append(
        {
            "ts": int(time.time()),
            "metodo": metodo[:10],
            "ruta": ruta[:160],
            "origen": origen[:80],
            "agente": agente[:200],
            "latencia_ms": max(0, latencia_ms),
            "status": status,
            "es_ping": es_ping,
        }
    )


def pendientes() -> int:
    """Devuelve cuántas llamadas esperan en el buffer (para tests)."""
    return len(_buffer)


async def drenar(engine: AsyncEngine) -> int:
    """Persiste el buffer pendiente en la tabla ``llamadas``.

    Args:
        engine: Motor async de SQLAlchemy.

    Returns:
        Número de filas persistidas.
    """
    total = 0
    while _buffer:
        filas: list[dict[str, Any]] = []
        while _buffer and len(filas) < LOTE_FLUSH:
            filas.append(_buffer.popleft())
        async with get_session(engine) as session:
            await LlamadaRepository(session).insertar_lote(filas)
            await session.commit()
        total += len(filas)
    return total


async def vaciador(engine: AsyncEngine, parar: asyncio.Event) -> None:
    """Vacia el buffer a DB cada ``INTERVALO_FLUSH_S`` hasta ``parar``.

    Args:
        engine: Motor async de SQLAlchemy.
        parar: Evento que termina el bucle al activarse.
    """
    while not parar.is_set():
        try:
            await asyncio.wait_for(parar.wait(), timeout=INTERVALO_FLUSH_S)
        except TimeoutError:
            pass
        if _buffer:
            try:
                await drenar(engine)
            except Exception as exc:  # noqa: BLE001 — nunca tumbar el vaciador
                logger.warning("Vaciado de llamadas falló: %s", exc)
    if _buffer:
        try:
            await drenar(engine)
        except Exception as exc:  # noqa: BLE001 — apagado, se registran pérdidas
            logger.warning("Vaciado final de llamadas falló: %s", exc)
