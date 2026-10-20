# CLI de Nous

La CLI `nous` es la interfaz de operación del sistema: arranque del
servidor, diagnóstico, migraciones, transferencia de memoria y
actualización de versiones. No añade dependencias — usa `argparse`
(stdlib) y delega toda la lógica en `src.core`/`src.storage`.

## Instalación

```bash
poetry install          # registra el script `nous` en el venv
nous --help             # o: python -m src.cli --help
pipx install .          # comando `nous` global (como gbrain)
```

La salida usa colores ANSI cuando la terminal es TTY — `NO_COLOR=1` o
`NOUS_SIN_COLOR=1` los desactiva.

## Serve

- **`nous serve`** (stdio): transporte para clientes MCP — el proceso
  habla JSON-RPC por stdin/stdout. En una terminal interactiva, cada
  línea vacía produce un aviso de protocolo inofensivo.
- **`nous serve --transport http`**: API REST + MCP HTTP en
  `NOUS_API_HOST:PORT` (`/api/*`, `/mcp`). El dashboard es un producto
  separado (repo `nous-dashboard`, ADR-001): si existe el repo hermano
  `../nous-dashboard`, el CLI levanta su dev server con `yarn dev` en
  :3000 — `--sin-dashboard` lo omite — y si hay un `out/` exportado,
  también se sirve estático en `/`. Antes de arrancar hace un
  pre-flight a PostgreSQL: si no responde, aborta con un mensaje claro
  en lugar de un traceback.

  El wheel ya no empaqueta el dashboard — la forma oficial es la
  imagen propia de `nous-dashboard` (o `yarn dev`); el serving
  estático single-process queda como conveniencia local. La
  configuración se lee de `~/.config/nous/.env` cuando no hay `.env`
  junto al código.

  Auth: `/api/*` y `/mcp` exigen `Bearer <NOUS_API_TOKEN>`; el HTML
  estático del dashboard es público (no contiene datos) — el flujo
  es abrir `/login`, pegar el token, y la app llama `/api` con él.

## Referencia de comandos

| Comando | Qué hace | Salida JSON |
|---|---|---|
| `nous serve [--transport stdio\|http] [--host] [--port] [--sin-dashboard]` | Arranca el servidor MCP/HTTP; en http también levanta `yarn dev` del dashboard si existe el repo `../nous-dashboard` | — |
| `nous version` | Versión, método de instalación, Python | `--json` |
| `nous check-update` | Compara la versión instalada con la disponible | `--json` |
| `nous upgrade [--no-migrate] [--yes]` | Actualiza el código y migra la DB | `--json` |
| `nous doctor` | Diagnóstico: DB, modelos en caché, token | `--json` |
| `nous db upgrade\|current\|heads\|history` | Delega en Alembic | — |
| `nous seed [--eventos N]` | Siembra eventos de ejemplo | — |
| `nous stats` | Total y desglose por dimensión | `--json` |
| `nous export <archivo.jsonl>` | Vuelca la memoria a JSONL | — |
| `nous import <archivo> [--fuente F] [--rama R]` | Importa JSONL / memlord / gbrain / Obsidian | — |
| `nous skills install --target .agents` | Instala skills/agentes en tu agente | — |
| `nous skills list` | Lista los artefactos empaquetados | — |
| `nous prune [--dias 90] [--amplitud 0.3]` | Poda eventos viejos y débiles | — |
| `nous benchmark` | Lanza `scripts/benchmark_search.py` | — |
| `nous users create\|list` | Crea usuarios (handle único) y los lista | `--json` |
| `nous workspaces create\|list\|invite\|canjear` | Workspaces e invitaciones `ns_inv_*` de un solo uso | `--json` |
| `nous keys list\|create\|revoke` | Keys `ns_u_*` por usuario — en claro solo al emitir | `--json` |

## Import

`--fuente` acepta: `jsonl` (default), `memlord`, `gbrain`, `obsidian`.
`--rama` asigna una rama distinta de `main` a lo importado. Archivos y
directorios se importan de forma idempotente (ids duplicados se omiten).

