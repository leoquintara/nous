# Nous

**Memoria persistente 4D para agentes de IA — local-first y auto-hospedable.**
Un producto de Quintara AI ([quintarai.com](https://quintarai.com)).

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
└────────────┘               │  (25 tools)  │   │  Next.js 16  │
                             └──────┬───────┘   └──────┬───────┘
┌────────────┐                     │                   │ REST+token
│  CLI `nous`│ ────────┐     ┌─────▼───────────────────▼─────┐
└────────────┘         ├────▶│         src/core              │
                       │     │  clasificador · embeddings    │
                       │     │  búsqueda híbrida · temporal  │
                       │     │  predicción IA · trazas       │
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

Requisitos: Python 3.12+, Poetry, PostgreSQL 18 con pgvector
(o Docker para `docker-compose`).

```bash
git clone <repo> && cd nous
cp .env.example .env             # edita valores (ver "Configuración")
docker-compose up -d             # PostgreSQL 18 + pgvector
poetry install
poetry run nous db upgrade       # migraciones Alembic
poetry run nous serve            # MCP stdio (default)
```

Verificación del entorno:

```bash
poetry run nous doctor           # DB, modelos en caché, token
poetry run nous stats            # conteos por dimensión
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
`nous-somatic` (mapa de señales).

Agentes: `nous-keeper` (qué persistir), `nous-curator` (higiene de
loops/objetivos/fuentes), `nous-pathfinder` (metas → hitos → avance),
`nous-mirror` (observador somático).

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
| `nous benchmark` | Benchmark de búsqueda (persiste resultados) |
| `nous check-update` / `nous upgrade` | Gestión de versiones |
| `nous users create\|list` | Usuarios del sistema |
| `nous workspaces create\|list\|invite\|canjear` | Workspaces e invitaciones `ns_inv_*` |
| `nous keys create\|list\|revoke` | Keys `ns_u_*` por usuario (en claro una vez) |

Referencia completa: [docs/cli.md](docs/cli.md).

## Dashboard

```bash
cd dashboard && npm install && npm run dev    # http://localhost:3000
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

Detalle: [docs/dashboard.md](docs/dashboard.md).

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
actividad, benchmarks, llamadas, ping, importar · identidad
(`/api/identidad`, `/api/workspaces/*`, `/api/llaves`,
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
| `NOUS_MAX_USUARIOS_LIBRES` | `1` | Límite sin licencia |
| `NOUS_MAX_WORKSPACES_LIBRES` | `3` | Límite sin licencia |

## Desarrollo

```bash
poetry run pytest --cov=src        # tests (unit + integración nous_test)
poetry run ruff check src/
poetry run mypy --strict src/
cd dashboard && npm test && npx tsc --noEmit
```

Convenciones en [AGENTS.md](AGENTS.md) — capas estrictas
MCP → Core → Storage, tipado estricto, Conventional Commits.

## Documentación

- [docs/architecture.md](docs/architecture.md) — capas, esquema, flujos
- [docs/api.md](docs/api.md) — referencia REST
- [docs/cli.md](docs/cli.md) — referencia CLI
- [docs/dashboard.md](docs/dashboard.md) — features del dashboard
- [docs/deployment.md](docs/deployment.md) — auto-hospedaje productivo
- [docs/security.md](docs/security.md) — modelo de seguridad
- [docs/neuroscience.md](docs/neuroscience.md) — bases del modelo 4D

## Licencia

AGPL 3.0 open source — ver [LICENSE](LICENSE).
Licencia comercial: licensing@quintarai.com

Nous es un producto de Quintara AI. © 2026 Quintara AI.
