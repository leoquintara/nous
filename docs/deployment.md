# Deployment de Nous

Nous es **self-hosted por diseño**: corre donde viva tu PostgreSQL.

## Desarrollo local

```bash
cp .env.example .env
docker-compose up -d            # solo PostgreSQL 18 + pgvector
poetry install
poetry run nous db upgrade
poetry run nous serve           # MCP stdio
poetry run nous serve --transport http
# ↑ REST + MCP en :8720. El dashboard (repo nous-dashboard, ADR-001)
#   corre aparte: yarn dev en :3000, o su build out/ hermano se sirve
#   estático en :8720 automáticamente. --sin-dashboard omite el dev
#   server.
```

## Docker completo

El servicio `app` está detrás del profile `app`; el dashboard separado
detrás de `dashboard`:

```bash
# .env: define NOUS_API_TOKEN (obligatorio — el build falla sin él)
docker compose --profile app up -d          # db + app (API+MCP :8720)
docker compose --profile dashboard up -d    # + nous-dashboard :3000
```

levanta `nous-db` (pgvector/pgvector:pg18, volumen `nous_pgdata`) y
`nous-app` — una imagen (~1.5GB, ONNX-only sin torch) con API REST +
MCP HTTP + modelos ONNX horneados (embeddings MiniLM + reranker).
`http://localhost:8720` sirve `/api/*` y `/mcp`; si el repo hermano
`nous-dashboard` tiene un `out/` generado, también lo sirve en `/`
(single-process local). El entrypoint corre `alembic upgrade head`
en cada arranque — DB fresca migra sola.

El dashboard vive en `../nous-dashboard` (ADR-001 — licencia
comercial, frontera legal = API REST): imagen propia nginx en :80
expuesta en :3000, build `docker build ../nous-dashboard`.

Notas de la imagen:

- La imagen app ya no compila el dashboard — ese stage vive en el
  Dockerfile del repo `nous-dashboard` (export estático → nginx).
- Los modelos se descargan UNA vez en el build
  (`NOUS_HF_OFFLINE=false` solo ahí); el contenedor corre con
  `HF_OFFLINE=true` y funciona sin red.
- Volumen postgres: mount en `/var/lib/postgresql` (postgres:18+
  versiona el PGDATA en subdirectorios — montar `/data` rompe).
- Healthchecks en ambos servicios; `docker compose ps` muestra
  `(healthy)` cuando está listo.
- `skills` como primer argumento bypasea el servidor: instala los
  skills/agentes empaquetados en el bind-mount `/agents` sin tocar
  la DB (ver README §Skills). `NOUS_SKILLS_TARGET` cambia el destino
  interno si montas en otra ruta.

## Instalación como comando global (pipx)

```bash
pipx install .                          # instala nous (API + MCP)
mkdir -p ~/.config/nous && cp .env ~/.config/nous/.env
nous serve --transport http   # API + MCP en :8720
```

Sin `.env` junto al código, Nous lee `~/.config/nous/.env` (POSIX) —
en Windows `%USERPROFILE%\.config\nous\.env`. ADR-001: el dashboard
ya no viaja en el wheel — corre aparte (su imagen o `yarn dev` en el
repo `nous-dashboard`).

## Producto en un servidor propio

1. **PostgreSQL 18 + pgvector** con backup propio (pg_dump programado —
   la memoria es tu dato más valioso).
2. **Token obligatorio**: genera `NOUS_API_TOKEN=ns_$(openssl rand -hex 32)`;
   sin token la API queda abierta (solo aceptable en localhost).
3. **Bind**: `NOUS_API_HOST` — `127.0.0.1` si los agentes corren en la
   misma máquina; expón `0.0.0.0` solo detrás de firewall/VPN, nunca
   directo a internet (usa un reverse proxy con TLS si necesitas acceso
   remoto).
4. **Login del dashboard** (repo `nous-dashboard`): el acceso se
   configura en su propia config — consulta `nous-dashboard/README.md`.
5. **Cifrado de secrets LLM**: `NOUS_CLAVE_CIFRADO` (si no se define se
   deriva del token — configurarlo explícito si rotas el token).
6. **Durabilidad**: `NOUS_COMMIT_SINCRONO=true` si prefieres fsync por
   commit a ~0.5s de riesgo de escritura.
7. **Modelos**: `NOUS_HF_OFFLINE=true` exige los modelos en caché local;
   descárgalos una vez con `NOUS_HF_OFFLINE=false`.
8. **Licencia (multi-tenant)**: sin `NOUS_LICENSE_KEY` la edición libre
   permite **1 usuario y 3 workspaces** — suficiente para uso personal
   self-hosted. Con licencia se levantan los límites. El estado se
   consulta en `GET /api/identidad` (`licencia.activa`) y en el
   dashboard `/equipo`.
9. **Agentes por usuario**: los clientes MCP stdio usan `NOUS_API_KEY`
   (key `ns_u_*` del usuario) en el entorno del proceso; ausente, stdio
   actúa como el owner local. En HTTP cada agente lleva su propia
   `Authorization: Bearer ns_u_*`.

## Consolidación-sueño periódica

La consolidación (ADR-003) es **pull-based por diseño**: se invoca
cuando conviene, nunca en segundo plano sorpresa. Para automatizarla:

```bash
# cron (Linux/macOS) — sueño diario a las 03:00
0 3 * * * /usr/local/bin/nous consolidar --json >> /var/log/nous-sueno.log

# Task Scheduler (Windows) — mismo efecto
schtasks /create /tn "NousSueno" /sc daily /st 03:00 ^
  /tr "C:\ruta\a\nous.exe consolidar --json"
```

En Docker el comando corre contra el contenedor:
`docker exec nous-app python -m src.cli.main consolidar --json`.
Cada corrida persiste un `reportes_consolidacion` auditable —
revisables vía MCP (`reportes_consolidacion`), REST
(`/api/consolidacion/reportes`) o el agente `nous-dreamer`.

## Upgrades

```bash
nous check-update      # fuente: compara con upstream git; paquete: PyPI
nous upgrade           # pull --ff-only + poetry install + db upgrade
```

Nunca toca datos de memoria — solo código, dependencias y migraciones.

## Migraciones de base de datos

```bash
nous db current    # revisión aplicada
nous db upgrade    # aplica pendientes
nous db history    # historial
```

Las migraciones son la única vía de cambio de esquema (regla AGENTS).
Para empezar limpio sin borrar el esquema:

```sql
TRUNCATE eventos, relaciones, objetivos, trayectorias, proyecciones,
         benchmarks, llamadas, fuentes RESTART IDENTITY CASCADE;
```

(`configuracion` se conserva: guarda proveedores IA y preferencias —
también se puede truncar para un reset total).
