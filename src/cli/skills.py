"""Instalador de skills y agentes de Nous (``nous skills install``).

Los artefactos viven empaquetados en este módulo: le enseñan a un
agente de IA las convenciones de la memoria Nous (dimensiones,
cuándo recordar, cómo responder a ``contexto.completo=false``) y
cómo invocar las tools MCP.

Destinos conocidos y su estructura:

- ``.agents`` → ``skills/<nombre>/SKILL.md`` + ``agents/<nombre>.md``
- ``.claude`` → ``skills/<nombre>/SKILL.md`` + ``agents/<nombre>.md``
- ``.opencode`` → ``skills/<nombre>/SKILL.md`` + ``agent/<nombre>.md``
- ``.cursor`` → ``skills/<nombre>/SKILL.md`` + ``agents/<nombre>.md``
- ruta personalizada → ``<ruta>/skills/...`` y ``<ruta>/agents/...``
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# ── Artefactos empaquetados ────────────────────────────────────────

_SKILL_RECORDAR = """---
name: nous-recordar
description: Persistir eventos en la memoria 4D de Nous. Usar cuando
  el usuario pida recordar algo, haya una decisión, emoción o acción
  relevante, o el contexto de una conversación deba sobrevivir.
---

# Recordar en Nous

La memoria Nous persiste eventos en tres dimensiones: **cognitiva**
(ideas, decisiones, reflexiones), **somática** (emociones, estrés,
cuerpo) y **conductual** (acciones, hábitos, commits). Además soporta
**tags** y **relaciones explícitas**.

## Cuándo llamar `recordar_evento`

- El usuario dice "recuerda", "guarda", "anota" o describe un hecho
  durable (decisión, preferencia, dolor, hito).
- Ocurre algo con peso emocional o estratégico que futuras sesiones
  deberían conocer.
- El usuario relaciona dos hechos → pasa `relaciona_ids` para crear el
  vínculo explícito.

## Cómo llamarla

- `contenido`: el hecho en una frase clara y auto-contenida.
- `dimension`: déjala vacía si no estás seguro — el clasificador decide;
  si la respuesta trae `dimension_dudosa: true`, considera corregirla.
- `amplitud`: 0.0–1.0; sube si el usuario enfatiza ("mucho", "crítico").
- `tags`: etiquetas que enlacen el tema ("trabajo", "franz"). Los
  `#hashtags` en el contenido también se indexan.
- `rama`: solo si el usuario pide una línea de universo alternativa.

## Lee la respuesta

- `contexto.completo=false` → el evento menciona entidades sin
  antecedentes. **Pregunta al usuario por la pieza faltante** (quién es,
  qué pasó) y registra la aclaración como evento relacionado con
  `relaciona_ids`.
- `vinculos_creados`: cuántas relaciones explícitas se materializaron.
"""

_SKILL_CONSULTAR = """---
name: nous-consultar
description: Consultar la memoria 4D de Nous antes de responder.
  Usar cuando la pregunta depende de hechos previos del usuario,
  historial, decisiones o contexto personal.
---

# Consultar en Nous

`consultar_memoria(consulta, top_n)` ejecuta búsqueda híbrida (BM25 +
vectorial + rerank + recencia) y devuelve resultados con scores y
`contexto_temporal` (los eventos adyacentes en la línea de universo).

## Cuándo consultar

- Antes de responder preguntas sobre el pasado del usuario.
- Cuando el usuario pregunte "¿por qué...?", "¿qué dije sobre...?",
  "¿cuándo fue...?".

## Interpretar resultados

- `score_final` alto (>0.7) = evidencia fuerte. Cita los eventos.
- `contexto_temporal` = la escena alrededor del recuerdo — suele
  contener el "porqué".
- Los **tags** del evento (`evento.tags`) indican hilos temáticos;
  los eventos que comparten tag están conectados.
- Si los resultados no bastan, di qué falta — no inventes.
"""

_SKILL_CONTEXTO = """---
name: nous-contexto
description: Responder al aviso de contexto incompleto de Nous.
  Usar cuando recordar_evento devuelva contexto.completo=false.
