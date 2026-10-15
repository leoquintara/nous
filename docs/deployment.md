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
# ↑ REST + MCP + dashboard estático en :8720 (usa dashboard/out si
#   existe); además levanta el dev server Next en :3000 con hot-reload.
#   --sin-dashboard lo omite.
```

## Docker completo

El servicio `app` está detrás del profile `app`:

```bash
docker-compose --profile app up -d
```

levanta `nous-db` (pgvector/pgvector:pg18, volumen `nous_pgdata`) y
`nous-app` (REST + MCP HTTP en :8720). La imagen se construye con el
`Dockerfile` raíz.

## Instalación como comando global (pipx)

```bash
cd dashboard && npm ci && npm run build:export   # genera out/
cp -r out ../src/dashboard_static                # empaqueta el build
cd .. && pipx install .                          # instala nous + dashboard
mkdir -p ~/.config/nous && cp .env ~/.config/nous/.env
nous serve --transport http   # API + MCP + dashboard en :8720
```

Sin `.env` junto al código, Nous lee `~/.config/nous/.env` (POSIX) —
en Windows `%USERPROFILE%\.config\nous\.env`. El dashboard va dentro
del wheel: `pipx install` desde un checkout sin `src/dashboard_static`
instala solo API+MCP.

## Producto en un servidor propio

1. **PostgreSQL 18 + pgvector** con backup propio (pg_dump programado —
   la memoria es tu dato más valioso).
2. **Token obligatorio**: genera `NOUS_API_TOKEN=ns_$(openssl rand -hex 32)`;
   sin token la API queda abierta (solo aceptable en localhost).
3. **Bind**: `NOUS_API_HOST` — `127.0.0.1` si los agentes corren en la
   misma máquina; expón `0.0.0.0` solo detrás de firewall/VPN, nunca
   directo a internet (usa un reverse proxy con TLS si necesitas acceso
   remoto).
4. **Login del dashboard**: `NOUS_DASHBOARD_USUARIO` +
   `NOUS_DASHBOARD_PASSWORD` si el dashboard se sirve en red.
5. **Cifrado de secrets LLM**: `NOUS_CLAVE_CIFRADO` (si no se define se
   deriva del token — configurarlo explícito si rotas el token).
6. **Durabilidad**: `NOUS_COMMIT_SINCRONO=true` si prefieres fsync por
   commit a ~0.5s de riesgo de escritura.
7. **Modelos**: `NOUS_HF_OFFLINE=true` exige los modelos en caché local;
   descárgalos una vez con `NOUS_HF_OFFLINE=false`.

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
