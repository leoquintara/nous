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
name: nous-remember
description: Persistir eventos en la memoria 4D de Nous. Usar cuando
  el usuario pida recordar algo, haya una decisión, emoción o acción
  relevante, o el contexto de una conversación deba sobrevivir.
---

# Recordar en Nous

La memoria Nous persiste eventos en tres dimensiones: **cognitiva**
(ideas, decisiones, reflexiones), **somática** (emociones, estrés,
cuerpo) y **conductual** (acciones, hábitos, commits). Además soporta
**tags**, **relaciones explícitas** y una **línea de universo con
ramas** (líneas temporales paralelas para escenarios).

## Cuándo llamar `recordar_evento`

- El usuario dice "recuerda", "guarda", "anota" o describe un hecho
  durable (decisión, preferencia, dolor, hito).
- Ocurre algo con peso emocional o estratégico que futuras sesiones
  deberían conocer.
- El usuario relaciona dos hechos → pasa `relaciona_ids` para crear el
  vínculo explícito.

## Elige el `tipo` epistémico

- `evento` (default): algo que ocurrió.
- `hecho`: dato objetivo y durable (identidad, fecha de nacimiento,
  rol en una empresa).
- `creencia`: opinión revisable del usuario.
- `compromiso`: promesa pendiente → **abre un loop** que
  `compromisos_abiertos` lista hasta que se cierre. Añade
  `con_quien` para la contraparte.
- `preferencia`: gusto del usuario (herramienta, estilo, formato).

## Cómo llamarla

- `contenido`: el hecho en una frase clara y auto-contenida.
- `dimension`: déjala vacía si no estás seguro — el clasificador decide;
  si la respuesta trae `dimension_dudosa: true`, considera corregirla.
- `amplitud`: 0.0–1.0; sube si el usuario enfatiza ("mucho", "crítico").
- `tags`: etiquetas que enlacen el tema. Los `#hashtags` en el
  contenido también se indexan.
- `rama`: línea de universo donde registrarlo ("main" por defecto);
  usa otra solo si el usuario pide un escenario alternativo.
- `ttl`: caducidad ("30d", "12h", "2w" o epoch) para recuerdos
  perecederos — expirados dejan de ser evocables pero quedan en la
  línea.
- `reemplaza_id`: UUID del evento que este sustituye ("mi peso es 79"
  reemplaza "mi peso es 80"). El viejo queda para arqueología.

## Lee la respuesta

- `contexto.completo=false` → el evento menciona entidades sin
  antecedentes. **Pregunta al usuario por la pieza faltante** (quién es,
  qué pasó) y registra la aclaración como evento relacionado con
  `relaciona_ids`.
- `vinculos_creados`: cuántas relaciones explícitas se materializaron.
- `indexado=false` es normal: el embedding se genera en segundo plano;
  la búsqueda por texto ya lo ve.

## Olvidar (derecho al olvido)

- `olvidar_evento(evento_id)` borra el evento y sus vínculos de la
  línea — irreversible. Solo cuando el usuario pida olvidar algo.
- Para **corregir** un dato usa `reemplaza_id` (auditable) — no
  `olvidar_evento`.
"""

_SKILL_CONSULTAR = """---
name: nous-recall
description: Consultar la memoria 4D de Nous antes de responder.
  Usar cuando la pregunta depende de hechos previos del usuario,
  historial, decisiones, contexto personal o de la línea de universo.
---

# Consultar en Nous

Elige la tool según la forma de la pregunta:

| Pregunta | Tool |
|---|---|
| "¿qué sé de X?" (por tema) | `consultar_memoria(consulta, top_n, rama?)` |
| Arranque de sesión | `contexto_inicial(top_n)` |
| "¿qué pasó ayer/entre X e Y?" (rango) | `obtener_linea_de_universo(desde, hasta)` |
| "¿cómo fue mi día/semana?" (bloques) | `obtener_episodios(desde, hasta)` |
| "¿qué debo pendiente?" | `compromisos_abiertos()` |
| Hilos temáticos | `listar_tags()` |
| Grafo de vínculos | `listar_relaciones()` |
| ¿Quién escribe en la memoria? | `listar_fuentes()` |

- `consultar_memoria` ejecuta búsqueda híbrida (BM25 + vectorial +
  rerank + recencia) y devuelve resultados con scores y
  `contexto_temporal` (los eventos adyacentes en la línea de universo).
  Busca en `rama="main"` por defecto — los escenarios alternativos no
  contaminan la línea real; `rama="*"` para todas.
