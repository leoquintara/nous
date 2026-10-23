# Nous

[![ci](https://github.com/leoquintara/nous/actions/workflows/ci.yml/badge.svg)](https://github.com/leoquintara/nous/actions/workflows/ci.yml)
[![AGPL-3.0](https://img.shields.io/badge/licencia-AGPL--3.0-blue)](LICENSE)
[![nous-memory en PyPI](https://img.shields.io/pypi/v/nous-memory)](https://pypi.org/project/nous-memory/)

**Memoria persistente 4D para agentes de IA — local-first y auto-hospedable.**
Un producto de Quintara AI — landing, precios y licencias en
**[nousmemory.com](https://nousmemory.com)**.

Nous le da a cualquier agente (Claude, Cursor, Windsurf, Cline, Devin, o tu
propio cliente) una memoria que sobrevive a las sesiones: almacena lo que
piensas, sientes y haces en tres dimensiones, la conecta en un grafo y la
ordena sobre una **línea de universo 4D** — pasado real, ramas alternativas
y futuros proyectados.

> **Privacidad no negociable**: todo vive en tu PostgreSQL, los embeddings
> se generan localmente (ONNX/torch, sin llamadas externas) y ningún dato
> sale de tu máquina sin consentimiento explícito.

## Qué guarda

| Dimensión | Contenido |
|---|---|
| **Cognitiva** | Pensamientos, ideas, decisiones, reflexiones |
| **Somática** | Estados corporales, emociones, estrés, energía |
| **Conductual** | Acciones, hábitos, commits, interacciones |

Cada recuerdo puede ser `evento`, `hecho`, `creencia`, `preferencia` o
`compromiso` (los compromisos abren un *loop* hasta que se cierran), con
tags, vínculos explícitos, TTL, rama de universo y supersede auditable.
Además, cada evento se clasifica como memoria **episódica**, **semántica**
o **procedimental** — el tipo decide su curva de olvido.

## Patrones de memoria (ADR-003)

Siete mecanismos neurocognitivos sobre la línea de universo:

- **Decaimiento adaptativo**: cada recuerdo tiene una media-vida según
  dimensión, tipo de memoria, amplitud y re-evocaciones. Lo curado no decae.
- **Consolidación-sueño**: `nous consolidar` reactiva huellas fuertes,
  promueve lo saliente al tier **curado** y archiva lo débil — siempre
  reversible y con reporte persistido y auditable. Los compromisos y
  preferencias nunca se archivan.
- **Memoria prospectiva**: intenciones *time-based* ("recuérdame X el
  viernes") o *event-based* ("cuando aparezca el cliente Y") que disparan
  como recordatorios al consultar o al indexar el evento gatillo.
- **Interferencia**: un evento nuevo que supersedes o niega uno cercano
  genera un vínculo `contradice` explícito — la contradicción es dato,
  no ruido.
- **Contexto/estado-dependiente**: recuerdas mejor donde y como lo viviste.
  La búsqueda re-rankea por hora del día, fuente y valencia emocional
  cercanas al momento de codificación.
- **Minería de scripts**: secuencias recurrentes de la línea se detectan
  como patrones con soporte y confianza.
- **Olvido reversible**: archivar ≠ borrar. Lo archivado sale del recall
  pero sigue en la worldline; `nous restaurar <id>` lo devuelve. El
  borrado real solo existe vía `nous prune` — manual y explícito.

## Decisión asistida — System One (ADR-002)

Nous no decide por ti: un proveedor de decisión responde preguntas
tipadas (`choice`, `score`, `noul`) con probabilidades, no prosa.
El modelo opina; quien decide es el usuario.

| Provider | Tipo | Requisito |
|---|---|---|
| **Laya** (default) | Local, on-device | `pip install 'nous-memory[decision]'` |
| **Ollama** (Tev1/Nimbus) | HTTP local `/v1/systemone` | `NOUS_OLLAMA_ENDPOINT` + `NOUS_OLLAMA_MODEL` |
| **Jev** (TypeSafe) | Hosted | `NOUS_PRIVACY_PERMITIR_EXTERNOS=true` — nunca se loggea contenido |

`NOUS_DECISION_PROVIDER=auto` intenta Laya → Ollama (probe de socket,
sin stall) → heurístico local. Todo fallo de provider degrada a
heurísticas — jamás bloquea el camino caliente.

- **Nivel 1**: aumenta la clasificación — dimensión, amplitud y tipo
  de memoria se estiman con el provider cuando está disponible.
- **Nivel 2 (Pro)**: `consultar_decision` (MCP) y `POST /api/decision`
  (REST) — el consejero compone `choice` + `score` + `noul` sobre tu
  contexto 4D (línea + metas + loops abiertos). 402 sin licencia.

`src/core/decision/` es autocontenido — no importa storage ni MCP —
pensado para extraerse como paquete propio.

## El universo 4D

- **Línea temporal completa**: cada evento tiene su instante; se puede
  recorrer por rangos o agrupar en episodios narrativos.
- **Ramas**: líneas alternativas para escenarios ("¿y si…?") que comparten
  el pasado pero no contaminan la línea `main`.
- **Objetivos**: metas con fecha que anclan las proyecciones fantasma.
- **Proyección**: rachas dimensionales y futuros probables calculados del
  historial; opcionalmente re-ponderados por un LLM (Ollama local por
  defecto, Gemini/OpenAI-compatible como fallback configurable).
- **Compromisos**: loops abiertos — quién espera respuesta y desde cuándo.

## Arquitectura

```
┌────────────┐  stdio/HTTP   ┌──────────────┐   ┌──────────────┐
│ Agentes MCP │ ────────────▶ │  src/mcp     │   │  Dashboard   │
└────────────┘               │  (38 tools)  │   │  repo propio │
                             └──────┬───────┘   └──────┬───────┘
┌────────────┐                     │                   │ REST+token
│  CLI `nous`│ ────────┐     ┌─────▼───────────────────▼─────┐
└────────────┘         ├────▶│         src/core              │
                       │     │  clasificador · embeddings    │
                       │     │  búsqueda híbrida · temporal  │
                       │     │  predicción IA · trazas       │
                       │     │  memoria/ (patrones ADR-003)  │
                       │     │  decision/ (System One, ADR-002)│
                       │     └──────────────┬────────────────┘
                       │            ┌───────▼────────┐
                       └───────────▶│ src/storage    │
                                  │ PostgreSQL +    │
                                  │ pgvector + Alembic │
                                  └────────────────┘
```

Búsqueda híbrida 4D: BM25 (tsvector español) + coseno pgvector + RRF +
recencia + reranker cross-encoder local. Objetivo: <200ms p95.

## Quickstart

**Imagen publicada** (ghcr.io — sin clonar ni build):

```bash
docker run -d --name nous-db -e POSTGRES_USER=nous \
  -e POSTGRES_PASSWORD=nous -e POSTGRES_DB=nous \
  -p 5432:5432 pgvector/pgvector:pg18

docker run -d --name nous -p 8720:8720 \
  -e NOUS_DATABASE_URL=postgresql+asyncpg://nous:nous@host.docker.internal:5432/nous \
  -e NOUS_API_TOKEN=ns_$(openssl rand -hex 32) \
  ghcr.io/leoquintara/nous:latest

curl -s localhost:8720/health
```

**Desde el repo** (PostgreSQL + app; dashboard opcional por perfil):

```bash
git clone https://github.com/leoquintara/nous.git && cd nous
cp .env.example .env
echo "NOUS_API_TOKEN=ns_$(openssl rand -hex 32)" >> .env
docker compose --profile app up -d
curl -s localhost:8720/health    # API + MCP vivos

# con el dashboard (ADR-001: repo hermano nous-dashboard requerido)
git clone https://github.com/leoquintara/nous-dashboard.git ../nous-dashboard
docker compose --profile dashboard up -d   # http://localhost:3000
```

**pipx / PyPI** (comando `nous` global sobre tu PostgreSQL — requiere
**Python 3.11–3.13**; `onnxruntime` aún no tiene wheels para 3.15+):

```bash
docker compose up -d db          # solo PostgreSQL 18 + pgvector
pipx install nous-memory         # desde PyPI — o `pipx install .` desde el repo
# macOS: Homebrew instala Python 3.15 — pasa el intérprete explícito:
#   brew install python@3.12
#   pipx install --python /opt/homebrew/bin/python3.12 nous-memory
pipx install 'nous-memory[decision]'   # opcional: + Laya (provider local)
pipx install 'nous-memory[torch]'      # opcional: + sentence-transformers
nous db upgrade                  # migraciones Alembic
nous serve                       # MCP stdio (default)
```

El primer `nous serve` descarga los modelos de embeddings/reranker
(~150 MB) aunque `NOUS_HF_OFFLINE=true`: al detectar la caché vacía se
permite la descarga inicial y el offline estricto se reactiva en el
siguiente arranque.

Verificación del entorno:

```bash
nous doctor                      # DB, modelos en caché, token
nous stats                       # conteos por dimensión
```

## Conectar un agente (MCP)

**stdio** (default, sin token — proceso local):

```jsonc
// claude_desktop_config.json / .cursor/mcp.json / windsurf mcp_config
{
  "mcpServers": {
    "nous": {
      "command": "nous",
      "args": ["serve"]
    }
  }
}
```

**streamable-http** (requiere `NOUS_API_TOKEN`):

```jsonc
{
  "mcpServers": {
    "nous": {
      "url": "http://127.0.0.1:8720/mcp",
      "headers": { "Authorization": "Bearer ns_<tu-token>" }
    }
  }
}
```

Genera el token: `python -c "import secrets; print('ns_' + secrets.token_hex(32))"`.
El token estático equivale al **owner** del workspace `personal`; para un
usuario individual genera su key: `nous keys create --usuario <handle>`
y usa `Bearer ns_u_*` (o `NOUS_API_KEY` en el env del proceso stdio).

### Workspaces y equipo (multi-tenant)

La memoria se aísla por **workspace** — nada cruza entre ellos. Cada
evento/objetivo lleva además un **scope**: `personal` (solo su autor,
el default) o `workspace` (visible para el equipo). Las lecturas ven
`workspace ∪ propios`: los `personal` de otros son invisibles.

- **Roles**: `owner` (gestiona miembros/invitaciones), `member`
  (escribe), `viewer` (solo lee). El rol se resuelve en cada workspace.
- **Override**: las tools MCP y la REST aceptan `workspace=<slug|uuid>`
  para escribir/leer en otro espacio — exige membresía.
- **Gestión por MCP**: los agentes pueden administrar workspaces con
  las tools `listar_workspaces`, `crear_workspace`, `listar_miembros`,
  `invitar_miembro`, `cambiar_rol_miembro` y `quitar_miembro` — mismas
  reglas que REST (gestión solo owners del workspace destino).
- **Invitaciones**: `nous workspaces invite <slug>` emite un `ns_inv_*`
  de un solo uso; el invitado lo canjea en `/unirse` y recibe su `ns_u_*`.
- **Edición libre**: sin `NOUS_LICENSE_KEY`, self-hosted permite 1
  usuario y 3 workspaces.
- **Licencia**: compra en [nousmemory.com](https://nousmemory.com) →
  `nous activate-license <nslic_*>` canjea el serial contra
  `NOUS_LICENSE_ACTIVATION_URL` y guarda el JWT en
  `~/.nous/license.jwt` (validado localmente con
  `NOUS_LICENSE_PUBLIC_KEY`, sin phone-home);
  `NOUS_LICENSE_KEY` en env tiene prioridad si ambos existen.

```bash
nous users create --handle ana --nombre "Ana"
nous workspaces create --slug quintara --nombre "Quintara AI"
nous workspaces invite quintara --rol member        # → ns_inv_…
nous keys create --usuario ana --workspace quintara # → ns_u_…
```

Detalle del modelo: [docs/architecture.md](docs/architecture.md#multi-tenant).

### Skills y agentes para tu agente

`nous skills install` copia skills y agentes empaquetados que enseñan a
cualquier agente las convenciones de Nous:

```bash
nous skills install --target .agents     # o .claude/.cursor/.opencode
nous skills list
```

Con Docker no hace falta CLI local: el entrypoint de la imagen
entiende `skills` y escribe al bind-mount `/agents` — monta ahí el
destino que quieras:

```bash
# instala en el perfil global del usuario
docker run --rm -v "$HOME/.agents:/agents" nous-by-quintara-app skills

# o dentro del repo actual (skills de proyecto)
docker run --rm -v "$PWD/.agents:/agents" nous-by-quintara-app skills

docker run --rm nous-by-quintara-app skills list   # sin mount: solo lista
```

Skills: `nous-remember` (persistir), `nous-recall` (consultar),
`nous-context` (contexto incompleto), `nous-universe` (línea 4D),
`nous-workspaces` (scopes, roles y workspace= multi-tenant),
`nous-preferences`, `nous-checkin` (ritual somático post-hito),
`nous-somatic` (mapa de señales), `nous-consolidar` (sueño y
olvido reversible), `nous-prospectiva` (recordatorios por tiempo
y por evento), `nous-scripts` (minería de rutinas conductuales),
`nous-decision` (consejero prescriptivo Pro).

Agentes: `nous-keeper` (qué persistir), `nous-curator` (higiene de
loops/objetivos/fuentes), `nous-pathfinder` (metas → hitos → avance),
`nous-mirror` (observador somático), `nous-dreamer` (corrida de
consolidación-sueño auditable).

### Adoptar Nous en cualquier agente (meta-prompt)

Las skills empaquetadas enseñan **cómo** usar las tools de Nous — pero
el protocolo global de tu agente (dónde decide a qué memoria llamar)
vive en TU configuración: `CLAUDE.md`, `.cursorrules`,
`.windsurf/rules`, `AGENTS.md`, skills propios, prompts de sistema.
Si tu agente ya tiene reglas de memoria, las skills solas no bastan:
hay que enseñarle a **enrutar** a Nous.

Pégale este prompt a tu agente y deja que él mismo actualice su
configuración según sus convenciones — cada sistema la expresa
distinto:

```text
Acabo de conectar Nous, un servidor MCP de memoria 4D local-first.
Tu tarea: integrarlo en TU configuración global — no en este
proyecto ni en archivos de código.

1. Descubre cómo persistes memoria HOY: localiza tus archivos de
   reglas globales (AGENTS.md, CLAUDE.md, .cursorrules,
   .windsurf/rules, system prompt persistente, skills/agentes
   propios) y cualquier protocolo de memoria que ya uses.
2. Explora las tools del MCP `nous` ya conectado (o corre
   `nous skills list` si existe la CLI). Surface mínimo:
   contexto_inicial, consultar_memoria, recordar_evento,
   crear_intencion, listar_intenciones, compromisos_abiertos,
   obtener_linea_de_universo, proyectar_futuro, consolidar_memoria,
   minar_scripts.
3. Actualiza TUS reglas globales con el routing de memoria:
   - Eventos con tiempo/emoción/acción, hitos, compromisos y
     "recuérdame…" → recordar_evento / crear_intencion de Nous.
   - Consulta episódica ("¿qué pasó?", contexto vivido, estados)
     → consultar_memoria.
   - Si ya usas otro store (memoria nativa u otros MCP), define
     cuándo gana cada uno — nunca dupliques el mismo dato en dos.
4. CRÍTICO — recordatorios: las intenciones temporales de Nous NO
   disparan solas. No hay scheduler: disparan cuando se llama
   consultar_memoria/contexto_inicial (temporales) o cuando llega
   el evento trigger. Codifica en tus reglas: "al iniciar cada
   sesión, llamar contexto_inicial" — sin eso, los recordatorios
   quedan mudos.
5. Si tu sistema soporta skills/agentes: instala los oficiales con
   `nous skills install --target <tu directorio>` o escribe los
   tuyos siguiendo TUS convenciones de formato — lo importante es
   que tu yo-del-futuro sepa cuándo y cómo llamar a Nous.
6. Reporta qué tocaste: archivo por archivo y qué regla quedó en
   cada uno.
```

El paso 4 es el que marca la diferencia: Nous "recuerda cuando se lo
consulta" — un agente que nunca consulta jamás recibe un recordatorio.

## CLI

| Comando | Qué hace |
|---|---|
| `nous serve [--transport stdio\|http] [--host] [--port]` | Arranca el servidor |
| `nous doctor` | Diagnóstico del entorno |
| `nous db upgrade\|current\|heads\|history` | Migraciones Alembic |
| `nous stats` | Estadísticas de la memoria |
| `nous seed [--eventos N]` | Datos de ejemplo |
| `nous export <archivo.jsonl>` | Exporta toda la memoria |
| `nous import <archivo> [--fuente] [--rama]` | Importa JSONL/memlord/gbrain/Obsidian |
| `nous skills install --target .agents` | Instala skills/agentes |
| `nous prune [--dias 90] [--amplitud 0.3]` | Poda recuerdos débiles |
| `nous consolidar` | Corrida de sueño: promueve/fortalece/archiva + reporte |
| `nous intenciones` | Intenciones prospectivas pendientes |
| `nous archivados` | Memorias archivadas (recuperables) |
| `nous restaurar <uuid>` | Desarchiva una memoria consolidada |
| `nous benchmark` | Benchmark de búsqueda (persiste resultados) |
| `nous check-update` / `nous upgrade` | Gestión de versiones |
| `nous users create\|list` | Usuarios del sistema |
| `nous workspaces create\|list\|invite\|canjear` | Workspaces e invitaciones `ns_inv_*` |
| `nous activate-license <key>` | Activa licencia Pro → `~/.nous/license.jwt` |
| `nous keys create\|list\|revoke` | Keys `ns_u_*` por usuario (en claro una vez) |

Referencia completa: [docs/cli.md](docs/cli.md).

## Dashboard

**ADR-001**: el dashboard es un producto separado — repo hermano
`nous-dashboard` (licencia comercial), imagen propia, comunicación
exclusivamente vía la API REST de este servidor. **Sin el perfil
`dashboard` solo levanta la API+MCP en :8720 — no hay UI web incluida.**

```bash
git clone https://github.com/leoquintara/nous-dashboard.git ../nous-dashboard
cd ../nous-dashboard && yarn install && yarn dev    # http://localhost:3000
docker build -t nous-dashboard ../nous-dashboard    # imagen nginx:80
# o todo-orquestado desde este repo:
docker compose --profile dashboard up -d
```

Rutas: `/` cerebro 3D con replay de actividad · `/memoria` búsqueda ·
`/universo` línea temporal 4D interactiva · `/proyeccion` futuros ·
`/modelos` proveedores IA (primario + fallback) · `/fuentes` procedencias ·
`/trazas` (Logs) llamadas y pings · `/importar` migraciones visuales ·
`/benchmarks` · `/equipo` miembros/roles/invitaciones/licencia ·
`/unirse` canje público de invitación. Acceso: token `ns_*` o
usuario+contraseña si defines
`NOUS_DASHBOARD_USUARIO`/`NOUS_DASHBOARD_PASSWORD`. El nav incluye
switcher de workspace — el activo viaja en todas las llamadas.

Detalle: `nous-dashboard/docs/dashboard.md`.

## Migraciones de memoria

Desde la UI `/importar` o `nous import`:

- **Archivos**: JSONL nativo, exports de memlord/gbrain, vaults de
  Obsidian (wikilinks → vínculos).
- **Instancias remotas**: gbrain y memlord se importan **vía API MCP**
  (URL + token) — preview sin escribir, ejecución idempotente e
  historial de migraciones.

## API REST (resumen)

`POST /api/eventos` · `GET /api/buscar` · `GET /api/eventos` ·
`DELETE /api/eventos/{id}` · loops, objetivos, contexto-inicial, tags,
fuentes (+ramificar), relaciones, proyección, preferencias, proveedores IA,
actividad, benchmarks, llamadas, ping, importar · patrones ADR-003
(`/api/intenciones`, `/api/consolidar`, `/api/consolidacion/reportes`,
`/api/scripts`, `/api/archivados`, `/api/eventos/{id}/restaurar`) ·
consejero Pro `/api/decision` (402 sin licencia — ADR-002 Nivel 2) ·
identidad (`/api/identidad`, `/api/workspaces/*`, `/api/llaves`,
`/api/invitaciones/canjear` público). Referencia:
[docs/api.md](docs/api.md).

## Configuración (`.env`)

| Variable | Default | Propósito |
|---|---|---|
| `NOUS_DATABASE_URL` | `postgresql+asyncpg://…:5432/nous` | Conexión |
| `NOUS_API_TOKEN` | (vacío = dev local) | Bearer `ns_*` |
| `NOUS_MCP_TRANSPORT` | `stdio` | `stdio` o `http` |
| `NOUS_API_HOST/PORT` | `127.0.0.1:8720` | REST + MCP HTTP |
| `NOUS_EMBEDDING_MODEL` | `all-MiniLM-L12-v2` | Embeddings locales |
| `NOUS_HF_OFFLINE` | `true` | Nunca contacta HuggingFace |
| `NOUS_RERANKER_HABILITADO` | `true` | Cross-encoder local |
| `NOUS_PRECALENTAR_MODELOS` | `true` | Warm-up al arrancar |
| `NOUS_CLAVE_CIFRADO` | derivada del token | Passphrase pgcrypto |
| `NOUS_COMMIT_SINCRONO` | `false` | fsync por commit |
| `NOUS_API_KEY` | (owner local) | Key `ns_u_*` para identidad en stdio |
| `NOUS_LICENSE_KEY` | (vacío = edición libre) | Desbloquea multi-tenant completo |
| `NOUS_LICENSE_PUBLIC_KEY` | — | PEM para verificar firma RS256 del JWT |
| `NOUS_LICENSE_ACTIVATION_URL` | — | Servicio de activación (serial → JWT) |
| `NOUS_MAX_USUARIOS_LIBRES` | `1` | Límite sin licencia |
| `NOUS_MAX_WORKSPACES_LIBRES` | `3` | Límite sin licencia |
| `NOUS_DECISION_PROVIDER` | `auto` | `none/auto/laya/ollama/jev` — motor System One |
| `NOUS_OLLAMA_ENDPOINT` | `http://localhost:11434` | Provider Ollama local |
| `NOUS_OLLAMA_MODEL` | `tev1:0.8b` | Modelo Ollama |
| `NOUS_JEV_API_KEY` | — | Provider Jev (requiere flag de privacidad) |
| `NOUS_PRIVACY_PERMITIR_EXTERNOS` | `false` | Consentimiento para providers externos |

## Desarrollo

```bash
poetry run pytest --cov=src        # tests (unit + integración nous_test)
poetry run ruff check src/
poetry run mypy --strict src/
cd ../nous-dashboard && yarn vitest run && yarn tsc --noEmit
```

Convenciones en [AGENTS.md](AGENTS.md) — capas estrictas
MCP → Core → Storage, tipado estricto, Conventional Commits.

## Documentación

- [docs/architecture.md](docs/architecture.md) — capas, esquema, flujos
- [docs/api.md](docs/api.md) — referencia REST
- [docs/cli.md](docs/cli.md) — referencia CLI
- `nous-dashboard/docs/dashboard.md` — features del dashboard (repo propio)
- [docs/deployment.md](docs/deployment.md) — auto-hospedaje productivo
- [docs/security.md](docs/security.md) — modelo de seguridad
- [docs/neuroscience.md](docs/neuroscience.md) — bases del modelo 4D

## Licencia

AGPL 3.0 open source — ver [LICENSE](LICENSE).
Licencia comercial y precios: [nousmemory.com](https://nousmemory.com) —
licensing@quintarai.com

Nous es un producto de Quintara AI. © 2026 Quintara AI.
