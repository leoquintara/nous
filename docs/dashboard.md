# Dashboard Nous — Features y decisiones de arquitectura

> Next.js 16 (App Router, Turbopack) + TypeScript estricto + arquitectura hexagonal.
> Estilo: liquid glass + glassmorphism con `backdrop-filter`, paleta Quintara AI.

## Mapa de features

| Ruta | Feature | Consume |
|---|---|---|
| `/login` | Acceso por token `ns_*` o usuario+contraseña (POST `/api/login`) | `crearAuth` |
| `/` (Cerebro) | Cerebro 3D con membrana, nodos por dimensión, replay de actividad en 2 olas (nodo + relaciones), picking en espacio de pantalla, panel de procedencia por fuente | `GET /api/actividad`, `GET /api/eventos` |
| `/memoria` | Búsqueda híbrida 4D con contexto temporal antes/después | `GET /api/buscar` |
| `/universo` (tab "Timeline") | Línea temporal 4D interactiva: cursor, cauce recorrido luminoso (shader `uRecorrido`), ramas, fantasmas de futuro, eventos destacados por amplitud | `GET /api/eventos`, `/api/fuentes`, `/api/objetivos` |
| `/proyeccion` | Distribución dimensional + futuros re-ponderados por LLM (PanelIA manual) | `POST /api/proyeccion`, `/api/ia/predecir` |
| `/modelos` | Proveedores IA: primario + fallback, keys enmascaradas, probar conexión | `/api/ia/proveedores` |
| `/fuentes` | Fuentes de memoria, ramificación de universo | `/api/fuentes` |
| `/trazas` (tab "Logs") | Trazas de llamadas MCP/REST, agregados, pings | `/api/llamadas`, `/api/llamadas/stats` |
| `/importar` | Migraciones visuales: JSONL/memlord/gbrain/Obsidian por archivo + gbrain/memlord por URL+token vía API MCP | `POST /api/importar`, `/api/importar/historial` |
| `/benchmarks` | Corridas persistidas con métricas y commit | `GET /api/benchmarks` |
| `/equipo` | Miembros y roles del workspace activo, invitaciones `ns_inv_*`, keys propias, estado de licencia | `/api/identidad`, `/api/workspaces/*`, `/api/llaves` |
| `/unirse` | Canje público de invitación (sin login): token + handle → key `ns_u_*` mostrada una vez | `POST /api/invitaciones/canjear` |

El nav compartido incluye **switcher de workspace** (el activo viaja
como `?workspace=` en todas las llamadas y como campo en los POST),
toggle de tema, fullscreen nativo y `backdrop-filter: blur(10px)`.
Los eventos muestran badge `personal`/`workspace` (scope). Todas las
acciones de IA (PanelIA, "Analizar con IA") son manuales — ninguna se
dispara sola.

## Arquitectura hexagonal

```
src/
├── domain/modelos.ts          # Tipos puros: Evento, Proyeccion, BenchmarkRun
├── application/puertos.ts     # Interfaces: PuertoMemoria, PuertoAuth
├── infrastructure/nous-api.ts # Adaptador HTTP (fetch + Bearer) implementa los puertos
└── ui/                        # React: ProveedorApp (contexto), Shell (nav), brain/
```

- La UI **solo conoce los puertos**, nunca `fetch` directo. Cambiar HTTP por otra
  fuente (WebSocket, local cache) = nuevo adaptador en `infrastructure/`, cero
  cambios en páginas.
- `ProveedorApp` inyecta el `PuertoMemoria` ya autenticado por contexto React.

## Decisiones

### 1. Tema por `data-theme` en `<html>`, no por clase ni prefers-color-scheme solo

`ProveedorApp` hidrata el tema desde `localStorage` (fallback `prefers-color-scheme`)
y escribe `document.documentElement.dataset.theme`. Las variables CSS viven bajo
`:root` y `[data-theme="light"]` en `globals.css` — así el toggle es un flip de
atributo sin re-render del árbol y sin flash.

### 2. Token en localStorage, validado contra la API antes de aceptarlo

`/login` llama `verificarToken` (GET `/api/eventos?limite=1`) antes de persistir:
un token malo nunca entra a la sesión. El modo usuario+contraseña intercambia
credenciales por el token vía `POST /api/login` (opcional, requiere
`NOUS_DASHBOARD_USUARIO/PASSWORD`).

### 3. Cerebro 3D: Three.js con `InstancedMesh` y shaders propios

Migrado del renderer canvas 2D a WebGL real (Three.js): nodos como
`InstancedMesh` (un solo draw call), materiales con uniforms para
nacimiento/LOD/atenuación, pulsos sinápticos viajeros y membrana
procedural.

- **Picking en espacio de pantalla**: proyectar cada nodo a píxeles y
  elegir el más cercano a ≤13px — el raycast contra instancias atenuadas
  por LOD fallaba en nodos pequeños.
- **Replay de actividad en 2 olas**: semilla → arco al nodo → hasta 6
  arcos a sus relaciones (explícitas > semánticas por peso > vecinos
  estructurales, máx. 48 por replay), con etiquetas forzadas durante el
  efecto.
- **`src/ui/universo/materiales.ts`**: shaders del cauce temporal —
  `uRecorrido` ilumina solo el tramo ya vivido (inicio→cursor), atenúa
  el futuro y marca la cabeza con brillo suave; los fantasmas conservan
  brillo por probabilidad.
- El LOD es decisión de **render**, no de interacción — todo nodo nacido
  es seleccionable.

### 4. Liquid glass = dos superficies

`.vidrio` (translúcida, `backdrop-filter: blur` medio) para tarjetas/inputs;
`.vidrio-fuerte` (más blur + saturación) para nav y contenedores primarios.
Variables `--halo`, `--linea`, `--texto-suave` por tema.

### 5. Iconoir, nada de emojis ni SVGs ad-hoc

Brain/Search/Timer/Refresh/Key/User/HalfMoon/SunLight/LogOut de `iconoir-react`.
Icon-only buttons siempre con `aria-label`.

### 6. E2E real en navegador

`scripts/e2e_browser.js` (Playwright-core + Edge headless — Chrome del sistema
está roto con error SxS): login con token → home/canvas → las 3 páginas →
toggle de tema → captura de errores de consola. Requiere API en :8720 y dev
server en :3000.

## Dependencia de infra

La API REST vive en `src/main.py` (`NOUS_MCP_TRANSPORT=http`, puerto 8720) y
comparte la app ASGI con el transporte MCP: REST bajo `/api`, MCP en `/mcp`,
`/health` público, todo lo demás detrás de `BearerTokenMiddleware`. CORS abierto
solo a `localhost:3000`.
