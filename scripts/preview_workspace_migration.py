"""Preview de reclasificación memlord/gbrain → ruba/digitalbooting.

Clasifica cada evento por señales de dominio: proyecto de cliente
inmobiliario (ruba) vs equipo/infraestructura (digitalbooting). Los
sin-señal-clara se quedan en su workspace origen (memlord = archivo
de mensajes, gbrain = archivo de páginas).

Uso: python scripts/preview_workspace_migration.py
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter

import asyncpg

DB_URL = "postgresql://nous:nous@localhost:5433/nous"
WS_ORIGEN = ("memlord", "gbrain")

# Proyectos/plazas inmobiliarias de Ruba vistas en el corpus.
PATRON_RUBA = re.compile(
    r"ruba|rubacorp|zoho|lead\b|plaza\b|desarrollo(?:s)? inmob|residencial"
    r"|preventa|departamento|terreno|cotiz|landing|figma|precio|campaña"
    r"|mazda|recomienda y gana|power\s?point|google sheet|sheet_id"
    r"|benevento|amberes|aurea|zivalam|tossa|rincon|segovia|mercedes"
    r"|vonetto|mapan|acaray|creta|veracruz|toluca|puebla|mazatlan"
    r"|obregon|saltillo|torreon|chihuahua|juarez|leon\b|mexicali"
    r"|tijuana|queretaro|sanluis|sonora|guadalajara|monterrey"
    r"|estadodemexico|quintanaroo|cuentacon|ventascdmx|cuautitlan"
    r"|whatsapp business|formulario|propuesta grafica|brokers?",
    re.IGNORECASE,
)
PATRON_DB = re.compile(
    r"digitalbooting|digital booting|gitlab|merge request|\bpr\b|pipeline"
    r"|\bci\b|deploy|servidor|docker|postgres|redis|ssh|sftp|cron"
    r"|nous|mcp|obsidian|synapse|memlord|gbrain|openclaw|deepseek"
    r"|ollama|embedding|clockify|quincenal|onboarding|seniority"
    r"|fortino|cesar\b|eduardo|andres|heartbeat|vault|backup|recovery"
    r"|memory protocol|verification protocol|multitasking|agents\.md"
    r"|skills?|workspace|alembic|pytest|typescript|next\.?js|react\b"
    r"|github|release|licencia|license|jwt|oauth|anthropic|claude|devin"
    r"|minuta|participantes|devs pros|dbgrid|standup|sprint|retro\b"
    r"|del equipo|grupo signal|seniority|subagente|agente\b|powershell"
    r"|sherpa|contexto estrat|tarifa|write-routing|reglas de comunicacion"
    r"|dev senior|dev mid|dev junior|semi-senior|soporte.*\bdb\b",
    re.IGNORECASE,
)
# Señales fuertes de proyecto: ganan la clasificación solas.
PATRON_RUBA_FUERTE = re.compile(
    r"ruba\.com\.mx|rubacorp|benevento|amberes|aurea|zivalam|tossa"
    r"|rincon de segovia|las mercedes|vonetto|acaray|cuentacon|mazda",
    re.IGNORECASE,
)
# Documento interno de la organización: procesos, stack, protocolos,
# componentes y SDKs del equipo — aunque mencionen clientes de pasada.
PATRON_DB_FUERTE = re.compile(
    r"digital\s?booting|documentacion-tecnica|type:\s*synapse"
    r"|type:\s*proceso|onboarding|memory protocol|verification protocol"
    r"|checklist|\bsdk\b|stack tecnologico|componente, ui"
    r"|multitasking|heartbeat|clockify",
    re.IGNORECASE,
)


def clasificar(texto: str) -> str:
    """Devuelve 'ruba', 'digitalbooting' u 'origen' según las señales."""
    if PATRON_DB_FUERTE.search(texto):
        return "digitalbooting"
    if PATRON_RUBA_FUERTE.search(texto):
        return "ruba"
    ruba = len(PATRON_RUBA.findall(texto))
    db = len(PATRON_DB.findall(texto))
    if ruba > db:
        return "ruba"
    if db > ruba:
        return "digitalbooting"
    return "origen"


async def main() -> None:
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch(
        """
        SELECT e.id, w.slug AS ws, e.contenido
        FROM eventos e JOIN workspaces w ON w.id = e.workspace_id
        WHERE w.slug = ANY($1)
        """,
        list(WS_ORIGEN),
    )
    stats: Counter[tuple[str, str]] = Counter()
    muestras: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        destino = clasificar(r["contenido"] or "")
        stats[(r["ws"], destino)] += 1
        key = (r["ws"], destino)
        muestras.setdefault(key, [])
        if len(muestras[key]) < 4:
            muestras[key].append((r["contenido"] or "")[:80].replace("\n", " "))
    for (ws, destino), n in stats.most_common():
        print(f"{ws:>9} -> {destino:<14} {n:>6}")
    print("\n--- Muestras ---")
    for key, lista in sorted(muestras.items()):
        print(f"\n[{key[0]} -> {key[1]}]")
        for m in lista:
            print("   ", m)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
