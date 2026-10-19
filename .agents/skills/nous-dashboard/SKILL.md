---
name: nous-dashboard
description: Convenciones del dashboard Nous — Next.js 16, TypeScript estricto, yarn-only, arquitectura hexagonal, liquid glass + glassmorphism, paleta Quintara, hidratación SSR-safe. Usar al editar dashboard/, crear componentes, arreglar errores de hidratación (#418) o elegir dependencias del frontend.
---

# Nous Dashboard — convenciones

## Stack

- Next.js 16 (App Router) + TypeScript estricto.
- Estilos: CSS modules o Tailwind con tokens CSS custom properties.
- Iconos: Iconoir (`iconoir-react`).
- 3D: Three.js + React Three Fiber (migración del plugin DSH Memory).
- Arquitectura hexagonal: `domain/` (puros), `application/` (casos de uso),
  `infrastructure/` (adaptadores HTTP/MCP), `ui/` (componentes).

## Tema visual — liquid glass

- `backdrop-filter: blur()` + `saturate()` en tarjetas, nav, modales.
- Bordes translúcidos `rgba(255,255,255,0.18)` sobre fondos con gradiente sutil.
- Sombras suaves multicapa, radios generosos (16-24px).

## Paleta Quintara AI

| Token | Light | Dark |
|-------|-------|------|
| primary | `#198BF8` | `#4aa4fa` |
| secondary | `#00D6D4` | `#2de3e1` |
| ink | `#101050` | `#e8e8ff` |
| surface | `#f6f6f6` | `#14142b` |
| line | `#E7E7F2` | `rgba(231,231,242,0.12)` |
| danger | `#c51533` | `#ff5570` |
| success | `#00c850` | `#2edb72` |

Definir como CSS custom properties bajo `:root` y `[data-theme="dark"]`.
Nunca hardcodear hex en componentes — siempre `var(--token)`.

## Docstrings

- Cada método público lleva JSDoc **en español** (`/** Busca eventos... */`).
- Nomenclatura en español para dominio (`evento`, `dimension`), inglés para
  mecanismo técnico (`useEvents`, `fetchJson`).

## Toolchain — yarn, nunca npm

- Gestor: **yarn 4** (`packageManager: yarn@4.18.1`, `.yarnrc.yml` con
  `nodeLinker: node-modules`). Nunca `npm install` — no existe
  `package-lock.json` a propósito.
- Verificación antes de commit: `yarn vitest run && yarn tsc --noEmit && yarn eslint --max-warnings 0`.

## Estado del cliente y SSR

- Hooks que leen `localStorage` llevan prefijo `use` (`useWorkspace`)
  o eslint `react-hooks` los marca como condicionales.
- Estado derivado de localStorage/mediaQuery → `useSyncExternalStore`
  con `getServerSnapshot` que coincida con el HTML prerendereado
  (CSS `:root` = light). Leer `localStorage` en lazy-init de
  `useState` causa mismatch de hidratación **#418** en el export
  estático (no se reproduce en `next dev`).
- API same-origin: `BASE_URL` se resuelve por host/request; el
  token vive en localStorage y el adaptador lo adjunta.

## Sidebar y tema

- Shell colapsable con rail de iconos; tema claro/oscuro vía
  `useSyncExternalStore` (ver `tema.tsx`), CSS custom properties
  bajo `[data-theme="dark"]`.
