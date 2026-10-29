"""Detección de contexto incompleto al registrar eventos.

Cuando un evento menciona entidades (nombres propios, términos en
mayúscula) sin antecedentes en la memoria, el panorama es incompleto:
el sistema no puede vincularlo ni razonar sobre él con contexto. La
respuesta de ``recordar_evento`` incluye entonces un aviso para que
el agente pueda preguntar al usuario por la pieza faltante.
"""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.identidad import Contexto
from src.storage.repositories import EventoRepository

# Nombres propios / términos capitalizados de ≥4 letras. La primera
# palabra de la frase también cuenta: "Franz me estresa" — Franz es
# justamente la entidad que interesa detectar.
_PATRON_ENTIDAD = re.compile(r"\b[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]{3,}\b")

# #hashtag estilo Karpathy/Obsidian: vínculo implícito entre memorias
# que comparten etiqueta. Minúsculas, Unicode, sin espacios.
_PATRON_HASHTAG = re.compile(r"#([0-9A-Za-zÁÉÍÓÚÑÜáéíóúñü_\-]{2,64})")

_MAX_ENTIDADES = 5
_MAX_TAGS = 12


def extraer_hashtags(contenido: str) -> list[str]:
    """Extrae #hashtags del contenido, normalizados a minúsculas.

    Args:
        contenido: Texto del evento.

    Returns:
        Tags únicos en orden de aparición (máx. 12), sin el '#'.
    """
    vistos: dict[str, None] = {}
    for m in _PATRON_HASHTAG.finditer(contenido):
        tag = m.group(1).lower().lstrip("_").strip("_")
        # Los numéricos puros (#123) no son etiquetas semánticas.
        if not tag or tag.isdigit():
            continue
        vistos.setdefault(tag)
        if len(vistos) >= _MAX_TAGS:
            break
    return [t for t in vistos if t]


def fusionar_tags(explicitos: list[str] | None, contenido: str) -> list[str]:
    """Une los tags declarados con los #hashtags del contenido.

    Args:
        explicitos: Tags pasados por el agente (se normalizan).
        contenido: Texto del evento del que extraer #hashtags.

    Returns:
        Tags únicos en minúsculas (máx. 12).
    """
    fusion: dict[str, None] = {}
    for tag in explicitos or []:
        limpio = tag.strip().lower().lstrip("#").strip("_")
        if 2 <= len(limpio) <= 64:
            fusion.setdefault(limpio)
    for tag in extraer_hashtags(contenido):
        fusion.setdefault(tag)
    return list(fusion)[:_MAX_TAGS]


_PATRON_TTL = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)
_TTL_FACTOR = {"s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 604_800}


def parsear_ttl(ttl: str | int | None, ahora: int) -> int | None:
    """Traduce un TTL declarado a timestamp Unix de expiración.

    Acepta duraciones compactas (``"30d"``, ``"12h"``, ``"45m"``,
    ``"2w"``), un epoch en segundos como entero, o None (no expira).

    Args:
        ttl: Duración o epoch de expiración; None = sin caducidad.
        ahora: Timestamp Unix actual sobre el que sumar duraciones.

    Returns:
        Timestamp Unix de expiración, o None si no caduca.

    Raises:
        ValueError: Formato de TTL no reconocido o ya vencido.
    """
    if ttl is None:
        return None
    if isinstance(ttl, int):
        if ttl <= ahora:
            raise ValueError("el TTL ya estaría vencido")
        return ttl
    m = _PATRON_TTL.match(ttl.strip())
    if not m:
        raise ValueError(
            f"TTL no reconocido: {ttl!r} — usa '30d', '12h', '45m' o epoch"
        )
    return ahora + int(m.group(1)) * _TTL_FACTOR[m.group(2).lower()]


def extraer_entidades(contenido: str) -> list[str]:
    """Extrae nombres propios mencionados en el contenido.

    Args:
        contenido: Texto del evento.

    Returns:
        Entidades únicas en orden de aparición (máx. 5).
    """
    vistas: dict[str, None] = {}
    for m in _PATRON_ENTIDAD.finditer(contenido):
        vistas.setdefault(m.group(0))
        if len(vistas) >= _MAX_ENTIDADES:
            break
    return list(vistas)


async def analizar_contexto(
    session: AsyncSession,
    contenido: str,
    excluir_id: uuid.UUID | None = None,
    ctx: Contexto | None = None,
) -> dict[str, object]:
    """Evalúa si el evento menciona entidades sin antecedente.

    Para cada entidad capitalizada del contenido consulta el índice
    full-text: si ningún evento previo la menciona, se reporta como
    nueva y el panorama queda marcado como incompleto.

    Args:
        session: Sesión async activa.
        contenido: Texto del evento recién registrado.
        excluir_id: UUID del evento a excluir del chequeo (el propio).

    Returns:
        ``{"completo": True}`` si no hay entidades nuevas, o
        ``{"completo": False, "entidades_nuevas": [...],
        "sugerencia": "..."}`` si las hay.
    """
    repo = EventoRepository(session, ctx)
    nuevas: list[str] = []
    for entidad in extraer_entidades(contenido):
        if not await repo.existe_mencion(entidad, excluir_id):
            nuevas.append(entidad)
    if not nuevas:
        return {"completo": True}
    lista = ", ".join(f"'{e}'" for e in nuevas)
    return {
        "completo": False,
        "entidades_nuevas": nuevas,
        "sugerencia": (
            f"The event mentions {lista} with no prior memory. "
            "Ask the user for context (who/what it is, why it matters) "
            "so future queries get the full picture."
        ),
    }
