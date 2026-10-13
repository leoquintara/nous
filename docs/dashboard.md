# Dashboard Nous — Features y decisiones de arquitectura

> Next.js 16 (App Router, Turbopack) + TypeScript estricto + arquitectura hexagonal.
> Estilo: liquid glass + glassmorphism con `backdrop-filter`, paleta Quintara AI.

## Mapa de features

| Ruta | Feature | Consume |
|---|---|---|
| `/login` | Acceso por token `ns_*` o usuario+contraseña (POST `/api/login`) | `crearAuth` |
| `/` (Cerebro) | Cerebro 3D animado con membrana, neuronas por dimensión, pulsos sinápticos y selección de evento | eventos vía `listarEventos` |
| `/memoria` | Búsqueda híbrida 4D con contexto temporal antes/después | `GET /api/buscar` |
| `/proyeccion` | Distribución dimensional proyectada + trayectorias activas | `POST /api/proyeccion`, `GET /api/proyeccion/actual` |
| `/benchmarks` | Corridas persistidas con métricas y commit | `GET /api/benchmarks` |

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

### 3. Cerebro 3D: canvas 2D con proyección propia, sin Three.js

Migración del plugin DSH Memory. Se mantuvo el renderer manual (no WebGL) porque:
el grafo tiene ~300 nodos y canvas 2D lo sobra; cero dependencias pesadas;
la lógica física (springs, repulsión, LOD, pulsos, cascadas, membrana) se tipó
en módulos separados:

| Módulo | Responsabilidad |
|---|---|
| `tipos.ts` | Neurona, Arista, Membrana, Pulso — dominio del grafo |
| `geometria.ts` | Regiones cerebrales procedurales, membrana con ruido |
| `fisica.ts` | Integración de springs/repulsión por frame, cascadas |
| `CerebroCanvas.tsx` | Loop rAF, proyección 3D→2D, hit-testing, panel de evento |

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