---

# Contexto incompleto

Cuando `recordar_evento` devuelve `contexto.completo=false`, el evento
menciona entidades (`entidades_nuevas`) sin antecedentes en la memoria.

## Qué hacer

1. **No ignores el aviso** — es la señal de que el recuerdo quedará
   huérfano de contexto.
2. Pregunta al usuario por la pieza faltante con la `sugerencia` como
   guía (quién/qué es la entidad, por qué importa).
3. Registra la aclaración como evento nuevo con `relaciona_ids` al
   evento original — así el grafo queda conectado.
4. Si el usuario no quiere dar contexto, respétalo: la memoria es suya.
"""

_AGENT_MEMORISTA = """---
name: nous-memorista
description: Decide qué persistir en Nous y con qué metadatos.
  Delega aquí cuando no sea obvio si un hecho merece memoria.
tools: nous
---

# Memorista Nous

Tu única responsabilidad es decidir qué se persiste en Nous y cómo.

## Reglas

1. Persiste hechos, decisiones, emociones y acciones con valor
   futuro — no cháchara ni datos efímeros de la sesión.
2. La dimensión la decide el clasificador; solo fíjala si es obvia.
3. Sube `amplitud` (0.7+) cuando el usuario enfatice intensidad.
4. Vincula con `relaciona_ids` los eventos que el usuario declare
   relacionados — un recuerdo aislado vale menos.
5. Si `recordar_evento` devuelve `contexto.completo=false`, pide la
   pieza faltante antes de seguir.
6. Nunca registres secretos, tokens ni datos bancarios.
"""


@dataclass(frozen=True, slots=True)
class Artefacto:
    """Un skill o agente empaquetado listo para instalar."""

    nombre: str
    contenido: str
    es_agente: bool


ARTEFACTOS: tuple[Artefacto, ...] = (
    Artefacto("nous-recordar", _SKILL_RECORDAR, es_agente=False),
    Artefacto("nous-consultar", _SKILL_CONSULTAR, es_agente=False),
    Artefacto("nous-contexto", _SKILL_CONTEXTO, es_agente=False),
    Artefacto("nous-memorista", _AGENT_MEMORISTA, es_agente=True),
)

# Subdirectorio de agentes por target conocido (varía por herramienta).
_DIR_AGENTES = {
    ".agents": "agents",
    ".claude": "agents",
    ".cursor": "agents",
    ".opencode": "agent",
}


def instalar(target: str, raiz: Path) -> dict[str, list[str]]:
    """Copia los artefactos de Nous al destino elegido.

    Args:
        target: ``.agents``/``.claude``/``.opencode``/``.cursor`` o una
            ruta personalizada (relativa a ``raiz`` o absoluta).
        raiz: Directorio del proyecto donde instalar.

    Returns:
        Dict con las rutas escritas en ``instalados``.
    """
    base = Path(target) if Path(target).is_absolute() else raiz / target
    dir_agentes = _DIR_AGENTES.get(target, "agents")
    escritos: list[str] = []
    for art in ARTEFACTOS:
        if art.es_agente:
            destino = base / dir_agentes / f"{art.nombre}.md"
        else:
            destino = base / "skills" / art.nombre / "SKILL.md"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(art.contenido, encoding="utf-8")
        escritos.append(str(destino))
    return {"instalados": escritos}


def cmd_skills(accion: str, target: str, raiz: Path) -> int:
    """Implementa ``nous skills install|list``."""
    if accion == "list":
        for art in ARTEFACTOS:
            tipo = "agente" if art.es_agente else "skill"
            sys.stdout.write(f"  {tipo:7s} {art.nombre}\n")
        return 0
    if not raiz.is_dir():
        sys.stderr.write(f"error: {raiz} no es un directorio\n")
        return 1
    resultado = instalar(target, raiz)
    for ruta in resultado["instalados"]:
        sys.stdout.write(f"  - {ruta}\n")
    sys.stdout.write(
        f"{len(resultado['instalados'])} artefactos instalados.\n"
    )
    return 0