Para instancias **remotas** de gbrain/memlord, la importación se hace
vía API MCP desde el dashboard (`/importar`) o
`POST /api/importar` con `{fuente, url, token}` — la CLI solo maneja
archivos.

## Skills y agentes

`nous skills install --target .agents` copia los artefactos empaquetados
al directorio de tu agente (`--target` es ruta completa o un nombre
corto como `.agents`, `.claude`, `.cursor`, `.opencode`). Skills:
`nous-remember`, `nous-recall`, `nous-context`, `nous-universe`,
`nous-preferences`, `nous-checkin`, `nous-somatic`. Agentes:
`nous-keeper`, `nous-curator`, `nous-pathfinder`, `nous-mirror`.

## Prune

`nous prune` elimina eventos más viejos que `--dias` con amplitud por
debajo de `--amplitud`. No toca hechos, objetivos ni compromisos
abiertos — solo eventos débiles y envejecidos.

Códigos de salida: `0` éxito, `1` error de ejecución, `2` uso
incorrecto (argparse).

## Upgrade de versiones (modelo gbrain)

Inspirado en `gbrain upgrade`/`check-update`: detecta el método de
instalación y ejecuta el flujo apropiado.

### Detección del método

- **`fuente`**: checkout git con `pyproject.toml` (desarrollo /
  self-hosted desde repo).
- **`paquete`**: instalado vía pip/pipx (distribución empaquetada).

### `check-update`

- Fuente con remoto: `git fetch --tags` y cuenta commits por detrás
  del upstream (`git rev-list HEAD..@{u}`); reporta el último tag
  `v*` como versión reciente.
- Fuente sin remoto: `actualizacion_disponible: null` — no hay fuente
  de versiones, se reporta honestamente.
- Paquete: consulta el JSON del índice PyPI
  (`pypi.org/pypi/nous-memory/json`) y compara versiones
  numéricamente (`1.10.0 > 1.9.9`).
- **Homónimo PyPI**: el paquete se publica como `nous-memory` — el
  nombre `nous` en PyPI es de otro proyecto. Si la metadata del
  índice no identifica a Quintara AI como autor, `check-update`
  reporta "sin índice propio" y `upgrade` **aborta** antes de
  ejecutar `pip install --upgrade nous-memory` — nunca instala
  código ajeno. Hasta publicar, la vía de upgrade de una
  instalación pipx es `pipx install --force <ruta-del-repo>`.

### `upgrade`

1. **Preflight** (fuente): aborta si el árbol tiene cambios sin
   commit — nunca pisa trabajo local.
2. **Código**: `git pull --ff-only` (nunca merge ni rebase; falla
   limpio si la historia diverge) o `pip install --upgrade nous-memory`.
   Sin remoto, el pull se omite y solo corren deps + migraciones.
3. **Dependencias**: `poetry install` (fuente).
4. **Post-upgrade**: `alembic upgrade head` — equivalente al
   post-install de gbrain; `--no-migrate` lo omite (equivalente a
   `--swap-only`).

Cada paso se reporta con `✓`/`✗` y el comando falla con código 1 si
cualquier paso falla — nunca deja el sistema a medio actualizar en
silencio.

## Export/Import (portabilidad local-first)

`nous export` vuelca JSONL con los campos semánticos del evento —
**nunca el embedding**: al importar se regenera con el modelo local
configurado, así el archivo sigue siendo válido si cambia el proveedor
o el modelo. El import es idempotente: ids existentes se omiten,
líneas corruptas se cuentan como errores sin abortar.

```jsonl
{"id": "…", "timestamp_unix": 1757…, "dimension": "cognitiva",
 "fuente": "seed", "contenido": "…", "amplitud": 0.7, "frecuencia": null}
```

## Privacidad

- `check-update` en modo paquete es la única operación con red; en
  modo fuente solo toca el remoto git ya configurado. Ningún dato de
  memoria sale de la máquina.
- `doctor` reporta metadata (conteos, booleans) — nunca contenido.

## Tests

```bash
pytest tests/unit/test_cli.py tests/unit/test_upgrade.py   # sin I/O
pytest tests/integration/test_cli.py                        # vs nous_test
```