- `contexto_inicial` devuelve en UNA llamada: memoria núcleo + loops
  abiertos + hechos calientes de la semana + stats. Úsala al iniciar
  una sesión — es barata, sin LLM.
- Los timestamps son **Unix epoch en segundos**, no milisegundos.
- `rama="*"` en `obtener_linea_de_universo` recorre todas las líneas
  de universo, no solo `main`.

## Interpretar resultados

- `score_final` alto (>0.7) = evidencia fuerte. Cita los eventos.
- `contexto_temporal` = la escena alrededor del recuerdo — suele
  contener el "porqué".
- Los **tags** del evento (`evento.tags`) indican hilos temáticos;
  los eventos que comparten tag están conectados.
- Si los resultados no bastan, di qué falta — no inventes.
"""

_SKILL_CONTEXTO = """---
name: nous-context
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
   evento original — así el grafo queda conectado y el recuerdo deja
   de ser huérfano en la línea de universo.
4. Si el usuario no quiere dar contexto, respétalo: la memoria es suya.
"""

_SKILL_UNIVERSO = """---
name: nous-universe
description: Navegar la línea de universo 4D de Nous — metas
  (objetivos), proyecciones de futuro, ramas alternativas y
  compromisos abiertos. Usar para preguntas sobre hacia dónde va el
  usuario, escenarios futuros o promesas pendientes.
---

# El universo 4D de Nous

Nous no solo recuerda el pasado: mantiene una **línea de universo**
con histórico completo, **ramas** (líneas paralelas para escenarios),
**objetivos** (metas que anclan las proyecciones fantasma) y
**compromisos** (loops abiertos con contraparte).

## Objetivos — los destinos del futuro

- `crear_objetivo(titulo, fecha_objetivo, dimension?, descripcion?)` —
  el usuario declara una meta con plazo ("lanzar Nous en junio").
  `fecha_objetivo` es Unix epoch en **segundos**.
- `listar_objetivos(estado?, rama?)` — activos por defecto; `estado="*"`
  para todos. Ordenados por fecha límite, el más próximo primero.
- `cerrar_objetivo(id, "cumplido"|"abandonado", resolucion?)` —
  transición auditable, nunca borrado; con `resolucion` se registra
  el cierre como evento enlazado en la línea.

Regla de producto: el usuario **no elige dimensión técnica** al crear
un objetivo — si no es obvia, déjala transversal (None).

## Proyección — los futuros fantasma

- `proyectar_trayectoria(horizonte_dias, dias_historial)` — detecta
  rachas dimensionales activas y proyecta la distribución probable.
  Devuelve `dimension_probable`, `probabilidad`, `trayectorias`
  detectadas y persiste la proyección como historial auditable.
- Los futuros se recalculan desde el punto temporal enfocado; las
  alternativas son heurísticas, no promesas.

## Compromisos — los loops abiertos

- `recordar_evento(..., tipo="compromiso", con_quien="X")` abre un loop.
- `compromisos_abiertos(rama?)` lista quién espera respuesta, con
  `dias_abierto` — los más antiguos primero.
- `cerrar_compromiso(id, resolucion)` cierra con auditoría: queda
  enlazado al evento de resolución, nunca se borra.

## Ramas — líneas alternativas

- Todo evento/objetivo/compromiso vive en una `rama` ("main" default).
- `ramificar_fuente(nombre, rama)` crea la línea alterna como
  sub-fuente ``{fuente}:{rama}`` — descubre las fuentes con
  `listar_fuentes()` primero.
- Registra en otra rama solo si el usuario explora un escenario.
- `obtener_linea_de_universo(..., rama="*")` recorre todas las ramas;
  `consultar_memoria(..., rama="*")` busca en todas — por defecto
  ambas se quedan en "main".
"""

_SKILL_PREFERENCIAS = """---
name: nous-preferences
description: Leer y guardar preferencias duraderas del usuario en
  Nous. Usar cuando el usuario declare un ajuste estable de
  comportamiento o configuración que aplique a futuras sesiones.
---

# Preferencias duraderas en Nous

Distinto de un recuerdo: `recordar_evento` guarda hechos sobre el
mundo ("prefiero el café sin azúcar"); `guardar_preferencia` guarda
**ajustes operativos** del agente — cómo responderte, en qué formato,
qué herramientas evitar.

## Tools

- `leer_preferencia(clave)` — devuelve el documento JSON guardado, o
  `{}` si no existe. Léela al inicio si el usuario suele pedir un
  estilo concreto.
- `guardar_preferencia(clave, valor)` — upsert por clave; `valor` es
  JSON arbitrario.

## Convenciones

- Claves namespacadas por punto: `estilo.respuestas`,
  `formato.codigo`, `timezone.local`.
- Una clave = un documento coherente. No mezcles temas.
- Si el usuario corrige una preferencia, sobrescribe la misma clave —
  no crees una variante.
- Nunca guardes secretos ni tokens: las preferencias son legibles.
"""

_AGENT_MEMORISTA = """---
name: nous-keeper
description: Decide qué persistir en Nous y con qué metadatos.
  Delega aquí cuando no sea obvio si un hecho merece memoria.
tools: nous
---

# Memorista Nous

Tu única responsabilidad es decidir qué se persiste en Nous y cómo.

## Reglas

1. Persiste hechos, decisiones, emociones y acciones con valor
   futuro — no cháchara ni datos efímeros de la sesión.
2. Elige el `tipo` correcto: `hecho` para datos objetivos,
   `creencia` para opiniones revisables, `preferencia` para gustos,
   `compromiso` para promesas (añade `con_quien` — abre un loop que
   `compromisos_abiertos` vigilará).
3. La dimensión la decide el clasificador; solo fíjala si es obvia.
4. Sube `amplitud` (0.7+) cuando el usuario enfatice intensidad.
5. Vincula con `relaciona_ids` los eventos que el usuario declare
   relacionados — un recuerdo aislado vale menos.
6. Datos perecederos → `ttl`. Datos que sustituyen a otro →
   `reemplaza_id` (la cadena queda auditable en la línea).
7. Registra en `rama` distinta de "main" solo para escenarios
   alternativos declarados por el usuario.
8. Una meta con plazo NO es un evento: usa `crear_objetivo` — los
   objetivos anclan las proyecciones del universo.
9. Si `recordar_evento` devuelve `contexto.completo=false`, pide la
   pieza faltante antes de seguir.
10. Nunca registres secretos, tokens ni datos bancarios.
"""

_AGENT_CURATOR = """---
name: nous-curator
description: Higiene periódica de la memoria — loops viejos,
  objetivos vencidos, fuentes y ramas. Delega aquí la revisión de
  salud del universo Nous.
tools: nous
---

# Curador Nous

Tu responsabilidad es la higiene de la línea de universo: que nada
pudra en silencio.

## Qué revisar

1. `compromisos_abiertos()` — loops con `dias_abierto` alto:
   recuerda al usuario qué promesas llevan más tiempo esperando.
   Nunca cierres uno sin su confirmación — la resolución es suya.
2. `listar_objetivos()` — metas cuya `fecha_objetivo` ya pasó y
   siguen activas: sugiere cerrarlas (`cerrar_objetivo` cumplido o
   abandonado) o replantear la fecha. El usuario decide.
3. `listar_fuentes()` — fuentes que ya no escriben: sugiere archivar
   (el dashboard las oculta sin borrar el historial).
4. `listar_tags()` — tags duplicados u obsoletos que ensucian los
   hilos temáticos.
5. `proyectar_trayectoria()` — si la dimensión dominante lleva mucho
   tiempo en un estado (p. ej. estrés somático sostenido),
   coméntalo con tacto.

## Reglas

- Propón, no ejecutes: la memoria es del usuario y toda transición
  destructiva o de cierre necesita su gesto explícito.
- `olvidar_evento` solo si el usuario lo pide literalmente.
- El reporte es breve: hallazgos ordenados por antigüedad, una
  acción sugerida por hallazgo.
"""


@dataclass(frozen=True, slots=True)
class Artefacto:
    """Un skill o agente empaquetado listo para instalar."""

    nombre: str
    contenido: str
    es_agente: bool


ARTEFACTOS: tuple[Artefacto, ...] = (
    Artefacto("nous-remember", _SKILL_RECORDAR, es_agente=False),
    Artefacto("nous-recall", _SKILL_CONSULTAR, es_agente=False),
    Artefacto("nous-context", _SKILL_CONTEXTO, es_agente=False),
    Artefacto("nous-universe", _SKILL_UNIVERSO, es_agente=False),
    Artefacto("nous-preferences", _SKILL_PREFERENCIAS, es_agente=False),
    Artefacto("nous-keeper", _AGENT_MEMORISTA, es_agente=True),
    Artefacto("nous-curator", _AGENT_CURATOR, es_agente=True),
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
